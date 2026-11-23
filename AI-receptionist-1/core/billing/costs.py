"""
Cost per minute and margin (Roadmap Task 5.1).

💰 Every rate is a list-price estimate from late 2026 vendor pricing pages. ⚠ VERIFY each one
before you publish prices, and recompute quarterly: change the table, the platform page and the
call records pick it up.

`cost_usd` on each call and usage event uses the midpoint of the range, so the platform view shows
an estimate of what the call cost you, not what the vendor will invoice to the cent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

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
    CostItem("carrier_inbound", "Twilio Elastic SIP — inbound carrier", 0.0035, 0.0055, "Twilio SIP pricing (US zone 1)"),
    CostItem("livekit_sip", "LiveKit Cloud — SIP minutes", 0.003, 0.004, "LiveKit pricing (Ship / Scale)"),
    CostItem("livekit_agent", "LiveKit Cloud — agent session minutes", 0.010, 0.010, "LiveKit pricing, beyond included"),
    CostItem("stt", "Speech-to-text — Deepgram Nova-3 streaming", 0.0077, 0.0077, "Deepgram list price"),
    CostItem("llm", "LLM — mini-class model, ~4 turns/min, cached prompt", 0.002, 0.005, "Prompt size and caching"),
    CostItem("tts", "Text-to-speech — ElevenLabs Flash", 0.012, 0.016, "Agent speaks ~40–50 % of the call"),
    CostItem("carrier_outbound", "Outbound carrier (termination)", 0.01, 0.01, "Termination costs more", applies="outbound"),
]

# Inbound carrier rates outside the US (USD per minute). AU and UK local numbers cost more to receive on
# than US ones. ⚠ VERIFY against Twilio's current AU and UK voice pricing before publishing dental prices.
COUNTRY_CARRIER_INBOUND: Dict[str, Dict[str, object]] = {
    "AU": {"low": 0.010, "high": 0.015, "basis": "Twilio Elastic SIP, Australia inbound (⚠ VERIFY)"},
    "GB": {"low": 0.008, "high": 0.012, "basis": "Twilio Elastic SIP, UK inbound (⚠ VERIFY)"},
}
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


def per_minute(*, outbound: bool = False, country: str = "") -> Dict[str, float]:
    """Variable cost of one call-minute: low, mid and high estimates. `country`: the carrier rate for
    numbers in that country (AU, GB), instead of the US one."""
    items = [i for i in COST_ITEMS if i.applies == "always" or (i.applies == "outbound" and outbound)]
    local = COUNTRY_CARRIER_INBOUND.get((country or "").upper())
    if local:
        items = [CostItem(i.key, i.label, float(local["low"]), float(local["high"]),  # type: ignore[arg-type]
                          str(local["basis"])) if i.key == "carrier_inbound" else i for i in items]
    return {"low": round(sum(i.low for i in items), 4), "mid": round(sum(i.mid for i in items), 4),
            "high": round(sum(i.high for i in items), 4)}


def call_cost_usd(seconds: float, *, outbound: bool = False) -> float:
    return round(max(0.0, seconds) / 60 * per_minute(outbound=outbound)["mid"], 4)


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
        "per_minute_by_country": {c: per_minute(country=c) for c in COUNTRY_CARRIER_INBOUND},
        "sms_usd_per_message": SMS_USD_PER_MESSAGE,
        "overage": {"usd_per_min": OVERAGE_USD_PER_MIN,
                    "margin_pct": round((OVERAGE_USD_PER_MIN - cpm) / OVERAGE_USD_PER_MIN * 100, 1)},
        "note": "List-price estimates (late 2026). Verify every rate before publishing prices; recompute quarterly.",
    }
