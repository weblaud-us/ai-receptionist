"""
Call records, written off the hot path (Roadmap Task 1.8).

Nothing touches Mongo between "caller stops talking" and "agent starts talking". The transcript
and latency rows accumulate in CallState (memory); a background task upserts them into `calls`
every FLUSH_INTERVAL_S, so a crashed worker loses at most that much, and the shutdown callback
writes the final record (outcome, duration, latency summary).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from agent.state import CallState
from core.sensitive import HAS_SUMMARY, SUMMARY_PROJECTION, seal, unseal
from agent.summary import summarise_call
from agent.telemetry import summarise
from config import settings
from core.billing.costs import call_cost_usd
from core.billing.usage import refresh_period_usage
from core.integrations import webhooks
from core.phone import mask_phone
from core.repositories import Repos
from core.services import availability as av
from core.timeutils import business_now
from core.tools.hotel import phone_variants
from core.tools.knowledge import questions_not_answered, record_unanswered
from database.mongodb import get_db

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 10.0
MAX_TRANSCRIPT_ITEMS = 400


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _after_hours(state: CallState) -> Optional[bool]:
    """Was the business closed when the call came in? Feeds "calls that would have been missed" (Phase 7)."""
    try:
        tenant = state.profile.tenant
        return not tenant.is_open(business_now(tenant.timezone, state.started_at))
    except Exception:
        return None


def _answer_window(state: CallState) -> Optional[str]:
    """open | lunch_break | after_hours when the call came in (dental overflow KPIs, Phase 5)."""
    try:
        tenant = state.profile.tenant
        return tenant.answer_window(business_now(tenant.timezone, state.started_at))
    except Exception:
        return None


def _overflow(state: CallState) -> Optional[bool]:
    """Forwarded while the business was open because reception was busy (answering_mode=overflow)."""
    try:
        return _answer_window(state) == "open" and state.profile.tenant.answering_mode == "overflow"
    except Exception:
        return None


def call_document(state: CallState, *, final: bool) -> dict[str, Any]:
    now = _utcnow()
    doc: dict[str, Any] = {
        "call_id": state.call_id,
        "channel": state.channel,
        "direction": state.direction,
        "caller_phone": state.caller_phone,
        "dialed_number": state.dialed_number,
        "sip_call_id": state.sip_call_id,
        "sip_trunk_id": state.sip_trunk_id,
        "agent_name": state.agent_name or settings.LIVEKIT_AGENT_NAME,
        "campaign_id": state.campaign_id,
        "lead_id": state.lead_id,
        "attempt_id": state.attempt_id,
        "web_demo_lead_id": state.web_demo_lead_id,
        "business_id": state.profile.business_id,
        "tenant_id": state.profile.business_id,
        "vertical": state.profile.business_type,
        "started_at": state.started_at,
        "status": "completed" if final else "in_progress",
        "outcome": state.outcome or ("completed" if final else None),
        "transcript": state.transcript[-MAX_TRANSCRIPT_ITEMS:],
        "turns": state.turns,
        "latency": summarise(state.turns),
        "greeting_latency_s": state.greeting_latency_s,
        "tools_used": state.tools_used,
        "provider_errors": state.provider_errors,
        "fallbacks": state.fallbacks,
        "transfer": state.transfer,
        "clinical_guard_hits": state.clinical_guard_hits,   # > 0: worth a look in transcript review
        "after_hours": _after_hours(state),
        "answer_window": _answer_window(state),
        "overflow": _overflow(state),
        "updated_at": now,
    }
    if final:
        doc["ended_at"] = now
        doc["duration_s"] = round((now - state.started_at).total_seconds(), 1)
        doc["ended_reason"] = state.ended_reason
    return doc


class CallRecorder:
    def __init__(self, state: CallState) -> None:
        self.state = state
        self._task: Optional[asyncio.Task[None]] = None
        self._dirty = True
        self.summary: Optional[str] = None        # set by finish(); the sales agent stores it on the attempt

    def attach(self, session: Any) -> None:
        @session.on("conversation_item_added")
        def _on_item(ev: Any) -> None:
            item = ev.item
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if role in ("user", "assistant") and text:
                self.state.transcript.append({
                    "role": role, "text": text, "at": _utcnow(),
                    "interrupted": bool(getattr(item, "interrupted", False)),
                })
                self._dirty = True

    def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop(), name=f"call-flush-{self.state.call_id}")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            if self._dirty:
                self._dirty = False
                await self._write(final=False)

    async def _write(self, *, final: bool) -> None:
        try:
            doc = call_document(self.state, final=final)
            tenant = getattr(self.state.profile, "tenant", None)
            if tenant is not None and tenant.clinical_encryption:
                doc = seal(doc, ("transcript",))      # health details: encrypted at rest (dental)
            await Repos(get_db(), self.state.profile.business_id).calls.update_one(
                {"call_id": self.state.call_id},
                {"$set": doc,
                 "$setOnInsert": {"created_at": self.state.started_at}},
                upsert=True,
            )
        except Exception:
            logger.warning("Saving call %s failed", self.state.call_id, exc_info=True)

    async def finish(self, reason: Optional[str] = None) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.state.ended_reason = reason or self.state.ended_reason
        await self._write(final=True)
        await self._record_usage()
        await self._summarise_and_notify()
        await self._update_demo_lead()
        await self._schedule_recovery()
        latency = summarise(self.state.turns)
        logger.info("Call %s ended: outcome=%s turns=%s p50=%ss p95=%ss tool_p50=%ss greeting=%ss",
                    self.state.call_id, self.state.outcome or "completed", latency["turns"],
                    latency["p50_s"], latency["p95_s"], latency["tool_p50_s"], self.state.greeting_latency_s)


    async def _update_demo_lead(self) -> None:
        """Landing-page demo: the call goes on the visitor's lead (platform Demo leads). Best effort."""
        lead_id = self.state.web_demo_lead_id
        if not lead_id:
            return
        from core.services import web_demo

        try:
            await web_demo.record_session(
                get_db(), lead_id, call_id=self.state.call_id, tenant_id=self.state.profile.business_id,
                started_at=self.state.started_at, duration_s=(_utcnow() - self.state.started_at).total_seconds(),
                outcome=self.state.outcome, summary=self.summary, tools_used=self.state.tools_used)
        except Exception:
            logger.warning("Demo lead %s not updated for call %s", lead_id, self.state.call_id, exc_info=True)

    async def _schedule_recovery(self) -> None:
        """A text to a caller who hung up without booking (opt-in per business; never raises)."""
        if self.state.profile.business_id == "unassigned":
            return
        from core.services import recovery

        await recovery.schedule(self.state)

    async def _summarise_and_notify(self) -> None:
        """Store a one-paragraph summary and fire `call.completed` (Tasks 2.2 and 2.8). Best effort."""
        tenant_id = self.state.profile.business_id
        if tenant_id == "unassigned":
            return
        duration = (_utcnow() - self.state.started_at).total_seconds()
        summary: Optional[str] = None
        try:
            summary = await summarise_call(self.state.transcript, outcome=self.state.outcome,
                                           tools_used=self.state.tools_used, duration_s=duration,
                                           prompt=self.state.summary_prompt)
            self.summary = summary
            if summary:
                t = getattr(self.state.profile, "tenant", None)
                # Audit PRIV-08: the summary retells what the caller said; sealed like the transcript.
                sets = seal({"summary": summary}, ("summary",)) if t is not None and t.clinical_encryption \
                    else {"summary": summary}
                await Repos(get_db(), tenant_id).calls.update_one({"call_id": self.state.call_id}, {"$set": sets})
        except Exception:
            logger.warning("Summary for call %s failed", self.state.call_id, exc_info=True)
        await self._log_unanswered_questions(tenant_id)
        tenant = getattr(self.state.profile, "tenant", None)
        if tenant is None:
            return
        try:
            webhooks.emit(tenant, "call.completed", {
                "call_id": self.state.call_id, "channel": self.state.channel, "direction": self.state.direction,
                "caller_phone": self.state.caller_phone, "dialed_number": self.state.dialed_number,
                "started_at": self.state.started_at, "ended_at": _utcnow(), "duration_s": round(duration, 1),
                "outcome": self.state.outcome or "completed", "summary": summary,
                "tools_used": list(dict.fromkeys(self.state.tools_used)), "turns": len(self.state.turns),
            })
        except Exception:
            logger.debug("call.completed webhook failed", exc_info=True)

    async def _log_unanswered_questions(self, tenant_id: str) -> None:
        """Questions the assistant said it couldn't answer go on the owner's "unanswered" list (Task 2.6),
        whatever the knowledge base size. take_message logs most of them during the call; this catches
        the rest. Duplicates within a call are skipped. Best effort."""
        if self.state.direction == "outbound":
            return
        try:
            repos = Repos(get_db(), tenant_id)
            if await repos.unanswered.find_one({"call_id": self.state.call_id}, {"_id": 1}):
                return      # already logged during the call (in the model's words); don't add a near-duplicate
            for question in questions_not_answered(self.state.transcript):
                await record_unanswered(repos, question, channel=self.state.channel, call_id=self.state.call_id,
                                        caller_phone=self.state.caller_phone, source="transcript")
        except Exception:
            logger.debug("Unanswered-question scan failed for %s", self.state.call_id, exc_info=True)

    async def _record_usage(self) -> None:
        """One usage event per call, rounded up to the second (Phase 5 billing reads `usage_events`),
        then this period's usage cached on the tenant so the next call knows about a cap."""
        tenant_id = self.state.profile.business_id
        if tenant_id == "unassigned":
            return
        seconds = math.ceil((_utcnow() - self.state.started_at).total_seconds())
        outbound = self.state.direction == "outbound"
        cost = call_cost_usd(seconds, outbound=outbound)
        # Your own sales calls (Phase 4) are a cost to you, not usage a customer pays for.
        # Calls answered as "unavailable" (suspended / capped) aren't charged to the business either.
        billable = not (settings.SALES_TENANT_ID and tenant_id == settings.SALES_TENANT_ID)             and self.state.outcome != "unavailable"
        try:
            repos = Repos(get_db(), tenant_id)
            await repos.usage.update_one(
                {"call_id": self.state.call_id, "kind": "voice_minute"},
                {"$set": {"quantity": max(1, math.ceil(seconds / 60)), "seconds": seconds,
                          "direction": self.state.direction, "cost_usd": cost, "billable": billable,
                          "at": _utcnow()},
                 "$setOnInsert": {"invoiced": False}},
                upsert=True)
            await repos.calls.update_one({"call_id": self.state.call_id}, {"$set": {"cost_usd": cost}})
        except Exception:
            logger.warning("Usage event for call %s failed", self.state.call_id, exc_info=True)
            return
        tenant = getattr(self.state.profile, "tenant", None)
        if tenant is None or tenant.is_legacy or not billable:
            return
        try:
            await refresh_period_usage(get_db(), tenant)
        except Exception:
            logger.debug("Period usage refresh failed for %s", tenant_id, exc_info=True)


_LEAD_LABEL = {"dental_emergency": "dental emergency", "cosmetic_consult": "cosmetic consultation enquiry"}
_UPCOMING = ["pending", "confirmed"]
_OPEN_LEADS = ["new", "contacted"]


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ago(dt: Any, now: datetime) -> str:
    dt = _aware(dt)
    if dt is None:
        return "recently"
    days = (now.date() - dt.date()).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def _clip(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _appt_line(a: dict[str, Any], tz: str, today: Any) -> str:
    start = _aware(a.get("start"))
    when = av.spoken(start, tz, today=today) if start else "?"
    who = f" with {a['resource_name']}" if a.get("resource_name") else ""
    return f"{a.get('service_name') or 'appointment'}{who}, {when}"


async def load_contact(state: CallState) -> Optional[dict[str, Any]]:
    """Returning-caller context, looked up by caller ID before the agent's first reply.

    Everything is fetched in one parallel round trip (bounded by the worker's timeout). Only what the
    agent needs to sound informed goes in — never clinical free text: a phone may be shared, and the
    agent must confirm who it's talking to before using any of it.
    """
    if not state.caller_phone:
        return None
    tenant = state.profile.tenant
    variants = phone_variants(state.caller_phone)
    if tenant.vertical == "hotel":
        return await _load_hotel_guest(state, variants)
    repos = Repos(get_db(), tenant.id)
    now = datetime.now(timezone.utc)
    today = business_now(tenant.timezone, now).date()
    by_phone = {"phone": {"$in": variants}}
    contact, upcoming, past, calls, msgs, leads = await asyncio.gather(
        repos.contacts.find_one(by_phone, {"name": 1, "notes": 1, "tags": 1, "do_not_contact": 1, "stats": 1,
                                           "preferred_resource_id": 1}),
        repos.appointments.find({**by_phone, "start": {"$gte": now}, "status": {"$in": _UPCOMING}},
                                sort=[("start", 1)], limit=2,
                                projection={"service_name": 1, "resource_name": 1, "start": 1, "status": 1}),
        repos.appointments.find({**by_phone, "start": {"$lt": now}}, sort=[("start", -1)], limit=3,
                                projection={"service_name": 1, "resource_name": 1, "start": 1, "status": 1}),
        repos.calls.find({"caller_phone": {"$in": variants}, "call_id": {"$ne": state.call_id}, **HAS_SUMMARY},
                         sort=[("started_at", -1)], limit=2, projection={**SUMMARY_PROJECTION, "started_at": 1}),
        repos.messages.find({"$or": [{"caller_phone": {"$in": variants}}, {"callback_number": {"$in": variants}}],
                             "status": "new"}, sort=[("created_at", -1)], limit=2,
                            projection={"message": 1, "created_at": 1}),
        repos.leads.find({**by_phone, "status": {"$in": _OPEN_LEADS}}, sort=[("created_at", -1)], limit=2,
                         projection={"kind": 1, "urgency": 1, "created_at": 1}),   # never the sealed problem text
    )
    if not (contact or upcoming or past or calls or msgs or leads):
        return None
    contact = contact or {}
    tz = tenant.timezone
    regular = None
    if contact.get("preferred_resource_id"):          # the patient's regular dentist (plan E4)
        res = await repos.resources.find_one({"_id": contact["preferred_resource_id"], "active": {"$ne": False}},
                                             {"name": 1})
        regular = (res or {}).get("name")
    enquiries = []
    for lead in leads:
        label = _LEAD_LABEL.get(lead.get("kind") or "", "job request" if lead.get("urgency") else "enquiry")
        if lead.get("urgency"):
            label += f" ({str(lead['urgency']).replace('_', ' ')})"
        enquiries.append(f"{label}, {_ago(lead.get('created_at'), now)}")
    history = {
        "name": contact.get("name"),
        "upcoming": [_appt_line(a, tz, today) for a in upcoming],
        "past": [f"{_appt_line(a, tz, today)} ({a.get('status')})" for a in past],
        "recent_calls": [f"{_ago(c.get('started_at'), now)}: {_clip(unseal(c, ('summary',)).get('summary'), 200)}"
                         for c in calls],
        "open_messages": [f"{_ago(m.get('created_at'), now)}: {_clip(m.get('message'), 150)}" for m in msgs],
        "open_enquiries": enquiries,
        "notes": _clip(contact.get("notes"), 400),
        "tags": [str(t) for t in (contact.get("tags") or [])][:8],
        "do_not_contact": bool(contact.get("do_not_contact")),
        "no_shows": int(((contact.get("stats") or {}).get("no_shows")) or 0),
        "regular_resource": regular,
    }
    logger.info("Returning caller %s (%d upcoming, %d past, %d calls, %d open messages)",
                mask_phone(state.caller_phone), len(upcoming), len(past), len(calls), len(msgs))
    return history


async def _load_hotel_guest(state: CallState, variants: list[str]) -> Optional[dict[str, Any]]:
    db = get_db()
    tenant = state.profile.tenant
    scope: dict[str, Any] = {} if tenant.is_legacy else {"tenant_id": tenant.id}
    guest, bookings = await asyncio.gather(
        db.guests.find_one({**scope, "phone": {"$in": variants}}, {"name": 1, "total_stays": 1}),  # scoped: **scope (empty only for the pre-migration .env hotel)
        db.bookings.find({**scope, "guest_phone": {"$in": variants}},  # scoped: **scope (empty only for the pre-migration .env hotel)
                         {"booking_id": 1, "check_in_date": 1, "status": 1, "room_type": 1})
        .sort("created_at", -1).limit(3).to_list(3),
    )
    if not guest and not bookings:
        return None
    summary = "; ".join(
        f"{b.get('room_type', 'room')} booking {b.get('booking_id')} for {b.get('check_in_date')} ({b.get('status')})"
        for b in bookings
    )
    logger.info("Returning caller %s (%d recent bookings)", mask_phone(state.caller_phone), len(bookings))
    return {"name": (guest or {}).get("name"), "summary": summary}


async def record_contact_call(state: CallState) -> None:
    """Upsert the caller in `contacts` and count the call. Background; never on the hot path."""
    tenant = state.profile.tenant
    if not state.caller_phone or tenant.is_legacy or tenant.id == "unassigned":
        return
    try:
        repos = Repos(get_db(), tenant.id)
        await repos.contacts.upsert_by_phone(state.caller_phone, {})
        await repos.contacts.update_one({"phone": state.caller_phone}, {"$inc": {"stats.calls": 1}})
    except Exception:
        logger.debug("contact call count failed", exc_info=True)
