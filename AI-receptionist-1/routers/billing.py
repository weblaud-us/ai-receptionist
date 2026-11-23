"""
Billing, numbers and data export (Roadmap Phase 5).

Tenant (prefix /t) — suspended and cancelled businesses can still reach these, to pay or leave:
GET   /t/billing                        plan, status, usage this period, alerts, invoices (via Portal)
POST  /t/billing/checkout               Stripe Checkout (subscribe / change plan / reactivate)   owner
POST  /t/billing/portal                 Stripe Customer Portal (card, plan, invoices, cancel)     owner
PATCH /t/billing                        {hard_cap}                                                 owner
POST  /t/billing/verify-phone           text a code to the owner's phone (trial transfers)        owner
POST  /t/billing/verify-phone/confirm   check the code                                            owner
GET   /t/numbers/search                 available local numbers (?country=US&area_code=512)       admin
POST  /t/numbers                        buy + connect a number (runs as a job)                    owner
GET   /t/numbers/orders                 order progress
GET   /t/export                         everything, one zip                                       owner
GET   /t/export/{kind}                  calls.csv · transcripts.json · contacts.csv · appointments.csv|.ics · messages.csv

Public:
POST  /billing/stripe/webhook           Stripe events (signature-checked, idempotent)

Platform (prefix /platform):
GET   /platform/costs                   cost per minute and margin per plan (Task 5.1)
GET   /platform/number-orders           every order, newest first
POST  /platform/number-orders/{id}/retry     run a failed order again
POST  /platform/number-orders/{id}/approve   regulatory bundle approved → continue
POST  /platform/tenants/{id}/cancel     cancel now (data kept CANCEL_RETENTION_DAYS)
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from config import settings
from core import ratelimit
from core.auth import Principal, TenantPrincipal, require_platform_admin, require_tenant
from core.billing import costs, lifecycle, plans, stripe_api, usage
from core.integrations import voice_code
from core.phone import digits, mask_phone
from core.repositories.repos import UsersStore
from core.services import numbers as number_service
from core.services import offboarding
from core.tenancy import resolve_by_id
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

billing_router = APIRouter(prefix="/t", tags=["Billing"])
stripe_webhook_router = APIRouter(prefix="/billing", tags=["Billing"])
platform_billing_router = APIRouter(prefix="/platform", tags=["Platform"])

_staff_any = require_tenant("staff", allow_inactive=True)
_owner_any = require_tenant("owner", allow_inactive=True)
_admin = require_tenant("admin")
_owner = require_tenant("owner")

PHONE_CODE_TTL = timedelta(minutes=10)
PHONE_CODE_TRIES = 5


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _audit(tp: TenantPrincipal, request: Request, action: str, target: str = "",
                 diff: Optional[Dict[str, Any]] = None) -> None:
    await tp.repos().audit.record(user_id=tp.user.user_id, action=action, target_type="billing", target_id=target,
                                  diff=diff or {}, ip=_ip(request))


def _stripe_http(exc: stripe_api.StripeError) -> HTTPException:
    if exc.status == 503:
        return HTTPException(503, "Billing isn't configured on this server")
    return HTTPException(502, "Our payment provider didn't answer. Try again in a minute.")


# ─────────────────────────────────────────────
# PLAN AND USAGE
# ─────────────────────────────────────────────

@billing_router.get("/billing")
async def get_billing(tp: TenantPrincipal = Depends(_staff_any)):
    t, b = tp.tenant, tp.tenant.billing
    start, end = usage.period_window(t)
    seconds = await usage.seconds_between(get_db(), t.id, start, end)
    plan = plans.get_plan(b.plan)
    return {
        "status": t.status, "plan": b.plan, "plan_name": plan.name if plan else "Custom", "interval": b.interval,
        "self_serve": b.self_serve, "billing_enabled": settings.billing_enabled,
        "has_customer": bool(b.stripe_customer_id), "subscription_status": b.subscription_status,
        "trial_ends_at": to_jsonable(b.trial_ends_at), "trial_used": b.trial_used,
        "cancel_at_period_end": b.cancel_at_period_end, "past_due_since": to_jsonable(b.past_due_since),
        "cancelled_at": to_jsonable(b.cancelled_at), "delete_after": to_jsonable(b.delete_after),
        "number_release_after": to_jsonable(b.number_release_after),
        "hard_cap": b.hard_cap, "verified_phone": mask_phone(b.verified_phone) if b.verified_phone else "",
        "usage": usage.summary(t, seconds), "gate": usage.gate(t, seconds),
        "limits": t.limits.model_dump(), "overage_usd_per_min": plans.OVERAGE_USD_PER_MIN,
        "currency": plans.billing_currency(t.currency),
        "overage_per_min": plans.OVERAGE_PER_MIN.get(plans.billing_currency(t.currency), plans.OVERAGE_USD_PER_MIN),
        "catalogue": plans.catalogue(t.currency, t.vertical), "can_manage": tp.can("owner"),
    }


class CheckoutBody(BaseModel):
    plan: str
    interval: Literal["month", "year"] = "month"
    setup_fee: bool = False                  # optional "done-for-you setup"


@billing_router.post("/billing/checkout")
async def checkout(body: CheckoutBody, request: Request, tp: TenantPrincipal = Depends(_owner_any)):
    t, b = tp.tenant, tp.tenant.billing
    if t.is_legacy:
        raise HTTPException(409, "Run migration m0003 first")
    if not settings.billing_enabled:
        raise HTTPException(503, "Billing isn't configured on this server")
    plan = plans.get_plan(body.plan)
    price = plans.price_id(body.plan, body.interval)
    if plan is None or not plan.self_serve or not price or not plans.plan_allowed(plan, t.vertical):
        raise HTTPException(422, "That plan isn't available — contact us for custom plans")
    setup_price = plans.setup_fee_price(plan.key) if (plan.setup_fee_required or body.setup_fee) else ""
    if plan.setup_fee_required and not setup_price:
        raise HTTPException(422, "That plan isn't available yet — contact us to get started")
    if b.stripe_subscription_id and t.status in ("trial", "active", "past_due"):
        # A live subscription changes plan in the Portal (Stripe prorates); never a second subscription.
        raise HTTPException(409, "You already have a subscription — change plan from 'Manage billing'")
    db = get_db()
    try:
        customer_id = b.stripe_customer_id
        if not customer_id:
            owner = await UsersStore(db).get(tp.user.user_id) or {}
            customer = await stripe_api.create_customer(email=owner.get("email") or tp.user.email,
                                                        name=t.name, tenant_id=t.id)
            customer_id = customer["id"]
            await db.tenants.update_one({"_id": t.id}, {"$set": {"billing.stripe_customer_id": customer_id}})
        trial_days = settings.TRIAL_DAYS if not b.trial_used and t.status in ("incomplete", "trial") else 0
        session = await stripe_api.create_checkout_session(
            customer_id=customer_id, tenant_id=t.id, price=price, trial_days=trial_days,
            currency=plans.billing_currency(t.currency),
            overage_price=settings.STRIPE_PRICE_OVERAGE.strip(),
            setup_fee_price=setup_price,
            success_url=lifecycle.dashboard("/onboarding?checkout=success"),
            cancel_url=lifecycle.dashboard("/billing?checkout=cancelled"),
            attempt=secrets.token_hex(4))
    except stripe_api.StripeError as exc:
        raise _stripe_http(exc)
    await _audit(tp, request, "billing.checkout_started", body.plan, {"interval": body.interval, "trial_days": trial_days})
    return {"url": session.get("url"), "id": session.get("id")}


@billing_router.post("/billing/portal")
async def portal(tp: TenantPrincipal = Depends(_owner_any)):
    if not tp.tenant.billing.stripe_customer_id:
        raise HTTPException(409, "There's no subscription yet — choose a plan first")
    try:
        session = await stripe_api.create_portal_session(tp.tenant.billing.stripe_customer_id,
                                                         lifecycle.dashboard("/billing"))
    except stripe_api.StripeError as exc:
        raise _stripe_http(exc)
    return {"url": session.get("url")}


class BillingPatch(BaseModel):
    hard_cap: Optional[bool] = None


@billing_router.patch("/billing")
async def patch_billing(body: BillingPatch, request: Request, tp: TenantPrincipal = Depends(_owner)):
    sets: Dict[str, Any] = {}
    if body.hard_cap is not None:
        sets["hard_cap"] = body.hard_cap
    if sets:
        await lifecycle.apply(get_db(), tp.tenant, sets, audit="billing.settings", actor=tp.user.user_id)
    return {"success": True, **sets}


class PhoneBody(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=6)


def _number_in_country(number: str, country: str) -> bool:
    """By calling code, not number validity: +1 counts for US and CA, +44 for GB. Unparseable = no."""
    try:
        import phonenumbers

        regions = phonenumbers.region_codes_for_country_code(phonenumbers.parse(number, None).country_code)
        return country in regions
    except Exception:
        return False


@billing_router.post("/billing/verify-phone")
async def verify_phone_start(body: PhoneBody, tp: TenantPrincipal = Depends(_owner_any)):
    number = "+" + digits(body.phone)
    if len(number) < 9:
        raise HTTPException(422, "Use international format, e.g. +15125551234")
    if any(number.startswith(p) for p in settings.transfer_blocked_prefixes):
        raise HTTPException(422, "Premium-rate numbers can't be used")
    # The code is read out by a (paid) phone call, so keep it to the practice's own country and a
    # few calls a day per number: this endpoint must not become a way to ring strangers.
    country = (tp.tenant.country or "").upper()
    if country and not _number_in_country(number, country):
        raise HTTPException(422, "Use a phone number in the same country as your business.")
    if not await ratelimit.allow_fixed(f"phoneverify:{tp.tenant.id}", 5, 3600):
        raise HTTPException(429, "Too many codes requested. Try again in an hour.")
    if not await ratelimit.allow_fixed(f"phoneverify:to:{digits(number)}", 3, 86400):
        raise HTTPException(429, "That number has had several codes today. Try again tomorrow.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    from core.auth.tokens import token_hash
    from core.billing.usage import utcnow

    await get_db().phone_verifications.replace_one(  # scoped: _id is the tenant id
        {"_id": tp.tenant.id},
        {"_id": tp.tenant.id, "phone": number, "code_hash": token_hash(code), "tries": 0,
         "expires_at": utcnow() + PHONE_CODE_TTL}, upsert=True)
    # No SMS on this platform: we call the number and read the code out (core/integrations/voice_code).
    sent = bool(await voice_code.call_with_code(number, code, country))
    if not sent and not settings.is_development:
        if not voice_code.configured():
            raise HTTPException(503, "Verifying a number by phone call isn't available yet. Contact support and "
                                     "we'll verify your transfer number for you.")
        raise HTTPException(502, "We couldn't call that number. Check it and try again.")
    out: Dict[str, Any] = {"success": True, "sent": sent, "channel": "call"}
    if not sent:
        out["dev_code"] = code               # development without Twilio voice only
    return out


@billing_router.post("/billing/verify-phone/confirm")
async def verify_phone_confirm(body: CodeBody, request: Request, tp: TenantPrincipal = Depends(_owner_any)):
    from core.auth.tokens import token_hash
    from core.billing.usage import aware, utcnow

    db = get_db()
    row = await db.phone_verifications.find_one({"_id": tp.tenant.id})  # scoped: _id is the tenant id
    if not row or (aware(row.get("expires_at")) or utcnow()) < utcnow() or row.get("tries", 0) >= PHONE_CODE_TRIES:
        raise HTTPException(400, "That code has expired — ask for a new one")
    if not secrets.compare_digest(token_hash(body.code), row["code_hash"]):
        await db.phone_verifications.update_one({"_id": tp.tenant.id}, {"$inc": {"tries": 1}})  # scoped: _id is the tenant id
        raise HTTPException(400, "That code didn't match")
    await db.phone_verifications.delete_one({"_id": tp.tenant.id})  # scoped: _id is the tenant id
    await lifecycle.apply(db, tp.tenant, {"verified_phone": row["phone"]},
                          extra={} if tp.tenant.notifications.owner_phone else {"notifications.owner_phone": row["phone"]},
                          audit="billing.phone_verified", actor=tp.user.user_id)
    return {"success": True, "phone": mask_phone(row["phone"])}


# ─────────────────────────────────────────────
# NUMBERS
# ─────────────────────────────────────────────

def _numbers_http(exc: number_service.ProvisioningError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@billing_router.get("/numbers/search")
async def search_numbers(country: str = Query("US", min_length=2, max_length=2),
                         area_code: str = Query("", max_length=5, pattern=r"^\d{0,5}$"),
                         contains: str = Query("", max_length=10, pattern=r"^[\d*]{0,10}$"),
                         tp: TenantPrincipal = Depends(_admin)):
    if not await ratelimit.allow_fixed(f"numsearch:{tp.tenant.id}", 30, 60):
        raise HTTPException(429, "Slow down a little")
    try:
        found = await number_service.search(country, area_code=area_code, contains=contains)
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    return {"numbers": found, "mode": number_service.mode(),
            "instant": country.upper() in settings.number_instant_countries or number_service.mode() == "fake"}


class NumberOrderBody(BaseModel):
    e164: str = Field(min_length=8, max_length=20)


async def _run_order_in_background(order_id: str) -> None:
    await number_service.run_order_in_background(order_id)


@billing_router.post("/numbers")
async def order_number(body: NumberOrderBody, request: Request, tp: TenantPrincipal = Depends(_owner)):
    if not tp.user.platform_admin:
        user = await UsersStore(get_db()).get(tp.user.user_id) or {}
        if not user.get("email_verified_at") and not user.get("is_platform_admin"):
            raise HTTPException(403, "Confirm your e-mail address first (check your inbox)")
    try:
        order = await number_service.start_order(get_db(), tp.tenant, body.e164, requested_by=tp.user.user_id)
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    if order["status"] == "pending":
        await _run_order_in_background(order["_id"])
    return {"order": to_jsonable(number_service.public_order(order))}


@billing_router.get("/numbers/orders")
async def number_orders(tp: TenantPrincipal = Depends(_staff_any)):
    rows = await get_db().number_orders.find({"tenant_id": tp.tenant.id}).sort("created_at", -1).limit(20).to_list(20)
    return {"orders": [to_jsonable(number_service.public_order(r)) for r in rows], "mode": number_service.mode()}


# ─────────────────────────────────────────────
# EXPORT (Task 5.5)
# ─────────────────────────────────────────────

@billing_router.get("/export")
async def export_all(request: Request, tp: TenantPrincipal = Depends(_owner_any)):
    if not await ratelimit.allow_fixed(f"export:{tp.tenant.id}", 10, 3600):
        raise HTTPException(429, "Too many exports. Try again in an hour.")
    data = await offboarding.export_zip(get_db(), tp.tenant)
    await _audit(tp, request, "data.export", "all", {"bytes": len(data)})
    name = f"{tp.tenant.slug}-export.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@billing_router.get("/export/{kind}")
async def export_kind(kind: str, request: Request, tp: TenantPrincipal = Depends(_owner_any)):
    if kind not in offboarding.EXPORT_KINDS:
        raise HTTPException(404, "Unknown export")
    if not await ratelimit.allow_fixed(f"export:{tp.tenant.id}", 10, 3600):
        raise HTTPException(429, "Too many exports. Try again in an hour.")
    text = await offboarding.export_one(get_db(), tp.tenant, kind)
    await _audit(tp, request, "data.export", kind)
    return Response(content=text, media_type=offboarding.MEDIA_TYPES[kind.rsplit(".", 1)[1]],
                    headers={"Content-Disposition": f'attachment; filename="{tp.tenant.slug}-{kind}"',
                             "Cache-Control": "no-store"})


# ─────────────────────────────────────────────
# STRIPE WEBHOOK
# ─────────────────────────────────────────────

@stripe_webhook_router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    try:
        event = stripe_api.verify_webhook(payload, request.headers.get("Stripe-Signature", ""))
    except stripe_api.SignatureError as why:
        logger.warning("Rejected Stripe webhook: %s", why)
        raise HTTPException(400, "Invalid signature")
    try:
        result = await lifecycle.handle_event(get_db(), event)
    except lifecycle.InProgress:
        raise HTTPException(409, "Event is being processed")
    return {"received": True, **result}


# ─────────────────────────────────────────────
# PLATFORM
# ─────────────────────────────────────────────

@platform_billing_router.get("/costs")
async def cost_model(admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    since = usage.utcnow() - timedelta(days=30)
    totals = {"calls": 0, "seconds": 0.0, "cost_usd": 0.0}
    async for row in db.usage_events.aggregate([  # cross-tenant: platform economics, totals only
        {"$match": {"kind": "voice_minute", "at": {"$gte": usage.naive_utc(since)}}},
        {"$group": {"_id": None, "calls": {"$sum": 1}, "seconds": {"$sum": {"$ifNull": ["$seconds", 0]}},
                    "cost": {"$sum": {"$ifNull": ["$cost_usd", 0]}}}},
    ]):
        totals = {"calls": row["calls"], "seconds": row["seconds"], "cost_usd": round(row["cost"], 2)}
    by_status: Dict[str, int] = {}
    async for row in db.tenants.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        by_status[str(row["_id"])] = row["n"]
    by_plan: Dict[str, int] = {}
    mrr = 0.0
    async for row in db.tenants.aggregate([{"$match": {"status": {"$in": ["trial", "active", "past_due"]}}},
                                           {"$group": {"_id": {"plan": "$billing.plan", "currency": "$currency",
                                                               "interval": "$billing.interval"},
                                                       "n": {"$sum": 1}}}]):
        key = str(row["_id"].get("plan") or "manual")
        by_plan[key] = by_plan.get(key, 0) + row["n"]
        plan = plans.get_plan(key)
        if plan:   # list price in the business's currency (annual: 10 months over 12), reported in USD
            mrr += row["n"] * plan.monthly_revenue_usd(str(row["_id"].get("currency") or ""),
                                                       str(row["_id"].get("interval") or "month"))
    mrr = round(mrr, 2)
    return {**costs.model(), "last_30d": totals, "tenants_by_status": by_status, "tenants_by_plan": by_plan,
            "list_mrr_usd": mrr}


@platform_billing_router.get("/number-orders")
async def all_number_orders(status: Optional[str] = None, admin: Principal = Depends(require_platform_admin)):
    q = {"status": status} if status else {}
    rows = await get_db().number_orders.find(q).sort("created_at", -1).limit(200).to_list(200)
    return {"orders": [to_jsonable(number_service.public_order(r)) for r in rows]}


@platform_billing_router.post("/number-orders/{order_id}/retry")
async def retry_number_order(order_id: str, admin: Principal = Depends(require_platform_admin)):
    order = await get_db().number_orders.find_one({"_id": order_id})
    if not order:
        raise HTTPException(404, "Order not found")
    if order["status"] not in ("failed", "verify_failed", "pending"):
        raise HTTPException(409, f"Order is {order['status']}")
    try:
        order = await number_service.run_order(get_db(), order_id)
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    except Exception:
        raise HTTPException(502, "The order failed again — see its error")
    return {"order": to_jsonable(number_service.public_order(order))}


class ApproveOrderBody(BaseModel):
    bundle_sid: str = Field(default="", max_length=40)      # BU… approved regulatory bundle
    address_sid: str = Field(default="", max_length=40)     # AD… (most AU/UK/EU numbers)


@platform_billing_router.post("/number-orders/{order_id}/approve")
async def approve_number_order(order_id: str, body: Optional[ApproveOrderBody] = None,
                               admin: Principal = Depends(require_platform_admin)):
    body = body or ApproveOrderBody()
    try:
        await number_service.complete_regulatory(get_db(), order_id, admin=admin.user_id,
                                                 bundle_sid=body.bundle_sid, address_sid=body.address_sid)
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    await _run_order_in_background(order_id)
    return {"success": True}


@platform_billing_router.post("/tenants/{tenant_id}/cancel")
async def platform_cancel(tenant_id: str, admin: Principal = Depends(require_platform_admin)):
    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    if tenant.billing.stripe_subscription_id and settings.billing_enabled:
        try:
            await stripe_api.request("DELETE", f"/subscriptions/{tenant.billing.stripe_subscription_id}")
        except stripe_api.StripeError as exc:
            if exc.status != 404:
                raise _stripe_http(exc)
    await lifecycle.cancel(get_db(), tenant, actor=f"platform:{admin.user_id}")
    return {"success": True}
