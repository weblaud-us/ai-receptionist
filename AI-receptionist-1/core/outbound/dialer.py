"""
The dialer engine (Roadmap Task 4.4).

    tick()  every ~20 s (jobs worker; or the API itself when there is no Redis)
      for each running AI campaign:
        free  = max_concurrent − calls in progress, capped by the campaign's daily cap
        claim that many leads                 atomic: queued/callback → in_progress
        gate each one again                   fail → the lead is parked or stopped, nothing is dialled
        pick a caller ID                      local presence; a number over its daily volume is rested
        write the call attempt, then dispatch the sales agent into room out-<attempt id>

The engine never talks on the phone: the sales agent (agent/sales.py) receives the attempt id,
re-checks the gate itself, dials, and reports the outcome with `outcomes.finish_attempt`.

`human_assisted` campaigns are not dialled by the tick. A person presses "Dial next" in the
dashboard (`dial_next`), is put into the room first, and only then is the lead rung — so nobody
ever answers to silence.

Nothing here runs unless OUTBOUND_DIALER_ENABLED is true and `readiness()` is clean.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from pymongo import ReturnDocument

from config import settings
from core.outbound import gate, outcomes, persona
from core.outbound import models as m
from core.outbound import phones
from core.phone import mask_phone
from core.tenancy.ids import new_id

logger = logging.getLogger(__name__)

TICK_SECONDS = 20
MAX_DIALS_PER_TICK = 10                       # across all campaigns: a bug can never start a flood
DAY = timedelta(hours=24)
STUCK_LEAD = timedelta(minutes=30)
CAPPED_NUMBER_RETRY = timedelta(hours=1)

Dispatch = Callable[[str, Dict[str, Any]], Awaitable[None]]


def room_name(attempt_id: str) -> str:
    return f"out-{attempt_id}"


async def livekit_dispatch(room: str, metadata: Dict[str, Any]) -> None:
    """Ask LiveKit to start the sales agent in `room` (explicit dispatch by agent name)."""
    from livekit import api

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=settings.SALES_AGENT_NAME, room=room, metadata=json.dumps(metadata)))
    finally:
        await lk.aclose()


def readiness() -> List[str]:
    """Why the dialer cannot dial, as sentences for the dashboard. Empty = ready."""
    problems = []
    if not settings.OUTBOUND_DIALER_ENABLED:
        problems.append("The dialer is switched off (OUTBOUND_DIALER_ENABLED=false).")
    if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        problems.append("LiveKit is not configured.")
    if not settings.sales_trunk_id:
        problems.append("No outbound trunk for sales calls (SALES_SIP_OUTBOUND_TRUNK_ID).")
    if not settings.SALES_TENANT_ID:
        problems.append("SALES_TENANT_ID is not set (your own business, which the sales agent speaks for).")
    return problems


def campaign_problems(campaign: Mapping[str, Any]) -> List[str]:
    """Why this campaign cannot be started."""
    from core.outbound import lookup

    problems = list(readiness())
    if not campaign.get("from_numbers"):
        problems.append("The campaign has no caller ID (from number).")
    if campaign.get("mode") == "ai_cold_b2b_us" and not lookup.configured():
        problems.append("Line-type lookups are not configured (Twilio credentials), so no lead can pass the gate.")
    return problems


# ── Bookkeeping ──────────────────────────────────────────────────────────────

def _stale_before(now: datetime) -> datetime:
    return now - timedelta(minutes=settings.SALES_MAX_CALL_MINUTES + 5)


async def active_count(db: Any, campaign_id: str, now: datetime) -> int:
    return await db[m.ATTEMPTS].count_documents(
        {"campaign_id": campaign_id, "status": {"$in": list(m.ACTIVE_ATTEMPT_STATUSES)},
         "created_at": {"$gte": _stale_before(now)}})


async def dialled_since(db: Any, query: Dict[str, Any], since: datetime) -> int:
    return await db[m.ATTEMPTS].count_documents({**query, "created_at": {"$gte": since},
                                                 "outcome": {"$nin": list(m.UNCOUNTED_OUTCOMES)}})


async def reap(db: Any, now: datetime) -> int:
    """Close attempts whose agent died, and release leads claimed by a tick that never finished."""
    reaped = 0
    stale = await db[m.ATTEMPTS].find(
        {"status": {"$in": list(m.ACTIVE_ATTEMPT_STATUSES)}, "created_at": {"$lt": _stale_before(now)}},
        {"_id": 1}).to_list(200)
    for row in stale:
        if await outcomes.finish_attempt(db, row["_id"], "failed", now=now, detail="no result from the agent"):
            reaped += 1
    live = {a["lead_id"] async for a in db[m.ATTEMPTS].find(
        {"status": {"$in": list(m.ACTIVE_ATTEMPT_STATUSES)}}, {"lead_id": 1})}
    stuck = await db[m.LEADS].find({"status": "in_progress", "claimed_at": {"$lt": now - STUCK_LEAD}},
                                   {"_id": 1}).to_list(500)
    for lead in stuck:
        if lead["_id"] not in live:
            await db[m.LEADS].update_one({"_id": lead["_id"], "status": "in_progress"},
                                         {"$set": {"status": "queued", "next_eligible_at": now}})
            reaped += 1
    return reaped


async def claim_lead(db: Any, campaign_id: str, now: datetime) -> Optional[Dict[str, Any]]:
    """One due lead, atomically moved to in_progress. Promised callbacks go first."""
    for status in m.DIALLABLE_LEAD_STATUSES:
        lead = await db[m.LEADS].find_one_and_update(
            {"campaign_id": campaign_id, "status": status, "next_eligible_at": {"$lte": now}},
            {"$set": {"status": "in_progress", "claimed_at": now, "claimed_from": status}},
            sort=[("next_eligible_at", 1)], return_document=ReturnDocument.AFTER)
        if lead is not None:
            return lead
    return None


async def release_lead(db: Any, lead: Mapping[str, Any], *, eligible_at: datetime) -> None:
    await db[m.LEADS].update_one(
        {"_id": lead["_id"], "status": "in_progress"},
        {"$set": {"status": lead.get("claimed_from") or "queued", "next_eligible_at": eligible_at}})


async def apply_gate_failure(db: Any, lead: Mapping[str, Any], decision: gate.GateDecision, now: datetime) -> None:
    """Park (temporary failure) or stop (permanent failure) a lead the gate refused."""
    if decision.permanent:
        await db[m.LEADS].update_one(
            {"_id": lead["_id"], "status": {"$ne": "dnc"}},
            {"$set": {"status": decision.lead_status(), "next_eligible_at": None, "updated_at": now}})
    else:
        await release_lead(db, lead, eligible_at=decision.retry_at or now + timedelta(hours=1))


async def pick_from_number(db: Any, campaign: Mapping[str, Any], lead: Mapping[str, Any], now: datetime) -> Optional[str]:
    """Caller ID for this lead: same area code, else same state, else any — skipping numbers that
    have reached SALES_MAX_CALLS_PER_NUMBER_PER_DAY. None = every number is resting."""
    lead_code = phones.area_code(lead["phone"])

    def rank(number: str) -> int:
        if lead_code and phones.area_code(number) == lead_code:
            return 0
        info = phones.parse(number)
        return 1 if info and info.region_code and info.region_code == lead.get("region_code") else 2

    for number in sorted(campaign.get("from_numbers") or [], key=rank):
        if await dialled_since(db, {"from": number}, now - DAY) < settings.SALES_MAX_CALLS_PER_NUMBER_PER_DAY:
            return number
    return None


# ── One dial ─────────────────────────────────────────────────────────────────

async def dial_lead(db: Any, campaign: Mapping[str, Any], lead: Dict[str, Any], now: datetime,
                    dispatch: Dispatch, *, operator: Optional[str] = None) -> Dict[str, Any]:
    """Gate a claimed lead and, if it passes, start the call. Never raises for a refused lead."""
    decision = await gate.run_gate(db, lead, campaign, now)
    if not decision.allowed:
        await apply_gate_failure(db, lead, decision, now)
        return {"dialed": False, "reason": "gate", "failures": decision.failures}

    from_number = await pick_from_number(db, campaign, lead, now)
    if from_number is None:
        await release_lead(db, lead, eligible_at=now + CAPPED_NUMBER_RETRY)
        return {"dialed": False, "reason": "numbers_resting", "failures": []}

    attempt_id = new_id("att")
    persona_id = persona.pick(campaign.get("persona_ids") or [], lead["_id"])
    attempt = {
        "_id": attempt_id, "lead_id": lead["_id"], "campaign_id": campaign["_id"], "mode": campaign["mode"],
        "call_id": room_name(attempt_id), "from": from_number, "to": lead["phone"], "persona_id": persona_id,
        "status": "dispatched", "outcome": None, "amd": None, "sip_status": None, "operator": operator,
        "attempt_no": int(lead.get("attempts") or 0) + 1, "gate": decision.to_doc(),
        "created_at": now, "started_at": None, "ended_at": None, "duration_sec": None,
        "summary": None, "next_step": None, "cost_usd": 0.0, "amd_label": None,
    }
    await db[m.ATTEMPTS].insert_one(attempt)
    await db[m.LEADS].update_one({"_id": lead["_id"]},
                                 {"$inc": {"attempts": 1}, "$set": {"last_attempt_at": now, "updated_at": now}})
    try:
        await dispatch(attempt["call_id"], {"attempt_id": attempt_id, "lead_id": lead["_id"],
                                            "campaign_id": campaign["_id"]})
    except Exception:
        logger.error("Could not dispatch the sales agent for attempt %s", attempt_id, exc_info=True)
        await outcomes.finish_attempt(db, attempt_id, "dispatch_failed", now=now)   # hands the attempt back
        await db[m.LEADS].update_one({"_id": lead["_id"], "status": "queued"},
                                     {"$set": {"next_eligible_at": now + timedelta(minutes=10)}})
        return {"dialed": False, "reason": "dispatch_failed", "failures": []}
    logger.info("Dialling %s for campaign %s (attempt %s, from %s)", mask_phone(lead["phone"]), campaign["_id"],
                attempt_id, mask_phone(from_number))
    return {"dialed": True, "attempt_id": attempt_id, "room": attempt["call_id"], "from": from_number}


async def dial_next(db: Any, campaign: Mapping[str, Any], *, operator: str, dispatch: Dispatch = livekit_dispatch,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """Human-assisted power dialling: the next lead that passes the gate, for the person who asked."""
    now = now or datetime.now(timezone.utc)
    if await active_count(db, campaign["_id"], now) >= int(campaign.get("max_concurrent") or 1):
        return {"dialed": False, "reason": "busy", "failures": []}
    if await dialled_since(db, {"campaign_id": campaign["_id"]}, now - DAY) >= int(campaign.get("daily_cap") or 150):
        return {"dialed": False, "reason": "daily_cap", "failures": []}
    for _ in range(25):                           # skip past leads the gate refuses
        lead = await claim_lead(db, campaign["_id"], now)
        if lead is None:
            return {"dialed": False, "reason": "no_leads", "failures": []}
        result = await dial_lead(db, campaign, lead, now, dispatch, operator=operator)
        if result["dialed"] or result["reason"] != "gate":
            return {**result, "lead_id": lead["_id"]}
    return {"dialed": False, "reason": "no_leads", "failures": []}


# ── The tick ─────────────────────────────────────────────────────────────────

async def _maybe_complete(db: Any, campaign: Mapping[str, Any], now: datetime) -> bool:
    if campaign.get("mode") == "ai_consented":     # new sign-ups keep arriving from the form
        return False
    open_leads = await db[m.LEADS].count_documents(
        {"campaign_id": campaign["_id"], "status": {"$in": ["queued", "callback", "in_progress"]}})
    if open_leads or not await db[m.ATTEMPTS].count_documents({"campaign_id": campaign["_id"]}):
        return False
    await db[m.CAMPAIGNS].update_one({"_id": campaign["_id"], "status": "running"},
                                     {"$set": {"status": "completed", "completed_at": now, "updated_at": now}})
    logger.info("Campaign %s completed: no leads left to dial", campaign["_id"])
    return True


async def tick(db: Any, *, now: Optional[datetime] = None, dispatch: Dispatch = livekit_dispatch) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = {"campaigns": 0, "dialed": 0, "refused": 0, "reaped": 0, "completed": 0}
    stats["reaped"] = await reap(db, now)          # always: tidy up even while the dialer is off
    if readiness():
        return stats
    budget = MAX_DIALS_PER_TICK
    campaigns = await db[m.CAMPAIGNS].find({"status": "running"}).to_list(100)
    for campaign in campaigns:
        stats["campaigns"] += 1
        if campaign.get("mode") == "human_assisted":
            continue
        free = int(campaign.get("max_concurrent") or 1) - await active_count(db, campaign["_id"], now)
        left_today = int(campaign.get("daily_cap") or 150) - await dialled_since(
            db, {"campaign_id": campaign["_id"]}, now - DAY)
        slots = min(free, left_today, budget)
        tried = 0
        while slots > 0 and tried < 4 * MAX_DIALS_PER_TICK:
            lead = await claim_lead(db, campaign["_id"], now)
            if lead is None:
                break
            tried += 1
            result = await dial_lead(db, campaign, lead, now, dispatch)
            if result["dialed"]:
                stats["dialed"] += 1
                slots -= 1
                budget -= 1
            else:
                stats["refused"] += 1
                if result["reason"] in ("numbers_resting", "dispatch_failed"):
                    break
        if slots > 0 and await _maybe_complete(db, campaign, now):
            stats["completed"] += 1
        if budget <= 0:
            break
    return stats


async def queue_leads(db: Any, campaign: Mapping[str, Any], query: Dict[str, Any], *, limit: int = 5000,
                      now: Optional[datetime] = None) -> Dict[str, int]:
    """Put `new` leads matching `query` into a campaign's queue, pre-screening them for its mode."""
    from core.outbound.importer import prescreen

    now = now or datetime.now(timezone.utc)
    leads = await db[m.LEADS].find({**query, "owner": m.OWNER_PLATFORM, "status": "new"}).limit(limit).to_list(limit)
    counts = await prescreen(db, leads, campaign, now)
    queued = 0
    for lead in leads:
        blocked = lead["status"] != "new"
        sets = {"campaign_id": campaign["_id"], "gate": lead["gate"], "dnc": lead["dnc"], "updated_at": now,
                "status": lead["status"] if blocked else "queued", "next_eligible_at": None if blocked else now}
        res = await db[m.LEADS].update_one({"_id": lead["_id"], "status": "new"},
                                           {"$set": sets, "$push": {"gate_log": {"$each": [lead["gate"]], "$slice": -gate.GATE_LOG_MAX}}})
        queued += int(res.modified_count == 1 and not blocked)
    return {"matched": len(leads), "queued": queued, "blocked": counts["blocked"]}


class DialerLoop:
    """Runs `tick` inside the API process when there is no Redis (development, single-host setups)."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if not settings.OUTBOUND_DIALER_ENABLED or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._loop(), name="sales-dialer")
        logger.info("Sales dialer started in this process (every %ss)", TICK_SECONDS)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        from database.mongodb import get_db

        while True:
            try:
                await tick(get_db())
            except Exception:
                logger.error("Sales dialer tick failed", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)


dialer_loop = DialerLoop()
