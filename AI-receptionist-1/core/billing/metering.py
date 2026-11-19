"""
Overage metering (Roadmap Task 5.3): our `usage_events` → Stripe Billing Meter events.

Once a day (and every billing tick in the last REPORT_NEAR_END before a period closes, so the final
minutes reach that period's invoice) each subscribed tenant's overage for the period so far is
compared with what was already reported, and the difference is sent as one meter event.

Exactly once, even across crashes and retries:
1. The report is written to `billing.overage_pending` *before* it is sent.
2. Its identifier is `tenant_id:period:cumulative_total` — Stripe drops a second event with the
   same identifier, so re-sending a pending report after a crash can never bill twice.
3. Only after Stripe accepts it does the total move to `billing.overage_reported[period]`.
A pending report is always re-sent first, before anything new is computed.

Trial minutes are never billed (the trial is hard-capped instead).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from core.billing import stripe_api
from core.billing.usage import aware, included_minutes, on_trial, period_key, period_window, seconds_between, utcnow
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

REPORT_NEAR_END = timedelta(hours=2)
BILLED_STATUSES = ("active", "past_due")


def overage_minutes(seconds: float, included: int) -> int:
    return max(0, math.ceil((seconds - included * 60) / 60))


def due(tenant: TenantConfig, now: datetime, period_end: datetime) -> bool:
    last = aware(tenant.billing.overage_last_report)
    return last is None or last.date() != now.date() or now >= period_end - REPORT_NEAR_END


async def _send(db: Any, tenant: TenantConfig, pending: Dict[str, Any], now: datetime) -> int:
    ts = aware(pending.get("timestamp")) or now
    await stripe_api.send_meter_event(tenant.billing.stripe_customer_id, int(pending["delta"]),
                                      identifier=str(pending["identifier"]), timestamp=ts)
    await db.tenants.update_one({"_id": tenant.id}, {
        "$set": {f"billing.overage_reported.{pending['period']}": int(pending["total"]),
                 "billing.overage_last_report": now},
        "$unset": {"billing.overage_pending": ""},
    })
    logger.info("Reported %s overage minutes for %s (period %s, total %s)", pending["delta"], tenant.id,
                pending["period"], pending["total"])
    return int(pending["delta"])


async def report_overage(db: Any, tenant: TenantConfig, now: Optional[datetime] = None,
                         seconds: Optional[float] = None) -> int:
    """Send this tenant's unreported overage. Returns the minutes sent (0 = nothing to do)."""
    now = now or utcnow()
    b = tenant.billing
    if not (stripe_api.configured() and b.self_serve and b.stripe_customer_id and b.stripe_subscription_id):
        return 0
    if b.overage_pending:                          # an unconfirmed report: send exactly that again
        return await _send(db, tenant, dict(b.overage_pending), now)
    if tenant.status not in BILLED_STATUSES or on_trial(tenant):
        return 0
    start, end = period_window(tenant, now)
    if not due(tenant, now, end):
        return 0
    if seconds is None:
        seconds = await seconds_between(db, tenant.id, start, end)
    key = period_key(start)
    total = overage_minutes(seconds, included_minutes(tenant))
    delta = total - int(b.overage_reported.get(key, 0))
    if delta <= 0:
        return 0
    pending = {"period": key, "total": total, "delta": delta, "identifier": f"{tenant.id}:{key}:{total}",
               "timestamp": min(now, end - timedelta(seconds=1)), "created_at": now}
    await db.tenants.update_one({"_id": tenant.id}, {"$set": {"billing.overage_pending": pending}})
    return await _send(db, tenant, pending, now)
