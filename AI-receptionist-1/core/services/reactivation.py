"""
Dental patient recall (reactivation): one neutral SMS, and at most one follow-up, to patients the
practice's own system shows as overdue for a visit.

    await refresh(tenant)      # PMS → `recalls` rows (one per patient per cycle); cancels the booked
    await send_due(tenant)     # gate every due row, send, record the audit trail
    await preview(tenant)      # how many patients would be texted now, and why the rest wouldn't
    await cancel_for_phone(db, phone, tenant_id=…)   # STOP: cancel everything queued at once
    await on_booking(tenant, phone, appointment_id)  # a booking cancels follow-ups and is credited

Rules (core/outbound/gate.evaluate_recall, rules.py, ⚖ counsel to confirm):
- **PMS only.** Recall rows come from `list_lapsed_patients` of the connected Cliniko / Dentally,
  never from an import or the call log. No PMS connected means no recall.
- **Consent basis per country** is recorded on every send: AU `explicit_opt_in` or
  `au_inferred_existing_patient`; GB `explicit_opt_in` or `uk_pecr_soft_opt_in`. Inferred / soft opt-in
  needs a visit within 24 months and no opt-out; the PMS's own "no marketing" flag always wins.
- **SMS only** by default; voice stays off (`reactivation.voice_enabled`).
- **Neutral copy.** The template names the practice and says "Reply STOP"; it may not mention
  conditions or treatments (core/tenancy/models.recall_template_problems).
- **Sending hours** in the practice's time zone: weekdays 09:00–18:00, Saturday 10:00–16:00, never
  Sunday, and never outside 08:00–20:00 whatever is configured. Outside them a send is deferred.
- **Frequency:** at most one recall text per patient per 30 days (stored history, plus
  core/dedupe across workers) and two per cycle.
- **Attribution:** a booking within 14 days of a recall text is credited to it
  (`attributed_booking_id`), which also ends the cycle.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core import dedupe
from config import settings
from core.outbound import gate, phones, rules
from core.outbound.models import RECALLS
from core.repositories import Repos
from core.services import scheduling_provider as sp
from core.signed_links import with_signed_ref
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from core.tools.hotel import phone_variants

logger = logging.getLogger(__name__)

CAMPAIGN_TYPE = "patient_reactivation"
OPEN_STATUSES = ("scheduled", "sent")          # still waiting to send, or waiting for a follow-up
SEND_BATCH = 50
REFRESH_EVERY_S = 20 * 3600
MAX_FIRST_NAME = 40


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: Any) -> Optional[datetime]:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def available(tenant: TenantConfig) -> bool:
    """Recall can run for this practice at all: dental, a connected PMS, a recall country, and SMS
    (recall texts are SMS; the platform currently sends none)."""
    return (settings.SMS_ENABLED and tenant.vertical == "dental" and sp.connected(tenant)
            and (tenant.country or "").upper() in rules.RECALL_COUNTRIES)


def _settings(tenant: TenantConfig) -> Dict[str, Any]:
    return tenant.reactivation.model_dump()


def _mobile(raw: str, country: str) -> str:
    info = phones.parse(raw, country)
    return info.e164 if info else ""


def _cycle(last_visit: datetime) -> str:
    """A recall cycle follows one last visit: a new visit starts a new cycle."""
    return last_visit.astimezone(timezone.utc).strftime("%Y-%m-%d")


# ── Copy ─────────────────────────────────────────────────────────────────────

def booking_link(tenant: TenantConfig, recall_id: str) -> str:
    """The practice's online booking page with a signed recall reference (for attribution).
    Falls back to the website; empty when the practice has neither."""
    base = tenant.links.booking or tenant.website
    return with_signed_ref(base, "recall", recall_id) if base else ""


def render(tenant: TenantConfig, first_name: str, link: str) -> str:
    text = tenant.reactivation.effective_template.format(
        first_name=(first_name or "there").strip()[:MAX_FIRST_NAME] or "there",
        clinic_name=tenant.name, booking_link=link or tenant.phone or "")
    if "stop" not in text.lower():                   # templates are validated; this is the backstop
        text += " Reply STOP to opt out."
    return " ".join(text.split())


# ── PMS → recall rows ────────────────────────────────────────────────────────

async def _lapsed(tenant: TenantConfig, now: datetime, *, exclude_booked: bool) -> List[sp.LapsedPatient]:
    provider = sp.provider_for(tenant)
    if provider is None:
        return []
    since = now - timedelta(days=round(tenant.reactivation.recall_interval_months * 30.44))
    return await provider.list_lapsed_patients(since, exclude_with_future_booking=exclude_booked, now=now)


async def refresh(tenant: TenantConfig, *, now: Optional[datetime] = None, db: Any = None) -> Dict[str, int]:
    """Bring the recall rows up to date from the PMS. Never sends. Raises sp.PMSError on a PMS failure."""
    from database.mongodb import get_db

    now = now or utcnow()
    stats = {"patients": 0, "new": 0, "booked": 0, "skipped_no_mobile": 0}
    if not available(tenant):
        return stats
    repos = Repos(db if db is not None else get_db(), tenant.id)
    country = tenant.country.upper()
    for p in await _lapsed(tenant, now, exclude_booked=False):
        stats["patients"] += 1
        phone = _mobile(p.mobile, country)
        if not phone:
            stats["skipped_no_mobile"] += 1
        cycle = _cycle(p.last_visit_at)
        existing = await repos.recalls.find_one({"pms_patient_id": p.pms_patient_id, "cycle": cycle})
        if p.has_future_booking:
            if existing and existing.get("status") in OPEN_STATUSES:
                await _close_for_booking(repos, existing, p.next_booking_id and f"pms:{p.next_booking_id}", now)
                stats["booked"] += 1
            continue
        fields = {"phone": phone, "first_name": p.first_name[:MAX_FIRST_NAME], "last_visit_at": p.last_visit_at,
                  "has_future_booking": False, "sms_marketing_consent": p.sms_marketing_consent, "updated_at": now}
        if existing:
            await repos.recalls.update_one({"_id": existing["_id"]}, {"$set": fields})
            continue
        await repos.recalls.insert_one({
            "_id": new_id("rcl"), "campaign_type": CAMPAIGN_TYPE, "pms_provider": sp.provider_name(tenant),
            "pms_patient_id": p.pms_patient_id, "cycle": cycle, "status": "scheduled", "attempts": [],
            "next_send_at": now, "created_at": now, **fields})
        stats["new"] += 1
    return stats


async def preview(tenant: TenantConfig, *, now: Optional[datetime] = None, db: Any = None) -> Dict[str, Any]:
    """How many patients would be texted, ignoring today's sending hours. No writes."""
    from database.mongodb import get_db

    now = now or utcnow()
    if not available(tenant):
        return {"available": False, "eligible": 0, "blocked": {}}
    db = db if db is not None else get_db()
    country = tenant.country.upper()
    settings_ = {**_settings(tenant), "enabled": True}
    eligible = 0
    blocked: Dict[str, int] = {}
    for p in await _lapsed(tenant, now, exclude_booked=True):
        row = {"phone": _mobile(p.mobile, country), "last_visit_at": p.last_visit_at,
               "has_future_booking": p.has_future_booking, "attempts": []}
        facts = await _facts(db, tenant, row, sms_marketing_consent=p.sms_marketing_consent)
        decision = gate.evaluate_recall(row, settings_, country=country, tz=tenant.timezone, facts=facts, now=now)
        failures = [f for f in decision.failures if f != "outside_hours"]
        if failures:
            for f in failures:
                blocked[f] = blocked.get(f, 0) + 1
        else:
            eligible += 1
    return {"available": True, "eligible": eligible, "blocked": blocked}


# ── Sending ──────────────────────────────────────────────────────────────────

async def _facts(db: Any, tenant: TenantConfig, row: Dict[str, Any], *,
                 sms_marketing_consent: Optional[bool]) -> gate.RecallFacts:
    from core.compliance.optout import is_opted_out

    phone = row.get("phone") or ""
    if not phone:
        return gate.RecallFacts(pms_marketing_consent=sms_marketing_consent)
    repos = Repos(db, tenant.id)
    try:
        opted_out = await is_opted_out(db, tenant.id, phone)
    except Exception:
        logger.warning("Recall: opt-out check failed for %s; not sending", tenant.id, exc_info=True)
        opted_out = True                              # fail closed
    contact = await repos.contacts.find_one({"phone": {"$in": phone_variants(phone)}}, {"consent": 1}) or {}
    explicit = ((contact.get("consent") or {}).get("recall_sms") or {})
    last = await repos.recalls.find({"phone": phone, "last_sent_at": {"$ne": None}}, sort=[("last_sent_at", -1)],
                                    limit=1, projection={"last_sent_at": 1})
    return gate.RecallFacts(
        opted_out=opted_out,
        explicit_opt_in_at=_aware(explicit.get("at")) if explicit.get("granted") else None,
        pms_marketing_consent=sms_marketing_consent,
        last_recall_sent_at=_aware(last[0].get("last_sent_at")) if last else None,
    )


async def send_due(tenant: TenantConfig, *, now: Optional[datetime] = None, db: Any = None,
                   limit: int = SEND_BATCH) -> Dict[str, int]:
    """Gate and send every recall that is due. Never raises for one row."""
    from core.integrations import sms as sms_api
    from database.mongodb import get_db

    now = now or utcnow()
    stats = {"sent": 0, "deferred": 0, "closed": 0, "failed": 0}
    if not available(tenant):
        return stats
    db = db if db is not None else get_db()
    repos = Repos(db, tenant.id)
    country = tenant.country.upper()
    settings_ = _settings(tenant)
    rows = await repos.recalls.find({"status": {"$in": list(OPEN_STATUSES)}, "next_send_at": {"$lte": now}},
                                    sort=[("next_send_at", 1)], limit=limit)
    for row in rows:
        facts = await _facts(db, tenant, row, sms_marketing_consent=row.get("sms_marketing_consent"))
        decision = gate.evaluate_recall(row, settings_, country=country, tz=tenant.timezone, facts=facts, now=now)
        gate_doc = decision.to_doc()
        if not decision.allowed:
            if decision.permanent:
                status = ("opted_out" if {"opted_out", "pms_marketing_opt_out"} & set(decision.failures)
                          else "completed" if "max_attempts" in decision.failures else "blocked")
                await repos.recalls.update_one({"_id": row["_id"]}, {"$set": {
                    "status": status, "next_send_at": None, "gate": gate_doc, "closed_at": now,
                    "closed_reason": ",".join(decision.failures)}})
                stats["closed"] += 1
            else:
                await repos.recalls.update_one({"_id": row["_id"]}, {"$set": {
                    "next_send_at": decision.retry_at or now + timedelta(hours=1), "gate": gate_doc}})
                stats["deferred"] += 1
            continue
        phone = row["phone"]
        # Across workers (and as a second lock beside the stored history): one text per 30 days.
        if not await dedupe.first_time("recall_sms", f"{tenant.id}:{phone}", rules.RECALL_MIN_GAP_DAYS * 86400):
            await repos.recalls.update_one({"_id": row["_id"]}, {"$set": {"next_send_at": now + timedelta(days=1)}})
            stats["deferred"] += 1
            continue
        body = render(tenant, row.get("first_name") or "", booking_link(tenant, row["_id"]))
        sid = await sms_api.send_sms_sid(phone, body)
        if not sid:
            await repos.recalls.update_one({"_id": row["_id"]}, {"$set": {"next_send_at": now + timedelta(hours=6),
                                                                          "gate": gate_doc}})
            stats["failed"] += 1
            continue
        attempts = len(row.get("attempts") or []) + 1
        follow_up = attempts < rules.RECALL_MAX_ATTEMPTS
        attempt = {"sent_at": now, "twilio_sid": sid, "channel": "sms",
                   "consent_basis": decision.checks.get("consent_basis"), "consent_verified_at": now,
                   "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
        await repos.recalls.update_one({"_id": row["_id"]}, {
            "$push": {"attempts": attempt},
            "$set": {"status": "sent", "last_sent_at": now, "sent_at": row.get("sent_at") or now,
                     "twilio_sid": sid, "consent_basis": attempt["consent_basis"], "consent_verified_at": now,
                     "gate": gate_doc,
                     "next_send_at": now + timedelta(days=max(rules.RECALL_FOLLOW_UP_DAYS, rules.RECALL_MIN_GAP_DAYS))
                     if follow_up else None,
                     **({} if follow_up else {"status": "completed", "closed_at": now,
                                              "closed_reason": "max_attempts"})}})
        stats["sent"] += 1
    if stats["sent"]:
        logger.info("Recall texts for %s: %s", tenant.id, stats)
    return stats


# ── Stopping and attribution ─────────────────────────────────────────────────

async def _close_for_booking(repos: Repos, row: Dict[str, Any], booking_id: Optional[str], now: datetime) -> str:
    """Booked: credit the recall when a text went out in the last 14 days, else just stop it."""
    sent = _aware(row.get("last_sent_at"))
    credited = bool(sent and sent >= now - timedelta(days=rules.RECALL_ATTRIBUTION_DAYS) and booking_id)
    sets: Dict[str, Any] = {"status": "booked" if credited else "cancelled", "next_send_at": None, "closed_at": now,
                            "closed_reason": "booked"}
    if credited:
        sets.update({"attributed_booking_id": booking_id, "attributed_at": now})
    await repos.recalls.update_one({"_id": row["_id"]}, {"$set": sets})
    return sets["status"]


async def on_booking(tenant: TenantConfig, phone: str, appointment_id: str, *, now: Optional[datetime] = None,
                     db: Any = None) -> int:
    """A patient booked: cancel their pending recall follow-ups at once. Never raises."""
    from database.mongodb import get_db

    try:
        now = now or utcnow()
        repos = Repos(db if db is not None else get_db(), tenant.id)
        number = _mobile(phone, (tenant.country or "").upper()) or phone      # "0412 345 678" → +61412345678
        rows = await repos.recalls.find({"phone": {"$in": phone_variants(number)},
                                         "status": {"$in": list(OPEN_STATUSES)}})
        for row in rows:
            await _close_for_booking(repos, row, appointment_id, now)
        return len(rows)
    except Exception:
        logger.warning("Recall: could not close recalls after a booking for %s", tenant.id, exc_info=True)
        return 0


async def cancel_for_phone(db: Any, phone: str, *, tenant_id: Optional[str], reason: str = "opted_out",
                           now: Optional[datetime] = None) -> int:
    """STOP: cancel every queued recall text to this number, in that practice (or, for a STOP to the
    platform's shared number, in every practice). Never raises."""
    now = now or utcnow()
    q: Dict[str, Any] = {"phone": {"$in": phone_variants(phone)}, "status": {"$in": list(OPEN_STATUSES)}}
    if tenant_id:
        q["tenant_id"] = tenant_id
    try:
        res = await db[RECALLS].update_many(q, {"$set": {  # cross-tenant: q has tenant_id, or none for a platform-wide STOP
            "status": "opted_out", "next_send_at": None, "closed_at": now, "closed_reason": reason}})
        return int(getattr(res, "modified_count", 0) or 0)
    except Exception:
        logger.warning("Recall: could not cancel queued texts after a STOP", exc_info=True)
        return 0


async def run(tenant: TenantConfig, now: datetime) -> int:
    """The jobs tick: refresh from the PMS about once a day, then send what's due. Returns texts sent."""
    if not tenant.reactivation.enabled or not available(tenant):
        return 0
    if await dedupe.first_time("recall_refresh", tenant.id, REFRESH_EVERY_S):
        try:
            await refresh(tenant, now=now)
        except sp.PMSError as e:
            logger.warning("Recall refresh for %s failed: %s", tenant.id, e)
    return (await send_due(tenant, now=now))["sent"]


async def stats(tenant: TenantConfig, since: datetime, until: datetime, *, db: Any = None) -> Dict[str, int]:
    """For insights: recall texts sent and bookings credited to them in the period."""
    from database.mongodb import get_db

    repos = Repos(db if db is not None else get_db(), tenant.id)
    q_time = {"$gte": since, "$lt": until}
    sent = await repos.recalls.count({"last_sent_at": q_time})
    booked = await repos.recalls.count({"attributed_at": q_time, "attributed_booking_id": {"$ne": None}})
    return {"recall_texts_sent": sent, "reactivation_bookings_attributed": booked}
