"""
Migration 0008 — the agent that learns from its calls (SALES_LEARNING_PLAN.md). Idempotent.

    python -m migrations.m0008_sales_learning            # dry run: what would change
    python -m migrations.m0008_sales_learning --apply    # deploy/deploy.sh runs this on every deploy

- Gives the last 90 days of calls the lead's local weekday and hour at dial time (`local`) and the
  country, so "when businesses pick up" has history from day one (core/outbound/timing.py).
- Creates each offer's learning state (core/outbound/playbook.learning_doc): autopilot, no playbook
  yet. Nothing is used on a call until a campaign's legal sign-off allows learning.
Nothing is deleted or overwritten: calls that already have `local` and offers that already have a
state are left as they are. (The indexes come with migrations/m0002_indexes.)
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from core.log import configure_logging
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound import playbook as pb
from core.outbound import timing
from database.mongodb import connect_db, disconnect_db, get_db

BACKFILL_DAYS = 90


async def run(db: Any, *, apply: bool, now: datetime | None = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = {"calls_timed": 0, "calls_without_zone": 0, "learning_states": 0}
    leads: Dict[str, Dict[str, Any]] = {}
    query = {"created_at": {"$gte": now - timedelta(days=BACKFILL_DAYS)}, "local": {"$exists": False}}
    async for a in db[m.ATTEMPTS].find(query, {"lead_id": 1, "created_at": 1}):
        lid = str(a.get("lead_id") or "")
        if lid not in leads:
            leads[lid] = await db[m.LEADS].find_one({"_id": lid}, {"timezone": 1, "timezones": 1, "country": 1}) or {}
        lead = leads[lid]
        at = a.get("created_at")
        if isinstance(at, datetime) and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        slot = timing.local_slot(lead, at) if isinstance(at, datetime) else None
        stats["calls_timed" if slot else "calls_without_zone"] += 1
        if apply:
            await db[m.ATTEMPTS].update_one({"_id": a["_id"], "local": {"$exists": False}},
                                            {"$set": {"local": slot, "country": str(lead.get("country") or "")}})
    async for offer in db[of.OFFERS].find({}, {"_id": 1}):
        if await db[pb.LEARNING].find_one({"_id": offer["_id"]}, {"_id": 1}):
            continue
        stats["learning_states"] += 1
        if apply:
            await pb.learning_doc(db, str(offer["_id"]))
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        stats = await run(get_db(), apply=args.apply)
    finally:
        await disconnect_db()
    print(("APPLIED: " if args.apply else "DRY RUN: ") + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    asyncio.run(main())
