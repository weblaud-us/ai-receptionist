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
    applies: str = "always"            # always | recording | outbound

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
    CostItem("recording", "Recording (egress + storage)", 0.005, 0.01, "⚠ VERIFY egress pricing", applies="recording"),
    CostItem("carrier_outbound", "Outbound carrier (termination)", 0.01, 0.01, "Termination costs more", applies="outbound"),
]

FIXED_MONTHLY: List[Dict[str, object]] = [
    {"label": "LiveKit Cloud plan (Ship)", "low": 50, "high": 50},
    {"label": "MongoDB Atlas M10 + staging", "low": 60, "high": 90},
    {"label": "Redis (managed, small)", "low": 10, "high": 30},
    {"label": "API + worker hosting", "low": 60, "high": 200},
    {"label": "Sentry, logs, status page", "low": 0, "high": 80},
    {"label": "E-mail, domain, misc.", "low": 20, "high": 20},
]


def per_minute(*, recording: bool = False, outbound: bool = False) -> Dict[str, float]:
    """Variable cost of one call-minute: low, mid and high estimates."""
    items = [i for i in COST_ITEMS if i.applies == "always"
             or (i.applies == "recording" and recording) or (i.applies == "outbound" and outbound)]
    return {"low": round(sum(i.low for i in items), 4), "mid": round(sum(i.mid for i in items), 4),
            "high": round(sum(i.high for i in items), 4)}


def call_cost_usd(seconds: float, *, recording: bool = False, outbound: bool = False) -> float:
    return round(max(0.0, seconds) / 60 * per_minute(recording=recording, outbound=outbound)["mid"], 4)


def plan_margin(plan: Plan, cost_per_min: float) -> float:
    """Gross margin at full use of the included minutes (the roadmap's pricing table)."""
    if plan.monthly_usd <= 0:
        return 0.0
    cost = plan.included_minutes * cost_per_min + FIXED_PER_TENANT_MONTH_USD
    return round((plan.monthly_usd - cost) / plan.monthly_usd * 100, 1)


def model() -> Dict[str, object]:
    """Everything the platform 'unit economics' card shows."""
    base = per_minute()
    cpm = base["mid"]
    return {
        "items": [{"key": i.key, "label": i.label, "low": i.low, "high": i.high, "basis": i.basis,
                   "applies": i.applies} for i in COST_ITEMS],
        "per_minute": base,
        "per_minute_with_recording": per_minute(recording=True),
        "fixed_monthly": FIXED_MONTHLY,
        "fixed_monthly_total": {"low": sum(int(f["low"]) for f in FIXED_MONTHLY),       # type: ignore[call-overload]
                                "high": sum(int(f["high"]) for f in FIXED_MONTHLY)},    # type: ignore[call-overload]
        "fixed_per_tenant_month": FIXED_PER_TENANT_MONTH_USD,
        "plans": [{"key": p.key, "name": p.name, "monthly_usd": p.monthly_usd, "included_minutes": p.included_minutes,
                   "margin_pct": plan_margin(p, cpm)} for p in PLANS.values() if p.monthly_usd > 0],
        "overage": {"usd_per_min": OVERAGE_USD_PER_MIN,
                    "margin_pct": round((OVERAGE_USD_PER_MIN - cpm) / OVERAGE_USD_PER_MIN * 100, 1)},
        "note": "List-price estimates (late 2026). Verify every rate before publishing prices; recompute quarterly.",
    }
