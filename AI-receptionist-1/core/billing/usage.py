"""
Usage and the call gate (Roadmap Task 5.3).

Usage comes from our own `usage_events` (one row per call, seconds rounded up to the second), never
from Stripe — the dashboard shows it in near-real time. `tenant.billing.period_seconds` is a cached
copy refreshed at the end of every call, so the voice worker can decide how to answer the next call
without a database round trip.

The call gate, read at call start:

| Gate        | When                                                         | What the caller gets           |
|-------------|--------------------------------------------------------------|--------------------------------|
| ok          | everything else                                              | the receptionist               |
| suspended   | status suspended / cancelled (still inside the grace period) | forwarded to staff, or a       |
| cap         | trial minutes used up, or the owner chose a hard cap at 100 %| neutral "please call back"     |

Never silence: a business that hasn't paid still gets its calls forwarded (Task 5.3 "Suspension").
Default behaviour at 100 % of included minutes is to keep answering and bill overage.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from config import settings
from core.tenancy.models import TenantConfig

Gate = Literal["ok", "suspended", "cap"]
ALERT_PERCENTS = (80, 100)


def aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def naive_utc(dt: datetime) -> datetime:
    """Mongo stores UTC without a zone; compare like with like."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def on_trial(tenant: TenantConfig) -> bool:
    return tenant.status == "trial" and tenant.billing.self_serve


def included_minutes(tenant: TenantConfig) -> int:
    """Minutes included this period. During a self-serve trial: the trial cap."""
    if on_trial(tenant):
        return min(settings.TRIAL_MINUTES, tenant.limits.included_minutes or settings.TRIAL_MINUTES)
    return tenant.limits.included_minutes


def period_window(tenant: TenantConfig, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """The current billing period: Stripe's when there is a subscription, else the calendar month
    in the business's timezone (manual plans), else the trial since sign-up."""
    now = now or utcnow()
    b = tenant.billing
    start, end = aware(b.period_start), aware(b.period_end)
    if start and end and start <= now < end + timedelta(days=2):
        return start, end
    if on_trial(tenant) and aware(b.trial_ends_at):
        trial_end = aware(b.trial_ends_at)
        assert trial_end is not None
        trial_start = trial_end - timedelta(days=settings.TRIAL_DAYS)
        return trial_start, trial_end
    try:
        zone = ZoneInfo(tenant.timezone)
    except Exception:
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    first = datetime.combine(local.date().replace(day=1), time.min, tzinfo=zone)
    nxt = datetime.combine((first + timedelta(days=32)).date().replace(day=1), time.min, tzinfo=zone)
    return first.astimezone(timezone.utc), nxt.astimezone(timezone.utc)


def period_key(start: datetime) -> str:
    return start.astimezone(timezone.utc).strftime("%Y%m%d")


async def seconds_between(db: Any, tenant_id: str, start: datetime, end: Optional[datetime] = None) -> float:
    match: Dict[str, Any] = {"tenant_id": tenant_id, "kind": "voice_minute", "billable": {"$ne": False},
                             "at": {"$gte": naive_utc(start)}}
    if end is not None:
        match["at"]["$lt"] = naive_utc(end)
    total = 0.0
    async for row in db.usage_events.find(match, {"seconds": 1, "quantity": 1}):  # scoped: match has tenant_id
        secs = row.get("seconds")
        total += float(secs) if isinstance(secs, (int, float)) else float(row.get("quantity") or 0) * 60
    return total


async def refresh_period_usage(db: Any, tenant: TenantConfig, now: Optional[datetime] = None) -> float:
    """Recompute this period's billable seconds and cache them on the tenant. Off the hot path."""
    start, end = period_window(tenant, now)
    seconds = await seconds_between(db, tenant.id, start, end)
    await db.tenants.update_one({"_id": tenant.id}, {"$set": {"billing.period_seconds": round(seconds, 1)}})
    return seconds


def summary(tenant: TenantConfig, seconds: float, now: Optional[datetime] = None) -> Dict[str, Any]:
    start, end = period_window(tenant, now)
    included = included_minutes(tenant)
    used_min = seconds / 60
    overage = max(0, math.ceil((seconds - included * 60) / 60)) if included else 0
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "used_minutes": round(used_min, 1), "used_seconds": round(seconds, 1),
        "included_minutes": included,
        "percent": round(used_min / included * 100, 1) if included else None,
        "overage_minutes": 0 if on_trial(tenant) else overage,
        "trial": on_trial(tenant),
        "hard_cap": tenant.billing.hard_cap or on_trial(tenant),
        "capped": gate(tenant, seconds) == "cap",
    }


def gate(tenant: TenantConfig, seconds: Optional[float] = None) -> Gate:
    """How the next call is answered (see the module table)."""
    if tenant.is_legacy:
        return "ok"
    if tenant.status in ("suspended", "cancelled"):
        return "suspended"
    used = tenant.billing.period_seconds if seconds is None else seconds
    if on_trial(tenant) and used >= included_minutes(tenant) * 60:
        return "cap"
    if tenant.billing.hard_cap and tenant.limits.included_minutes and used >= tenant.limits.included_minutes * 60:
        return "cap"
    return "ok"
