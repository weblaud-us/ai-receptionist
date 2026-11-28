"""
Cost per minute and margin (Roadmap Task 5.1).

💰 Every rate is a list-price estimate from late 2026 vendor pricing pages. ⚠ VERIFY each one
before you publish prices, and recompute quarterly: change the table, the platform page and the
call records pick it up.

`cost_usd` on each call and usage event uses the midpoint of the range, so the platform view shows
an estimate of what the call cost you, not what the vendor will invoice to the cent.

The phone carrier is the one line that depends on the call: its country, landline or mobile, and
direction (an outbound call pays termination *instead of* the inbound rate, never both). That rate
comes from Twilio's pricing API via core/billing/carrier_rates.py; this module only adds it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.billing.plans import OVERAGE_USD_PER_MIN, PLANS, Plan

FIXED_PER_TENANT_MONTH_USD = 3.0      # number, SMS, storage — per business per month


@dataclass(frozen=True)
class CostItem:
    key: str
    label: str
    low: float                         # USD per call-minute
    high: float
    basis: str
    applies: str = "always"            # always | outbound

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


COST_ITEMS: List[CostItem] = [
    # The phone carrier line. This default is a US inbound minute; a call's own rate (its country, landline or
    # mobile, inbound or outbound) replaces it: see `per_minute(carrier=…)` and core/billing/carrier_rates.py.
    CostItem("carrier_inbound", "Phone carrier — Twilio Elastic SIP (US inbound; each call uses its own rate)",
             0.0034, 0.0045, "Twilio trunking, US / Canada local inbound (account rate, Nov 2026)"),
    CostItem("livekit_sip", "LiveKit Cloud — SIP minutes", 0.003, 0.004, "LiveKit pricing (Ship / Scale)"),
    CostItem("livekit_agent", "LiveKit Cloud — agent session minutes", 0.010, 0.010, "LiveKit pricing, beyond included"),
    CostItem("stt", "Speech-to-text — Deepgram Nova-3 streaming", 0.0077, 0.0077, "Deepgram list price"),
    CostItem("llm", "LLM — mini-class model, ~4 turns/min, cached prompt", 0.002, 0.005, "Prompt size and caching"),
    # ~775 characters per minute of speech at $0.037–0.039 per 1k (Scale / Startup plans), the agent speaking
    # 40–50 % of the call. Deepgram Aura (the fallback) costs about the same per minute. ⚠ VERIFY.
    CostItem("tts", "Text-to-speech — Cartesia Sonic (Deepgram Aura fallback)", 0.0116, 0.0155,
             "Cartesia list price, ~775 chars per spoken minute, agent speaks 40–50 % of the call"),
]
# Only a call over the phone network pays the carrier and LiveKit's SIP minutes; a browser or WhatsApp call doesn't.
CARRIER_KEYS = ("carrier_inbound", "livekit_sip")
PHONE_CHANNELS = ("phone", "voice")

# Carrier rates (USD per minute) for when Twilio's pricing API can't be asked (no credentials, Twilio down).
# The live, account-specific rate for the exact number comes from core/billing/carrier_rates.py; refresh this
# table from it with `uv run python -m scripts.carrier_rates --countries US,CA,AU,GB`.
# "in": receiving a call on our number, by its type. "out": calling a landline or a mobile there.
FALLBACK_CARRIER: Dict[str, Dict[str, Any]] = {
    "US": {"in": {"local": 0.0034, "mobile": 0.0034, "toll_free": 0.0130}, "out": {"landline": 0.0100, "mobile": 0.0100},
           "basis": "Twilio Elastic SIP, United States (account rate, Nov 2026)"},
    "CA": {"in": {"local": 0.0045, "mobile": 0.0045, "toll_free": 0.0130}, "out": {"landline": 0.0095, "mobile": 0.0095},
           "basis": "Twilio Elastic SIP, Canada (account rate, Nov 2026)"},
    "AU": {"in": {"local": 0.0060, "mobile": 0.0060, "toll_free": 0.0460}, "out": {"landline": 0.0212, "mobile": 0.0710},
           "basis": "Twilio Elastic SIP, Australia (account rate, Nov 2026)"},
    "GB": {"in": {"local": 0.0060, "mobile": 0.0060, "toll_free": 0.0758}, "out": {"landline": 0.0118, "mobile": 0.0265},
           "basis": "Twilio Elastic SIP, United Kingdom (account rate, Nov 2026)"},
}
# Any other country, when Twilio can't be asked: on the high side, so a guess never looks cheap.
FALLBACK_DEFAULT: Dict[str, Any] = {"in": {"local": 0.0100, "mobile": 0.0100, "toll_free": 0.0500},
                                    "out": {"landline": 0.0500, "mobile": 0.1500},
                                    "basis": "Estimate: no Twilio rate for this country"}
FALLBACK_PREMIUM_USD = 0.50      # a premium-rate or unknown special route, outbound
# The markets the Economics page shows a rate table for.
LAUNCH_COUNTRIES = ("US", "AU", "GB")

# Per outgoing text (confirmations, missed-call recovery). ⚠ VERIFY: AU and UK SMS cost several times US.
SMS_USD_PER_MESSAGE: Dict[str, float] = {"US": 0.0083, "AU": 0.0515, "GB": 0.0463}
# Which market a vertical-specific plan's margin is worked out for (its most expensive launch market).
PLAN_COST_COUNTRY: Dict[str, str] = {"dental": "AU"}

FIXED_MONTHLY: List[Dict[str, object]] = [
    {"label": "LiveKit Cloud plan (Ship)", "low": 50, "high": 50},
    {"label": "MongoDB Atlas M10 + staging", "low": 60, "high": 90},
    {"label": "Redis (managed, small)", "low": 10, "high": 30},
    {"label": "API + worker hosting", "low": 60, "high": 200},
    {"label": "Sentry, logs, status page", "low": 0, "high": 80},
    {"label": "E-mail, domain, misc.", "low": 20, "high": 20},
]


def _out_kind(line_kind: str) -> str:
    """The fallback table's column for an outbound call: anything that may ring a mobile is costed as one."""
    return "mobile" if line_kind in ("mobile", "landline_or_mobile", "voip") else "landline"


def _in_kind(line_kind: str) -> str:
    return {"mobile": "mobile", "toll_free": "toll_free"}.get(line_kind, "local")


def fallback_carrier(country: str, *, outbound: bool, line_kind: str = "") -> Tuple[float, str, bool]:
    """(USD per minute, basis, known country) from the static table, for when Twilio can't be asked."""
    row = FALLBACK_CARRIER.get((country or "").upper())
    table = row or FALLBACK_DEFAULT
    if outbound and line_kind == "premium_rate":
        return FALLBACK_PREMIUM_USD, "Estimate: premium-rate number", bool(row)
    rate = table["out"][_out_kind(line_kind)] if outbound else table["in"][_in_kind(line_kind)]
    return float(rate), str(table["basis"]), bool(row)


def per_minute(*, outbound: bool = False, country: str = "", carrier: Optional[float] = None,
               channel: str = "phone") -> Dict[str, float]:
    """Variable cost of one call-minute: low, mid and high estimates.

    `carrier`: this call's phone-network rate in USD per minute (core/billing/carrier_rates.py), which
    replaces the carrier line. Without it, `country` / `outbound` pick a rate from the fallback table (a
    landline there, or a local number of ours). A call that isn't on the phone network (`channel`
    "browser", "whatsapp") pays no carrier and no SIP minutes."""
    items = list(COST_ITEMS)
    if channel not in PHONE_CHANNELS:
        items = [i for i in items if i.key not in CARRIER_KEYS]
    else:
        if carrier is None and (outbound or country):
            carrier = fallback_carrier(country or "US", outbound=outbound)[0]
        if carrier is not None:
            rate = max(0.0, float(carrier))
            items = [CostItem(i.key, i.label, rate, rate, "This call's rate") if i.key == "carrier_inbound" else i
                     for i in items]
    return {"low": round(sum(i.low for i in items), 4), "mid": round(sum(i.mid for i in items), 4),
            "high": round(sum(i.high for i in items), 4)}


def call_cost_usd(seconds: float, *, outbound: bool = False, carrier: Optional[float] = None,
                  channel: str = "phone", country: str = "") -> float:
    rate = per_minute(outbound=outbound, carrier=carrier, channel=channel, country=country)["mid"]
    return round(max(0.0, seconds) / 60 * rate, 4)


def plan_margin(plan: Plan, cost_per_min: float) -> float:
    """Gross margin at full use of the included minutes (the roadmap's pricing table)."""
    if plan.monthly_usd <= 0:
        return 0.0
    cost = plan.included_minutes * cost_per_min + FIXED_PER_TENANT_MONTH_USD
    return round((plan.monthly_usd - cost) / plan.monthly_usd * 100, 1)


def _plan_country(plan: Plan) -> str:
    return next((PLAN_COST_COUNTRY[v] for v in plan.verticals if v in PLAN_COST_COUNTRY), "")


def _plan_cpm(plan: Plan, default: float) -> float:
    country = _plan_country(plan)
    return per_minute(country=country)["mid"] if country else default


def model() -> Dict[str, object]:
    """Everything the platform 'unit economics' card shows."""
    base = per_minute()
    cpm = base["mid"]
    return {
        "items": [{"key": i.key, "label": i.label, "low": i.low, "high": i.high, "basis": i.basis,
                   "applies": i.applies} for i in COST_ITEMS],
        "per_minute": base,
        "fixed_monthly": FIXED_MONTHLY,
        "fixed_monthly_total": {"low": sum(int(f["low"]) for f in FIXED_MONTHLY),       # type: ignore[call-overload]
                                "high": sum(int(f["high"]) for f in FIXED_MONTHLY)},    # type: ignore[call-overload]
        "fixed_per_tenant_month": FIXED_PER_TENANT_MONTH_USD,
        "plans": [{"key": p.key, "name": p.name, "monthly_usd": p.monthly_usd, "included_minutes": p.included_minutes,
                   "margin_pct": plan_margin(p, _plan_cpm(p, cpm)), "cost_country": _plan_country(p) or "US"}
                  for p in PLANS.values() if p.monthly_usd > 0],
        "sms_usd_per_message": SMS_USD_PER_MESSAGE,
        "overage": {"usd_per_min": OVERAGE_USD_PER_MIN,
                    "margin_pct": round((OVERAGE_USD_PER_MIN - cpm) / OVERAGE_USD_PER_MIN * 100, 1)},
        "note": "List-price estimates (late 2026). Verify every rate before publishing prices; recompute quarterly.",
    }
