"""
Replay a playbook test's decisions day by day over the real calls (read-only), to see how the planned
checks (core/outbound/experiments.py) would have judged it on your own numbers.

    uv run python -m scripts.learning_replay --offer off_weblaud_studio            # its latest test
    uv run python -m scripts.learning_replay --offer off_weblaud_studio --test exp_ab12cd34ef56

Nothing is written.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from config import settings
from core.outbound import experiments as ex
from core.outbound import learning_stats as ls
from core.outbound import models as m


def _aware(dt: Any) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def replay(db: Any, offer_id: str, test_id: Optional[str] = None) -> List[str]:
    query = {"_id": test_id} if test_id else {"offer_id": offer_id}
    tests = await db["sales_experiments"].find(query).sort("started_at", -1).limit(1).to_list(1)
    if not tests:
        return [f"No playbook test found for {offer_id}."]
    exp = tests[0]
    rows = await db[m.ATTEMPTS].find({"experiment_id": exp["_id"], "status": "done"}, {"transcript": 0, "gate": 0}) \
        .limit(20_000).to_list(20_000)
    started = _aware(exp["started_at"])
    end = _aware(exp.get("ended_at") or datetime.now(timezone.utc))
    out = [f"Test {exp['_id']}: {exp['status']} (started {started:%Y-%m-%d}, {len(rows)} calls)"]
    looks: List[int] = []
    day = started
    while day <= end + timedelta(days=1):
        upto = [r for r in rows if _aware(r["created_at"]) < day]
        stats = ls.arm_stats(ls.lead_outcomes(upto, now=day, only_applied=True),
                             key=lambda o: o.arm if o.arm in ("champion", "challenger") else None)
        ch, cp = stats.get("challenger", ls.ArmStats()), stats.get("champion", ls.ArmStats())
        d = ex.decide(ch, cp, looks_done=looks, started_at=started, now=day, max_days=settings.SALES_EXPERIMENT_MAX_DAYS)
        out.append(f"{day:%Y-%m-%d}  new {ch.progress}/{ch.decided}  current {cp.progress}/{cp.decided}  "
                   f"P(better)={d.snapshot.get('p_better')}  → {d.action}: {d.reason}")
        if d.look:
            looks = sorted(set(looks + [x for x in ex.LOOKS if x <= d.look]))
        if d.action in ("promote", "rollback", "stop"):
            break
        day += timedelta(days=1)
    return out


async def main() -> None:
    from core.log import configure_logging
    from database.mongodb import connect_db, disconnect_db, get_db

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offer", required=True)
    parser.add_argument("--test", default=None, help="a test id (default: the offer's latest)")
    args = parser.parse_args()
    configure_logging("WARNING", "text")
    await connect_db(ensure_indexes=False)
    try:
        for line in await replay(get_db(), args.offer, args.test):
            print(line)
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
