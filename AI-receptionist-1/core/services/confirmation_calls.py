"""
Appointment confirmation calls (plan Phase 2.5): a short outbound call the day before, by voice,
for patients the WhatsApp reminder couldn't reach (or for every appointment, if the practice asks).

    tenant jobs tick → run_due(tenant)              who to call now, claimed one by one
                     → dispatch()                   the receptionist worker joins room confirm-<id>-<n>
    agent/confirm.py → dials the patient from the practice's own number, AI disclosed first,
                       confirm / move (date of birth checked) / cancel → record_outcome()

These are service calls about the patient's own booking, never marketing. Rules that don't depend on
the model:
- Only inside the practice's calling window (business-local), never within 2 hours of the visit.
- Never to a contact marked do-not-contact or opted out.
- At most MAX_ATTEMPTS calls per appointment, at least RETRY_AFTER apart.
- A voicemail gives the practice name and number only: no treatment, no time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from config import settings
from core.repositories import Repos
from core.tenancy.models import TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

KIND = "appointment_confirmation"
MAX_ATTEMPTS = 2
RETRY_AFTER = timedelta(hours=2)
MIN_LEAD = timedelta(hours=2)            # never ring this close to the visit
MAX_LEAD = timedelta(hours=30)
STUCK_AFTER = timedelta(hours=1)         # a "calling" claim this old means the job died: try again
# Someone answered and didn't decide ("no_decision"): don't ring them again.
FINAL = ("confirmed", "cancelled", "rescheduled", "no_decision", "skipped")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_window(t: TenantConfig, now: datetime) -> bool:
    """Business-local calling hours, e.g. "10:00-18:00"; never on a Sunday."""
    local = _aware(now).astimezone(ZoneInfo(t.timezone))
    if local.weekday() == 6:
        return False
    try:
        start_s, end_s = t.confirmation_calls.window.split("-")
        start, end = time.fromisoformat(start_s.strip()), time.fromisoformat(end_s.strip())
    except ValueError:
        return False
    return start <= local.time() < end


def ready() -> bool:
    return bool(settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID and settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY)


def _query(t: TenantConfig, now: datetime) -> Dict[str, Any]:
    q: Dict[str, Any] = {
        "status": "confirmed",
        "start": {"$gte": now + MIN_LEAD, "$lte": now + MAX_LEAD},
        "confirmation.status": {"$nin": list(FINAL)},
        "$or": [{"confirmation.attempts": {"$exists": False}},
                {"confirmation.attempts": {"$lt": MAX_ATTEMPTS}}],
    }
    if t.confirmation_calls.only_without_whatsapp:
        q["reminders.day_before.error"] = "send_failed"     # the WhatsApp reminder didn't reach them
    return q


async def _blocked(repos: Repos, t: TenantConfig, appt: Dict[str, Any]) -> Optional[str]:
    if not appt.get("phone"):
        return "no_phone"
    contact = await repos.contacts.find_one({"_id": appt.get("contact_id")}) if appt.get("contact_id") else None
    if (contact or {}).get("do_not_contact"):
        return "do_not_contact"
    from core.compliance.optout import is_opted_out

    try:
        if await is_opted_out(get_db(), t.id, appt["phone"]):
            return "opted_out"
    except Exception:
        return "opt_out_check_failed"            # fail closed
    return None


async def caller_id(repos: Repos) -> Optional[str]:
    """The practice's own number, so the patient sees who is calling."""
    row = await repos.phone_numbers.find_one({"status": {"$ne": "released"}})
    return (row or {}).get("e164")


async def dispatch(room: str, metadata: Dict[str, Any]) -> None:
    """Start the receptionist worker in `room`; it sees `kind` and runs the confirmation call."""
    from livekit import api

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=settings.LIVEKIT_AGENT_NAME, room=room, metadata=json.dumps(metadata)))
    finally:
        await lk.aclose()


async def run_due(t: TenantConfig, now: datetime) -> int:
    """Claim and dispatch every appointment that should get a confirmation call now."""
    cfg = t.confirmation_calls
    now = _aware(now)
    if not cfg.enabled or not ready() or not in_window(t, now):
        return 0
    repos = Repos(get_db(), t.id)
    from_number = await caller_id(repos)
    if not from_number:
        logger.warning("Confirmation calls are on for %s but it has no phone number to call from", t.id)
        return 0
    started = 0
    for appt in await repos.appointments.find(_query(t, now), sort=[("start", 1)], limit=20):
        c = appt.get("confirmation") or {}
        last = c.get("last_at")
        if last and now - _aware(last) < (STUCK_AFTER if c.get("status") == "calling" else RETRY_AFTER):
            continue
        reason = await _blocked(repos, t, appt)
        if reason:
            await repos.appointments.update_one({"appointment_id": appt["appointment_id"]},
                                                {"$set": {"confirmation.status": "skipped", "confirmation.reason": reason}})
            continue
        attempts = int(c.get("attempts") or 0)
        claimed = await repos.appointments.update_one(          # one runner per appointment and attempt
            {"appointment_id": appt["appointment_id"], "confirmation.last_at": last},
            {"$set": {"confirmation.status": "calling", "confirmation.last_at": now},
             "$inc": {"confirmation.attempts": 1}})
        if not claimed.modified_count:
            continue
        room = f"confirm-{appt['appointment_id']}-{attempts + 1}"
        try:
            await dispatch(room, {"kind": KIND, "tenant_id": t.id, "appointment_id": appt["appointment_id"],
                                  "from": from_number})
            started += 1
        except Exception:
            logger.error("Couldn't start the confirmation call for %s", appt["appointment_id"], exc_info=True)
            await repos.appointments.update_one({"appointment_id": appt["appointment_id"]},
                                                {"$set": {"confirmation.status": "failed"}})
    return started


async def record_outcome(tenant_id: str, appointment_id: str, status: str, *, call_id: str,
                         detail: str = "") -> None:
    """Written by the agent when the call ends. A moved booking keeps its new appointment confirmed."""
    repos = Repos(get_db(), tenant_id)
    sets: Dict[str, Any] = {"confirmation.status": status, "confirmation.call_id": call_id,
                            "confirmation.at": utcnow()}
    if detail:
        sets["confirmation.detail"] = detail[:200]
    await repos.appointments.update_one({"appointment_id": appointment_id}, {"$set": sets})
    if status == "rescheduled":
        appt = await repos.appointments.find_one({"appointment_id": appointment_id}, {"rescheduled_to": 1})
        if appt and appt.get("rescheduled_to"):
            await repos.appointments.update_one({"appointment_id": appt["rescheduled_to"]},
                                                {"$set": {"confirmation.status": "confirmed",
                                                          "confirmation.call_id": call_id}})


def opener(*, agent_name: str, business: str, patient_first_name: str, when: str) -> str:
    """First sentence: who is calling, that it's an AI, and the question. No treatment named."""
    who = f"{patient_first_name}'s" if patient_first_name else "an"
    return (f"Hi, this is {agent_name}, the AI assistant at {business}. I'm calling about {who} appointment "
            f"{when}. Can you still make it?")


def voicemail(*, business: str, phone: str) -> str:
    """No time, no treatment: whoever hears the voicemail learns only who called."""
    back = f" on {phone}" if phone else ""
    return f"Hello, this is {business} calling about an appointment. Please call us back{back}. Thank you."


def first_name(name: str) -> str:
    parts = (name or "").split()
    return parts[0] if parts else ""


def instructions(*, business: str, when: str) -> str:
    return f"""# This call
You are calling a patient of {business} the day before their appointment ({when}); they did not call you.
You already said who you are, that you're the practice's AI assistant, and asked if they can make it.
- If they confirm, call confirm_appointment, thank them briefly and say goodbye.
- If they want another time, use check_availability, then reschedule_appointment (ask the patient's full
  name and date of birth first).
- If they want to cancel, offer another time once; if they still want to cancel, use cancel_appointment
  (name and date of birth first).
- If you've reached the wrong person, or they can't talk now, apologise, offer to take a message, and end.
- Keep it short. Never give clinical advice. Say goodbye before end_call."""


def outcome_of(confirmed: bool, tool_outcome: Optional[str], amd: Optional[str], answered: bool) -> str:
    """The confirmation status to store, from what happened on the call."""
    if confirmed:
        return "confirmed"
    if tool_outcome in ("rescheduled", "cancelled"):
        return str(tool_outcome)
    if amd and amd.startswith("machine"):
        return "voicemail"
    if not answered:
        return "no_answer"
    return "no_decision"

