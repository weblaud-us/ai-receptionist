"""
Self-serve signup (Roadmap Task 5.4) — public, no auth.

GET  /public/plans                 plan catalogue for the pricing / signup page
POST /signup                       e-mail + password + business → signed in, next: Stripe Checkout
POST /signup/verify-email          confirm the e-mail address (link from the verification e-mail)
POST /signup/resend-verification   (signed in) send the link again

Flow: landing page → /signup → POST /t/billing/checkout (Stripe, card required, 14-day trial) →
onboarding wizard → number → live. The e-mail must be verified before a phone number is bought.

Abuse: SIGNUP_RATE_PER_IP_HOUR per IP; the card is collected before anything answers calls; the trial
is capped at TRIAL_MINUTES and transfers only to the owner's verified number.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from config import settings
from core import ratelimit
from core.auth import Principal, current_user, service as auth_service
from core.billing import plans
from core.integrations.email import send_email
from core.repositories import Repos
from core.repositories.repos import UsersStore
from core.services.tenant_admin import create_tenant
from core.tenancy.models import VERTICALS
from database.mongodb import get_db

logger = logging.getLogger(__name__)

public_plans_router = APIRouter(tags=["Signup"])
signup_router = APIRouter(prefix="/signup", tags=["Signup"])

EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "GB", "CH", "NO", "IS"}
MIDDLE_EAST = {"AE", "SA", "QA", "KW", "BH", "OM", "JO", "EG"}
CURRENCY = {"US": "USD", "CA": "CAD", "GB": "GBP", "AE": "AED", "SA": "SAR", "QA": "QAR", "AU": "AUD", "CH": "CHF"}


def region_for(country: str) -> Literal["us", "eu", "me"]:
    c = country.upper()
    return "eu" if c in EU else "me" if c in MIDDLE_EAST else "us"


def currency_for(country: str) -> str:
    c = country.upper()
    return CURRENCY.get(c) or ("EUR" if c in EU else "USD")


class SignupBody(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    password: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=120)
    business_name: str = Field(min_length=2, max_length=120)
    vertical: str = "generic"
    country: str = Field(default="US", min_length=2, max_length=2)
    timezone: str = Field(default="America/New_York", max_length=64)
    plan: str = "growth"
    interval: Literal["month", "year"] = "month"
    accept_terms: bool = False

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("unknown timezone")
        return v


class VerifyBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@public_plans_router.get("/public/plans")
async def public_plans():
    return {**plans.catalogue(), "signup_enabled": settings.SIGNUP_ENABLED, "billing_enabled": settings.billing_enabled}


async def _send_verification(user_id: str, email: str) -> bool:
    token = await auth_service.start_email_verification(user_id)
    link = auth_service.dashboard_link(f"/verify-email?token={token}")
    return await send_email(email, "Confirm your e-mail",
                            f"Welcome! Confirm your e-mail address to finish setting up your AI receptionist:\n\n"
                            f"{link}\n\nThe link works for 3 days. If you didn't sign up, ignore this e-mail.")


@signup_router.post("")
async def signup(body: SignupBody, request: Request, response: Response):
    if not settings.SIGNUP_ENABLED:
        raise HTTPException(404, "Sign-up isn't open yet")
    if not body.accept_terms:
        raise HTTPException(422, "Please accept the terms of service and privacy policy")
    plan = plans.get_plan(body.plan)
    if plan is None or not plan.self_serve:
        raise HTTPException(422, f"plan must be one of {', '.join(plans.SELF_SERVE_PLANS)}")
    if settings.billing_enabled and not plans.price_id(plan.key, body.interval):
        raise HTTPException(422, "That plan isn't available for that billing period")
    if body.vertical not in VERTICALS:
        raise HTTPException(422, f"vertical must be one of {', '.join(VERTICALS)}")
    ip = ratelimit.client_ip(request)
    if settings.SIGNUP_RATE_PER_IP_HOUR > 0 and not await ratelimit.allow_fixed(
            f"signup:ip:{ip}", settings.SIGNUP_RATE_PER_IP_HOUR, 3600):
        raise HTTPException(429, "Too many sign-ups from this network. Try again later.", headers={"Retry-After": "3600"})
    email = body.email.strip().lower()
    if await UsersStore(get_db()).by_email(email):
        raise HTTPException(409, "An account with that e-mail already exists — sign in instead")

    country = body.country.upper()
    billing = {"plan": plan.key, "interval": body.interval}
    status = "incomplete"
    if not settings.billing_enabled:
        # Development without Stripe: a local trial, nothing charged (production refuses this config).
        from datetime import timedelta

        from core.billing.usage import utcnow

        status = "trial"
        billing.update(trial_used=True, trial_ends_at=utcnow() + timedelta(days=settings.TRIAL_DAYS))
    user = await auth_service.create_user(email=email, password=body.password, name=body.name)   # checks the password
    try:
        tenant = await create_tenant(
            name=body.business_name, vertical=body.vertical, timezone=body.timezone, country=country,
            currency=currency_for(country), status=status, region=region_for(country),
            extra={"notifications": {"owner_email": email}, "billing": billing,
                   "limits": plans.limits_for(plan), "onboarding": {"step": "checkout", "source": "self_serve"}})
    except ValueError as e:
        await get_db().users.delete_one({"_id": user["_id"]})
        raise HTTPException(422, str(e)[:300])
    await auth_service.add_membership(user["_id"], tenant.id, "owner")
    # Phase 6: evidence of what was agreed to — which versions, when, from where.
    from core.compliance import legal

    await legal.record_user_acceptance(get_db(), user["_id"], legal.SIGNUP_DOCUMENTS, ip=ip,
                                       user_agent=request.headers.get("user-agent", ""))
    await Repos(get_db(), tenant.id).audit.record(user_id=user["_id"], action="tenant.signup", target_type="tenant",
                                                  target_id=tenant.id, diff={"plan": plan.key, "ip": ip}, ip=ip)
    emailed = await _send_verification(user["_id"], email)
    doc = await UsersStore(get_db()).get(user["_id"]) or user
    result = await auth_service.issue_session(doc, tenant.id, user_agent=request.headers.get("user-agent", ""))
    from routers.auth import _set_refresh

    _set_refresh(response, result.refresh_token)
    logger.info("Self-serve signup: tenant %s (%s, plan %s)", tenant.id, body.vertical, plan.key)
    return {**result.body(), "verification_emailed": emailed,
            "next": "checkout" if settings.billing_enabled else "onboarding"}


@signup_router.post("/verify-email")
async def verify_email(body: VerifyBody):
    doc = await auth_service.finish_email_verification(body.token)
    return {"success": True, "email": doc.get("email")}


@signup_router.post("/resend-verification")
async def resend_verification(request: Request, user: Principal = Depends(current_user)):
    if user.legacy:
        raise HTTPException(400, "Not available for this account")
    if not await ratelimit.allow_fixed(f"verify:{user.user_id}", 3, 3600):
        raise HTTPException(429, "Please wait a while before asking for another e-mail")
    doc = await UsersStore(get_db()).get(user.user_id)
    if not doc:
        raise HTTPException(404, "User not found")
    if doc.get("email_verified_at"):
        return {"success": True, "already_verified": True}
    return {"success": True, "emailed": await _send_verification(user.user_id, doc["email"])}
