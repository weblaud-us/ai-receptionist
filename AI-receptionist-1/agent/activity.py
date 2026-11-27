"""
"What the assistant is doing", live, for browser calls (landing-page demo, /call and /talk pages).

Each tool step becomes a small JSON event on the LiveKit text-stream topic ``laudline.activity``:
``{"id", "tool", "label", "status": "running" | "done" | "failed" | "alert", "detail"}``. The page shows
it in plain words ("Checking the diary…" → "Found 3 times ✓") and updates the row in place by id.

Only for browser calls, never phone (SIP) calls: nobody is watching a phone call, and events must
never slow one down. Details are limited to service, day and time words; never a phone number, a
name or a symptom. Sending is best effort: any failure is logged and ignored, so it can't break a call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

TOPIC = "laudline.activity"


def _clip(v: Any, n: int = 80) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _parse(result: str) -> dict:
    try:
        r = json.loads(result)
        return r if isinstance(r, dict) else {}
    except (TypeError, ValueError):
        return {}


def _when(args: dict) -> str:
    parts = [_clip(args.get("service"), 40), _clip(args.get("date"), 24), _clip(args.get("time"), 12)]
    return " · ".join(p for p in parts if p)


def _availability(args: dict, r: dict) -> tuple[str, str, str]:
    if r.get("error"):
        return "failed", "Couldn't reach the diary", ""
    slots = r.get("slots") or []
    if slots:
        first = _clip(slots[0].get("say"), 60)
        return "done", f"Found {len(slots)} free time{'s' if len(slots) != 1 else ''}", first
    alts = r.get("alternatives") or []
    if alts:
        return "done", "Nothing free then, found other times", _clip(alts[0].get("say"), 60)
    return "done", "Nothing free then", ""


def _booked(args: dict, r: dict) -> tuple[str, str, str]:
    if r.get("success"):
        return "done", "Booked the appointment", " · ".join(p for p in (_clip(r.get("service"), 40), _clip(r.get("when"), 50)) if p)
    return "failed", "Couldn't book that time", _clip(r.get("message"), 80)


def _triage(args: dict, r: dict) -> tuple[str, str, str]:
    severity = str(args.get("severity_level") or "")
    red = bool(args.get("swelling_or_bleeding_red_flag")) or severity == "life_threatening"
    if r.get("error"):
        return "running", "Taking the emergency details", ""
    if red:
        return "alert", "Emergency: told to get urgent care first, alerted the practice", ""
    return "done", "Urgent problem logged for the dentist", "the team will call back"


def _knowledge(args: dict, r: dict) -> tuple[str, str, str]:
    found = bool(r.get("answer") or r.get("results") or r.get("found") or r.get("success"))
    return "done", "Answered from the practice's own information" if found else "Not in the practice's information: won't guess", ""


def _simple(done: str) -> Callable[[dict, dict], tuple[str, str, str]]:
    def f(_args: dict, r: dict) -> tuple[str, str, str]:
        if r.get("error"):
            return "failed", f"{done} (needs more details)", ""
        return "done", done, ""
    return f


# tool name → (label while running, function(args, result) → (status, label when finished, detail))
STEPS: dict[str, tuple[str, Callable[[dict, dict], tuple[str, str, str]]]] = {
    "check_availability": ("Checking the diary…", _availability),
    "book_appointment": ("Booking the appointment…", _booked),
    "create_booking": ("Booking…", _booked),
    "find_my_appointments": ("Looking up the appointment…", _simple("Found the appointment")),
    "reschedule_appointment": ("Moving the appointment…", _simple("Moved the appointment")),
    "cancel_appointment": ("Cancelling the appointment…", _simple("Cancelled the appointment")),
    "cancel_booking": ("Cancelling…", _simple("Cancelled")),
    "join_waitlist": ("Adding to the waitlist…", _simple("Added to the waitlist")),
    "triage_dental_emergency": ("Checking how urgent this is…", _triage),
    "answer_from_knowledge": ("Checking the practice's information…", _knowledge),
    "take_message": ("Taking a message for the team…", _simple("Message passed to the team")),
    "capture_lead": ("Saving the details for the team…", _simple("Details saved for the team")),
    "qualify_cosmetic_lead": ("Noting the treatment interest…", _simple("Flagged for a consultation follow-up")),
    "send_info_link": ("Sending the link…", _simple("Link sent")),
}


def detail_for_start(name: str, args: dict) -> str:
    return _when(args) if name in ("check_availability", "join_waitlist") else ""


class Activity:
    """Publishes steps for one call. `send` is the room's text sender (None → disabled)."""

    def __init__(self, send: Optional[Callable[[str], Any]]):
        self._send = send
        self._last: Optional[asyncio.Task] = None

    @property
    def enabled(self) -> bool:
        return self._send is not None

    async def _deliver(self, prev: Optional[asyncio.Task], text: str) -> None:
        if prev is not None:
            await asyncio.gather(prev, return_exceptions=True)   # "running" goes out before "done"
        send = self._send
        if send is None:
            return
        try:
            await send(text)
        except Exception as e:   # best effort: never let the page's feed break the call
            logger.debug("activity event not sent: %s", e)

    async def _emit(self, event: dict) -> None:
        """Queue the event without waiting for the network, so the tool itself never waits on the page."""
        if not self._send:
            return
        self._last = asyncio.create_task(self._deliver(self._last, json.dumps(event, ensure_ascii=False)))

    async def started(self, name: str, args: dict) -> Optional[str]:
        step = STEPS.get(name)
        if not step or not self._send:
            return None
        step_id = uuid.uuid4().hex[:10]
        await self._emit({"id": step_id, "tool": name, "label": step[0], "status": "running",
                          "detail": detail_for_start(name, args)})
        return step_id

    async def finished(self, step_id: Optional[str], name: str, args: dict, result: str) -> None:
        step = STEPS.get(name)
        if not step_id or not step:
            return
        try:
            status, label, detail = step[1](args, _parse(result))
        except Exception:   # an unexpected result shape: still close the row
            status, label, detail = "done", step[0].rstrip("…"), ""
        await self._emit({"id": step_id, "tool": name, "label": label, "status": status, "detail": detail})


def for_state(state: Any) -> Activity:
    """The publisher for this call: on for browser calls in a LiveKit room, off otherwise."""
    if getattr(state, "channel", "phone") != "browser":
        return Activity(None)
    try:
        from livekit.agents import get_job_context

        participant = get_job_context().room.local_participant
    except Exception:
        return Activity(None)

    async def send(text: str) -> None:
        await participant.send_text(text, topic=TOPIC)

    return Activity(send)
