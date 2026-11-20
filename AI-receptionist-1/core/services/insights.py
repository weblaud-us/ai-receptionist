"""
What the assistant did for a business: the pilot scorecard (Roadmap Task 7.1) and the monthly
"your assistant this month" report (Task 7.3, retention).

    value_report(db, tenant, since, until)   → the numbers below, for any period
    report_text(tenant, report, title)       → the same as a plain-text e-mail

| Metric                       | How it's counted                                                       |
|------------------------------|------------------------------------------------------------------------|
| calls_answered               | inbound phone calls the assistant handled (test calls, busy/unavailable and unassigned excluded) |
| would_have_missed            | overflow / after-hours forwarding: every answered call; otherwise calls while the business was closed |
| bookings, est_booking_value  | appointments made by the assistant (not the dashboard); value from the booked service price |
| leads, messages              | job requests and messages taken                                         |
| handled_without_human_pct    | answered calls not transferred to staff                                 |
| transfers                    | calls handed to staff                                                   |
| latency_p50_s                | median of each call's median response time                              |
| unanswered_questions         | questions it couldn't answer — the to-do list for the knowledge base     |
| satisfaction                 | the owner's latest 1–10 score (POST /t/feedback)                        |

Dental practices also get a `dental` block (the ROI a monthly retainer is sold on):

| Metric                          | How it's counted                                                    |
|---------------------------------|---------------------------------------------------------------------|
| overflow_calls_answered         | answered calls in a lunch break, after hours, or forwarded because reception was busy (`answer_window` / `overflow` on the call; older calls: `after_hours`) |
| by_window                       | the same, split into lunch_break / after_hours / busy_overflow       |
| consultations_booked            | AI-made, not-cancelled appointments for a cosmetic or implant service |
| emergency_triages_captured      | `dental_emergency` leads                                             |
| cosmetic_leads                  | `cosmetic_consult` leads (qualified implant and cosmetic enquiries)  |
| missed_call_sms_recoveries      | missed-call texts sent (`calls.recovery_sent_at`)                    |
| estimated_pipeline_value_minor  | booked consultations × their service's estimated value, plus qualified leads' values, never counting a lead and a booking from the same call twice; minor units of `currency` |
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from config import settings
from core.repositories import Repos
from core.tenancy.models import TenantConfig
from core.timeutils import business_now

NOT_ANSWERED = frozenset({"unassigned", "busy", "unavailable"})
MAX_CALLS = 50_000
HIGH_VALUE_CATEGORIES = ("cosmetic", "implant")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: Any) -> Any:
    return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


async def latest_feedback(db: Any, tenant_id: str) -> Optional[Dict[str, Any]]:
    rows = await Repos(db, tenant_id).feedback.find({}, sort=[("at", -1)], limit=1)
    return rows[0] if rows else None


async def value_report(db: Any, tenant: TenantConfig, since: datetime, until: datetime) -> Dict[str, Any]:
    repos = Repos(db, tenant.id)
    q_time = {"$gte": since, "$lt": until}
    calls = await repos.calls.find(
        {"started_at": q_time, "direction": {"$ne": "outbound"}}, limit=MAX_CALLS,
        projection={"outcome": 1, "after_hours": 1, "transfer": 1, "latency": 1, "duration_s": 1, "channel": 1,
                    "started_at": 1, "answer_window": 1, "overflow": 1, "recovery_sent_at": 1})
    answered = [c for c in calls if (c.get("channel") or "phone") == "phone" and c.get("outcome") not in NOT_ANSWERED]
    if tenant.answering_mode in ("overflow", "after_hours"):
        missed = len(answered)
    else:
        missed = sum(1 for c in answered if c.get("after_hours") is True)
    transfers = sum(1 for c in answered if c.get("transfer") == "ok" or c.get("outcome") == "transferred")
    p50s: List[float] = []
    for c in answered:
        p50 = (c.get("latency") or {}).get("p50_s")
        if isinstance(p50, (int, float)):
            p50s.append(float(p50))

    appts = await repos.appointments.find(
        {"created_at": q_time, "source": {"$nin": ["dashboard", "import"]}}, limit=MAX_CALLS,
        projection={"price": 1, "status": 1, "service_id": 1, "call_id": 1})
    value_cents = 0
    for a in appts:
        price = a.get("price") or {}
        if isinstance(price, dict) and isinstance(price.get("amount"), (int, float)) and a.get("status") != "cancelled":
            value_cents += int(price["amount"])

    leads = await repos.leads.count({"created_at": q_time})
    messages = await repos.messages.count({"created_at": q_time})
    unanswered = await repos.unanswered.count({"created_at": q_time})
    minutes = round(sum(float(c.get("duration_s") or 0) for c in answered) / 60)

    by_day: Dict[str, int] = {}
    for c in answered:
        started = _aware(c.get("started_at"))
        if isinstance(started, datetime):
            day = business_now(tenant.timezone, started).date().isoformat()
            by_day[day] = by_day.get(day, 0) + 1

    fb = await latest_feedback(db, tenant.id)
    dental = await _dental_report(repos, tenant, q_time, calls, answered, appts) if tenant.vertical == "dental" else None
    return {
        **({"dental": dental} if dental is not None else {}),
        "since": since, "until": until, "answering_mode": tenant.answering_mode,
        "calls_answered": len(answered), "minutes": minutes,
        "would_have_missed": missed,
        "bookings": len(appts), "est_booking_value": round(value_cents / 100, 2), "currency": tenant.currency,
        "leads": leads, "messages": messages,
        "transfers": transfers,
        "handled_without_human_pct": _pct(len(answered) - transfers, len(answered)),
        "latency_p50_s": round(statistics.median(p50s), 2) if p50s else None,
        "unanswered_questions": unanswered,
        "satisfaction": ({"score": fb.get("score"), "at": fb.get("at"), "comment": fb.get("comment", "")} if fb else None),
        "by_day": [{"day": d, "calls": n} for d, n in sorted(by_day.items())],
    }


def call_window(c: Dict[str, Any]) -> Optional[str]:
    """lunch_break | after_hours | busy_overflow for a call the business couldn't have taken, else None."""
    if c.get("overflow") is True:
        return "busy_overflow"
    window = c.get("answer_window")
    if window in ("lunch_break", "after_hours"):
        return str(window)
    if window is None and c.get("after_hours") is True:          # calls from before answer_window existed
        return "after_hours"
    return None


async def _dental_report(repos: Repos, tenant: TenantConfig, q_time: Dict[str, Any], calls: List[Dict[str, Any]],
                         answered: List[Dict[str, Any]], appts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_window = {"lunch_break": 0, "after_hours": 0, "busy_overflow": 0}
    for c in answered:
        w = call_window(c)
        if w:
            by_window[w] += 1
    services = {s["_id"]: s for s in await repos.services.find(
        {}, limit=1000, projection={"category": 1, "estimated_value_minor": 1})}
    consult_value = 0
    consult_calls = set()
    consultations = 0
    for a in appts:
        svc = services.get(a.get("service_id")) or {}
        if a.get("status") == "cancelled" or svc.get("category") not in HIGH_VALUE_CATEGORIES:
            continue
        consultations += 1
        if a.get("call_id"):
            consult_calls.add(a["call_id"])
        if isinstance(svc.get("estimated_value_minor"), int):
            consult_value += svc["estimated_value_minor"]
    leads = await repos.leads.find({"created_at": q_time, "kind": {"$in": ["dental_emergency", "cosmetic_consult"]}},
                                   limit=MAX_CALLS, projection={"kind": 1, "estimated_value_minor": 1, "call_id": 1})
    cosmetic = [x for x in leads if x.get("kind") == "cosmetic_consult"]
    lead_value = sum(int(x["estimated_value_minor"]) for x in cosmetic
                     if isinstance(x.get("estimated_value_minor"), int)
                     and not (x.get("call_id") and x["call_id"] in consult_calls))   # booked: counted once, above
    return {
        "overflow_calls_answered": sum(by_window.values()),
        "by_window": by_window,
        "consultations_booked": consultations,
        "emergency_triages_captured": sum(1 for x in leads if x.get("kind") == "dental_emergency"),
        "cosmetic_leads": len(cosmetic),
        "missed_call_sms_recoveries": sum(1 for c in calls if c.get("recovery_sent_at")),
        **(await _recall_stats(repos, tenant, q_time)),
        "estimated_pipeline_value_minor": consult_value + lead_value,
        "currency": tenant.currency,
    }


async def _recall_stats(repos: Repos, tenant: TenantConfig, q_time: Dict[str, Any]) -> Dict[str, int]:
    from core.services import reactivation

    return await reactivation.stats(tenant, q_time["$gte"], q_time["$lt"], db=repos.db)


def money_minor(amount_minor: int, currency: str) -> str:
    """12345600, AUD -> "A$123,456" (whole units; for reports, not invoices)."""
    symbol = {"AUD": "A$", "GBP": "£", "USD": "$", "EUR": "€", "NZD": "NZ$", "CAD": "C$"}.get(currency.upper(), "")
    units = f"{amount_minor / 100:,.0f}"
    return f"{symbol}{units}" if symbol else f"{units} {currency}"


def period_for(days: int, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    return now - timedelta(days=days), now


def previous_month(tenant: TenantConfig, now: Optional[datetime] = None) -> tuple[datetime, datetime, str]:
    """[first day of last month, first day of this month) in business-local time, as UTC, plus 'YYYY-MM'."""
    local = business_now(tenant.timezone, now)
    this_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return last_month.astimezone(timezone.utc), this_month.astimezone(timezone.utc), last_month.strftime("%Y-%m")


def report_text(tenant: TenantConfig, r: Dict[str, Any], title: str, *, unanswered: Sequence[str] = ()) -> str:
    money = f"{r['est_booking_value']:,.0f} {r['currency']}" if r["est_booking_value"] else ""
    lines = [title, ""]
    if not r["calls_answered"] and not r["bookings"]:
        lines.append("Your assistant didn't answer any calls in this period. If that's unexpected, check that "
                     "call forwarding is on (Phone numbers in the dashboard).")
    else:
        lines += [f"• {r['calls_answered']} calls answered ({r['minutes']} minutes)",
                  f"• {r['would_have_missed']} of them would otherwise have been missed"
                  + (" (after hours)" if tenant.answering_mode == "all_calls" else ""),
                  f"• {r['bookings']} bookings made by your assistant" + (f", worth about {money}" if money else ""),
                  f"• {r['leads'] + r['messages']} messages and job requests taken",
                  f"• {r['transfers']} calls passed to your team"]
        if r["handled_without_human_pct"] is not None:
            lines.append(f"• {r['handled_without_human_pct']:.0f}% handled start to finish without your team")
        if r["latency_p50_s"] is not None:
            lines.append(f"• Typical response time: {r['latency_p50_s']:.1f} seconds")
        d = r.get("dental")
        if d:
            w = d["by_window"]
            lines += ["", "For your practice:",
                      f"• {d['overflow_calls_answered']} calls answered while reception couldn't "
                      f"({w['lunch_break']} at lunch, {w['after_hours']} after hours, {w['busy_overflow']} while busy)",
                      f"• {d['consultations_booked']} implant and cosmetic consultations booked",
                      f"• {d['cosmetic_leads']} treatment enquiries qualified for your coordinator",
                      f"• {d['emergency_triages_captured']} dental emergencies triaged",
                      f"• {d['missed_call_sms_recoveries']} missed callers texted a booking link"]
            if d.get("recall_texts_sent"):
                lines.append(f"• {d['recall_texts_sent']} overdue patients sent a recall text, "
                             f"{d['reactivation_bookings_attributed']} booked within 14 days")
            if d["estimated_pipeline_value_minor"]:
                lines.append("• Estimated treatment pipeline: "
                             + money_minor(d["estimated_pipeline_value_minor"], d["currency"]))
    if unanswered:
        lines += ["", "Questions it couldn't answer — add the answers in Knowledge and it will next time:"]
        lines += [f"  – {q}" for q in unanswered[:10]]
    if settings.DASHBOARD_URL:
        lines += ["", f"See every call: {settings.DASHBOARD_URL.rstrip('/')}/results"]
    lines += ["", "You get this e-mail because you're the owner contact for this business. "
                  "Turn it off under Settings → Notifications."]
    return "\n".join(lines)
