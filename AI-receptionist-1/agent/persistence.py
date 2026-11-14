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
from datetime import datetime, timezone
from typing import Any, Optional

from agent.state import CallState
from agent.telemetry import summarise
from config import settings
from core.phone import mask_phone
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
        "agent_name": settings.LIVEKIT_AGENT_NAME,
        "business_id": state.profile.business_id,
        "started_at": state.started_at,
        "status": "completed" if final else "in_progress",
        "outcome": state.outcome or ("completed" if final else None),
        "transcript": state.transcript[-MAX_TRANSCRIPT_ITEMS:],
        "turns": state.turns,
        "latency": summarise(state.turns),
        "greeting_latency_s": state.greeting_latency_s,
        "tools_used": state.tools_used,
        "recording": state.recording,
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
            await get_db().calls.update_one(
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
        latency = summarise(self.state.turns)
        logger.info("Call %s ended: outcome=%s turns=%s p50=%ss p95=%ss tool_p50=%ss greeting=%ss",
                    self.state.call_id, self.state.outcome or "completed", latency["turns"],
                    latency["p50_s"], latency["p95_s"], latency["tool_p50_s"], self.state.greeting_latency_s)


async def load_contact(state: CallState) -> Optional[dict[str, Any]]:
    """Returning-caller context: name and a one-line booking history. One round trip each."""
    if not state.caller_phone:
        return None
    db = get_db()
    variants = phone_variants(state.caller_phone)
    guest, bookings = await asyncio.gather(
        db.guests.find_one({"phone": {"$in": variants}}, {"name": 1, "total_stays": 1}),
        db.bookings.find({"guest_phone": {"$in": variants}},
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
