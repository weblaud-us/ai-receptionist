"""
What the result of one dial does to the attempt and to the lead (Roadmap Tasks 4.2 and 4.4).

An attempt is finished exactly once (`finish_attempt` is atomic on `status != "done"`), so the
agent's shutdown hook, a crash clean-up and a person correcting the outcome can never both apply.

    retry outcomes (no answer, busy, voicemail, machine…)   → queued again after the campaign's
                                                             retry delay, or `exhausted`
    decisive outcomes (demo booked, not interested, DNC…)    → the lead is finished
    uncounted outcomes (blocked by the gate, operator absent) → the attempt is handed back

A lead that is on the do-not-call list is never moved out of `dnc`, whatever comes later.

"Not interested" is not an opt-out (SALES_DIALER_PLAN.md Phase 1A): the offer goes into the lead's
`declined_offers` (that offer is never pitched to them again) and `cooldown_until` keeps every other
offer away for SALES_NOT_INTERESTED_COOLDOWN_DAYS. `offer_history` records each decision, once per
attempt, so a person correcting the outcome can lift it again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Sequence

from pymongo import ReturnDocument

from config import settings
from core.outbound import followup
from core.outbound import models as m
from core.outbound import offers as of
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


def dial_timeout(error: BaseException) -> bool:
    """LiveKit's "sip request timed out" (twirp `canceled`, HTTP 408, no SIP status attached): the far end
    rang until our ring timeout and nobody answered. Twilio logs the same call as no-answer."""
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    return code in ("canceled", "deadline_exceeded") and status == 408


def amd_outcome(category: str) -> Optional[str]:
    """Outcome when the answering-machine detector did not hear a person. None = a person answered."""
    if category == "human":
        return None
    if category in ("machine-vm", "machine-unavailable"):
        return "voicemail"
    if category == "machine-ivr":
        return "machine"
    return "uncertain"


def hang_up_on_amd(category: str, *, human_assisted: bool, consented: bool) -> Optional[str]:
    """The outcome to hang up with after the answering-machine check, or None to go ahead with the call.
    An "uncertain" verdict (a quiet "hello?", a noisy line) is still answered when a person is on our side
    or when they asked for the call: hanging up on someone who requested it is worse than greeting a
    machine. A cold call hangs up and tries again later."""
    machine = amd_outcome(category)
    if machine == "uncertain" and (human_assisted or consented):
        return None
    return machine


def retry_delay(campaign: Dict[str, Any], attempts_made: int) -> timedelta:
    hours = list(campaign.get("retry_after_hours") or [48])
    return timedelta(hours=hours[min(max(attempts_made, 1), len(hours)) - 1])


def call_cost(duration_s: Optional[float]) -> float:
    return round(max(0.0, float(duration_s or 0)) / 60 * settings.SALES_COST_PER_MINUTE_USD, 4)


async def apply_to_lead(db: Any, lead_id: str, campaign: Optional[Dict[str, Any]], outcome: str, *,
                        now: datetime, callback_at: Optional[datetime] = None,
                        attempt: Optional[Dict[str, Any]] = None) -> None:
    lead = await db[m.LEADS].find_one({"_id": lead_id})
    if lead is None or lead.get("status") == "dnc":
        return
    if attempt is not None and attempt.get("kind") == "single":
        # A single call (core/outbound/single_call.py): no answer puts the lead back where it was, and
        # a callback leaves its campaign. Decisive outcomes go on below, the same as a campaign's.
        from core.outbound import single_call

        if await single_call.after_call(db, lead, attempt, outcome, now=now, callback_at=callback_at):
            return
        campaign = None
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
    elif outcome in m.SYSTEM_OUTCOMES and lead.get("status") not in ("in_progress", "queued"):
        # The dialer closing an attempt whose agent died ("failed"): a tool may already have moved
        # the lead on during the call (meeting booked, callback, wrong number…). Keep that.
        pass
    else:                                                        # a retry outcome, or an unknown one
        made = int(lead.get("attempts") or 0)
        if made >= int(campaign.get("max_attempts") or 3):
            sets.update(status="exhausted", next_eligible_at=None)
        else:
            due = now + retry_delay(campaign, made)
            if campaign.get("retry_timing") == "smart":      # an hour when businesses there pick up (Phase 6)
                from core.outbound import timing

                due = await timing.smart_retry(db, lead, campaign, earliest=due, now=now) or due
            sets.update(status="queued", next_eligible_at=due)
    update: Dict[str, Any] = {"$set": sets}
    if inc.get("attempts"):
        update["$inc"] = inc
    await db[m.LEADS].update_one({"_id": lead_id, "status": {"$ne": "dnc"}}, update)
    if outcome == "not_interested":
        await decline_offer(db, lead_id, attempt or {"campaign_id": campaign.get("_id"),
                                                     "offer_id": campaign.get("offer_id")}, now=now)
    if outcome == "dnc":
        await registry.add_to_dnc(db, lead["phone"], reason="requested_on_call", source="attempt", now=now)


def cooldown_days() -> int:
    return max(0, int(settings.SALES_NOT_INTERESTED_COOLDOWN_DAYS))


async def decline_offer(db: Any, lead_id: str, attempt: Dict[str, Any], *, now: datetime) -> None:
    """They said no to this offer: never pitch it again, and give every other offer a rest."""
    offer_id = of.offer_id_of(attempt)
    until = now + timedelta(days=cooldown_days())
    await db[m.LEADS].update_one({"_id": lead_id},
                                 {"$addToSet": {"declined_offers": offer_id}, "$max": {"cooldown_until": until}})
    entry = {"offer_id": offer_id, "campaign_id": attempt.get("campaign_id"), "attempt_id": attempt.get("_id"),
             "outcome": "not_interested", "reason": str(attempt.get("reason") or "")[:200], "at": now}
    query: Dict[str, Any] = {"_id": lead_id}
    if attempt.get("_id"):                       # once per attempt, however often the outcome is re-applied
        query["offer_history.attempt_id"] = {"$ne": attempt["_id"]}
    await db[m.LEADS].update_one(query, {"$push": {"offer_history": entry}})


async def undecline_attempt(db: Any, lead_id: str, attempt_id: str) -> None:
    """A person corrected a "not interested" call: drop its record and work the suppression out again
    from what is left."""
    lead = await db[m.LEADS].find_one({"_id": lead_id}, {"offer_history": 1})
    if lead is None:
        return
    history = [h for h in (lead.get("offer_history") or []) if h.get("attempt_id") != attempt_id]
    declined = [h for h in history if h.get("outcome") == "not_interested"]
    until = max((_aware(h["at"]) for h in declined if h.get("at")), default=None)
    await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {
        "offer_history": history,
        "declined_offers": sorted({h["offer_id"] for h in declined if h.get("offer_id")}),
        "cooldown_until": (until + timedelta(days=cooldown_days())) if until else None}})


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def finish_attempt(db: Any, attempt_id: str, outcome: str, *, now: Optional[datetime] = None,
                         amd: Optional[str] = None, sip_status: Optional[int] = None,
                         duration_s: Optional[float] = None, summary: Optional[str] = None,
                         next_step: Optional[str] = None, callback_at: Optional[datetime] = None,
                         detail: str = "", reason: Optional[str] = None,
                         extra: Optional[Dict[str, Any]] = None,
                         only_from: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    """Close an attempt and move its lead on. None when the attempt was already closed, or (with
    `only_from`) was no longer in one of those statuses: a call the agent has just started dialling
    is never closed under it by someone cancelling one that hadn't rung yet."""
    now = now or datetime.now(timezone.utc)
    sets: Dict[str, Any] = {**(extra or {}), "status": "done", "outcome": outcome, "ended_at": now,
                            "detail": detail[:200]}
    for key, value in (("amd", amd), ("sip_status", sip_status), ("summary", summary), ("next_step", next_step),
                       ("reason", reason)):
        if value is not None:
            sets[key] = value
    if duration_s is not None:
        sets["duration_sec"] = round(float(duration_s), 1)
        sets["cost_usd"] = call_cost(duration_s)
    if callback_at is not None:
        sets["callback_at"] = callback_at           # so the learning loop can tell whether it was kept
    attempt = await db[m.ATTEMPTS].find_one_and_update(
        {"_id": attempt_id, "status": {"$in": list(only_from)} if only_from else {"$ne": "done"}}, {"$set": sets},
        return_document=ReturnDocument.AFTER)
    if attempt is None:
        return None
    # On a human-assisted call the person's wrap-up form is the outcome, whichever finished first.
    wrapup = attempt.get("wrapup") or {}
    if wrapup.get("outcome") and wrapup["outcome"] != outcome and outcome != "dnc":
        outcome = str(wrapup["outcome"])
        callback_at = _aware(wrapup["callback_at"]) if wrapup.get("callback_at") else callback_at
        fix: Dict[str, Any] = {"outcome": outcome, "outcome_set_by": wrapup.get("by")}
        if callback_at is not None:
            fix["callback_at"] = callback_at
        await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": fix})
        attempt.update(fix)
    single = attempt.get("kind") == "single"
    campaign = None if single else await db[m.CAMPAIGNS].find_one({"_id": attempt.get("campaign_id")})
    await apply_to_lead(db, attempt["lead_id"], campaign, outcome, now=now, callback_at=callback_at, attempt=attempt)
    if single and outcome not in m.UNCOUNTED_OUTCOMES:      # a call went out: the pipeline counts it as contact
        await db[m.LEADS].update_one({"_id": attempt["lead_id"]}, {"$inc": {"single_calls": 1}})
    await _record_call(db, attempt, outcome, now)
    logger.info("Attempt %s finished: %s%s", attempt_id, outcome, f" ({detail})" if detail else "")
    return attempt


async def _record_call(db: Any, attempt: Dict[str, Any], outcome: str, now: datetime) -> None:
    """The lead's timeline gets every call someone (or a machine) answered, and a follow-up task when
    they were interested. Best effort: a failure here never undoes the outcome."""
    if outcome in m.UNCOUNTED_OUTCOMES or outcome in ("no_answer", "busy", "failed"):
        return
    try:
        text = f"Call: {m.OUTCOME_LABELS.get(outcome, outcome)}"
        if attempt.get("summary"):
            text += f". {attempt['summary']}"
        await followup.add_activity(db, attempt["lead_id"], "call", text, by=attempt.get("operator") or "sales_agent",
                                    meta={"attempt_id": attempt["_id"], "outcome": outcome}, now=now)
        lead = await db[m.LEADS].find_one({"_id": attempt["lead_id"]})
        if lead:
            await followup.auto_follow_up(db, lead, attempt, outcome, now=now)
            await _crm_after_call(db, lead, attempt, outcome, now)
    except Exception:
        logger.warning("Could not record call %s on the lead's timeline", attempt.get("_id"), exc_info=True)


async def _crm_after_call(db: Any, lead: Dict[str, Any], attempt: Dict[str, Any], outcome: str, now: datetime) -> None:
    """The built-in CRM (SALES_SUITE_PLAN.md wave 3): open or move the deal, start follow-up emails."""
    from core.outbound import crm
    from core.outbound import offers as of

    await crm.ensure_deal(db, lead, attempt, outcome, now=now)
    if attempt.get("email_consent_at") and not lead.get("email_consent_at"):
        await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"email_consent_at": attempt["email_consent_at"]}})
        lead = {**lead, "email_consent_at": attempt["email_consent_at"]}
    trigger = {"demo_booked": "meeting_booked", "interested": "info_sent"}.get(outcome)
    if trigger:
        offer = await of.offer_for(db, attempt.get("offer_id"), attempt.get("offer_version"))
        await followup.schedule_sequence(db, lead, offer, trigger, attempt_id=attempt.get("_id"), now=now)
    if outcome in ("dnc", "not_interested", "wrong_number"):
        await followup.stop_sequences(db, str(lead["_id"]), reason=m.OUTCOME_LABELS.get(outcome, outcome), now=now)


async def relabel_attempt(db: Any, attempt_id: str, outcome: str, *, now: Optional[datetime] = None,
                          by: str = "", callback_at: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """A person corrects a finished attempt's outcome (wrong classification, or a call they took over).
    The lead follows only when the new outcome is decisive; a queued retry is left as it is."""
    if outcome not in m.OUTCOMES:
        raise ValueError(f"unknown outcome {outcome}")
    now = now or datetime.now(timezone.utc)
    fix: Dict[str, Any] = {"outcome": outcome, "outcome_set_by": by, "outcome_set_at": now}
    if callback_at is not None:
        fix["callback_at"] = callback_at
    before = await db[m.ATTEMPTS].find_one_and_update(
        {"_id": attempt_id, "status": "done"}, {"$set": fix}, return_document=ReturnDocument.BEFORE)
    if before is None:
        return None
    attempt = {**before, **fix}
    if before.get("outcome") == "not_interested" and outcome != "not_interested":
        await undecline_attempt(db, attempt["lead_id"], attempt_id)
    if outcome in m.LEAD_STATUS_FOR_OUTCOME:
        campaign = await db[m.CAMPAIGNS].find_one({"_id": attempt.get("campaign_id")})
        await apply_to_lead(db, attempt["lead_id"], campaign, outcome, now=now, attempt=attempt, callback_at=callback_at)
    return attempt
