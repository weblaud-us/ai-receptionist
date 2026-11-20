"""
Per-tenant background jobs (Roadmap Tasks 2.8 and 2.9), run every TENANT_JOBS_POLL_SECONDS by the
jobs worker (jobs/worker.py) when REDIS_URL is set, otherwise by the API process:

- calendar sync   — refresh Google busy times every CALENDAR_SYNC_MINUTES
- reminders       — day-before and hour-before reminders for appointments (generalises the
                    hotel's services/reminders.py). Each reminder is claimed with an atomic
                    update first, so a restart or a second runner never sends it twice.
- daily digest    — calls, outcomes, bookings, waiting messages and unanswered questions,
                    e-mailed to the owner once a day at DAILY_DIGEST_HOUR (business-local).
- transcript purge — strips transcripts and per-turn rows from calls older than the tenant's
                    `agent.transcript_retention_days` (Task 2.2). The call record itself (who,
                    when, outcome, summary, usage) is kept for billing and statistics.
- recording purge — deletes recordings older than `agent.recording.retention_days` (Phase 6).
- monthly report  — "your assistant this month" on the 1st (Phase 7, Task 7.3).
- pilot report    — weekly scorecard to pilot businesses on Mondays (Phase 7, Task 7.1).

Every reminder and digest is claimed with an atomic update before it is sent, so several job
workers (or a retry) never send one twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings
from core.integrations.email import send_email
from core.notifications import notify_contact
from core.repositories import Repos
from core.repositories.repos import TenantsStore
from core.services import availability as av
from core.services import calendar_sync
from core.tenancy.models import TenantConfig
from core.log import set_tenant
from database.mongodb import get_db

logger = logging.getLogger(__name__)

REMINDERS = (
    # (key, lead time, window): send when start is within [now+lead-window/2, now+lead+window/2]
    ("day_before", timedelta(hours=24), timedelta(hours=2)),
    ("hour_before", timedelta(hours=1), timedelta(minutes=30)),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def active_tenants() -> List[TenantConfig]:
    out = []
    for doc in await TenantsStore(get_db()).list(limit=10_000):
        try:
            t = TenantConfig.from_doc(doc)
        except Exception:
            logger.warning("Skipping invalid tenant %s", doc.get("_id"))
            continue
        if t.active:
            out.append(t)
    return out


# ── calendar ─────────────────────────────────────────────────────────────────

async def maybe_sync_calendar(t: TenantConfig, now: datetime) -> bool:
    if not calendar_sync.connected(t):
        return False
    last = calendar_sync.google_settings(t).get("last_sync_at")
    if isinstance(last, datetime):
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(minutes=settings.CALENDAR_SYNC_MINUTES):
            return False
    await calendar_sync.sync_busy(t, now=now)
    return True


async def run_reactivation(t: TenantConfig, now: datetime) -> int:
    """Dental patient recall: refresh from the PMS daily, send what's due (core/services/reactivation)."""
    from core.services import reactivation

    return await reactivation.run(t, now)


async def maybe_sync_pms(t: TenantConfig, now: datetime) -> bool:
    """Dental PMS (Cliniko / Dentally): refresh the mirrored dentists and appointment types."""
    from core.services import pms_sync

    if not pms_sync.due(t, now):
        return False
    await pms_sync.sync(t)
    return True


# ── reminders ────────────────────────────────────────────────────────────────

async def send_reminders(t: TenantConfig, now: datetime) -> int:
    repos = Repos(get_db(), t.id)
    sent = 0
    today = now.astimezone(ZoneInfo(t.timezone)).date()
    for key, lead, window in REMINDERS:
        lo, hi = now + lead - window / 2, now + lead + window / 2
        due = await repos.appointments.find(
            {"status": "confirmed", "start": {"$gte": lo, "$lte": hi}, f"reminders.{key}.sent_at": None}, limit=500)
        for appt in due:
            # Claim first: only the runner whose update matches sends it.
            claim = await repos.appointments.update_one(
                {"appointment_id": appt["appointment_id"], f"reminders.{key}.sent_at": None},
                {"$set": {f"reminders.{key}.sent_at": now}})
            if claim.modified_count != 1:
                continue
            contact = await repos.contacts.find_one({"_id": appt.get("contact_id")}) if appt.get("contact_id") else None
            if (contact or {}).get("do_not_contact") or not appt.get("phone"):
                await repos.appointments.update_one({"appointment_id": appt["appointment_id"]},
                                                    {"$set": {f"reminders.{key}.skipped": "do_not_contact"}})
                continue
            when = av.spoken(appt["start"], t.timezone, today=today)
            what = appt.get("service_name") or "appointment"
            ok = await notify_contact(t, appt["phone"],
                                      f"Reminder from {t.name}: your {what} is {when}. "
                                      f"Need to change it? Call {t.phone or 'us'}.")
            if not ok:
                await repos.appointments.update_one({"appointment_id": appt["appointment_id"]},
                                                    {"$set": {f"reminders.{key}.error": "send_failed"}})
            sent += int(ok)
    return sent


# ── daily digest ─────────────────────────────────────────────────────────────

async def digest_text(t: TenantConfig, day_start: datetime, day_end: datetime) -> Optional[str]:
    repos = Repos(get_db(), t.id)
    calls = await repos.calls.find({"started_at": {"$gte": day_start, "$lt": day_end}}, limit=5000,
                                   projection={"outcome": 1, "duration_s": 1})
    outcomes: Dict[str, int] = {}
    for c in calls:
        outcomes[c.get("outcome") or "completed"] = outcomes.get(c.get("outcome") or "completed", 0) + 1
    booked = await repos.appointments.count({"created_at": {"$gte": day_start, "$lt": day_end}, "source": {"$ne": "dashboard"}})
    waiting = await repos.messages.count({"status": "new"}) + await repos.leads.count({"status": "new"})
    unanswered = await repos.unanswered.find({"status": "open", "created_at": {"$gte": day_start, "$lt": day_end}}, limit=10)
    if not calls and not waiting:
        return None
    lines = [f"Today at {t.name}:", "",
             f"• {len(calls)} call{'s' if len(calls) != 1 else ''} answered "
             f"({round(sum(c.get('duration_s') or 0 for c in calls) / 60)} minutes)",
             f"• {booked} booking{'s' if booked != 1 else ''} made by your assistant",
             f"• {waiting} message{'s' if waiting != 1 else ''} or job request{'s' if waiting != 1 else ''} waiting for you"]
    if outcomes:
        lines.append("• Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items(), key=lambda x: -x[1])))
    if unanswered:
        lines += ["", "Questions your assistant couldn't answer (add answers in Knowledge):"]
        lines += [f"  – {u['question']}" for u in unanswered]
    if settings.DASHBOARD_URL:
        lines += ["", f"Open the dashboard: {settings.DASHBOARD_URL.rstrip('/')}/"]
    return "\n".join(lines)


async def maybe_send_digest(t: TenantConfig, now: datetime, raw: Dict[str, Any]) -> bool:
    if not (t.notifications.daily_digest and t.notifications.owner_email):
        return False
    local = now.astimezone(ZoneInfo(t.timezone))
    if local.hour < settings.DAILY_DIGEST_HOUR:
        return False
    if ((raw.get("jobs") or {}).get("digest_sent_on")) == local.date().isoformat():
        return False
    day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc)
    # Claim the day first so a crash mid-send can't produce two e-mails.
    claimed = await get_db().tenants.update_one(
        {"_id": t.id, "jobs.digest_sent_on": {"$ne": local.date().isoformat()}},
        {"$set": {"jobs.digest_sent_on": local.date().isoformat()}})
    if claimed.modified_count != 1:
        return False
    text = await digest_text(t, day_start, day_start + timedelta(days=1))
    if text is None:
        return False
    return await send_email(t.notifications.owner_email, f"{t.name}: today's calls", text)


# ── transcript retention ─────────────────────────────────────────────────────

async def purge_transcripts(t: TenantConfig, now: datetime) -> int:
    cutoff = now - timedelta(days=t.agent.transcript_retention_days)
    fields = ["transcript", "transcript_enc", "turns"]
    if t.clinical_encryption:
        # Dental: the plaintext summary carries the same health details ("swelling on the left", or the
        # caller's own first sentence when the LLM summary failed), so it goes with the transcript.
        fields.append("summary")
    res = await Repos(get_db(), t.id).calls.update_many(
        {"status": "completed",
         "$and": [{"$or": [{f: {"$exists": True}} for f in fields if f != "turns"]},
                  {"$or": [{"ended_at": {"$lt": cutoff}}, {"ended_at": None, "started_at": {"$lt": cutoff}}]}]},
        {"$unset": {f: "" for f in fields}, "$set": {"transcript_purged_at": now}})
    purged = int(getattr(res, "modified_count", 0) or 0)
    if purged:
        logger.info("Purged %d transcripts older than %d days for %s", purged, t.agent.transcript_retention_days, t.id)
    return purged


async def purge_leads(t: TenantConfig, now: datetime) -> int:
    """Leads older than privacy.lead_retention_days are deleted (0 = keep). Dental default: one year."""
    days = t.privacy.lead_retention_days
    if not days:
        return 0
    res = await Repos(get_db(), t.id).leads.delete_many({"created_at": {"$lt": now - timedelta(days=days)}})
    deleted = int(getattr(res, "deleted_count", 0) or 0)
    if deleted:
        logger.info("Deleted %d leads older than %d days for %s", deleted, days, t.id)
    return deleted


# ── recording retention (Phase 6) ────────────────────────────────────────────

async def purge_recordings(t: TenantConfig, now: datetime) -> int:
    from core.compliance.retention import purge_recordings as purge

    return await purge(get_db(), t, now)


# ── monthly report and weekly pilot report (Phase 7) ─────────────────────────

async def _claim(t: TenantConfig, field: str, value: str) -> bool:
    """Claim a once-per-period send before sending, so a crash or a second runner can't send twice."""
    res = await get_db().tenants.update_one({"_id": t.id, f"jobs.{field}": {"$ne": value}},
                                            {"$set": {f"jobs.{field}": value}})
    return res.modified_count == 1


async def _unanswered_questions(t: TenantConfig, since: datetime, until: datetime) -> List[str]:
    rows = await Repos(get_db(), t.id).unanswered.find(
        {"status": "open", "created_at": {"$gte": since, "$lt": until}}, limit=10)
    return [r["question"] for r in rows if r.get("question")]


async def maybe_send_monthly_report(t: TenantConfig, now: datetime) -> bool:
    from core.services import insights

    if not (settings.MONTHLY_REPORT_ENABLED and t.notifications.monthly_report and t.notifications.owner_email):
        return False
    local = now.astimezone(ZoneInfo(t.timezone))
    if local.day != 1 or local.hour < settings.REPORT_HOUR:
        return False
    since, until, label = insights.previous_month(t, now)
    if t.created_at and (t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=timezone.utc)) >= until:
        return False                     # signed up this month: nothing to report yet
    if not await _claim(t, "monthly_report_for", label):
        return False
    report = await insights.value_report(get_db(), t, since, until)
    month = since.astimezone(ZoneInfo(t.timezone)).strftime("%B")
    text = insights.report_text(t, report, f"Your assistant at {t.name} in {month}:",
                                unanswered=await _unanswered_questions(t, since, until))
    return await send_email(t.notifications.owner_email, f"{t.name}: your assistant in {month}", text)


async def maybe_send_pilot_report(t: TenantConfig, now: datetime) -> bool:
    from core.services import insights

    if not (t.pilot.active and t.notifications.owner_email):
        return False
    local = now.astimezone(ZoneInfo(t.timezone))
    if local.weekday() != 0 or local.hour < settings.REPORT_HOUR:
        return False
    iso = local.isocalendar()
    if not await _claim(t, "pilot_report_week", f"{iso[0]}-W{iso[1]:02d}"):
        return False
    since, until = now - timedelta(days=7), now
    report = await insights.value_report(get_db(), t, since, until)
    text = insights.report_text(t, report, f"Pilot week report for {t.name} (last 7 days):",
                                unanswered=await _unanswered_questions(t, since, until))
    text += ("\n\nOn our weekly call we'll go through anything that went wrong. Reply to this e-mail with "
             "a score from 1 to 10 for how the assistant did this week.")
    ok = await send_email(t.notifications.owner_email, f"{t.name}: pilot week report", text)
    if settings.PILOT_REPORT_BCC:
        await send_email(settings.PILOT_REPORT_BCC, f"[pilot] {t.name}: week report", text)
    return ok


# ── runner ───────────────────────────────────────────────────────────────────

async def run_once(now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or utcnow()
    stats = {"tenants": 0, "synced": 0, "reminders": 0, "digests": 0, "purged": 0, "recordings_deleted": 0,
             "monthly_reports": 0, "pilot_reports": 0, "pms_synced": 0, "leads_purged": 0,
             "recall_texts": 0}
    raw_docs = {d["_id"]: d for d in await TenantsStore(get_db()).list(limit=10_000)}
    for t in await active_tenants():
        stats["tenants"] += 1
        set_tenant(t.id)
        for name, job in (("synced", maybe_sync_calendar(t, now)), ("pms_synced", maybe_sync_pms(t, now)),
                          ("recall_texts", run_reactivation(t, now)),
                          ("reminders", send_reminders(t, now)),
                          ("digests", maybe_send_digest(t, now, raw_docs.get(t.id, {}))),
                          ("purged", purge_transcripts(t, now)), ("leads_purged", purge_leads(t, now)),
                          ("recordings_deleted", purge_recordings(t, now)),
                          ("monthly_reports", maybe_send_monthly_report(t, now)),
                          ("pilot_reports", maybe_send_pilot_report(t, now))):
            try:
                stats[name] += int(await job)
            except Exception:
                logger.error("Tenant job %s failed for %s", name, t.id, exc_info=True)
    set_tenant(None)
    return stats


class TenantJobsService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not settings.ENABLE_TENANT_JOBS or (self._task and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="tenant-jobs")
        logger.info("Tenant jobs started (every %ss)", settings.TENANT_JOBS_POLL_SECONDS)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.error("Tenant jobs run failed", exc_info=True)
            try:
                # No Redis = no jobs worker: billing (Phase 5) runs here too. Idempotent per tick.
                from core.billing import jobs as billing_jobs

                await billing_jobs.run_once(get_db())
            except Exception:
                logger.error("Billing run failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(30, settings.TENANT_JOBS_POLL_SECONDS))
            except asyncio.TimeoutError:
                continue


_service: Optional[TenantJobsService] = None


def get_tenant_jobs() -> TenantJobsService:
    global _service
    if _service is None:
        _service = TenantJobsService()
    return _service
