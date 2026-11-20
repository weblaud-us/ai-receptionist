"""
Subscription lifecycle (Roadmap Task 5.3): Stripe webhooks → tenant status and limits.

| Stripe event                          | Action                                                       |
|---------------------------------------|--------------------------------------------------------------|
| checkout.session.completed            | store customer + subscription, activate (trial), start wizard |
| customer.subscription.created/updated | plan → tenant.limits, status, period, trial end              |
| invoice.paid                          | status active (a $0 trial invoice leaves the trial alone)     |
| invoice.payment_failed                | past_due + e-mail; Stripe Smart Retries retry the card       |
| customer.subscription.deleted         | cancelled; data kept CANCEL_RETENTION_DAYS, then deleted     |

Idempotent by event id: each event is claimed in `stripe_events` before it is handled. A handler
that fails releases its claim and the endpoint answers 500, so Stripe delivers it again. Handlers
only ever move a tenant to the state Stripe says it is in, so an old event replayed late is harmless.

Status that *we* set is respected: a business suspended by the 7-day job stays suspended on a
`past_due` update and is reactivated only by a payment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from config import settings
from core.billing import plans, stripe_api
from core.billing.usage import aware, utcnow
from core.integrations.email import send_email
from core.repositories import Repos
from core.repositories.repos import TenantsStore, UsersStore
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

# Stripe subscription status → tenant status
_STATUS = {
    "trialing": "trial", "active": "active", "past_due": "past_due", "unpaid": "suspended",
    "paused": "suspended", "canceled": "cancelled", "incomplete": "incomplete",
    "incomplete_expired": "incomplete",
}


class Ignored(Exception):
    """An event we don't act on (unknown tenant, unrelated product)."""


# ── Helpers ──────────────────────────────────────────────────────────────────

async def owner_emails(db: Any, tenant: TenantConfig) -> List[str]:
    emails = []
    if tenant.notifications.owner_email:
        emails.append(tenant.notifications.owner_email)
    for u in await UsersStore(db).for_tenant(tenant.id):
        if any(m.get("tenant_id") == tenant.id and m.get("role") == "owner" for m in u.get("memberships", [])):
            if u.get("email") and u["email"] not in emails:
                emails.append(u["email"])
    return emails


async def email_owners(db: Any, tenant: TenantConfig, subject: str, text: str) -> int:
    sent = 0
    for to in await owner_emails(db, tenant):
        sent += int(await send_email(to, subject, text))
    return sent


def dashboard(path: str) -> str:
    base = (settings.DASHBOARD_URL or "").rstrip("/")
    return f"{base}{path}" if base else path


async def load(db: Any, tenant_id: str) -> Optional[TenantConfig]:
    doc = await TenantsStore(db).get(tenant_id)
    return TenantConfig.from_doc(doc) if doc else None


async def apply(db: Any, tenant: TenantConfig, billing: Dict[str, Any], *, status: Optional[str] = None,
                limits: Optional[Dict[str, Any]] = None, extra: Optional[Dict[str, Any]] = None,
                audit: str = "", actor: str = "stripe") -> None:
    sets: Dict[str, Any] = {f"billing.{k}": v for k, v in billing.items()}
    if status and status != tenant.status:
        sets["status"] = status
    for k, v in (limits or {}).items():
        sets[f"limits.{k}"] = v
    sets.update(extra or {})
    if not sets:
        return
    await TenantsStore(db).update(tenant.id, sets)
    invalidate(tenant.id)
    if audit or (status and status != tenant.status):
        await Repos(db, tenant.id).audit.record(
            user_id=actor, action=audit or "billing.status", target_type="tenant", target_id=tenant.id,
            diff={"status": {"from": tenant.status, "to": status}} if status and status != tenant.status else {})


async def find_tenant(db: Any, obj: Dict[str, Any]) -> Optional[TenantConfig]:
    """The tenant a Stripe object belongs to (metadata first, then the customer id). A business whose
    data was already deleted is never brought back by a late event."""
    tenant = await _find_tenant(db, obj)
    return None if tenant is None or tenant.billing.deleted_at else tenant


async def _find_tenant(db: Any, obj: Dict[str, Any]) -> Optional[TenantConfig]:
    tenant_id = ((obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
                 or ((obj.get("subscription_details") or {}).get("metadata") or {}).get("tenant_id"))
    if tenant_id:
        t = await load(db, str(tenant_id))
        if t:
            return t
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if customer:
        doc = await db.tenants.find_one({"billing.stripe_customer_id": str(customer)})
        if doc:
            return TenantConfig.from_doc(doc)
    return None


# ── Subscription sync ────────────────────────────────────────────────────────

def _plan_of(sub: Dict[str, Any]) -> Optional[tuple]:
    for price in stripe_api.subscription_prices(sub):
        found = plans.plan_for_price(price)
        if found:
            return found
    return None


def _reactivation_fields() -> Dict[str, Any]:
    return {"past_due_since": None, "cancelled_at": None, "delete_after": None, "number_release_after": None}


async def sync_subscription(db: Any, tenant: TenantConfig, sub: Dict[str, Any]) -> str:
    """Make the tenant match a Stripe subscription. Returns the new tenant status."""
    stripe_status = str(sub.get("status") or "")
    status = _STATUS.get(stripe_status, tenant.status)
    start, end = stripe_api.subscription_period(sub)
    billing: Dict[str, Any] = {
        "stripe_subscription_id": sub.get("id") or tenant.billing.stripe_subscription_id,
        "subscription_status": stripe_status,
        "period_start": start, "period_end": end,
        "trial_ends_at": stripe_api.subscription_trial_end(sub),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }
    if sub.get("customer"):
        billing["stripe_customer_id"] = sub["customer"] if isinstance(sub["customer"], str) else sub["customer"].get("id")
    if stripe_status == "trialing":
        billing["trial_used"] = True
    limits = None
    found = _plan_of(sub)
    if found:
        plan_key, interval = found
        billing["plan"], billing["interval"] = plan_key, interval
        plan = plans.get_plan(plan_key)
        if plan and plan.self_serve:
            limits = plans.limits_for(plan)
    elif tenant.billing.plan == "manual":
        billing["plan"] = "agency"           # a subscription on a price we don't list: custom terms

    # Our own suspension is lifted only by a payment, not by "still past due".
    if tenant.status == "suspended" and status == "past_due":
        status = "suspended"
    if status == "past_due" and not tenant.billing.past_due_since:
        billing["past_due_since"] = utcnow()
    if status in ("trial", "active"):
        billing.update(_reactivation_fields())
    if status == "cancelled":
        return await cancel(db, tenant, billing_extra=billing)
    await apply(db, tenant, billing, status=status, limits=limits)
    return status


async def cancel(db: Any, tenant: TenantConfig, *, billing_extra: Optional[Dict[str, Any]] = None,
                 now: Optional[datetime] = None, actor: str = "stripe") -> str:
    now = now or utcnow()
    retention = timedelta(days=settings.CANCEL_RETENTION_DAYS)
    billing = {**(billing_extra or {}), "cancelled_at": aware(tenant.billing.cancelled_at) or now,
               "delete_after": aware(tenant.billing.delete_after) or now + retention,
               "number_release_after": aware(tenant.billing.number_release_after) or now + retention,
               "subscription_status": (billing_extra or {}).get("subscription_status") or "canceled"}
    first_time = tenant.status != "cancelled"
    await apply(db, tenant, billing, status="cancelled", audit="billing.cancelled", actor=actor)
    if first_time:
        when = billing["delete_after"].strftime("%d %B %Y")
        await email_owners(db, tenant, f"Your {tenant.name} receptionist is cancelled",
                           f"Your subscription has ended. Calls to your number are now forwarded to your "
                           f"transfer number (or callers are asked to call back).\n\n"
                           f"Your data is kept until {when}; download it any time before then from "
                           f"{dashboard('/billing')} (Export all data). After that date your calls, transcripts, "
                           f"contacts, appointments and recordings are deleted and your AI number is released.\n\n"
                           f"Changed your mind? Re-subscribe from the same page before {when} and everything "
                           f"comes back as it was.")
    return "cancelled"


# ── Event handlers ───────────────────────────────────────────────────────────

async def on_checkout_completed(db: Any, session: Dict[str, Any]) -> str:
    if session.get("mode") != "subscription":
        raise Ignored("not a subscription checkout")
    tenant = await find_tenant(db, session)
    if tenant is None:
        raise Ignored("unknown tenant")
    billing: Dict[str, Any] = {}
    if session.get("customer"):
        billing["stripe_customer_id"] = str(session["customer"])
    sub_id = session.get("subscription")
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id")
    if sub_id:
        billing["stripe_subscription_id"] = str(sub_id)
    onboarding = dict(tenant.onboarding)
    onboarding.setdefault("step", "business")
    onboarding["checkout_completed_at"] = utcnow().isoformat()
    first = tenant.status == "incomplete"
    await apply(db, tenant, billing, extra={"onboarding": onboarding}, audit="billing.checkout")
    status: str = tenant.status
    if sub_id:
        try:
            sub = await stripe_api.retrieve_subscription(str(sub_id))
            refreshed = await load(db, tenant.id) or tenant
            status = await sync_subscription(db, refreshed, sub)
        except stripe_api.StripeError:
            # The subscription.created / updated events carry the same data; they will sync it.
            logger.warning("Could not read subscription after checkout for %s", tenant.id)
            if tenant.status == "incomplete":
                refreshed = await load(db, tenant.id) or tenant
                await apply(db, refreshed, {"trial_used": True}, status="trial")
                status = "trial"
    if first:
        await email_owners(db, tenant, "You're in — let's set up your receptionist",
                           f"Thanks for subscribing. Finish setting up {tenant.name} (about 10 minutes): "
                           f"{dashboard('/onboarding')}")
    return status


async def on_subscription_changed(db: Any, sub: Dict[str, Any]) -> str:
    tenant = await find_tenant(db, sub)
    if tenant is None:
        raise Ignored("unknown tenant")
    if tenant.billing.stripe_subscription_id and sub.get("id") and sub["id"] != tenant.billing.stripe_subscription_id \
            and tenant.status not in ("cancelled", "incomplete"):
        # An old subscription (e.g. replaced on reactivation) must not overwrite the current one.
        raise Ignored("not the tenant's current subscription")
    return await sync_subscription(db, tenant, sub)


async def on_subscription_deleted(db: Any, sub: Dict[str, Any]) -> str:
    tenant = await find_tenant(db, sub)
    if tenant is None:
        raise Ignored("unknown tenant")
    if tenant.billing.stripe_subscription_id and sub.get("id") != tenant.billing.stripe_subscription_id:
        raise Ignored("not the tenant's current subscription")
    return await cancel(db, tenant, billing_extra={"subscription_status": "canceled", "cancel_at_period_end": False})


def _invoice_subscription(invoice: Dict[str, Any]) -> Optional[str]:
    sub = invoice.get("subscription")
    if not sub:
        sub = (((invoice.get("parent") or {}).get("subscription_details")) or {}).get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return str(sub) if sub else None


async def on_invoice_paid(db: Any, invoice: Dict[str, Any]) -> str:
    tenant = await find_tenant(db, invoice)
    if tenant is None:
        raise Ignored("unknown tenant")
    if tenant.status in ("trial", "incomplete") and (
            int(invoice.get("amount_paid") or 0) == 0 or invoice.get("billing_reason") == "subscription_create"):
        # The invoice that opens the subscription: $0 for a trial, or just the one-time setup fee (dental
        # plans charge it up front, trial or not). The subscription events set trial vs active.
        return tenant.status
    billing: Dict[str, Any] = {"past_due_since": None}
    if tenant.status == "cancelled":
        # Only a payment on a *new* subscription reactivates (a late invoice of the old one doesn't).
        sub_id = _invoice_subscription(invoice)
        if not sub_id or sub_id == tenant.billing.stripe_subscription_id:
            return tenant.status
        billing.update(_reactivation_fields(), stripe_subscription_id=sub_id)
    was_unpaid = tenant.status in ("past_due", "suspended")
    await apply(db, tenant, billing, status="active", audit="billing.paid")
    if was_unpaid:
        await email_owners(db, tenant, "Payment received — you're back on",
                           f"Thanks — your payment went through and {tenant.name}'s receptionist is answering "
                           f"calls again.")
    return "active"


async def on_payment_failed(db: Any, invoice: Dict[str, Any]) -> str:
    tenant = await find_tenant(db, invoice)
    if tenant is None:
        raise Ignored("unknown tenant")
    if tenant.status in ("cancelled",):
        return tenant.status
    billing: Dict[str, Any] = {}
    if not tenant.billing.past_due_since:
        billing["past_due_since"] = utcnow()
    status = "suspended" if tenant.status == "suspended" else "past_due"
    await apply(db, tenant, billing, status=status, audit="billing.payment_failed")
    if tenant.status not in ("past_due", "suspended"):
        await email_owners(db, tenant, "Your payment didn't go through",
                           f"We couldn't charge your card for {tenant.name}'s AI receptionist. We'll retry "
                           f"automatically over the next few days. To avoid any interruption, update your card: "
                           f"{dashboard('/billing')}\n\nIf it isn't fixed within {settings.PAST_DUE_SUSPEND_DAYS} "
                           f"days, calls will be forwarded to your transfer number instead of being answered "
                           f"by the assistant.")
    return status


HANDLERS = {
    "checkout.session.completed": on_checkout_completed,
    "customer.subscription.created": on_subscription_changed,
    "customer.subscription.updated": on_subscription_changed,
    "customer.subscription.deleted": on_subscription_deleted,
    "invoice.paid": on_invoice_paid,
    "invoice.payment_failed": on_payment_failed,
}


class InProgress(Exception):
    """The same event is being handled right now by another request; Stripe should retry later."""


async def handle_event(db: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    """Claim → handle → mark done. Returns a small result for the webhook response and logs."""
    event_id, kind = str(event["id"]), str(event["type"])
    handler = HANDLERS.get(kind)
    if handler is None:
        return {"status": "ignored", "type": kind}
    try:
        await db.stripe_events.insert_one({"_id": event_id, "type": kind, "status": "processing",
                                           "received_at": utcnow(), "livemode": bool(event.get("livemode"))})
    except DuplicateKeyError:
        row = await db.stripe_events.find_one({"_id": event_id}) or {}
        if row.get("status") == "processing":
            raise InProgress(event_id)
        return {"status": "duplicate", "type": kind}
    obj = ((event.get("data") or {}).get("object")) or {}
    try:
        result = await handler(db, obj)
    except Ignored as why:
        await db.stripe_events.update_one({"_id": event_id}, {"$set": {"status": "ignored", "note": str(why)}})
        logger.info("Stripe %s %s ignored: %s", kind, event_id, why)
        return {"status": "ignored", "type": kind}
    except Exception:
        await db.stripe_events.delete_one({"_id": event_id})        # let Stripe's retry run it again
        raise
    await db.stripe_events.update_one({"_id": event_id}, {"$set": {"status": "done", "result": str(result),
                                                                   "done_at": datetime.now(timezone.utc)}})
    logger.info("Stripe %s %s → %s", kind, event_id, result)
    return {"status": "ok", "type": kind, "result": result}
