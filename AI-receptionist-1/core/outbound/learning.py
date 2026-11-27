"""
The learning loop (SALES_LEARNING_PLAN.md): one tick, every minute, in the jobs worker
(`sales_learning_tick`), or in the API process when there is no Redis (`LearningLoop`).

    coach reviews       core/outbound/coach.py        each finished AI call, within its own budget
    playbook tests      core/outbound/playbook.py     passed → live or ready to test; failed → kept off calls
    the next playbook   core/outbound/learn.py        autopilot: the AI writes one from the coach's lessons
    live tests          core/outbound/experiments.py  start the next one; keep it, roll it back, or stop
    pick-up rates       core/outbound/timing.py       hourly: when businesses answer, for smart retries
    daily clean-up      the coach's notes after SALES_REVIEW_RETENTION_DAYS

`assign` runs at dial time (core/outbound/dialer.dial_lead): which playbook a call uses. During a test
each business is enrolled once, at its first dial, into the arm `experiments.assign_arm` gives it, and
keeps it (`lead.assign`). A business already in conversation about the offer when the test starts
stays on the current playbook and isn't counted, so no conversation is finished by another playbook.

Each step is isolated: one failing never stops the others, and none of them is on a call's path.
Decisions are applied once (a conditional update on the test), even with two workers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from config import settings
from core.outbound import models as m

logger = logging.getLogger(__name__)

STATS = "sales_learning_stats"          # small state documents: last clean-up, cached tables
EXPERIMENTS = "sales_experiments"
TICK_SECONDS = 60
PURGE_EVERY = timedelta(hours=24)
ACTOR = "system:learning"
CHECK_GRACE = timedelta(hours=2)        # a call this recent may not be reviewed yet: not counted in coverage
ACTIVE = ("running", "paused_traffic")
MAX_TEST_ATTEMPTS = 20_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _due(db: Any, key: str, every: timedelta, now: datetime) -> bool:
    """True once per `every` (claimed atomically, so two workers never both run it)."""
    from pymongo.errors import DuplicateKeyError

    try:
        res = await db[STATS].update_one({"_id": key, "$or": [{"at": {"$lt": now - every}}, {"at": None}]},
                                         {"$set": {"at": now}}, upsert=True)
    except DuplicateKeyError:             # it exists and isn't due: the filter missed and the upsert collided
        return False
    return bool(res.modified_count or res.upserted_id)


async def _audit(db: Any, action: str, target_type: str, target_id: str, diff: Dict[str, Any]) -> None:
    from core.outbound.guardrails import _system_audit

    await _system_audit(db, action, target_type, target_id, diff, actor=ACTOR)


OFF: Dict[str, Any] = {"playbook_id": None, "arm": "off", "experiment_id": None, "playbook_applied": None}


async def _cached(cache: Optional[Dict[Any, Any]], key: Any, load: Any) -> Any:
    if cache is not None and key in cache:
        return cache[key]
    value = await load()
    if cache is not None:
        cache[key] = value
    return value


def consent_ok(campaign: Mapping[str, Any]) -> bool:
    """The person who signed this campaign's legal review also allowed learning, under today's policy."""
    from core.outbound.playbook import LEARNING_POLICY_VERSION

    consent = (campaign.get("legal_review") or {}).get("learning") or {}
    return bool(consent.get("allowed")) and consent.get("policy") == LEARNING_POLICY_VERSION


# ── At dial time ─────────────────────────────────────────────────────────────

async def _talked_before(db: Any, lead_id: str, offer_id: str, before: datetime) -> bool:
    return bool(await db[m.ATTEMPTS].count_documents(
        {"lead_id": lead_id, "offer_id": offer_id, "status": "done", "created_at": {"$lt": before},
         "outcome": {"$in": sorted(m.HUMAN_OUTCOMES - {"wrong_number"})}}, limit=1))


async def _enrol(db: Any, lead: Mapping[str, Any], exp: Mapping[str, Any], offer_id: str, now: datetime) -> Optional[str]:
    """The arm of this business in this test, enrolling it at its first dial. None = not in the test
    (it was already talking to us about this offer when the test began)."""
    from core.outbound import experiments as ex

    mine = lead.get("assign") or {}
    if mine.get("experiment_id") == exp["_id"]:
        return mine.get("arm") if mine.get("enrolled") else None
    started = _aware(exp.get("started_at")) or now
    if await _talked_before(db, str(lead["_id"]), offer_id, started):
        arm, enrolled = "champion", False
    else:
        arm, enrolled = ex.assign_arm(str(exp["_id"]), str(lead["_id"]), float(exp.get("share") or 0.33)), True
    await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"assign": {
        "experiment_id": exp["_id"], "arm": arm, "enrolled": enrolled, "at": now}}})
    return arm if enrolled else None


async def assign(db: Any, campaign: Mapping[str, Any], lead: Mapping[str, Any], *, offer_id: str,
                 offer_version: Optional[int], variant_id: str, now: datetime,
                 cache: Optional[Dict[Any, Any]] = None) -> Dict[str, Any]:
    """Which playbook a call uses: {"playbook_id", "arm" (champion | challenger | off), "experiment_id",
    "playbook_applied"}. "off" = this campaign doesn't use playbooks. Never raises: any problem means
    the reviewed baseline, never a blocked dial."""
    from core.outbound import playbook as pb

    try:
        if not settings.SALES_PLAYBOOKS_ENABLED or not campaign.get("offer_id") or campaign.get("mode") == "human_assisted":
            return dict(OFF)
        if not consent_ok(campaign) or not offer_version:
            return dict(OFF)
        doc = await _cached(cache, ("learning", offer_id), lambda: pb.learning_doc(db, offer_id))
        if doc.get("mode") == "off":
            return dict(OFF)
        snap = await _cached(cache, ("version", offer_id, offer_version),
                             lambda: pb.version_doc(db, offer_id, int(offer_version)))
        if not snap:
            return dict(OFF)

        async def usable(pid: Optional[str]) -> bool:
            return bool(pid) and await pb.eligible(db, pid, version_checksum=snap["checksum"], variant_id=variant_id,
                                                   cache=cache)

        exp = None
        if doc.get("experiment_id"):
            exp = await _cached(cache, ("experiment", doc["experiment_id"]),
                                lambda: db[EXPERIMENTS].find_one({"_id": doc["experiment_id"], "status": "running"}))
        if exp:
            arm = await _enrol(db, lead, exp, offer_id, now)
            if arm is not None:
                pid = exp.get("challenger_id") if arm == "challenger" else exp.get("champion_id")
                ok = await usable(pid) if pid else True
                return {"playbook_id": pid if ok else None, "arm": arm, "experiment_id": exp["_id"],
                        # the arm's playbook couldn't be used on this version: the call isn't counted
                        "playbook_applied": None if ok else False}
        champion = doc.get("live_playbook_id")
        out: Dict[str, Any] = {"playbook_id": None, "arm": "champion", "experiment_id": None, "playbook_applied": None}
        if champion:
            if await usable(champion):
                out["playbook_id"] = champion
            else:
                out["playbook_applied"] = False    # it hasn't passed its tests for this version yet: the baseline runs
        return out
    except Exception:
        logger.warning("Playbook assignment failed for lead %s; using the baseline", lead.get("_id"), exc_info=True)
        return dict(OFF)


# ── Live tests ───────────────────────────────────────────────────────────────

async def start_experiment(db: Any, offer_id: str, challenger: Mapping[str, Any], *, now: datetime,
                           by: str = ACTOR) -> Optional[Dict[str, Any]]:
    """Try a playbook that passed its test calls on a share of live calls, against the current one."""
    from core.outbound import playbook as pb

    state = await pb.learning_doc(db, offer_id)
    exp = {"_id": f"exp_{uuid.uuid4().hex[:12]}", "offer_id": offer_id, "champion_id": state.get("live_playbook_id"),
           "challenger_id": challenger["_id"], "share": float(state.get("share") or settings.SALES_EXPERIMENT_SHARE),
           "status": "running", "started_at": now, "ended_at": None, "looks_done": [], "last_eval": None,
           "decision": None, "by": by}
    claimed = await db[pb.LEARNING].update_one({"_id": offer_id, "experiment_id": None},
                                               {"$set": {"experiment_id": exp["_id"], "updated_at": now}})
    if not claimed.modified_count:
        return None                                       # another test is running
    await db[EXPERIMENTS].insert_one(exp)
    await db[pb.PLAYBOOKS].update_one({"_id": challenger["_id"]}, {"$set": {"status": "testing", "experiment_id": exp["_id"]}})
    await _audit(db, "sales.experiment.start", "playbook", challenger["_id"],
                 {"experiment_id": exp["_id"], "champion_id": exp["champion_id"], "share": exp["share"]})
    return exp


async def _coverage(db: Any, attempts: List[Mapping[str, Any]], now: datetime) -> float:
    """Share of the challenger's conversations (older than CHECK_GRACE) that were checked: the fixed-rule
    scan, and the coach's review when the coach is on."""
    from core.outbound import learning_stats as ls

    due = [a for a in attempts if a.get("arm") == "challenger" and ls.is_reached(a)
           and (_aware(a.get("ended_at")) or now) <= now - CHECK_GRACE]
    if not due:
        return 1.0
    checked = 0
    for a in due:
        scanned = a.get("checks") is not None
        reviewed = (a.get("review") or {}).get("status") in ("done", "skipped")
        checked += int(scanned and (reviewed or not settings.SALES_COACH_ENABLED))
    return checked / len(due)


def _latency_gap(attempts: List[Mapping[str, Any]]) -> Optional[float]:
    """Mean p95 reply time on the challenger's reviewed calls minus the champion's (10 calls each at least)."""
    by: Dict[str, List[float]] = {"challenger": [], "champion": []}
    for a in attempts:
        v = ((a.get("review") or {}).get("metrics") or {}).get("latency_p95_s")
        if isinstance(v, (int, float)) and a.get("arm") in by:
            by[str(a["arm"])].append(float(v))
    if min(len(by["challenger"]), len(by["champion"])) < 10:
        return None
    return sum(by["challenger"]) / len(by["challenger"]) - sum(by["champion"]) / len(by["champion"])


async def evaluate(db: Any, exp: Mapping[str, Any], *, now: datetime) -> Any:
    from core.outbound import experiments as ex
    from core.outbound import learning_stats as ls

    attempts = await db[m.ATTEMPTS].find({"experiment_id": exp["_id"], "status": "done"},
                                         {"transcript": 0, "gate": 0}).limit(MAX_TEST_ATTEMPTS).to_list(MAX_TEST_ATTEMPTS)
    outcomes = ls.lead_outcomes(attempts, now=now, only_applied=True)
    stats = ls.arm_stats(outcomes, key=lambda o: o.arm if o.arm in ("champion", "challenger") else None)
    ch, cp = stats.get("challenger", ls.ArmStats()), stats.get("champion", ls.ArmStats())
    started = _aware(exp.get("started_at")) or now
    campaigns = await db[m.CAMPAIGNS].find({"offer_id": exp["offer_id"]}, {"paused_reason": 1, "guardrails": 1}).to_list(200)
    paused_for_optouts = any((c.get("paused_reason") or {}).get("code") == "opt_outs"
                             and (_aware((c.get("paused_reason") or {}).get("at")) or started) >= started for c in campaigns)
    max_optout = min([float((c.get("guardrails") or {}).get("max_optout_rate") or 0.05) for c in campaigns] or [0.05])
    coverage = await _coverage(db, attempts, now)
    decision = ex.decide(ch, cp, looks_done=list(exp.get("looks_done") or []), started_at=started, now=now,
                         coverage=coverage, latency_gap_s=_latency_gap(attempts), campaign_optout_paused=paused_for_optouts,
                         max_days=settings.SALES_EXPERIMENT_MAX_DAYS, max_optout_rate=max_optout,
                         paused=exp.get("status") == "paused_traffic")
    decision.snapshot["coverage"] = round(coverage, 3)
    return decision


_FINAL = {"promote": "promoted", "rollback": "rolled_back", "stop": "inconclusive"}


async def apply_decision(db: Any, exp: Mapping[str, Any], decision: Any, *, now: datetime) -> bool:
    """Carry out a decision exactly once. True when the test ended."""
    from core.outbound.experiments import LOOKS

    last_eval = {"at": now, "action": decision.action, "reason": decision.reason, **decision.snapshot}
    if decision.action in ("continue", "pause_traffic", "resume_traffic"):
        sets: Dict[str, Any] = {"last_eval": last_eval}
        if decision.look:
            sets["looks_done"] = sorted(set(list(exp.get("looks_done") or []) + [x for x in LOOKS if x <= decision.look]))
        if decision.action == "pause_traffic":
            sets["status"] = "paused_traffic"
        elif decision.action == "resume_traffic":
            sets["status"] = "running"
        await db[EXPERIMENTS].update_one({"_id": exp["_id"], "status": {"$in": list(ACTIVE)}}, {"$set": sets})
        if decision.action != "continue":
            await _audit(db, f"sales.experiment.{decision.action}", "playbook", str(exp["challenger_id"]),
                         {"experiment_id": exp["_id"], "reason": decision.reason})
        return False
    status = _FINAL[decision.action]
    done = await db[EXPERIMENTS].update_one(
        {"_id": exp["_id"], "status": {"$in": list(ACTIVE)}},
        {"$set": {"status": status, "ended_at": now, "last_eval": last_eval,
                  "decision": {"action": decision.action, "reason": decision.reason, "look": decision.look, "at": now}}})
    if not done.modified_count:
        return False                                      # another worker already decided
    await _finish(db, {**exp, "status": status}, now=now, reason=decision.reason)
    await _audit(db, f"sales.experiment.{decision.action}", "playbook", str(exp["challenger_id"]),
                 {"experiment_id": exp["_id"], "reason": decision.reason, "look": decision.look,
                  "p_better": decision.snapshot.get("p_better")})
    if decision.action == "rollback":
        await _alert_rollback(db, exp, decision.reason)
    return True


async def _finish(db: Any, exp: Mapping[str, Any], *, now: datetime, reason: str) -> None:
    """The pointer changes for a test that ended (idempotent, so a crash halfway is finished next tick)."""
    from core.outbound import playbook as pb

    offer_id, challenger = exp["offer_id"], exp["challenger_id"]
    if exp["status"] == "promoted":
        await pb.make_live(db, offer_id, challenger, now=now, reason=reason)
    else:
        outcome = "rolled_back" if exp["status"] == "rolled_back" else "retired"
        await db[pb.PLAYBOOKS].update_one({"_id": challenger, "status": "testing"},
                                          {"$set": {"status": outcome, "decision": {"action": outcome, "reason": reason[:300],
                                                                                    "at": now}}})
    sets: Dict[str, Any] = {"experiment_id": None, "updated_at": now}
    if exp["status"] == "rolled_back":
        sets["last_rollback_at"] = now
    await db[pb.LEARNING].update_one({"_id": offer_id, "experiment_id": exp["_id"]}, {"$set": sets})


async def _alert_rollback(db: Any, exp: Mapping[str, Any], reason: str) -> None:
    from core.outbound import offers as of
    from core.outbound.guardrails import alert

    offer = await db[of.OFFERS].find_one({"_id": exp["offer_id"]}, {"name": 1}) or {}
    name = offer.get("name") or exp["offer_id"]
    await alert(f"Sales playbook rolled back: {name}",
                f"The new playbook being tried on calls for \"{name}\" was rolled back on its own.\n\n{reason}\n\n"
                "Calls use the previous playbook again. See Sales dialer → Offers → Learning for the numbers.")


async def stop_experiment(db: Any, offer_id: str, *, reason: str, by: str, now: Optional[datetime] = None) -> bool:
    """A person stops the running test: the challenger is set aside, the current playbook stays."""
    from core.outbound import playbook as pb

    now = now or utcnow()
    state = await pb.learning_doc(db, offer_id)
    exp = await db[EXPERIMENTS].find_one({"_id": state.get("experiment_id")}) if state.get("experiment_id") else None
    if not exp:
        return False
    done = await db[EXPERIMENTS].update_one({"_id": exp["_id"], "status": {"$in": list(ACTIVE)}}, {"$set": {
        "status": "stopped", "ended_at": now, "decision": {"action": "stop", "reason": reason, "by": by, "at": now}}})
    if done.modified_count:
        await _finish(db, {**exp, "status": "stopped"}, now=now, reason=reason)
    return bool(done.modified_count)


async def reconcile(db: Any, *, now: datetime) -> int:
    """Finish pointer changes a crash left half-done: an offer still pointing at a test that ended."""
    from core.outbound import playbook as pb

    fixed = 0
    for state in await db[pb.LEARNING].find({"experiment_id": {"$ne": None}}).to_list(200):
        exp = await db[EXPERIMENTS].find_one({"_id": state["experiment_id"]})
        if exp is None:
            await db[pb.LEARNING].update_one({"_id": state["_id"]}, {"$set": {"experiment_id": None}})
            fixed += 1
        elif exp["status"] not in ACTIVE:
            await _finish(db, exp, now=now, reason=str((exp.get("decision") or {}).get("reason") or ""))
            fixed += 1
    return fixed


async def experiments_tick(db: Any, *, now: datetime) -> Dict[str, int]:
    """Start the next test where a challenger is ready, and judge the running ones."""
    from core.outbound import playbook as pb

    stats = {"started": 0, "decided": 0, "reconciled": await reconcile(db, now=now)}
    for ready in await db[pb.PLAYBOOKS].find({"status": "ready"}).sort("created_at", 1).to_list(50):
        state = await pb.learning_doc(db, ready["offer_id"])
        if state.get("mode") != "autopilot" or state.get("experiment_id") or not settings.SALES_PLAYBOOKS_ENABLED:
            continue
        if await start_experiment(db, ready["offer_id"], ready, now=now):
            stats["started"] += 1
    for exp in await db[EXPERIMENTS].find({"status": {"$in": list(ACTIVE)}}).to_list(50):
        try:
            decision = await evaluate(db, exp, now=now)
            if await apply_decision(db, exp, decision, now=now):
                stats["decided"] += 1
        except Exception:
            logger.error("Judging playbook test %s failed", exp.get("_id"), exc_info=True)
    return stats


async def learning_curve(db: Any, offer_id: str, *, now: Optional[datetime] = None, weeks: int = 12) -> Dict[str, Any]:
    """Week by week, for the offer's AI calls: businesses reached, the share that took a real next step,
    and the share that booked; with the dates playbooks went live or were rolled back."""
    from core.outbound import learning_stats as ls
    from core.outbound import playbook as pb

    now = now or utcnow()
    since = now - timedelta(weeks=weeks)
    rows = await db[m.ATTEMPTS].find({"offer_id": offer_id, "status": "done", "created_at": {"$gte": since},
                                      "mode": {"$ne": "human_assisted"}},
                                     {"transcript": 0, "gate": 0}).limit(50_000).to_list(50_000)
    by_week: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        at = _aware(r.get("created_at")) or now
        monday = (at - timedelta(days=at.weekday())).strftime("%Y-%m-%d")
        by_week.setdefault(monday, []).append(r)
    points = []
    for week in sorted(by_week):
        s = ls.arm_stats(ls.lead_outcomes(by_week[week], now=now), key=lambda o: "all").get("all", ls.ArmStats())
        points.append({"week": week, "reached": s.leads, "next_step_rate": s.rate(s.progress),
                       "meeting_rate": s.rate(s.meetings, s.leads)})
    marks = []
    for doc in await db[pb.PLAYBOOKS].find({"offer_id": offer_id}, {"n": 1, "status": 1, "live_since": 1, "retired_at": 1,
                                                                    "decision": 1}).to_list(100):
        live_since = _aware(doc.get("live_since"))
        if live_since and live_since >= since:
            marks.append({"at": doc["live_since"], "kind": "live", "playbook": doc["n"]})
        if doc.get("status") == "rolled_back" and (doc.get("decision") or {}).get("at"):
            marks.append({"at": doc["decision"]["at"], "kind": "rolled_back", "playbook": doc["n"]})
    return {"points": points, "marks": sorted(marks, key=lambda x: _aware(x["at"]) or now)}


# ── The tick ─────────────────────────────────────────────────────────────────

async def tick(db: Any, *, now: Optional[datetime] = None, llm: Any = None) -> Dict[str, int]:
    from core.outbound import coach, playbook

    now = now or utcnow()
    stats: Dict[str, int] = {}
    try:
        stats.update({f"coach_{k}": v for k, v in (await coach.tick(db, now=now, llm=llm)).items()})
    except Exception:
        logger.error("Sales coach tick failed", exc_info=True)
    try:
        stats.update({f"playbook_{k}": v for k, v in (await playbook.advance(db, now=now)).items()})
    except Exception:
        logger.error("Moving playbooks on after their test calls failed", exc_info=True)
    try:
        from core.outbound import learn

        stats.update({f"learn_{k}": v for k, v in (await learn.tick(db, now=now, llm=llm)).items()})
    except Exception:
        logger.error("Writing the next playbook failed", exc_info=True)
    try:
        stats.update({f"test_{k}": v for k, v in (await experiments_tick(db, now=now)).items()})
    except Exception:
        logger.error("Playbook tests tick failed", exc_info=True)
    try:
        if await _due(db, "timing:refresh", timedelta(hours=1), now):
            from core.outbound import timing

            stats["timing_countries"] = await timing.refresh(db, now=now)
    except Exception:
        logger.error("Refreshing pick-up rates failed", exc_info=True)
    try:
        if await _due(db, "coach:purge", PURGE_EVERY, now):
            stats["coach_notes_cleared"] = await coach.purge_text(db, now=now)
    except Exception:
        logger.error("Clearing old coach notes failed", exc_info=True)
    return stats


class LearningLoop:
    """Runs `tick` inside the API process when there is no Redis. Separate from the dialer loop,
    which already runs long simulations inline: reviews never wait behind them."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if not settings.OUTBOUND_DIALER_ENABLED or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._loop(), name="sales-learning")
        logger.info("Sales learning loop started in this process (every %ss)", TICK_SECONDS)

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
                logger.error("Sales learning tick failed", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)


learning_loop = LearningLoop()
