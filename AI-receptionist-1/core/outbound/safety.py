"""
The sales safety tick (SALES_SUITE_PLAN.md wave 2), run by the jobs worker every minute and by the
API process when there is no Redis: queued simulated test calls, auto-pause guardrails, and caller
ID health (at most once an hour). Each part is independent; one failing never stops the others.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

META = "sales_safety_meta"
NUMBER_HEALTH_EVERY = timedelta(hours=1)


async def tick(db: Any, *, now: Optional[datetime] = None) -> Dict[str, int]:
    from core.outbound import guardrails, number_health, simulate

    now = now or datetime.now(timezone.utc)
    stats: Dict[str, int] = {}
    try:
        stats.update({f"guardrails_{k}": v for k, v in (await guardrails.check_all(db, now=now)).items()})
    except Exception:
        logger.error("Guardrails check failed", exc_info=True)
    try:
        last = await db[META].find_one({"_id": "number_health"})
        at = (last or {}).get("at")
        if isinstance(at, datetime) and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if not isinstance(at, datetime) or at < now - NUMBER_HEALTH_EVERY:
            await db[META].update_one({"_id": "number_health"}, {"$set": {"at": now}}, upsert=True)
            stats.update({f"numbers_{k}": v for k, v in (await number_health.auto_rest(db, now=now)).items()})
    except Exception:
        logger.error("Caller ID health check failed", exc_info=True)
    try:
        from core.outbound import reports

        stats.update({f"digest_{k}": v for k, v in (await reports.send_digests(db, now=now)).items()})
    except Exception:
        logger.error("Sales digest failed", exc_info=True)
    try:
        stats.update({f"simulations_{k}": v for k, v in (await simulate.tick(db, now=now)).items()})
    except Exception:
        logger.error("Simulated test calls failed", exc_info=True)
    return {k: v for k, v in stats.items() if v}
