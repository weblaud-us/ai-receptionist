"""
Demo data for the learning loop (SALES_LEARNING_PLAN.md), so the Learning tab, the coach cards and the
pick-up heatmap can be seen working without weeks of real calls.

    uv run python -m scripts.seed_learning_demo                       # dry run: what it would create
    uv run python -m scripts.seed_learning_demo --apply               # 45 days of calls, three playbooks
    uv run python -m scripts.seed_learning_demo --apply --fast-forward 30   # then watch the test decide
    uv run python -m scripts.seed_learning_demo --remove              # delete everything it made

Safe by construction: every document carries `demo: true`; leads belong to owner "demo" (the dialer
only queues owner "platform") with fictional numbers (+61 2 5550 xxxx, reserved for drama) and final
statuses; the campaign is paused. No model is called: reviews come from a fixed template.

Seeds offer `off_weblaud_studio` (run migrations m0006 first): playbook v1 live, v2 rolled back with
its reason, v3 being tested against v1 on a third of calls, where v3 is a little better.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from core.outbound import experiments as ex
from core.outbound import models as m
from core.outbound import playbook as pb
from core.outbound import timing
from core.outbound.offer_seeds import WEBLAUD_ID

CAMPAIGN_ID = "cmp_demo_learning"
EXPERIMENT_ID = "exp_demo_learning"
TZ = "Australia/Sydney"
GOOD_HOURS = {10, 11, 14, 15}
COLLECTIONS = (m.LEADS, m.ATTEMPTS, m.CAMPAIGNS, "calls", pb.PLAYBOOKS, "sales_experiments")

ENTRIES = [
    pb.Entry(id="busy_desk", kind="objection", when="they say they already have someone answering the phones",
             do="ask what happens to calls when that person is busy, then listen", say="What happens when she's with someone?"),
    pb.Entry(id="one_thing", kind="tone", when="they sound rushed", do="keep turns short and ask one thing at a time"),
]
CHALLENGER = ENTRIES + [pb.Entry(id="chore_first", kind="discovery", when="they mention a chore that eats time",
                                 do="ask how many hours a week it takes before saying anything about the offer",
                                 say="Roughly how long does that take each week?")]
LESSONS = [{"situation": "They say they already have a receptionist", "do": "Ask what happens when she is busy",
            "why": "It surfaces the gap", "kind": "objection", "objection_key": "already_have_receptionist"},
           {"situation": "They mention a time-consuming task", "do": "Ask how long it takes before pitching",
            "why": "Sizes the pain", "kind": "discovery", "objection_key": ""}]


def _call(rng: random.Random, i: int, at: datetime, *, arm: str, p_progress: float, p_answer: float) -> Dict[str, Any]:
    lead_id = f"lead_demo_{i}"
    answered = rng.random() < p_answer
    progress = answered and rng.random() < p_progress
    meeting = progress and rng.random() < 0.45
    outcome = ("demo_booked" if meeting else "interested" if progress else
               "not_interested" if answered and rng.random() < 0.6 else "no_conversation" if answered else "no_answer")
    score = rng.randint(55, 90) if progress else rng.randint(30, 70)
    review = {"status": "done", "at": at + timedelta(minutes=3), "rubric_version": "demo", "model": "demo",
              "overall": score, "scores": {s: max(0, min(5, round(score / 20 + rng.uniform(-1, 1)))) for s in
                                           ("bridge", "discovery", "listening", "value", "objections", "close", "tone")},
              "lessons": [rng.choice(LESSONS)], "moments": [], "flags": [], "entry_usage": [],
              "unanswered": ["Does it work with Dentally?"] if rng.random() < 0.1 else [],
              "metrics": {"talk_ratio": round(rng.uniform(0.35, 0.6), 2), "latency_p95_s": round(rng.uniform(0.9, 1.4), 2)}}
    return {
        "_id": f"att_demo_{i}", "call_id": f"out-att_demo_{i}", "lead_id": lead_id, "campaign_id": CAMPAIGN_ID,
        "mode": "ai_consented", "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "ai_automation",
        "persona_id": "ai_automation", "status": "done", "outcome": outcome, "amd": "human" if answered else None,
        "attempt_no": 1, "created_at": at, "ended_at": at + timedelta(minutes=2), "duration_sec": 95 if answered else None,
        "events": (["meeting"] if meeting else ["info_email"] if progress else []), "arm": arm,
        "experiment_id": EXPERIMENT_ID if arm in ("champion", "challenger") else None,
        "playbook_id": f"{WEBLAUD_ID}@pb3" if arm == "challenger" else f"{WEBLAUD_ID}@pb1", "playbook_applied": True,
        "country": "AU", "local": timing.local_slot({"timezone": TZ}, at), "dialled_as": "queued",
        "checks": {"severe": [], "other": [], "details": [], "long_turns": 0} if answered else None,
        "review": review if answered else None, "summary": "Demo call." if answered else None, "demo": True,
    }


def _transcript(a: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"role": "assistant", "text": "Hi, this is Max, an AI assistant from WebLaud about your practice's calls. Is now a bad time?"},
            {"role": "user", "text": "Go on, quickly."},
            {"role": "assistant", "text": "What takes your front desk the most time each week?"},
            {"role": "user", "text": "Recalls, mostly." if a["outcome"] != "not_interested" else "We're fine, thanks."}]


def plan(now: datetime, *, days: int, seed: int, rates: Sequence[float], start: int = 0) -> List[Dict[str, Any]]:
    """The demo calls: about 30 a day, a third on the challenger once the test starts (day 15)."""
    rng = random.Random(seed + start)
    calls: List[Dict[str, Any]] = []
    test_from = now - timedelta(days=max(1, days - 15))
    i = start
    for d in range(days, 0, -1):
        day = (now - timedelta(days=d)).astimezone(timezone.utc)
        local = day.astimezone(timing.ZoneInfo(TZ))
        if local.isoweekday() > 5:
            continue
        for _ in range(30):
            hour = rng.randint(9, 16)
            at = local.replace(hour=hour, minute=rng.randint(0, 59)).astimezone(timezone.utc)
            arm = "off"
            if at >= test_from:
                arm = ex.assign_arm(EXPERIMENT_ID, f"lead_demo_{i}", 0.33)
            p_answer = 0.45 if hour in GOOD_HOURS else 0.2
            calls.append(_call(rng, i, at, arm=arm, p_progress=rates[1] if arm == "challenger" else rates[0], p_answer=p_answer))
            i += 1
    return calls


async def remove(db: Any) -> Dict[str, int]:
    """Delete only what this script made (a script, not an API: the platform itself never deletes)."""
    out = {}
    for c in COLLECTIONS:
        res = await db[c].delete_many({"demo": True})
        out[c] = res.deleted_count
    await db[pb.LEARNING].update_one({"_id": WEBLAUD_ID, "demo": True},
                                     {"$set": {"live_playbook_id": None, "experiment_id": None}, "$unset": {"demo": ""}})
    return out


async def seed(db: Any, *, now: Optional[datetime] = None, days: int = 45, seed: int = 7,
               rates: Sequence[float] = (0.18, 0.27), apply: bool = True) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    calls = plan(now, days=days, seed=seed, rates=rates)
    stats = {"calls": len(calls), "leads": len({c["lead_id"] for c in calls}), "playbooks": 3}
    if not apply:
        return stats
    await remove(db)
    started = now - timedelta(days=max(1, days - 15))
    await db[m.CAMPAIGNS].insert_one({
        "_id": CAMPAIGN_ID, "name": "Demo: learning loop (never dials)", "mode": "ai_consented", "status": "paused",
        "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_ids": ["ai_automation"], "persona_ids": ["sales_v1"],
        "from_numbers": [], "calling_window": {"start": "09:30", "end": "17:00", "days": [1, 2, 3, 4, 5]},
        "retry_timing": "smart", "script_split": "even", "created_at": now - timedelta(days=days), "demo": True,
        "legal_review": {"confirmed_by": "demo", "at": now - timedelta(days=days), "note": "demo data", "material": None,
                         "learning": {"allowed": True, "policy": pb.LEARNING_POLICY_VERSION, "by": "demo", "at": now}}})
    leads = [{"_id": c["lead_id"], "owner": "demo", "phone": f"+6125550{int(c['lead_id'].rsplit('_', 1)[1]):04d}",
              "business_name": f"Demo Dental {c['lead_id'].rsplit('_', 1)[1]}", "country": "AU", "timezone": TZ,
              "timezones": [TZ], "status": "not_interested" if c["outcome"] == "not_interested" else "exhausted",
              "campaign_id": CAMPAIGN_ID, "demo": True, "created_at": c["created_at"]} for c in calls]
    await db[m.LEADS].insert_many(leads)
    await db[m.ATTEMPTS].insert_many(calls)
    await db.calls.insert_many([{"call_id": c["call_id"], "transcript": _transcript(c), "demo": True}
                                for c in calls if c["amd"] == "human"])
    learning_state = await pb.learning_doc(db, WEBLAUD_ID)
    base = {"offer_id": WEBLAUD_ID, "origin": "manual", "created_by": "demo", "targets": [], "demo": True, "go_live": False,
            "changes": [], "test_retries": 0}
    v1 = {**base, "_id": f"{WEBLAUD_ID}@pb1", "n": 1, "status": "live", "entries": [e.model_dump() for e in ENTRIES],
          "checksum": pb.checksum(ENTRIES), "parent": None, "created_at": now - timedelta(days=days), "why": "First lessons",
          "live_since": now - timedelta(days=days - 1), "decision": None}
    pushy = ENTRIES + [pb.Entry(id="push_close", kind="close", when="they hesitate", do="ask for the meeting again straight away")]
    v2 = {**base, "_id": f"{WEBLAUD_ID}@pb2", "n": 2, "status": "rolled_back", "entries": [e.model_dump() for e in pushy],
          "checksum": pb.checksum(pushy), "parent": v1["_id"], "origin": "learned", "created_at": now - timedelta(days=days - 5),
          "why": "Ask again when they hesitate",
          "decision": {"action": "rolled_back", "reason": "More people asked not to be called with the new playbook (6 of 48).",
                       "at": now - timedelta(days=days - 12)}}
    v3 = {**base, "_id": f"{WEBLAUD_ID}@pb3", "n": 3, "status": "testing", "entries": [e.model_dump() for e in CHALLENGER],
          "checksum": pb.checksum(CHALLENGER), "parent": v1["_id"], "origin": "learned", "created_at": started - timedelta(days=1),
          "why": "Size the problem before pitching", "experiment_id": EXPERIMENT_ID, "decision": None,
          "changes": [{"op": "add", "entry_id": "chore_first", "why": "Came up in 23 reviewed calls", "evidence": []}]}
    for doc in (v1, v2, v3):
        await db[pb.PLAYBOOKS].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    await db["sales_experiments"].replace_one({"_id": EXPERIMENT_ID}, {
        "_id": EXPERIMENT_ID, "offer_id": WEBLAUD_ID, "champion_id": v1["_id"], "challenger_id": v3["_id"], "share": 0.33,
        "status": "running", "started_at": started, "ended_at": None, "looks_done": [], "last_eval": None, "decision": None,
        "by": "demo", "demo": True}, upsert=True)
    await db[pb.LEARNING].update_one({"_id": WEBLAUD_ID}, {"$set": {
        "live_playbook_id": v1["_id"], "experiment_id": EXPERIMENT_ID, "mode": learning_state.get("mode") or "autopilot",
        "demo": True}})
    await timing.refresh(db, now=now)
    return stats


async def fast_forward(db: Any, *, now: datetime, days: int, seed: int, rates: Sequence[float]) -> List[str]:
    """Add a day of demo calls at a time and let the learning tick judge the test, as it would live."""
    from core.outbound import learning

    log = []
    start = await db[m.ATTEMPTS].count_documents({"demo": True})
    for d in range(1, days + 1):
        day = now + timedelta(days=d)
        calls = [c for c in plan(day, days=1, seed=seed, rates=rates, start=start + d * 100) if c["arm"] != "off"]
        for c in calls:
            c["experiment_id"], c["arm"] = EXPERIMENT_ID, ex.assign_arm(EXPERIMENT_ID, c["lead_id"], 0.33)
            c["playbook_id"] = f"{WEBLAUD_ID}@pb3" if c["arm"] == "challenger" else f"{WEBLAUD_ID}@pb1"
        if calls:
            await db[m.ATTEMPTS].insert_many(calls)
        await learning.experiments_tick(db, now=day)
        exp = await db["sales_experiments"].find_one({"_id": EXPERIMENT_ID})
        ev = exp.get("last_eval") or {}
        log.append(f"day {d}: {exp['status']} — {ev.get('reason', '')}")
        if exp["status"] not in ("running", "paused_traffic"):
            break
    return log


async def main() -> None:
    from core.log import configure_logging
    from database.mongodb import connect_db, disconnect_db, get_db

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the demo data (default: dry run)")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rates", default="0.18,0.27", help="next-step rates: current playbook, new playbook")
    parser.add_argument("--fast-forward", type=int, default=0, help="then simulate this many more days of the test")
    parser.add_argument("--remove", action="store_true", help="delete everything this script made")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        db = get_db()
        if args.remove:
            print("REMOVED:", await remove(db))
            return
        rates = [float(x) for x in args.rates.split(",")][:2]
        now = datetime.now(timezone.utc)
        stats = await seed(db, now=now, days=args.days, seed=args.seed, rates=rates, apply=args.apply)
        print(("APPLIED: " if args.apply else "DRY RUN: ") + ", ".join(f"{k}={v}" for k, v in stats.items()))
        if args.apply and args.fast_forward:
            for line in await fast_forward(db, now=now, days=args.fast_forward, seed=args.seed, rates=rates):
                print(line)
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
