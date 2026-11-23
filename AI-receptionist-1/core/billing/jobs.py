"""
The billing tick (Roadmap Tasks 5.3 and 5.5) — every 15 minutes in the jobs worker
(`billing_tick`), or in the API process when there is no Redis.

Per business, each step independent and idempotent:
0. calls a crashed worker left without a usage event get one (core/billing/reconcile.py, BACK-06)
1. refresh this period's usage from `usage_events` (the cached `billing.period_seconds`)
2. usage alerts at 80 % and 100 % of included minutes — each claimed once per period
3. overage → Stripe meter events (core/billing/metering.py)
4. past_due for more than PAST_DUE_SUSPEND_DAYS → suspended (calls forwarded, never silence)
5. cancelled: final warning 3 days before deletion → release numbers → delete the data
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import settings
from core.billing import metering
from core.billing.reconcile import reconcile_usage
from core.billing.lifecycle import apply, dashboard, email_owners, owner_emails
from core.integrations.email import send_email
from core.billing.usage import (
    ALERT_PERCENTS, aware, included_minutes, on_trial, period_key, period_window, refresh_period_usage, utcnow,
)
from core.repositories.repos import PhoneNumbersStore
from core.services import numbers as number_service
from core.services import offboarding
from core.tenancy.models import LEGACY_TENANT_ID, TenantConfig

logger = logging.getLogger(__name__)

FINAL_WARNING_BEFORE = timedelta(days=3)
LIVE = ("trial", "active", "past_due")


async def usage_alerts(db: Any, tenant: TenantConfig, seconds: float, now: datetime) -> int:
    included = included_minutes(tenant)
    if not included:
        return 0
    pct = seconds / 60 / included * 100
    start, _ = period_window(tenant, now)
    key = f"billing.alerts_sent.{period_key(start)}"
    newest: Optional[int] = None
    for threshold in sorted(ALERT_PERCENTS, reverse=True):
        if pct < threshold:
            continue
        res = await db.tenants.update_one({"_id": tenant.id, key: {"$ne": threshold}},
                                          {"$addToSet": {key: threshold}})
        if res.modified_count and newest is None:
            newest = threshold                   # e-mail only the highest newly crossed threshold
    if newest is None:
        return 0
    used = round(seconds / 60)
    trial, capped = on_trial(tenant), tenant.billing.hard_cap or on_trial(tenant)
    what = "trial minutes" if trial else "included minutes"
    if newest >= 100:
        subject = f"You've used all your {what}"
        if capped:
            body = (f"{tenant.name} has used all {included} {what} ({used} min). New calls are now forwarded to "
                    f"your transfer number, or callers are asked to call back.\n\n"
                    + ("Subscribe to a plan to keep the assistant answering: " if trial else
                       "Turn off the hard cap or upgrade to keep the assistant answering: ") + dashboard("/billing"))
        else:
            body = (f"{tenant.name} has used all {included} {what} this period ({used} min). The assistant keeps "
                    f"answering; extra minutes are billed at $0.25 each on your next invoice.\n\n"
                    f"Upgrade or set a hard cap: {dashboard('/billing')}")
    else:
        subject = f"You've used {newest}% of your {what}"
        body = (f"{tenant.name} has used {used} of {included} {what} this period. "
                + ("At 100 % new calls will be forwarded instead of answered. " if capped
                   else "Nothing changes at 100 % — extra minutes are $0.25 each. ")
                + f"Details: {dashboard('/billing')}")
    await email_owners(db, tenant, subject, body)
    return 1


async def suspend_overdue(db: Any, tenant: TenantConfig, now: datetime) -> bool:
    since = aware(tenant.billing.past_due_since)
    if tenant.status != "past_due" or since is None or now - since < timedelta(days=settings.PAST_DUE_SUSPEND_DAYS):
        return False
    await apply(db, tenant, {}, status="suspended", audit="billing.suspended", actor="billing-job")
    await email_owners(db, tenant, "Your receptionist is paused",
                       f"We still haven't been able to charge your card, so {tenant.name}'s assistant has stopped "
                       f"answering. Calls are being forwarded to your transfer number (or callers are asked to call "
                       f"back) — nobody hears silence.\n\nUpdate your card to switch it back on immediately: "
                       f"{dashboard('/billing')}")
    return True


async def offboard(db: Any, tenant: TenantConfig, now: datetime) -> Dict[str, int]:
    b, out = tenant.billing, {"warned": 0, "released": 0, "deleted": 0}
    delete_after, release_after = aware(b.delete_after), aware(b.number_release_after)
    if tenant.status != "cancelled" or b.deleted_at or delete_after is None:
        return out
    if now >= delete_after - FINAL_WARNING_BEFORE and now < delete_after:
        res = await db.tenants.update_one({"_id": tenant.id, "billing.final_warning_at": None},
                                          {"$set": {"billing.final_warning_at": now}})
        if res.modified_count:
            await email_owners(db, tenant, "Final notice: your data will be deleted",
                               f"{tenant.name}'s calls, transcripts, contacts, appointments and recordings will be "
                               f"permanently deleted on {delete_after:%d %B %Y}, and your AI phone number released. "
                               f"Download a copy or re-subscribe before then: {dashboard('/billing')}")
            out["warned"] = 1
    if release_after and now >= release_after:
        for n in await PhoneNumbersStore(db).for_tenant(tenant.id):
            if n.get("status") == "released" or n.get("purpose") == "outbound_sales":
                continue
            try:
                await number_service.release(db, n["e164"])
                out["released"] += 1
            except Exception:
                logger.warning("Releasing a number for %s failed; retrying next tick", tenant.id, exc_info=True)
    if now >= delete_after:
        remaining = [n for n in await PhoneNumbersStore(db).for_tenant(tenant.id) if n.get("status") != "released"
                     and n.get("purpose") != "outbound_sales"]
        if remaining:
            return out                            # never delete while a number could still route calls here
        emails = await owner_emails(db, tenant)
        await offboarding.delete_tenant_data(db, tenant)
        for to in emails:
            await send_email(to, "Your data has been deleted",
                             f"As promised when you cancelled, all of {tenant.name}'s data has now been deleted "
                             f"and its AI phone number released. Thanks for having tried us.")
        out["deleted"] = 1
    return out


async def run_once(db: Any, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or utcnow()
    stats = {"tenants": 0, "alerts": 0, "reported_minutes": 0, "suspended": 0, "warned": 0, "released": 0,
             "deleted": 0, "errors": 0, "reconciled": 0}
    async for doc in db.tenants.find({"_id": {"$ne": LEGACY_TENANT_ID}, "billing.deleted_at": None}):
        try:
            tenant = TenantConfig.from_doc(doc)
        except Exception:
            continue
        stats["tenants"] += 1
        try:
            seconds: Optional[float] = None
            stats["reconciled"] += await reconcile_usage(db, tenant, now)     # before usage is summed
            if tenant.status in LIVE:
                seconds = await refresh_period_usage(db, tenant, now)
                stats["alerts"] += await usage_alerts(db, tenant, seconds, now)
            if tenant.billing.overage_pending or tenant.status in metering.BILLED_STATUSES:
                stats["reported_minutes"] += await metering.report_overage(db, tenant, now, seconds)
            stats["suspended"] += int(await suspend_overdue(db, tenant, now))
            for k, v in (await offboard(db, tenant, now)).items():
                stats[k] += v
        except Exception:
            stats["errors"] += 1
            logger.error("Billing tick failed for %s", tenant.id, exc_info=True)
    return stats
