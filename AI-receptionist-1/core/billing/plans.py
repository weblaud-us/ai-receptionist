"""
Plans and prices (Roadmap Task 5.2) — the single source the signup page, Checkout, the webhook and
plan limits all read.

Prices here are what the dashboard shows; what a customer is actually charged is the Stripe Price
behind each plan (STRIPE_PRICE_*). Keep the two in step when you change a price: create a new
Stripe Price, point the setting at it, then change the number here. Existing subscribers stay on
their old Price until they change plan.

Currencies: each Stripe Price carries the other currencies as `currency_options` (USD base, plus
GBP, EUR and AUD). PRICE_BOOK below must match those amounts exactly. Checkout charges a business
in its own currency (TenantConfig.currency, set from its country at signup); anything else is
charged in USD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from config import settings

Interval = Literal["month", "year"]

OVERAGE_USD_PER_MIN = 0.25
SETUP_FEE_USD = 149
ANNUAL_MONTHS_CHARGED = 10          # annual billing: 2 months free

BASE_CURRENCY = "USD"
SUPPORTED_CURRENCIES: Tuple[str, ...] = ("USD", "GBP", "EUR", "AUD")

# 💰 Monthly list prices per currency. These are set prices, not live FX conversions: local
# round numbers are easier to sell. ⚠ Decide them yourself and mirror them as currency_options on
# each Stripe Price (monthly) and as 10× on the annual Prices. Business plans are B2B, so prices
# are shown before VAT/GST; Stripe Tax adds it on the invoice.
PRICE_BOOK: Dict[str, Dict[str, int]] = {
    "USD": {"starter": 69, "growth": 149, "pro": 299, "dental_essentials": 349, "dental_pro": 649},
    "GBP": {"starter": 55, "growth": 119, "pro": 239, "dental_essentials": 279, "dental_pro": 499},
    "EUR": {"starter": 65, "growth": 139, "pro": 279, "dental_essentials": 329, "dental_pro": 599},
    "AUD": {"starter": 105, "growth": 229, "pro": 459, "dental_essentials": 549, "dental_pro": 990},
}
OVERAGE_PER_MIN: Dict[str, float] = {"USD": OVERAGE_USD_PER_MIN, "GBP": 0.20, "EUR": 0.24, "AUD": 0.39}
SETUP_FEE: Dict[str, int] = {"USD": SETUP_FEE_USD, "GBP": 119, "EUR": 139, "AUD": 229}
# 💰 Dental plans carry a mandatory one-time onboarding fee (number porting/forwarding, PMS connection,
# treatment menu and fees, voice and greeting tuning, a test-call review). Per plan, per currency.
# ⚠ Placeholders: decide them yourself and mirror them as currency_options on each setup-fee Price.
DENTAL_SETUP_FEES: Dict[str, Dict[str, int]] = {
    "dental_essentials": {"USD": 649, "GBP": 499, "EUR": 599, "AUD": 990},
    "dental_pro": {"USD": 990, "GBP": 749, "EUR": 899, "AUD": 1490},
}

# Approximate USD value of one unit, used only to report revenue and margin in USD on the platform
# Economics page (costs are all in USD). ⚠ Refresh these every quarter; they never touch invoices.
USD_PER_UNIT: Dict[str, float] = {"USD": 1.0, "GBP": 1.27, "EUR": 1.08, "AUD": 0.65}


def billing_currency(currency: Optional[str]) -> str:
    """The currency a business is charged in: its own when we sell in it, else USD."""
    c = (currency or "").upper()
    return c if c in SUPPORTED_CURRENCIES else BASE_CURRENCY


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    monthly_usd: int                 # 0 = custom pricing (sales-led)
    included_minutes: int
    max_users: int
    max_numbers: int
    max_concurrent_calls: int
    tagline: str
    features: Tuple[str, ...] = field(default_factory=tuple)
    warm_transfer: bool = False
    international_transfers: bool = False
    self_serve: bool = True
    recommended: bool = False
    # Empty = offered to every business; otherwise only to these verticals (and they see only these).
    verticals: Tuple[str, ...] = ()
    setup_fee_required: bool = False            # the one-time onboarding fee is part of the plan

    @property
    def annual_usd(self) -> int:
        return self.monthly_usd * ANNUAL_MONTHS_CHARGED

    def monthly_in(self, currency: str) -> int:
        """List price per month in `currency` (0 = custom pricing)."""
        if self.monthly_usd <= 0:
            return 0
        return PRICE_BOOK.get(billing_currency(currency), {}).get(self.key) or self.monthly_usd

    def monthly_revenue_usd(self, currency: str, interval: str = "month") -> float:
        """What this plan earns per month, converted to USD for the platform's margin reports."""
        cur = billing_currency(currency)
        monthly: float = self.monthly_in(cur)
        if interval == "year":
            monthly = monthly * ANNUAL_MONTHS_CHARGED / 12
        return round(monthly * USD_PER_UNIT.get(cur, 1.0), 2)

    def setup_fee_in(self, currency: str) -> int:
        """One-time onboarding fee in `currency` (whole units)."""
        cur = billing_currency(currency)
        own = DENTAL_SETUP_FEES.get(self.key)
        if own:
            return own.get(cur) or own["USD"]
        return SETUP_FEE.get(cur, SETUP_FEE_USD)

    def checkout_ready(self) -> bool:
        """Stripe has what Checkout needs for this plan (its monthly Price, and the setup Price if required)."""
        if not settings.billing_enabled:
            return True
        return bool(price_id(self.key, "month")) and (not self.setup_fee_required or bool(setup_fee_price(self.key)))

    def public(self, currency: str = BASE_CURRENCY) -> Dict[str, object]:
        cur = billing_currency(currency)
        monthly = self.monthly_in(cur)
        return {"key": self.key, "name": self.name, "monthly_usd": self.monthly_usd,
                "annual_usd": self.annual_usd, "currency": cur, "monthly": monthly,
                "annual": monthly * ANNUAL_MONTHS_CHARGED, "included_minutes": self.included_minutes,
                "max_users": self.max_users, "max_numbers": self.max_numbers, "tagline": self.tagline,
                "features": list(self.features), "recommended": self.recommended,
                "setup_fee": self.setup_fee_in(cur), "setup_fee_required": self.setup_fee_required,
                "verticals": list(self.verticals),
                "self_serve": self.self_serve and self.checkout_ready()}


PLANS: Dict[str, Plan] = {
    "starter": Plan(
        key="starter", name="Starter", monthly_usd=69, included_minutes=150, max_users=1, max_numbers=1,
        max_concurrent_calls=2, tagline="Solo operators: answering, messages and FAQs",
        features=("150 minutes / month", "Answers FAQs and takes messages", "Call summaries by e-mail",
                  "1 dashboard user")),
    "growth": Plan(
        key="growth", name="Growth", monthly_usd=149, included_minutes=400, max_users=2, max_numbers=1,
        max_concurrent_calls=3, tagline="Calendar booking, SMS confirmations, summaries", recommended=True,
        features=("400 minutes / month", "Books appointments into your calendar", "SMS confirmations",
                  "Call summaries", "2 dashboard users")),
    # Warm transfer (the AI briefs a staff member before connecting the caller) is built
    # (agent/transfer.py, TransferConfig.warm, needs LIVEKIT_SIP_OUTBOUND_TRUNK_ID) but not yet tested
    # on a live trunk: don't sell it until it is. Then set warm_transfer=True and add it to features.
    "pro": Plan(
        key="pro", name="Pro", monthly_usd=299, included_minutes=1000, max_users=5, max_numbers=3,
        max_concurrent_calls=5, tagline="Several staff and resources, call transfers",
        features=("1,000 minutes / month", "Multiple staff and resources", "Transfers calls to your team",
                  "Priority support", "5 dashboard users")),
    "agency": Plan(
        key="agency", name="Agency / multi-location", monthly_usd=0, included_minutes=0, max_users=0,
        max_numbers=0, max_concurrent_calls=10, tagline="Agencies and chains: pooled minutes, custom terms",
        international_transfers=True, self_serve=False,
        features=("Pooled minutes", "Many locations", "Custom terms")),
    # Dental (AU / UK private and mixed practices): an overflow and after-hours receptionist sold as a
    # monthly retainer with a one-time onboarding fee. 1–3 locations.
    "dental_essentials": Plan(
        key="dental_essentials", name="Dental Essentials", monthly_usd=349, included_minutes=600, max_users=3,
        max_numbers=1, max_concurrent_calls=3, verticals=("dental",), setup_fee_required=True,
        tagline="Lunch-break, after-hours and busy-line cover for one practice",
        features=("600 minutes / month", "Books into Cliniko or Dentally", "Emergency triage and urgent alerts",
                  "Implant and cosmetic lead qualification", "Missed-call text-back", "ROI dashboard",
                  "3 dashboard users")),
    "dental_pro": Plan(
        key="dental_pro", name="Dental Pro", monthly_usd=649, included_minutes=1500, max_users=8, max_numbers=3,
        max_concurrent_calls=6, verticals=("dental",), setup_fee_required=True, recommended=True,
        tagline="Up to three locations and priority support",
        features=("1,500 minutes / month", "Up to 3 practice numbers", "Everything in Essentials",
                  "Priority support", "8 dashboard users")),
}

SELF_SERVE_PLANS: Tuple[str, ...] = tuple(k for k, p in PLANS.items() if p.self_serve)


def get_plan(key: Optional[str]) -> Optional[Plan]:
    return PLANS.get((key or "").lower())


def plans_for(vertical: Optional[str]) -> List[Plan]:
    """What a business of this vertical can buy: its own plans (plus custom/agency) when it has some,
    otherwise every plan that isn't reserved for a vertical."""
    own = [p for p in PLANS.values() if vertical and vertical in p.verticals]
    if own:
        return own + [p for p in PLANS.values() if not p.self_serve and not p.verticals]
    return [p for p in PLANS.values() if not p.verticals]


def plan_allowed(plan: Plan, vertical: Optional[str]) -> bool:
    return any(p.key == plan.key for p in plans_for(vertical))


def setup_fee_price(plan: str) -> str:
    """The Stripe Price for a plan's one-time setup fee (dental plans have their own)."""
    own = {"dental_essentials": settings.STRIPE_PRICE_DENTAL_SETUP_ESSENTIALS,
           "dental_pro": settings.STRIPE_PRICE_DENTAL_SETUP_PRO}.get(plan)
    return (own or settings.STRIPE_PRICE_SETUP_FEE or "").strip()


def _price_settings() -> Dict[Tuple[str, str], str]:
    return {
        ("starter", "month"): settings.STRIPE_PRICE_STARTER_MONTHLY,
        ("starter", "year"): settings.STRIPE_PRICE_STARTER_ANNUAL,
        ("growth", "month"): settings.STRIPE_PRICE_GROWTH_MONTHLY,
        ("growth", "year"): settings.STRIPE_PRICE_GROWTH_ANNUAL,
        ("pro", "month"): settings.STRIPE_PRICE_PRO_MONTHLY,
        ("pro", "year"): settings.STRIPE_PRICE_PRO_ANNUAL,
        ("dental_essentials", "month"): settings.STRIPE_PRICE_DENTAL_ESSENTIALS_MONTHLY,
        ("dental_essentials", "year"): settings.STRIPE_PRICE_DENTAL_ESSENTIALS_ANNUAL,
        ("dental_pro", "month"): settings.STRIPE_PRICE_DENTAL_PRO_MONTHLY,
        ("dental_pro", "year"): settings.STRIPE_PRICE_DENTAL_PRO_ANNUAL,
    }


def price_id(plan: str, interval: str) -> str:
    return (_price_settings().get((plan, interval)) or "").strip()


def plan_for_price(price: str) -> Optional[Tuple[str, Interval]]:
    """Stripe Price id → (plan, interval). None for prices this app doesn't know (e.g. the overage price)."""
    if not price:
        return None
    for (plan, interval), pid in _price_settings().items():
        if pid and pid.strip() == price:
            return plan, interval  # type: ignore[return-value]
    return None


def limits_for(plan: Plan) -> Dict[str, int]:
    """The `tenant.limits` a plan grants. Agency limits are set by hand, so they are left alone."""
    return {"included_minutes": plan.included_minutes, "max_users": plan.max_users,
            "max_numbers": plan.max_numbers, "max_concurrent_calls": plan.max_concurrent_calls}


def catalogue(currency: str = BASE_CURRENCY, vertical: Optional[str] = None) -> Dict[str, object]:
    """Plans and prices for a business: `vertical` picks its plans (dental practices see the dental ones)."""
    cur = billing_currency(currency)
    return {"plans": [p.public(cur) for p in plans_for(vertical)],
            "overage_usd_per_min": OVERAGE_USD_PER_MIN, "setup_fee_usd": SETUP_FEE_USD,
            "overage_per_min": OVERAGE_PER_MIN.get(cur, OVERAGE_USD_PER_MIN),
            "setup_fee": SETUP_FEE.get(cur, SETUP_FEE_USD),
            "trial": {"days": settings.TRIAL_DAYS, "minutes": settings.TRIAL_MINUTES, "card_required": True},
            "annual_months_free": 12 - ANNUAL_MONTHS_CHARGED, "currency": cur,
            "currencies": list(SUPPORTED_CURRENCIES), "tax_note": "Prices exclude VAT/GST and sales tax.",
            "terms_url": settings.TERMS_URL, "privacy_url": settings.PRIVACY_URL}


def plan_keys() -> List[str]:
    return list(PLANS)
