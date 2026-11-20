"""
Missed and abandoned call recovery: one text to a caller who hung up without booking or leaving a
message, with a link to book (or the number to call back).

    schedule(state)          called by agent/persistence.CallRecorder.finish at the end of every call
    send(tenant_id, call_id) the job (jobs/worker.missed_call_recovery), about 10 s later

Rules:
- Off unless the business turns it on (`missed_call_recovery.enabled`).
- Inbound phone calls with a known caller number only. Never after a booking, reschedule,
  cancellation, message, lead, transfer or voicemail, and never when the assistant ended the call.
- At most one text per caller per business per 24 h (core/dedupe.first_time), and never to someone
  who opted out: notify_contact checks the opt-out list and fails closed.
- ⚖ AU Spam Act 2003 and UK PECR: the text names the business and always says how to opt out
  ("Reply STOP"). It follows up a call the person just made to that business, nothing more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from core import dedupe
from core.notifications import fire, notify_contact
from core.phone import mask_phone
from core.tenancy.models import TenantConfig

if TYPE_CHECKING:
    from agent.state import CallState

logger = logging.getLogger(__name__)

DELAY_S = 10                       # the text lands within ~15 s of the hang-up
DEDUPE_TTL_S = 24 * 3600
# A call that ended with one of these needs no follow-up.
HANDLED_OUTCOMES = frozenset({"booked", "rescheduled", "cancelled", "message", "transferred", "voicemail",
                              "unassigned"})
# The assistant (not the caller) ended it, or it went somewhere else.
NOT_ABANDONED = frozenset({"agent_hangup", "transferred", "unassigned_number"})
OPT_OUT_LINE = "Reply STOP to opt out."

DEFAULT_WITH_LINK = "Thanks for calling {business}. Sorry we didn't get you booked in. Book online any time: {link}"
DEFAULT_PHONE_ONLY = "Thanks for calling {business}. Sorry we didn't get you booked in. Call us back on {phone}."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def wants_recovery(tenant: TenantConfig, *, channel: str, direction: str, caller_phone: Optional[str],
                   outcome: Optional[str], ended_reason: Optional[str]) -> bool:
    """Should this finished call get a recovery text?"""
    if not tenant.missed_call_recovery.enabled or tenant.is_legacy or not tenant.active:
        return False
    if channel != "phone" or direction != "inbound" or not caller_phone:
        return False
    if (outcome or "completed") in HANDLED_OUTCOMES:
        return False
    reason = ended_reason or ""
    # billing_*: the business is suspended or over its hard cap; we don't text on its behalf.
    return reason not in NOT_ABANDONED and not reason.startswith("billing_")


def message_text(tenant: TenantConfig) -> Optional[str]:
    """The text to send, or None when there is nothing useful to send (no link and no number)."""
    link = tenant.links.booking or tenant.website
    phone = tenant.phone
    template = tenant.missed_call_recovery.template
    if not template:
        if link:
            template = DEFAULT_WITH_LINK
        elif phone:
            template = DEFAULT_PHONE_ONLY
        else:
            return None
    if ("{link}" in template and not link) or ("{phone}" in template and not phone):
        return None
    try:
        text = template.format(business=tenant.name, link=link, phone=phone)
    except (KeyError, IndexError, ValueError):
        logger.warning("Recovery template for %s has unknown placeholders; using the standard one", tenant.id)
        text = (DEFAULT_WITH_LINK if link else DEFAULT_PHONE_ONLY).format(business=tenant.name, link=link, phone=phone)
    if tenant.name.lower() not in text.lower():
        text = f"{tenant.name}: {text}"                 # the sender must be identifiable
    if "stop" not in text.lower():
        text = f"{text} {OPT_OUT_LINE}"
    return text[:600]


async def schedule(state: "CallState") -> bool:
    """Queue the recovery text for a finished call (never raises). True = queued or sent."""
    try:
        tenant = state.profile.tenant
        if not wants_recovery(tenant, channel=state.channel, direction=state.direction,
                              caller_phone=state.caller_phone, outcome=state.outcome,
                              ended_reason=state.ended_reason):
            return False
        from jobs.queue import enqueue

        if await enqueue("missed_call_recovery", tenant.id, state.call_id, defer_s=DELAY_S,
                         job_id=f"mcr:{state.call_id}"):
            return True
        fire(send(tenant.id, state.call_id))           # no jobs queue (dev, Redis down): send now
        return True
    except Exception:
        logger.warning("Could not schedule the missed-call text for %s", state.call_id, exc_info=True)
        return False


async def send(tenant_id: str, call_id: str, *, db: Any = None) -> Dict[str, Any]:
    """Send the recovery text for one call, re-checking everything against the stored call."""
    from core.repositories import Repos
    from core.tenancy import resolve_by_id
    from database.mongodb import get_db

    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        return {"sent": False, "reason": "unknown_business"}
    repos = Repos(db or get_db(), tenant.id)
    call = await repos.calls.find_one({"call_id": call_id})
    if not call:
        return {"sent": False, "reason": "unknown_call"}
    if call.get("recovery_sent_at"):
        return {"sent": False, "reason": "already_sent"}
    phone = str(call.get("caller_phone") or "")
    if not wants_recovery(tenant, channel=call.get("channel") or "phone", direction=call.get("direction") or "inbound",
                          caller_phone=phone, outcome=call.get("outcome"), ended_reason=call.get("ended_reason")):
        return {"sent": False, "reason": "not_needed"}
    started = call.get("started_at")
    if started and await repos.appointments.find_one({"phone": phone, "created_at": {"$gte": started}}, {"_id": 1}):
        return {"sent": False, "reason": "booked_since"}       # e.g. they called back, or booked online
    text = message_text(tenant)
    if not text:
        return {"sent": False, "reason": "nothing_to_send"}
    if not await dedupe.first_time("missed_call", f"{tenant.id}:{phone}", DEDUPE_TTL_S):
        return {"sent": False, "reason": "recently_texted"}
    prefer = "whatsapp" if tenant.missed_call_recovery.channel == "whatsapp" else "sms"
    ok = await notify_contact(tenant, phone, text, prefer=prefer)
    if ok:
        await repos.calls.update_one({"call_id": call_id}, {"$set": {"recovery_sent_at": _utcnow(),
                                                                      "recovery_channel": prefer}})
        logger.info("Missed-call text sent to %s for call %s", mask_phone(phone), call_id)
    return {"sent": ok, "reason": "sent" if ok else "send_failed_or_opted_out"}
