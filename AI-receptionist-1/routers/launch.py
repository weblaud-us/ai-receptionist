"""
Pilot, launch and retention API (Roadmap Phase 7, §12).

Business (prefix /t):
GET   /t/insights?days=30                what the assistant did: calls answered, would-have-missed,
                                         bookings and their value, handled without staff, latency
POST  /t/feedback                        owner's 1–10 score + comment (pilot feedback, testimonials)

Platform (prefix /platform):
GET   /platform/pilots                   pilot businesses with their last-7-days scorecard
GET   /platform/reviews/queue            calls waiting for transcript review
GET   /platform/reviews/calls/{tenant_id}/{call_id}   one call with its transcript
POST  /platform/reviews                  verdict + issues + notes for a call
GET   /platform/reviews/stats            reviews and issue categories, eval-case counts
POST  /platform/eval-cases               turn a reviewed call into a redacted eval case (draft)
GET   /platform/eval-cases               list cases · PATCH /platform/eval-cases/{id} approve / reject
GET   /platform/eval-cases/export        approved cases as files for tests/evals/cases/
GET   /platform/margins?days=30          revenue vs cost per business (Task 7.2 "margin per tenant")
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.auth import Principal, TenantPrincipal, require_platform_admin, require_tenant
from core.billing import costs, plans
from core.billing.usage import naive_utc
from core.repositories.base import utcnow
from core.services import insights, reviews
from core.tenancy import resolve_by_id
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

launch_router = APIRouter(prefix="/t", tags=["Results"])
platform_launch_router = APIRouter(prefix="/platform", tags=["Platform"])

_staff = require_tenant("staff")
_member_any = require_tenant("staff", allow_inactive=True)


# ─────────────────────────────────────────────
# BUSINESS
# ─────────────────────────────────────────────

@launch_router.get("/insights")
async def tenant_insights(days: int = Query(default=30, ge=1, le=365), tp: TenantPrincipal = Depends(_staff)):
    since, until = insights.period_for(days)
    report = await insights.value_report(get_db(), tp.tenant, since, until)
    return to_jsonable({**report, "days": days, "pilot": tp.tenant.pilot.active})


class FeedbackBody(BaseModel):
    score: int = Field(ge=1, le=10)
    comment: str = Field(default="", max_length=2000)
    testimonial_ok: bool = False


@launch_router.post("/feedback")
async def tenant_feedback(body: FeedbackBody, request: Request, tp: TenantPrincipal = Depends(_member_any)):
    doc = {"_id": new_id("fb"), "user_id": tp.user.user_id, "role": tp.role,
           "score": body.score, "comment": body.comment.strip(), "testimonial_ok": body.testimonial_ok,
           "pilot": tp.tenant.pilot.active, "at": utcnow()}
    await tp.repos().feedback.insert_one(doc)
    logger.info("Feedback %d/10 from %s", body.score, tp.tenant_id)
    return {"success": True}


# ─────────────────────────────────────────────
# PLATFORM: pilots
# ─────────────────────────────────────────────

@platform_launch_router.get("/pilots")
async def pilots(admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    since, until = insights.period_for(7)
    review_stats = (await reviews.stats(db, days=30))["by_tenant"]
    out: List[Dict[str, Any]] = []
    async for doc in db.tenants.find({"pilot.active": True}):
        try:
            t = TenantConfig.from_doc(doc)
        except Exception:
            continue
        report = await insights.value_report(db, t, since, until)
        out.append({"id": t.id, "name": t.name, "vertical": t.vertical, "status": t.status,
                    "answering_mode": t.answering_mode, "pilot": t.pilot.model_dump(),
                    "last_7_days": report, "reviews_30d": review_stats.get(t.id, {"reviewed": 0, "issues": 0})})
    return to_jsonable({"pilots": out})


# ─────────────────────────────────────────────
# PLATFORM: transcript review and eval cases
# ─────────────────────────────────────────────

@platform_launch_router.get("/reviews/queue")
async def review_queue(tenant_id: Optional[str] = None, days: int = Query(default=14, ge=1, le=90),
                       limit: int = Query(default=50, ge=1, le=200), admin: Principal = Depends(require_platform_admin)):
    return to_jsonable({"calls": await reviews.review_queue(get_db(), tenant_id=tenant_id, days=days, limit=limit)})


@platform_launch_router.get("/reviews/calls/{tenant_id}/{call_id}")
async def review_call(tenant_id: str, call_id: str, admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    call = await db.calls.find_one({"tenant_id": tenant_id, "call_id": call_id}, {"_id": 0})  # scoped: tenant_id
    if call is None:
        raise HTTPException(404, "Call not found")
    review = await db[reviews.REVIEWS].find_one({"call_id": call_id}, {"_id": 0})
    return to_jsonable({"call": call, "review": review})


class ReviewBody(BaseModel):
    tenant_id: str
    call_id: str
    verdict: Literal["good", "issue"]
    issues: List[str] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=2000)
    expected: str = Field(default="", max_length=1000)


@platform_launch_router.post("/reviews")
async def save_review(body: ReviewBody, admin: Principal = Depends(require_platform_admin)):
    try:
        doc = await reviews.save_review(get_db(), tenant_id=body.tenant_id, call_id=body.call_id, verdict=body.verdict,
                                        issues=body.issues, notes=body.notes, expected=body.expected,
                                        reviewer=admin.email or admin.user_id)
    except LookupError:
        raise HTTPException(404, "Call not found")
    return to_jsonable({"review": doc})


@platform_launch_router.get("/reviews/stats")
async def review_stats(days: int = Query(default=30, ge=1, le=365), admin: Principal = Depends(require_platform_admin)):
    return to_jsonable(await reviews.stats(get_db(), days=days))


class CaseBody(BaseModel):
    tenant_id: str
    call_id: str
    title: str = Field(min_length=3, max_length=120)
    expected: str = Field(min_length=10, max_length=1000)     # what a good assistant does — the judge's rubric
    must_call: List[str] = Field(default_factory=list, max_length=5)
    must_not_call: List[str] = Field(default_factory=list, max_length=5)


@platform_launch_router.post("/eval-cases")
async def create_eval_case(body: CaseBody, admin: Principal = Depends(require_platform_admin)):
    tenant = await resolve_by_id(body.tenant_id)
    if tenant is None:
        raise HTTPException(404, "Business not found")
    try:
        case = await reviews.case_from_call(get_db(), tenant, body.call_id, title=body.title, expected=body.expected,
                                            must_call=body.must_call, must_not_call=body.must_not_call,
                                            author=admin.email or admin.user_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return to_jsonable({"case": {**reviews.case_file(case), "status": case["status"]}})


@platform_launch_router.get("/eval-cases")
async def list_eval_cases(status: Optional[str] = None, admin: Principal = Depends(require_platform_admin)):
    q = {"status": status} if status else {}
    rows = await get_db()[reviews.CASES].find(q).sort("created_at", -1).limit(500).to_list(500)
    return to_jsonable({"cases": [{**reviews.case_file(r), "status": r["status"], "created_at": r.get("created_at"),
                                   "author": r.get("author", "")} for r in rows]})


class CaseStatus(BaseModel):
    status: Literal["approved", "rejected", "draft"]
    expected: Optional[str] = Field(default=None, max_length=1000)
    caller_turns: Optional[List[str]] = Field(default=None, max_length=12)   # fix redaction by hand before approving


@platform_launch_router.patch("/eval-cases/{case_id}")
async def update_eval_case(case_id: str, body: CaseStatus, admin: Principal = Depends(require_platform_admin)):
    sets: Dict[str, Any] = {"status": body.status, "updated_at": utcnow(), "reviewed_by": admin.email or admin.user_id}
    if body.expected is not None:
        sets["expect.intent"] = body.expected
    if body.caller_turns is not None:
        turns = [t.strip()[:1000] for t in body.caller_turns if t.strip()]
        if not turns:
            raise HTTPException(422, "A case needs at least one caller turn")
        sets["caller_turns"] = turns
    res = await get_db()[reviews.CASES].update_one({"_id": case_id}, {"$set": sets})
    if res.matched_count == 0:
        raise HTTPException(404, "Case not found")
    return {"success": True}


@platform_launch_router.get("/eval-cases/export")
async def export_eval_cases(admin: Principal = Depends(require_platform_admin)):
    rows = await get_db()[reviews.CASES].find({"status": "approved"}).sort("created_at", 1).to_list(2000)
    return to_jsonable({"cases": [reviews.case_file(r) for r in rows]})


# ─────────────────────────────────────────────
# PLATFORM: margin per business
# ─────────────────────────────────────────────

def monthly_revenue(t: TenantConfig) -> Optional[float]:
    """List price per month the business pays us; None = invoiced outside Stripe (manual) — unknown here."""
    b = t.billing
    if b.plan == "manual":
        return None
    if t.status not in ("active", "past_due"):
        return 0.0                                     # trial, incomplete, suspended, cancelled
    plan = plans.get_plan(b.plan)
    if plan is None:
        return None
    return plan.monthly_revenue_usd(t.currency, b.interval)       # local list price, in USD


@platform_launch_router.get("/margins")
async def margins(days: int = Query(default=30, ge=7, le=90), admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    since = utcnow() - timedelta(days=days)
    usage: Dict[str, Dict[str, float]] = {}
    async for row in db.usage_events.aggregate([  # cross-tenant: margin per business (platform admin)
        {"$match": {"kind": "voice_minute", "at": {"$gte": naive_utc(since)}}},
        {"$group": {"_id": "$tenant_id", "seconds": {"$sum": {"$ifNull": ["$seconds", 0]}},
                    "cost": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
                    "billable_seconds": {"$sum": {"$cond": [{"$eq": ["$billable", False]}, 0,
                                                            {"$ifNull": ["$seconds", 0]}]}}}},
    ]):
        usage[str(row["_id"])] = row
    scale = days / 30.0
    fixed = costs.FIXED_PER_TENANT_MONTH_USD * scale
    rows: List[Dict[str, Any]] = []
    async for doc in db.tenants.find({"status": {"$ne": "cancelled"}}):
        try:
            t = TenantConfig.from_doc(doc)
        except Exception:
            continue
        u = usage.get(t.id, {})
        minutes = float(u.get("seconds", 0)) / 60
        cost = round(float(u.get("cost", 0)) + fixed, 2)
        monthly = monthly_revenue(t)
        revenue: Optional[float] = None
        if monthly is not None:
            overage_min = max(0.0, float(u.get("billable_seconds", 0)) / 60 - t.limits.included_minutes * scale)
            cur = plans.billing_currency(t.currency)
            overage_usd = plans.OVERAGE_PER_MIN.get(cur, plans.OVERAGE_USD_PER_MIN) * plans.USD_PER_UNIT.get(cur, 1.0)
            revenue = round(monthly * scale + (overage_min * overage_usd if monthly else 0), 2)
        margin = round(revenue - cost, 2) if revenue is not None else None
        rows.append({"id": t.id, "name": t.name, "plan": t.billing.plan, "status": t.status, "pilot": t.pilot.active,
                     "minutes": round(minutes), "cost_usd": cost, "revenue_usd": revenue, "margin_usd": margin,
                     "margin_pct": round(100 * margin / revenue, 1) if revenue else None,
                     "flag": "loss" if margin is not None and margin < 0 and t.status in ("active", "past_due") else ""})
    rows.sort(key=lambda r: (r["margin_usd"] is None, r["margin_usd"] if r["margin_usd"] is not None else 0))
    known = [r for r in rows if r["revenue_usd"] is not None]
    return {"days": days, "fixed_per_business_usd": round(fixed, 2), "businesses": rows,
            "totals": {"revenue_usd": round(sum(r["revenue_usd"] for r in known), 2),
                       "cost_usd": round(sum(r["cost_usd"] for r in rows), 2),
                       "margin_usd": round(sum(r["margin_usd"] for r in known), 2)}}
