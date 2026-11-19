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
from agent.summary import summarise_call
from agent.telemetry import summarise
from config import settings
from core.billing.costs import call_cost_usd
from core.billing.usage import refresh_period_usage
from core.integrations import webhooks
from core.phone import mask_phone
from core.repositories import Repos
from core.services import availability as av
from core.tools.hotel import phone_variants
from database.mongodb import get_db

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 10.0
MAX_TRANSCRIPT_ITEMS = 400


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        "recording": state.recording,
        "provider_errors": state.provider_errors,
        "fallbacks": state.fallbacks,
        "transfer": state.transfer,
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
            await Repos(get_db(), self.state.profile.business_id).calls.update_one(
                {"call_id": self.state.call_id},
                {"$set": call_document(self.state, final=final),
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
        latency = summarise(self.state.turns)
        logger.info("Call %s ended: outcome=%s turns=%s p50=%ss p95=%ss tool_p50=%ss greeting=%ss",
                    self.state.call_id, self.state.outcome or "completed", latency["turns"],
                    latency["p50_s"], latency["p95_s"], latency["tool_p50_s"], self.state.greeting_latency_s)


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
                await Repos(get_db(), tenant_id).calls.update_one({"call_id": self.state.call_id},
                                                                   {"$set": {"summary": summary}})
        except Exception:
            logger.warning("Summary for call %s failed", self.state.call_id, exc_info=True)
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
                "recording": bool(self.state.recording),
            })
        except Exception:
            logger.debug("call.completed webhook failed", exc_info=True)

    async def _record_usage(self) -> None:
        """One usage event per call, rounded up to the second (Phase 5 billing reads `usage_events`),
        then this period's usage cached on the tenant so the next call knows about a cap."""
        tenant_id = self.state.profile.business_id
        if tenant_id == "unassigned":
            return
        seconds = math.ceil((_utcnow() - self.state.started_at).total_seconds())
        outbound = self.state.direction == "outbound"
        cost = call_cost_usd(seconds, recording=bool(self.state.recording), outbound=outbound)
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


async def load_contact(state: CallState) -> Optional[dict[str, Any]]:
    """Returning-caller context: name and a one-line history. Two round trips, in parallel."""
    if not state.caller_phone:
        return None
    tenant = state.profile.tenant
    variants = phone_variants(state.caller_phone)
    if tenant.vertical == "hotel":
        return await _load_hotel_guest(state, variants)
    repos = Repos(get_db(), tenant.id)
    contact, appts = await asyncio.gather(
        repos.contacts.find_one({"phone": {"$in": variants}}, {"name": 1, "notes": 1, "do_not_contact": 1}),
        repos.appointments.find({"phone": {"$in": variants}}, sort=[("start", -1)], limit=3,
                                projection={"service_name": 1, "resource_name": 1, "start": 1, "status": 1}),
    )
    if not contact and not appts:
        return None
    parts = []
    for a in appts:
        when = av.spoken(a["start"], tenant.timezone) if isinstance(a.get("start"), datetime) else "?"
        who = f" with {a['resource_name']}" if a.get("resource_name") else ""
        parts.append(f"{a.get('service_name') or 'appointment'}{who} {when} ({a.get('status')})")
    logger.info("Returning caller %s (%d recent appointments)", mask_phone(state.caller_phone), len(appts))
    return {"name": (contact or {}).get("name"), "summary": "; ".join(parts)}


async def _load_hotel_guest(state: CallState, variants: list[str]) -> Optional[dict[str, Any]]:
    db = get_db()
    tenant = state.profile.tenant
    scope: dict[str, Any] = {} if tenant.is_legacy else {"tenant_id": tenant.id}
    guest, bookings = await asyncio.gather(
        db.guests.find_one({**scope, "phone": {"$in": variants}}, {"name": 1, "total_stays": 1}),
        db.bookings.find({**scope, "guest_phone": {"$in": variants}},
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
