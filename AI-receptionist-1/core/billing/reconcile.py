"""
Usage reconciliation (audit BACK-06): every call ends up with exactly one usage event.

A voice worker writes the usage event when the call ends (agent/persistence.CallRecorder). If the
worker process dies first, the call record is there but its minutes were never counted, so the
business isn't billed for them and its cap doesn't see them. The billing tick runs this before it
refreshes usage: calls that are certainly over (older than the call length cap plus a margin) and
have no usage event get one, from the call's own timestamps, capped at the call length limit.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from config import settings
from core.billing.costs import call_cost_usd
from core.billing.usage import aware
from core.repositories import Repos
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(days=3)
MARGIN = timedelta(minutes=15)


async def reconcile_usage(db: Any, tenant: TenantConfig, now: datetime) -> int:
    repos = Repos(db, tenant.id)
    max_s = tenant.agent.max_call_minutes * 60
    settled_before = now - timedelta(seconds=max_s) - MARGIN
    calls = await repos.calls.find({"started_at": {"$gte": now - LOOKBACK, "$lt": settled_before}},
                                   projection={"call_id": 1, "started_at": 1, "ended_at": 1, "updated_at": 1,
                                               "direction": 1, "outcome": 1}, limit=5000)
    if not calls:
        return 0
    have = {u["call_id"] for u in await repos.usage.find(
        {"kind": "voice_minute", "call_id": {"$in": [c["call_id"] for c in calls]}}, projection={"call_id": 1},
        limit=5000)}
    added = 0
    sales = bool(settings.SALES_TENANT_ID and tenant.id == settings.SALES_TENANT_ID)
    for call in calls:
        if call["call_id"] in have:
            continue
        start = aware(call.get("started_at"))
        end = aware(call.get("ended_at")) or aware(call.get("updated_at")) or start
        if start is None or end is None:
            continue
        seconds = min(max_s, max(0, math.ceil((end - start).total_seconds())))
        outbound = call.get("direction") == "outbound"
        await repos.usage.update_one(
            {"call_id": call["call_id"], "kind": "voice_minute"},
            {"$setOnInsert": {"quantity": max(1, math.ceil(seconds / 60)), "seconds": seconds,
                              "direction": call.get("direction") or "inbound",
                              "cost_usd": call_cost_usd(seconds, outbound=outbound),
                              "billable": not sales and call.get("outcome") != "unavailable",
                              "at": end, "invoiced": False, "source": "reconciled"}},
            upsert=True)
        added += 1
    if added:
        logger.warning("Reconciled %d calls with no usage event for %s (a worker likely crashed)", added, tenant.id)
    return added
