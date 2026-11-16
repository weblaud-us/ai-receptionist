"""
What the result of one dial does to the attempt and to the lead (Roadmap Tasks 4.2 and 4.4).

An attempt is finished exactly once (`finish_attempt` is atomic on `status != "done"`), so the
agent's shutdown hook, a crash clean-up and a person correcting the outcome can never both apply.

    retry outcomes (no answer, busy, voicemail, machine…)   → queued again after the campaign's
                                                             retry delay, or `exhausted`
    decisive outcomes (demo booked, not interested, DNC…)    → the lead is finished
    uncounted outcomes (blocked by the gate, operator absent) → the attempt is handed back

A lead that is on the do-not-call list is never moved out of `dnc`, whatever comes later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pymongo import ReturnDocument

from config import settings
from core.outbound import models as m
from core.outbound import registry

logger = logging.getLogger(__name__)

DEFAULT_CALLBACK_DELAY = timedelta(hours=24)

# SIP response → outcome when the call never connected.
_SIP_BUSY = {486, 600}
_SIP_NO_ANSWER = {408, 480, 487, 603}
_SIP_BAD_NUMBER = {404, 410, 484, 604}


def sip_outcome(status_code: Optional[int]) -> str:
    if status_code in _SIP_BUSY:
        return "busy"
    if status_code in _SIP_NO_ANSWER:
        return "no_answer"
    if status_code in _SIP_BAD_NUMBER:
        return "wrong_number"
    return "failed"


def amd_outcome(category: str) -> Optional[str]:
    """Outcome when the answering-machine detector did not hear a person. None = a person answered."""
    if category == "human":
        return None
    if category in ("machine-vm", "machine-unavailable"):
        return "voicemail"
    if category == "machine-ivr":
        return "machine"
    return "uncertain"


def retry_delay(campaign: Dict[str, Any], attempts_made: int) -> timedelta:
    hours = list(campaign.get("retry_after_hours") or [48])
    return timedelta(hours=hours[min(max(attempts_made, 1), len(hours)) - 1])


def call_cost(duration_s: Optional[float]) -> float:
    return round(max(0.0, float(duration_s or 0)) / 60 * settings.SALES_COST_PER_MINUTE_USD, 4)


async def apply_to_lead(db: Any, lead_id: str, campaign: Optional[Dict[str, Any]], outcome: str, *,
                        now: datetime, callback_at: Optional[datetime] = None) -> None:
    lead = await db[m.LEADS].find_one({"_id": lead_id})
    if lead is None or lead.get("status") == "dnc":
        return
    sets: Dict[str, Any] = {"updated_at": now, "last_outcome": outcome}
    inc: Dict[str, int] = {}
    campaign = campaign or {}
    if outcome in m.UNCOUNTED_OUTCOMES:
        inc["attempts"] = -1 if int(lead.get("attempts") or 0) > 0 else 0
        if lead.get("status") == "in_progress":                 # the gate may already have parked it
            sets.update(status="queued", next_eligible_at=now)
    elif outcome in m.LEAD_STATUS_FOR_OUTCOME:
        sets["status"] = m.LEAD_STATUS_FOR_OUTCOME[outcome]
        sets["next_eligible_at"] = (callback_at or now + DEFAULT_CALLBACK_DELAY) if outcome == "callback" else None
    else:                                                        # a retry outcome, or an unknown one
        made = int(lead.get("attempts") or 0)
        if made >= int(campaign.get("max_attempts") or 3):
            sets.update(status="exhausted", next_eligible_at=None)
        else:
            sets.update(status="queued", next_eligible_at=now + retry_delay(campaign, made))
    update: Dict[str, Any] = {"$set": sets}
    if inc.get("attempts"):
        update["$inc"] = inc
    await db[m.LEADS].update_one({"_id": lead_id, "status": {"$ne": "dnc"}}, update)
    if outcome == "dnc":
        await registry.add_to_dnc(db, lead["phone"], reason="requested_on_call", source="attempt", now=now)


async def finish_attempt(db: Any, attempt_id: str, outcome: str, *, now: Optional[datetime] = None,
                         amd: Optional[str] = None, sip_status: Optional[int] = None,
                         duration_s: Optional[float] = None, summary: Optional[str] = None,
                         next_step: Optional[str] = None, callback_at: Optional[datetime] = None,
                         detail: str = "") -> Optional[Dict[str, Any]]:
    """Close an attempt and move its lead on. None when the attempt was already closed."""
    now = now or datetime.now(timezone.utc)
    sets: Dict[str, Any] = {"status": "done", "outcome": outcome, "ended_at": now, "detail": detail[:200]}
    for key, value in (("amd", amd), ("sip_status", sip_status), ("summary", summary), ("next_step", next_step)):
        if value is not None:
            sets[key] = value
    if duration_s is not None:
        sets["duration_sec"] = round(float(duration_s), 1)
        sets["cost_usd"] = call_cost(duration_s)
    attempt = await db[m.ATTEMPTS].find_one_and_update(
        {"_id": attempt_id, "status": {"$ne": "done"}}, {"$set": sets}, return_document=ReturnDocument.AFTER)
    if attempt is None:
        return None
    campaign = await db[m.CAMPAIGNS].find_one({"_id": attempt.get("campaign_id")})
    await apply_to_lead(db, attempt["lead_id"], campaign, outcome, now=now, callback_at=callback_at)
    logger.info("Attempt %s finished: %s%s", attempt_id, outcome, f" ({detail})" if detail else "")
    return attempt


async def relabel_attempt(db: Any, attempt_id: str, outcome: str, *, now: Optional[datetime] = None,
                          by: str = "") -> Optional[Dict[str, Any]]:
    """A person corrects a finished attempt's outcome (wrong classification, or a call they took over).
    The lead follows only when the new outcome is decisive; a queued retry is left as it is."""
    if outcome not in m.OUTCOMES:
        raise ValueError(f"unknown outcome {outcome}")
    now = now or datetime.now(timezone.utc)
    attempt = await db[m.ATTEMPTS].find_one_and_update(
        {"_id": attempt_id, "status": "done"},
        {"$set": {"outcome": outcome, "outcome_set_by": by, "outcome_set_at": now}},
        return_document=ReturnDocument.AFTER)
    if attempt is None:
        return None
    if outcome in m.LEAD_STATUS_FOR_OUTCOME:
        campaign = await db[m.CAMPAIGNS].find_one({"_id": attempt.get("campaign_id")})
        await apply_to_lead(db, attempt["lead_id"], campaign, outcome, now=now)
    return attempt
