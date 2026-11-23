"""
Tenant API (Roadmap Task 2.10) — prefix /t. Everything here acts on the caller's active business.

Roles: staff = day-to-day (calls, messages, appointments, contacts); admin = settings, services,
staff, knowledge; owner = team and billing. Platform admins pass every check (X-Tenant-Id).

GET   /t/context                         tenant, role, vertical labels (the dashboard boots from this)
GET   /t/overview                        KPIs: calls today, bookings, messages, minutes used
GET   /t/settings · PATCH /t/settings    editable business + assistant settings (audited)
POST  /t/onboarding/template             apply a vertical's example services and FAQs
POST  /t/onboarding/step                 save wizard progress
GET   /t/verticals                       vertical catalog for the wizard
CRUD  /t/services /t/resources /t/exceptions /t/knowledge
GET   /t/knowledge/suggestions · POST /t/knowledge/test   unanswered common questions · try a caller's question
GET   /t/unanswered · POST …/{id}/answer · POST …/{id}/dismiss
POST  /t/knowledge/import                draft Q&A from the business's website (owner approves each)
POST  /t/onboarding/site-import         read the website, draft profile / hours / services / FAQs (nothing saved)
POST  /t/onboarding/apply-import        save the parts of that draft the owner kept
GET   /t/appointments · POST · PATCH /t/appointments/{id} · GET /t/slots
GET   /t/contacts · GET/PATCH /t/contacts/{id}
GET   /t/whatsapp/status · /t/whatsapp/templates · GET/POST /t/contacts/{id}/whatsapp   message a patient
GET   /t/calls · GET /t/calls/live · GET /t/calls/{call_id}
GET   /t/messages · PATCH /t/messages/{id} · GET /t/leads · PATCH /t/leads/{id}
GET   /t/phone-numbers                   numbers + call-forwarding instructions
GET   /t/team · POST /t/team/invite · PATCH/DELETE /t/team/{user_id}
POST  /t/test-call                       signed browser-call link to talk to your own assistant
GET   /t/audit                           settings changes, exports, deletions
GET   /t/integrations · Google Calendar connect/sync/disconnect
GET   /t/integrations/webhooks · POST · DELETE …/{id} · POST …/{id}/test   outgoing webhooks
GET   /t/voices                          voice catalogue with audio previews (ElevenLabs)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config import settings
from core.auth import TenantPrincipal, require_tenant, service as auth_service
from core.integrations import whatsapp as wa_api
from core.phone import digits
from core.integrations.email import send_email
from core.prompts.verticals import get_vertical, vertical_catalog
from core.repositories.base import utcnow
from core.repositories.repos import PhoneNumbersStore, UsersStore
from core.repositories.scoped import scoped_db
from core.sensitive import drop_ciphertext, unseal
from core import ratelimit
from core.services import availability as av
from core.services import site_import
from core.services import whatsapp_outbox as wa_outbox
from core.services.scheduling import SchedulingError, SchedulingService
from core.services.tenant_admin import EDITABLE_FIELDS, apply_template, update_tenant
from core.signed_links import build_call_link
from core.tenancy.ids import new_id
from core.tenancy.models import HoursEntry, TenantConfig
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

tenant_router = APIRouter(prefix="/t", tags=["Tenant"])
_staff = require_tenant("staff")
_admin = require_tenant("admin")
_owner = require_tenant("owner")
_staff_any_status = require_tenant("staff", allow_inactive=True)   # the app shell of a suspended business
_dentist = require_tenant("dentist")                                # their own day; staff and up see any dentist's
_member_any_status = require_tenant("dentist", allow_inactive=True)  # the app shell, for every role

LIVE_CALL_STALE_S = 120
_HIDDEN_TENANT_FIELDS = ("billing",)


def _clean(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not doc:
        return {}
    doc = drop_ciphertext(dict(doc))                 # encrypted fields never leave the server
    _id = doc.pop("_id", None)
    doc.pop("tenant_id", None)
    if _id is not None and "id" not in doc:
        doc["id"] = str(_id)
    return to_jsonable(doc)


def _by_id(raw: str) -> Dict[str, Any]:
    """Filter for a row by the id the API returned. Rows created by an upsert or a plain insert before
    ids were generated explicitly have an ObjectId `_id`; match those too."""
    if ObjectId.is_valid(raw):
        return {"_id": {"$in": [raw, ObjectId(raw)]}}
    return {"_id": raw}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _zone(tp: TenantPrincipal) -> ZoneInfo:
    try:
        return ZoneInfo(tp.tenant.timezone)
    except Exception:
        return ZoneInfo("UTC")


def _local_midnight_utc(tp: TenantPrincipal, day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_zone(tp)).astimezone(timezone.utc)


def _today(tp: TenantPrincipal) -> date:
    return datetime.now(_zone(tp)).date()


def _not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(404, what)


# ─────────────────────────────────────────────
# CONTEXT, OVERVIEW, SETTINGS
# ─────────────────────────────────────────────

def _tenant_view(tp: TenantPrincipal) -> Dict[str, Any]:
    doc = tp.tenant.to_doc()
    for k in _HIDDEN_TENANT_FIELDS:
        doc.pop(k, None)
    google = (doc.get("integrations") or {}).get("google_calendar") or {}
    google.pop("refresh_token_enc", None)            # encrypted, but never leaves the server
    ((doc.get("integrations") or {}).get("pms") or {}).pop("api_key_enc", None)    # the PMS key, likewise
    for hook in (doc.get("integrations") or {}).get("webhooks") or []:
        hook.pop("secret", None)                     # shown once, at creation
    doc["id"] = doc.pop("_id")
    return to_jsonable(doc)


def _billing_brief(tp: TenantPrincipal) -> Dict[str, Any]:
    """What the dashboard banner needs; the full picture is GET /t/billing (owners)."""
    from core.billing import usage

    b = tp.tenant.billing
    return {"plan": b.plan, "status": tp.tenant.status, "self_serve": b.self_serve,
            "trial_ends_at": to_jsonable(b.trial_ends_at), "delete_after": to_jsonable(b.delete_after),
            "cancel_at_period_end": b.cancel_at_period_end, "gate": usage.gate(tp.tenant),
            "billing_enabled": settings.billing_enabled}


@tenant_router.get("/context")
async def context(tp: TenantPrincipal = Depends(_member_any_status)):
    spec = get_vertical(tp.tenant.vertical)
    return {
        "billing": _billing_brief(tp),
        "tenant": _tenant_view(tp),
        "role": tp.role,
        "platform_admin": tp.user.platform_admin,
        "legacy": tp.tenant.is_legacy,
        "vertical": {"key": spec.key, "label": spec.label, "labels": spec.labels,
                     "uses_scheduling": spec.uses_scheduling, "tools": list(spec.tools)},
        "greeting": tp.tenant.greeting_text(),
        "sms_enabled": settings.SMS_ENABLED,           # the dashboard hides SMS-only options when False
    }


@tenant_router.get("/overview")
async def overview(tp: TenantPrincipal = Depends(_staff)):
    sdb = scoped_db(tp.tenant)
    repos = tp.repos()
    today = _today(tp)
    day_start = _local_midnight_utc(tp, today)
    month_start = _local_midnight_utc(tp, today.replace(day=1))
    since_30 = day_start - timedelta(days=29)

    calls_today = await sdb.calls.count_documents({"started_at": {"$gte": day_start}})
    outcomes: Dict[str, int] = {}
    async for row in sdb.calls.aggregate([
        {"$match": {"started_at": {"$gte": since_30}}},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}}},
    ]):
        outcomes[str(row["_id"] or "completed")] = row["n"]
    minutes = 0.0
    async for row in sdb.calls.aggregate([
        {"$match": {"started_at": {"$gte": month_start}}},
        {"$group": {"_id": None, "s": {"$sum": {"$ifNull": ["$duration_s", 0]}}}},
    ]):
        minutes = round((row.get("s") or 0) / 60, 1)

    per_day: Dict[str, int] = {}
    async for row in sdb.calls.aggregate([
        {"$match": {"started_at": {"$gte": since_30}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at",
                                               "timezone": tp.tenant.timezone}}, "n": {"$sum": 1}}},
    ]):
        per_day[row["_id"]] = row["n"]
    series = [{"date": (today - timedelta(days=i)).isoformat(),
               "calls": per_day.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(29, -1, -1)]

    if get_vertical(tp.tenant.vertical).uses_scheduling:
        bookings_today = await repos.appointments.count({"created_at": {"$gte": day_start}, "source": {"$ne": "dashboard"}})
        upcoming = await repos.appointments.find({"status": {"$in": ["confirmed", "pending"]},
                                                  "start": {"$gte": utcnow()}}, sort=[("start", 1)], limit=5)
    else:
        bookings_today = await sdb.bookings.count_documents({"created_at": {"$gte": day_start.replace(tzinfo=None)}})
        upcoming = []

    recent = await sdb.calls.find({}, {"transcript": 0, "turns": 0}).sort("started_at", -1).limit(6).to_list(6)
    answered = sum(outcomes.values())
    handled = answered - outcomes.get("hangup", 0) - outcomes.get("spam", 0) - outcomes.get("unassigned", 0) - outcomes.get("busy", 0)
    return {
        "today": today.isoformat(),
        "calls_today": calls_today,
        "bookings_today": bookings_today,
        "messages_new": await repos.messages.count({"status": "new"}),
        "leads_new": await repos.leads.count({"status": "new"}),
        "unanswered_open": await repos.unanswered.count({"status": "open"}),
        "minutes_used": minutes,
        "minutes_included": tp.tenant.limits.included_minutes,
        "calls_30d": answered,
        "handled_rate_pct": round(handled / answered * 100, 1) if answered else None,
        "outcomes_30d": outcomes,
        "series": series,
        "recent_calls": [_clean(c) for c in recent],
        "upcoming": [_clean(a) for a in upcoming],
    }


@tenant_router.get("/settings")
async def get_settings(tp: TenantPrincipal = Depends(_staff)):
    return {"tenant": _tenant_view(tp), "editable": sorted(EDITABLE_FIELDS), "can_edit": tp.can("admin")}


@tenant_router.patch("/settings")
async def patch_settings(body: Dict[str, Any], request: Request, tp: TenantPrincipal = Depends(_admin)):
    if tp.tenant.is_legacy:
        raise HTTPException(409, "This deployment still runs from .env. Run migration m0003 to make settings editable.")
    try:
        updated = await update_tenant(tp.tenant, body, allowed=EDITABLE_FIELDS, user_id=tp.user.user_id, ip=_ip(request))
    except ValueError as e:
        raise HTTPException(422, str(e)[:500])
    return {"tenant": _tenant_view(TenantPrincipal(tp.user, updated, tp.role)), "greeting": updated.greeting_text()}


class TemplateBody(BaseModel):
    vertical: str
    replace: bool = False


@tenant_router.post("/onboarding/template")
async def onboarding_template(body: TemplateBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    if tp.tenant.is_legacy:
        raise HTTPException(409, "Run migration m0003 first.")
    if body.vertical != tp.tenant.vertical:
        try:
            await update_tenant(tp.tenant, {"vertical": body.vertical}, allowed=EDITABLE_FIELDS,
                                user_id=tp.user.user_id, ip=_ip(request))
        except ValueError as e:
            raise HTTPException(422, str(e))
    added = await apply_template(tp.tenant.id, body.vertical, replace=body.replace)
    return {"success": True, "added": added}


class StepBody(BaseModel):
    step: str = Field(max_length=40)
    completed: bool = False


@tenant_router.post("/onboarding/step")
async def onboarding_step(body: StepBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    if tp.tenant.is_legacy:
        return {"success": True}
    progress = {**tp.tenant.onboarding, "step": body.step}
    if body.completed:
        progress["completed_at"] = utcnow().isoformat()
    await update_tenant(tp.tenant, {"onboarding": progress}, allowed=EDITABLE_FIELDS, user_id=tp.user.user_id, ip=_ip(request))
    return {"success": True, "onboarding": progress}


class SiteImportBody(BaseModel):
    url: str = Field(min_length=4, max_length=500)


@tenant_router.post("/onboarding/site-import")
async def onboarding_site_import(body: SiteImportBody, tp: TenantPrincipal = Depends(_admin)):
    """Read the business's website and draft its setup. Nothing is saved until /onboarding/apply-import."""
    if not await ratelimit.allow_fixed(f"site-import:{tp.tenant.id}", 10, 3600):
        raise HTTPException(429, "Too many website imports. Try again in an hour.")
    try:
        return {"draft": await site_import.import_site(_public_http_url(body.url))}
    except site_import.SiteImportError as e:
        raise HTTPException(e.status, str(e))


class ApplyImportBody(BaseModel):
    profile: Dict[str, Any] = Field(default_factory=dict)
    hours: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list, max_length=60)
    faqs: List[Dict[str, Any]] = Field(default_factory=list, max_length=40)
    links: Dict[str, str] = Field(default_factory=dict)


@tenant_router.post("/onboarding/apply-import")
async def onboarding_apply_import(body: ApplyImportBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    """Save the parts of a website draft the owner kept (examples are replaced, their own rows aren't)."""
    if tp.tenant.is_legacy:
        raise HTTPException(409, "Run migration m0003 first.")
    try:
        added = await site_import.apply_draft(tp.tenant, body.model_dump(), user_id=tp.user.user_id, ip=_ip(request))
    except ValueError as e:
        raise HTTPException(422, str(e)[:500])
    return {"success": True, "added": added}


@tenant_router.get("/verticals")
async def verticals(tp: TenantPrincipal = Depends(_staff)):
    return {"verticals": vertical_catalog()}


# ─────────────────────────────────────────────
# SERVICES, RESOURCES, EXCEPTIONS
# ─────────────────────────────────────────────

class Price(BaseModel):
    amount: Optional[int] = Field(default=None, ge=0)        # minor units
    currency: Optional[str] = Field(default=None, max_length=3)
    display: str = Field(default="", max_length=40)


ServiceCategory = Literal["cosmetic", "implant", "emergency", "routine", "other"]


class ServiceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    duration_min: int = Field(default=30, ge=5, le=600)
    buffer_min: int = Field(default=0, ge=0, le=240)
    price: Optional[Price] = None
    # What one of these is typically worth to the business (minor units, the tenant's currency):
    # the pipeline value of a booked consultation or a qualified lead (dental ROI).
    estimated_value_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    category: Optional[ServiceCategory] = None
    resource_ids: List[str] = Field(default_factory=list)
    ai_bookable: bool = True
    description: str = Field(default="", max_length=500)
    active: bool = True
    sort: int = 0


class ServicePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    duration_min: Optional[int] = Field(default=None, ge=5, le=600)
    buffer_min: Optional[int] = Field(default=None, ge=0, le=240)
    price: Optional[Price] = None
    estimated_value_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    category: Optional[ServiceCategory] = None
    resource_ids: Optional[List[str]] = None
    ai_bookable: Optional[bool] = None
    description: Optional[str] = Field(default=None, max_length=500)
    active: Optional[bool] = None
    sort: Optional[int] = None


class ResourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["staff", "chair", "room", "table", "bay", "technician", "other"] = "staff"
    hours: Optional[List[HoursEntry]] = None     # None = business hours
    capacity: int = Field(default=1, ge=1, le=500)
    active: bool = True
    sort: int = 0


class ResourcePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    kind: Optional[Literal["staff", "chair", "room", "table", "bay", "technician", "other"]] = None
    hours: Optional[List[HoursEntry]] = None
    clear_hours: bool = False                    # set true to go back to business hours
    capacity: Optional[int] = Field(default=None, ge=1, le=500)
    active: Optional[bool] = None
    sort: Optional[int] = None


async def _audit(tp: TenantPrincipal, request: Request, action: str, target_type: str, target_id: str,
                 diff: Optional[Dict[str, Any]] = None) -> None:
    await tp.repos().audit.record(user_id=tp.user.user_id, action=action, target_type=target_type,
                                  target_id=target_id, diff=to_jsonable(diff or {}), ip=_ip(request))


async def _valid_resource_ids(tp: TenantPrincipal, ids: List[str]) -> None:
    if ids and await tp.repos().resources.count({"_id": {"$in": ids}}) != len(set(ids)):
        raise HTTPException(422, "Unknown staff member / resource in resource_ids")


@tenant_router.get("/services")
async def list_services(tp: TenantPrincipal = Depends(_staff)):
    rows = await tp.repos().services.find({}, sort=[("sort", 1), ("name", 1)], limit=500)
    return {"services": [_clean(r) for r in rows]}


@tenant_router.post("/services")
async def create_service(body: ServiceBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    await _valid_resource_ids(tp, body.resource_ids)
    doc = {"_id": new_id("svc"), **body.model_dump(), "updated_at": utcnow()}
    await tp.repos().services.insert_one(doc)
    await _audit(tp, request, "service.create", "service", doc["_id"], {"name": body.name})
    return _clean(doc)


@tenant_router.patch("/services/{service_id}")
async def update_service(service_id: str, body: ServicePatch, request: Request, tp: TenantPrincipal = Depends(_admin)):
    sets = body.model_dump(exclude_unset=True)
    if "resource_ids" in sets:
        await _valid_resource_ids(tp, sets["resource_ids"] or [])
    if not sets:
        raise HTTPException(400, "Nothing to update")
    sets["updated_at"] = utcnow()
    res = await tp.repos().services.update_one({"_id": service_id}, {"$set": sets})
    if res.matched_count == 0:
        raise _not_found("Service not found")
    await _audit(tp, request, "service.update", "service", service_id, sets)
    return _clean(await tp.repos().services.find_one({"_id": service_id}))


@tenant_router.delete("/services/{service_id}")
async def delete_service(service_id: str, request: Request, tp: TenantPrincipal = Depends(_admin)):
    """Soft delete: deactivated services keep their history on past appointments."""
    res = await tp.repos().services.update_one({"_id": service_id}, {"$set": {"active": False, "updated_at": utcnow()}})
    if res.matched_count == 0:
        raise _not_found("Service not found")
    await _audit(tp, request, "service.deactivate", "service", service_id)
    return {"success": True}


@tenant_router.get("/resources")
async def list_resources(tp: TenantPrincipal = Depends(_staff)):
    rows = await tp.repos().resources.find({}, sort=[("sort", 1), ("name", 1)], limit=500,
                                           projection={"busy_cache": 0})
    return {"resources": [_clean(r) for r in rows]}


@tenant_router.post("/resources")
async def create_resource(body: ResourceBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    doc = {"_id": new_id("res"), **body.model_dump(), "updated_at": utcnow()}
    await tp.repos().resources.insert_one(doc)
    await _audit(tp, request, "resource.create", "resource", doc["_id"], {"name": body.name})
    return _clean(doc)


@tenant_router.patch("/resources/{resource_id}")
async def update_resource(resource_id: str, body: ResourcePatch, request: Request, tp: TenantPrincipal = Depends(_admin)):
    sets = body.model_dump(exclude_unset=True, exclude={"clear_hours"})
    if body.clear_hours:
        sets["hours"] = None
    if not sets:
        raise HTTPException(400, "Nothing to update")
    sets["updated_at"] = utcnow()
    res = await tp.repos().resources.update_one({"_id": resource_id}, {"$set": sets})
    if res.matched_count == 0:
        raise _not_found("Resource not found")
    await _audit(tp, request, "resource.update", "resource", resource_id, sets)
    return _clean(await tp.repos().resources.find_one({"_id": resource_id}, {"busy_cache": 0}))


@tenant_router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: str, request: Request, tp: TenantPrincipal = Depends(_admin)):
    upcoming = await tp.repos().appointments.count({"resource_id": resource_id, "status": {"$in": ["confirmed", "pending"]},
                                                    "start": {"$gte": utcnow()}})
    if upcoming:
        raise HTTPException(409, f"{upcoming} upcoming appointment(s) use this resource. Move or cancel them first.")
    res = await tp.repos().resources.update_one({"_id": resource_id}, {"$set": {"active": False, "updated_at": utcnow()}})
    if res.matched_count == 0:
        raise _not_found("Resource not found")
    await _audit(tp, request, "resource.deactivate", "resource", resource_id)
    return {"success": True}


class ExceptionBody(BaseModel):
    resource_id: Optional[str] = None         # None = the whole business
    date_from: date
    date_to: date                             # inclusive, business-local
    reason: str = Field(default="", max_length=200)


@tenant_router.get("/exceptions")
async def list_exceptions(tp: TenantPrincipal = Depends(_staff)):
    rows = await tp.repos().exceptions.find({"end": {"$gte": utcnow() - timedelta(days=30)}, "source": {"$ne": "google"}},
                                            sort=[("start", 1)], limit=500)
    return {"exceptions": [_clean(r) for r in rows]}


@tenant_router.post("/exceptions")
async def create_exception(body: ExceptionBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    if body.date_to < body.date_from:
        raise HTTPException(422, "date_to is before date_from")
    if body.resource_id and not await tp.repos().resources.find_one({"_id": body.resource_id}):
        raise HTTPException(422, "Unknown resource")
    doc = {"_id": new_id("exc"), "resource_id": body.resource_id,
           "start": _local_midnight_utc(tp, body.date_from),
           "end": _local_midnight_utc(tp, body.date_to + timedelta(days=1)),
           "date_from": body.date_from.isoformat(), "date_to": body.date_to.isoformat(), "reason": body.reason}
    await tp.repos().exceptions.insert_one(doc)
    await _audit(tp, request, "exception.create", "schedule_exception", doc["_id"], {"reason": body.reason})
    return _clean(doc)


@tenant_router.delete("/exceptions/{exception_id}")
async def delete_exception(exception_id: str, request: Request, tp: TenantPrincipal = Depends(_admin)):
    res = await tp.repos().exceptions.delete_one({"_id": exception_id})
    if res.deleted_count == 0:
        raise _not_found()
    await _audit(tp, request, "exception.delete", "schedule_exception", exception_id)
    return {"success": True}


# ─────────────────────────────────────────────
# KNOWLEDGE
# ─────────────────────────────────────────────

class KnowledgeBody(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    answer: str = Field(min_length=1, max_length=1500)
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    source: Literal["dashboard", "website"] = "dashboard"


class KnowledgePatch(BaseModel):
    question: Optional[str] = Field(default=None, min_length=2, max_length=300)
    answer: Optional[str] = Field(default=None, min_length=1, max_length=1500)
    tags: Optional[List[str]] = None
    active: Optional[bool] = None


@tenant_router.get("/knowledge")
async def list_knowledge(tp: TenantPrincipal = Depends(_staff)):
    rows = await tp.repos().knowledge.find({}, sort=[("sort", 1), ("updated_at", -1)], limit=1000)
    from core.tools.knowledge import KB_INLINE_MAX_CHARS, KB_INLINE_MAX_ITEMS, fits_inline, kb_chars

    active = [r for r in rows if r.get("active") is not False]
    return {"items": [_clean(r) for r in rows], "inline": fits_inline(active), "count": len(active),
            "chars": kb_chars(active), "max_items": KB_INLINE_MAX_ITEMS, "max_chars": KB_INLINE_MAX_CHARS}


@tenant_router.get("/knowledge/suggestions")
async def knowledge_suggestions(tp: TenantPrincipal = Depends(_staff)):
    """Questions callers commonly ask this kind of business that have no saved answer yet."""
    from core.services.knowledge_suggestions import suggestions_for

    rows = await tp.repos().knowledge.find({}, limit=1000, projection={"question": 1, "tags": 1, "active": 1})
    return {"suggestions": suggestions_for(tp.tenant.vertical, tp.tenant.country, rows)}


class KnowledgeTestBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@tenant_router.post("/knowledge/test")
async def test_knowledge(body: KnowledgeTestBody, tp: TenantPrincipal = Depends(_staff)):
    """Which saved answers a caller's question would find. Nothing is logged."""
    from core.tools.knowledge import fits_inline, rank_scored

    rows = await tp.repos().knowledge.find({"active": {"$ne": False}}, limit=1000)
    hits = rank_scored(rows, body.question)
    top = hits[0][0] if hits else 0
    return {"inline": fits_inline(rows),
            "matches": [{**_clean(it), "strength": "strong" if score >= max(2, top) else "weak"}
                        for score, it in hits]}


@tenant_router.post("/knowledge")
async def create_knowledge(body: KnowledgeBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    doc = {"_id": new_id("kb"), **body.model_dump(), "updated_at": utcnow()}
    await tp.repos().knowledge.insert_one(doc)
    await _audit(tp, request, "knowledge.create", "knowledge_item", doc["_id"], {"question": body.question})
    return _clean(doc)


@tenant_router.patch("/knowledge/{item_id}")
async def update_knowledge(item_id: str, body: KnowledgePatch, request: Request, tp: TenantPrincipal = Depends(_admin)):
    sets = body.model_dump(exclude_unset=True)
    if not sets:
        raise HTTPException(400, "Nothing to update")
    sets["updated_at"] = utcnow()
    res = await tp.repos().knowledge.update_one({"_id": item_id}, {"$set": sets})
    if res.matched_count == 0:
        raise _not_found()
    await _audit(tp, request, "knowledge.update", "knowledge_item", item_id, sets)
    return _clean(await tp.repos().knowledge.find_one({"_id": item_id}))


@tenant_router.delete("/knowledge/{item_id}")
async def delete_knowledge(item_id: str, request: Request, tp: TenantPrincipal = Depends(_admin)):
    res = await tp.repos().knowledge.delete_one({"_id": item_id})
    if res.deleted_count == 0:
        raise _not_found()
    await _audit(tp, request, "knowledge.delete", "knowledge_item", item_id)
    return {"success": True}


@tenant_router.get("/unanswered")
async def list_unanswered(status: str = Query("open", pattern="^(open|answered|dismissed)$"),
                          tp: TenantPrincipal = Depends(_staff)):
    from core.tools.knowledge import question_key

    rows = await tp.repos().unanswered.find({"status": status}, sort=[("created_at", -1)], limit=500)
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:                               # newest first, so the first row of a group is the latest ask
        key = r.get("qkey") or question_key(r.get("question", "")) or str(r["_id"])
        g = groups.get(key)
        if g is None:
            groups[key] = {**_clean(r), "count": 1, "call_ids": [r["call_id"]] if r.get("call_id") else [],
                           "first_asked_at": to_jsonable(r.get("created_at"))}
        else:
            g["count"] += 1
            g["first_asked_at"] = to_jsonable(r.get("created_at"))
            if r.get("call_id") and r["call_id"] not in g["call_ids"]:
                g["call_ids"].append(r["call_id"])
    items = list(groups.values())
    if status == "open":
        items.sort(key=lambda g: -g["count"])     # stable: ties stay newest first
    return {"items": items[:200], "total": len(rows)}


async def _same_question_ids(repos: Any, row: Dict[str, Any]) -> List[Any]:
    """Every open row asking the same thing as `row` (grouped in the list), so one answer clears them all."""
    from core.tools.knowledge import question_key

    key = row.get("qkey") or question_key(row.get("question", ""))
    if not key:
        return [row["_id"]]
    rows = await repos.unanswered.find({"status": "open"}, limit=1000, projection={"question": 1, "qkey": 1})
    ids = [r["_id"] for r in rows if (r.get("qkey") or question_key(r.get("question", ""))) == key]
    return ids or [row["_id"]]


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1, max_length=1500)
    question: Optional[str] = Field(default=None, max_length=300)
    tags: List[str] = Field(default_factory=list)


@tenant_router.post("/unanswered/{item_id}/answer")
async def answer_unanswered(item_id: str, body: AnswerBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    repos = tp.repos()
    row = await repos.unanswered.find_one(_by_id(item_id))
    if not row:
        raise _not_found()
    kb = {"_id": new_id("kb"), "question": (body.question or row["question"])[:300], "answer": body.answer,
          "tags": body.tags, "active": True, "source": "unanswered", "updated_at": utcnow()}
    await repos.knowledge.insert_one(kb)
    ids = await _same_question_ids(repos, row)
    await repos.unanswered.update_many({"_id": {"$in": ids}}, {"$set": {"status": "answered", "knowledge_id": kb["_id"],
                                                                         "answered_at": utcnow()}})
    await _audit(tp, request, "knowledge.create", "knowledge_item", kb["_id"], {"from_unanswered": item_id})
    return {"success": True, "item": _clean(kb)}


@tenant_router.post("/unanswered/{item_id}/dismiss")
async def dismiss_unanswered(item_id: str, tp: TenantPrincipal = Depends(_admin)):
    repos = tp.repos()
    row = await repos.unanswered.find_one(_by_id(item_id))
    if not row:
        raise _not_found()
    ids = await _same_question_ids(repos, row)
    await repos.unanswered.update_many({"_id": {"$in": ids}}, {"$set": {"status": "dismissed", "dismissed_at": utcnow()}})
    return {"success": True}


class ImportBody(BaseModel):
    url: str = Field(min_length=4, max_length=500)


def _public_http_url(url: str) -> str:
    """Only fetch public http(s) hosts — never the server's own network (SSRF)."""
    try:
        return site_import.public_url(url)
    except site_import.SiteImportError as e:
        raise HTTPException(e.status, str(e))


async def _crawl_or_http(url: str, *, max_pages: int) -> Dict[str, Any]:
    try:
        return await site_import.crawl(_public_http_url(url), max_pages=max_pages)
    except site_import.SiteImportError as e:
        raise HTTPException(e.status, str(e))


@tenant_router.post("/knowledge/import")
async def import_knowledge(body: ImportBody, tp: TenantPrincipal = Depends(_admin)):
    """Draft Q&A pairs from the business's website (homepage + its FAQ / contact / services pages).
    Nothing is saved: the owner approves each."""
    if not settings.OPENAI_API_KEY:
        raise HTTPException(503, "Website import needs OPENAI_API_KEY")
    crawled = await _crawl_or_http(body.url, max_pages=4)
    url = crawled["url"]
    text = "\n\n".join(p["text"] for p in crawled["pages"])[:20000]
    if len(text) < 100:
        raise HTTPException(422, "Not enough text on that page to draft answers")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, max_retries=1, timeout=30)
    prompt = ("From this website text for a local business, draft up to 15 short question-and-answer pairs a "
              "caller might ask (hours, location, parking, services, prices, policies). Use only facts stated in "
              "the text. Answers are one or two plain sentences. Reply as JSON: "
              '{"items": [{"question": "...", "answer": "..."}]}\n\nWEBSITE TEXT:\n' + text)
    try:
        completion = await client.chat.completions.create(
            model=settings.OPENAI_MODEL, temperature=0.2, response_format={"type": "json_object"},
            store=settings.OPENAI_STORE, messages=[{"role": "user", "content": prompt}])
        items = json.loads(completion.choices[0].message.content or "{}").get("items", [])
    except Exception:
        logger.warning("Website import LLM call failed", exc_info=True)
        raise HTTPException(502, "Couldn't draft answers right now; try again")
    drafts = [{"question": str(i.get("question", ""))[:300], "answer": str(i.get("answer", ""))[:1500]}
              for i in items if isinstance(i, dict) and i.get("question") and i.get("answer")][:15]
    return {"drafts": drafts, "source": url}


# ─────────────────────────────────────────────
# APPOINTMENTS
# ─────────────────────────────────────────────

class AppointmentBody(BaseModel):
    service_id: Optional[str] = None
    resource_id: Optional[str] = None         # None = any free resource at that time
    start: datetime
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=5, max_length=32)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: str = Field(default="", max_length=1000)
    party_size: Optional[int] = Field(default=None, ge=1, le=200)


class AppointmentPatch(BaseModel):
    status: Optional[Literal["confirmed", "completed", "no_show", "cancelled"]] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    cancel_reason: Optional[str] = Field(default=None, max_length=300)


def _scheduler(tp: TenantPrincipal) -> SchedulingService:
    if not get_vertical(tp.tenant.vertical).uses_scheduling:
        raise HTTPException(409, "This business uses the hotel bookings pages")
    return SchedulingService(tp.repos(), tp.tenant)


@tenant_router.get("/appointments")
async def list_appointments(date_from: Optional[date] = None, date_to: Optional[date] = None,
                            status: Optional[str] = None, resource_id: Optional[str] = None,
                            tp: TenantPrincipal = Depends(_staff)):
    today = _today(tp)
    start = _local_midnight_utc(tp, date_from or today)
    end = _local_midnight_utc(tp, (date_to or (date_from or today) + timedelta(days=6)) + timedelta(days=1))
    q: Dict[str, Any] = {"start": {"$gte": start, "$lt": end}}
    if status:
        q["status"] = {"$in": [s.strip() for s in status.split(",") if s.strip()]}
    if resource_id:
        q["resource_id"] = resource_id
    rows = await tp.repos().appointments.find(q, sort=[("start", 1)], limit=2000)
    return {"appointments": [_clean(r) for r in rows], "timezone": tp.tenant.timezone}


@tenant_router.get("/slots")
async def slots(service_id: Optional[str] = None, day: Optional[date] = None, resource_id: Optional[str] = None,
                days: int = Query(1, ge=1, le=14), tp: TenantPrincipal = Depends(_staff)):
    """Open times from `day` for `days` business-local days (one resource per time)."""
    svc = _scheduler(tp)
    service = await tp.repos().services.find_one({"_id": service_id}) if service_id else None
    resource = await tp.repos().resources.find_one({"_id": resource_id}) if resource_id else None
    day_from = day or _today(tp)
    try:
        found = await svc.find_slots(service=service, day_from=day_from, day_to=day_from + timedelta(days=days - 1),
                                     resource=resource, limit=2000)
    except SchedulingError as e:
        raise HTTPException(409, e.message)
    found.sort(key=lambda s: (s.start, s.resource_id))
    names = {r["_id"]: r.get("name") for r in await svc.resources()}
    return {"slots": [{"slot_id": s.slot_id, "start": to_jsonable(s.start), "end": to_jsonable(s.end),
                       "resource_id": s.resource_id, "resource": names.get(s.resource_id)} for s in found],
            "timezone": tp.tenant.timezone}


@tenant_router.post("/appointments")
async def create_appointment(body: AppointmentBody, request: Request, tp: TenantPrincipal = Depends(_staff)):
    svc = _scheduler(tp)
    service = await tp.repos().services.find_one({"_id": body.service_id}) if body.service_id else None
    if body.service_id and not service:
        raise HTTPException(422, "Unknown service")
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
    resource_id = body.resource_id
    if not resource_id:
        local_day = start.astimezone(_zone(tp)).date()
        free = await svc.find_slots(service=service, day_from=local_day, requested_at=start, limit=100,
                                    party_size=body.party_size)
        match = next((s for s in free if s.start == start), None)
        if not match:
            raise HTTPException(409, "No one is free at that time")
        resource_id = match.resource_id
    try:
        appt = await svc.book(resource_id=resource_id, start=start, service=service, name=body.name, phone=body.phone,
                              email=body.email, notes=body.notes, source="dashboard", party_size=body.party_size)
    except SchedulingError as e:
        raise HTTPException(409, e.message)
    await _audit(tp, request, "appointment.create", "appointment", appt["appointment_id"])
    return _clean(appt)


@tenant_router.patch("/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, body: AppointmentPatch, request: Request,
                             tp: TenantPrincipal = Depends(_staff)):
    repos = tp.repos()
    appt = await repos.appointments.find_one({"appointment_id": appointment_id})
    if not appt:
        raise _not_found("Appointment not found")
    if body.status == "cancelled":
        try:
            await _scheduler(tp).cancel(appointment_id, body.cancel_reason or "Cancelled from dashboard")
        except SchedulingError as e:
            raise HTTPException(409, e.message)
    sets: Dict[str, Any] = {}
    if body.status and body.status != "cancelled":
        sets["status"] = body.status
        if body.status in ("completed", "no_show"):
            await repos.slot_locks.delete_many({"appointment_id": appointment_id})
        if body.status == "no_show" and appt.get("contact_id"):
            await repos.contacts.update_one({"_id": appt["contact_id"]}, {"$inc": {"stats.no_shows": 1}})
    if body.notes is not None:
        sets["notes"] = body.notes
    if sets:
        sets["updated_at"] = utcnow()
        await repos.appointments.update_one({"appointment_id": appointment_id}, {"$set": sets})
    await _audit(tp, request, "appointment.update", "appointment", appointment_id, body.model_dump(exclude_unset=True))
    return _clean(await repos.appointments.find_one({"appointment_id": appointment_id}))


class ReassignBody(BaseModel):
    resource_id: str = Field(min_length=1, max_length=60)


@tenant_router.post("/appointments/{appointment_id}/reassign")
async def reassign_appointment(appointment_id: str, body: ReassignBody, request: Request,
                               tp: TenantPrincipal = Depends(_staff)):
    """Same time, another dentist (plan E3). Books the new practitioner first, then cancels the old
    booking, in the practice diary too, so the slot is never lost in between."""
    svc = _scheduler(tp)
    repos = tp.repos()
    appt = await repos.appointments.find_one({"appointment_id": appointment_id})
    if not appt or appt.get("status") not in ("pending", "confirmed"):
        raise _not_found("No active appointment with that ID")
    if appt.get("resource_id") == body.resource_id:
        return _clean(appt)
    resource = await repos.resources.find_one({"_id": body.resource_id})
    if not resource or resource.get("active") is False:
        raise HTTPException(422, "That practitioner doesn't exist or is inactive")
    start = appt["start"] if appt["start"].tzinfo else appt["start"].replace(tzinfo=timezone.utc)
    try:
        new = await svc.reschedule(appointment_id, resource_id=body.resource_id, start=start)
    except SchedulingError as e:
        if e.code == "taken":
            raise HTTPException(409, f"{resource.get('name') or 'That dentist'} isn't free at that time")
        raise HTTPException(409, e.message)
    await _audit(tp, request, "appointment.reassign", "appointment", appointment_id,
                 {"from": appt.get("resource_id"), "to": body.resource_id, "new_appointment_id": new["appointment_id"]})
    return _clean(new)


# ─────────────────────────────────────────────
# MY DAY (plan E: a dentist's patients today / tomorrow)
# ─────────────────────────────────────────────

async def _linked_resource(tp: TenantPrincipal) -> Optional[str]:
    doc = await UsersStore(get_db()).get(tp.user.user_id) or {}
    return next((m.get("resource_id") for m in doc.get("memberships", [])
                 if m.get("tenant_id") == tp.tenant.id), None)


async def _day_resource(tp: TenantPrincipal, resource_id: Optional[str]) -> Dict[str, Any]:
    if tp.can("staff"):
        if not resource_id:
            raise HTTPException(422, "Choose a dentist")
    else:                                         # a dentist: always their own, whatever was asked for
        resource_id = await _linked_resource(tp)
        if not resource_id:
            raise HTTPException(403, "Your account isn't linked to a practitioner yet. Ask the practice owner.")
    res = await tp.repos().resources.find_one({"_id": resource_id})
    if not res:
        raise _not_found("Practitioner not found")
    return res


@tenant_router.get("/my-day")
async def my_day(request: Request, day: Literal["today", "tomorrow"] = "today",
                 resource_id: Optional[str] = Query(None, max_length=60), tp: TenantPrincipal = Depends(_dentist)):
    from core.services.my_day import day_view

    if not get_vertical(tp.tenant.vertical).uses_scheduling:
        raise HTTPException(409, "This business doesn't use appointments")
    res = await _day_resource(tp, resource_id)
    local_day = _today(tp) + timedelta(days=1 if day == "tomorrow" else 0)
    out = await day_view(tp.repos(), tp.tenant, res, local_day)
    await _audit(tp, request, "my_day.view", "resource", res["_id"], {"day": local_day.isoformat()})
    return to_jsonable(out)


class MyDayPatch(BaseModel):
    status: Literal["completed", "no_show"]


@tenant_router.patch("/my-day/{appointment_id}")
async def my_day_mark(appointment_id: str, body: MyDayPatch, request: Request,
                      tp: TenantPrincipal = Depends(_dentist)):
    """A dentist marks their own patient seen or not arrived. Staff use PATCH /t/appointments/{id}."""
    repos = tp.repos()
    appt = await repos.appointments.find_one({"appointment_id": appointment_id})
    if not appt:
        raise _not_found("Appointment not found")
    if not tp.can("staff") and appt.get("resource_id") != await _linked_resource(tp):
        raise _not_found("Appointment not found")           # not theirs: don't confirm it exists
    if appt.get("status") not in ("pending", "confirmed", "completed", "no_show"):
        raise HTTPException(409, f"That appointment is {appt.get('status')}")
    sets: Dict[str, Any] = {"status": body.status, "updated_at": utcnow()}
    await repos.appointments.update_one({"appointment_id": appointment_id}, {"$set": sets})
    await repos.slot_locks.delete_many({"appointment_id": appointment_id})
    if body.status == "no_show" and appt.get("status") != "no_show" and appt.get("contact_id"):
        await repos.contacts.update_one({"_id": appt["contact_id"]}, {"$inc": {"stats.no_shows": 1}})
    await _audit(tp, request, "appointment.update", "appointment", appointment_id, {"status": body.status})
    return _clean(await repos.appointments.find_one({"appointment_id": appointment_id}))


# ─────────────────────────────────────────────
# CONTACTS
# ─────────────────────────────────────────────

class ContactPatch(BaseModel):
    preferred_resource_id: Optional[str] = Field(default=None, max_length=60)   # "" clears it
    name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)
    tags: Optional[List[str]] = None
    do_not_contact: Optional[bool] = None


@tenant_router.get("/contacts")
async def list_contacts(q: Optional[str] = None, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                        tp: TenantPrincipal = Depends(_staff)):
    query: Dict[str, Any] = {}
    if q and q.strip():
        pat = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"name": pat}, {"phone": pat}, {"email": pat}]
    repos = tp.repos()
    total = await repos.contacts.count(query)
    rows = await repos.contacts.find(query, sort=[("last_seen_at", -1)], skip=(page - 1) * limit, limit=limit)
    return {"contacts": [_clean(r) for r in rows], "total": total, "page": page, "pages": (total + limit - 1) // limit}


@tenant_router.get("/contacts/{contact_id}")
async def get_contact(contact_id: str, tp: TenantPrincipal = Depends(_staff)):
    repos = tp.repos()
    c = await repos.contacts.find_one(_by_id(contact_id))
    if not c:
        raise _not_found("Contact not found")
    appts = await repos.appointments.find({"contact_id": {"$in": [c["_id"], str(c["_id"])]}},
                                          sort=[("start", -1)], limit=50)
    calls = await repos.calls.find({"caller_phone": c.get("phone")}, sort=[("started_at", -1)], limit=20,
                                   projection={"transcript": 0, "turns": 0})
    msgs = await repos.messages.find({"$or": [{"caller_phone": c.get("phone")}, {"callback_number": c.get("phone")}]},
                                     sort=[("created_at", -1)], limit=20)
    return {**_clean(c), "appointments": [_clean(a) for a in appts], "calls": [_clean(x) for x in calls],
            "messages": [_clean(m) for m in msgs]}


@tenant_router.patch("/contacts/{contact_id}")
async def update_contact(contact_id: str, body: ContactPatch, request: Request, tp: TenantPrincipal = Depends(_staff)):
    sets = body.model_dump(exclude_unset=True)
    if not sets:
        raise HTTPException(400, "Nothing to update")
    if "preferred_resource_id" in sets:               # the patient's regular dentist (plan E3); "" clears it
        rid = sets["preferred_resource_id"] or None
        if rid:
            res_doc = await tp.repos().resources.find_one({"_id": rid})
            if not res_doc or res_doc.get("active") is False:
                raise HTTPException(422, "That practitioner doesn't exist or is inactive")
        sets["preferred_resource_id"] = rid
    sets["updated_at"] = utcnow()
    res = await tp.repos().contacts.update_one(_by_id(contact_id), {"$set": sets})
    if res.matched_count == 0:
        raise _not_found("Contact not found")
    await _audit(tp, request, "contact.update", "contact", contact_id, sets)
    return _clean(await tp.repos().contacts.find_one(_by_id(contact_id)))


# ─────────────────────────────────────────────
# WHATSAPP (staff → patient, from the patient profile)
# ─────────────────────────────────────────────

@tenant_router.get("/whatsapp/status")
async def whatsapp_status(tp: TenantPrincipal = Depends(_staff)):
    sender = wa_outbox.tenant_sender(tp.tenant)
    return {"configured": sender["configured"], "own_number": sender["own_number"],
            "templates_configured": bool(wa_api.account_id(sender["waba_id"]))}


@tenant_router.get("/whatsapp/templates")
async def whatsapp_templates(fresh: bool = False, tp: TenantPrincipal = Depends(_staff)):
    try:
        return {"templates": await wa_api.list_templates(wa_outbox.tenant_sender(tp.tenant)["waba_id"], fresh=fresh)}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


async def _contact_or_404(tp: TenantPrincipal, contact_id: str) -> Dict[str, Any]:
    c = await tp.repos().contacts.find_one(_by_id(contact_id))
    if not c:
        raise _not_found("Contact not found")
    return c


@tenant_router.get("/contacts/{contact_id}/whatsapp")
async def contact_whatsapp(contact_id: str, tp: TenantPrincipal = Depends(_staff)):
    c = await _contact_or_404(tp, contact_id)
    rows = await tp.repos().wa_outbox.find({"contact_id": c["_id"]}, sort=[("created_at", -1)], limit=10)
    return {"window": to_jsonable(wa_outbox.window(c)), "do_not_contact": bool(c.get("do_not_contact")),
            "messages": [_clean(r) for r in rows]}


class WhatsAppBody(BaseModel):
    text: Optional[str] = Field(default=None, max_length=4000)
    template: Optional[str] = Field(default=None, max_length=512)
    language: Optional[str] = Field(default=None, max_length=15)
    params: List[str] = Field(default_factory=list, max_length=20)


@tenant_router.post("/contacts/{contact_id}/whatsapp")
async def send_contact_whatsapp(contact_id: str, body: WhatsAppBody, request: Request,
                                tp: TenantPrincipal = Depends(_staff)):
    c = await _contact_or_404(tp, contact_id)
    if c.get("do_not_contact"):
        raise HTTPException(409, "This patient is marked do-not-contact.")
    sender = wa_outbox.tenant_sender(tp.tenant)
    if not sender["configured"]:
        raise HTTPException(503, "WhatsApp isn't set up for this practice yet.")
    if not await ratelimit.allow_fixed(f"wa-send:{tp.tenant.id}", 60, 60):
        raise HTTPException(429, "Too many WhatsApp messages in a minute. Try again shortly.")
    to = "+" + digits(c.get("phone"))
    repos = tp.repos()

    if body.template:
        if not body.language:
            raise HTTPException(422, "Choose the template's language.")
        params = [p.strip()[:1000] for p in body.params]
        if any(not p for p in params):
            raise HTTPException(422, "Fill in every field of the template.")
        try:
            known = await wa_api.list_templates(sender["waba_id"])
        except RuntimeError:
            known = []
        tpl = next((t for t in known if t["name"] == body.template and t["language"] == body.language), None)
        if tpl and len(params) != tpl["params"]:
            raise HTTPException(422, f"This template needs {tpl['params']} value(s).")
        result = await wa_api.send_template(to, body.template, body.language, params, sender["phone_number_id"])
        preview = wa_outbox.fill_template(tpl["body"], params) if tpl else f"[{body.template}] " + " · ".join(params)
        kind = "template"
    else:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(422, "Type a message or choose a template.")
        if not wa_outbox.window(c)["open"]:
            raise HTTPException(409, "This patient hasn't messaged you in the last 24 hours, so WhatsApp only "
                                     "allows an approved template.")
        result = await wa_api.send_text_result(to, text, sender["phone_number_id"])
        preview, kind = text, "text"

    row = await wa_outbox.record_send(repos, contact=c, to=to, kind=kind, body=preview, result=result,
                                      template=body.template, sent_by=tp.user.user_id)
    await _audit(tp, request, "whatsapp.send", "contact", str(c["_id"]),
                 {"kind": kind, "template": body.template, "ok": result.ok, "error_code": result.error_code})
    if not result.ok:
        raise HTTPException(502, result.error or "WhatsApp send failed.")
    return {"success": True, "message": _clean(row)}


# ─────────────────────────────────────────────
# CALLS, MESSAGES, LEADS
# ─────────────────────────────────────────────

@tenant_router.get("/calls")
async def list_calls(outcome: Optional[str] = Query(None, max_length=32), page: int = Query(1, ge=1),
                     limit: int = Query(25, ge=1, le=100), tp: TenantPrincipal = Depends(_staff)):
    sdb = scoped_db(tp.tenant)
    q: Dict[str, Any] = {"outcome": outcome} if outcome else {}
    total = await sdb.calls.count_documents(q)
    rows = await sdb.calls.find(q, {"transcript": 0, "turns": 0}).sort("started_at", -1) \
        .skip((page - 1) * limit).limit(limit).to_list(limit)
    return {"calls": [_clean(r) for r in rows], "total": total, "page": page, "pages": (total + limit - 1) // limit}


@tenant_router.get("/calls/live")
async def live(tp: TenantPrincipal = Depends(_staff)):
    cutoff = utcnow() - timedelta(seconds=LIVE_CALL_STALE_S)
    rows = await scoped_db(tp.tenant).calls.find({"status": "in_progress", "updated_at": {"$gte": cutoff}},
                                                 {"transcript": 0, "turns": 0}).sort("started_at", -1).to_list(50)
    return {"calls": [_clean(r) for r in rows], "total": len(rows)}


@tenant_router.get("/calls/{call_id}")
async def call_detail(call_id: str, tp: TenantPrincipal = Depends(_staff)):
    doc = await scoped_db(tp.tenant).calls.find_one({"call_id": call_id})
    if not doc:
        raise _not_found("Call not found")
    return _clean(unseal(doc))


class StatusBody(BaseModel):
    status: str = Field(pattern="^(new|handled|contacted|won|lost)$")


@tenant_router.get("/messages")
async def list_messages(status: Optional[str] = Query(None, pattern="^(new|handled)$"),
                        limit: int = Query(50, ge=1, le=200), tp: TenantPrincipal = Depends(_staff)):
    sdb = scoped_db(tp.tenant)
    q = {"status": status} if status else {}
    rows = await sdb.messages.find(q).sort("created_at", -1).limit(limit).to_list(limit)
    return {"messages": [_clean(r) for r in rows], "new": await sdb.messages.count_documents({"status": "new"})}


@tenant_router.patch("/messages/{message_id}")
async def update_message(message_id: str, body: StatusBody, tp: TenantPrincipal = Depends(_staff)):
    if body.status not in ("new", "handled"):
        raise HTTPException(422, "status must be new or handled")
    res = await scoped_db(tp.tenant).messages.update_one(
        {"message_id": message_id},
        {"$set": {"status": body.status, "handled_by": tp.user.user_id if body.status == "handled" else None,
                  "handled_at": utcnow() if body.status == "handled" else None, "updated_at": utcnow()}})
    if res.matched_count == 0:
        raise _not_found("Message not found")
    return {"success": True, "message_id": message_id, "status": body.status}


@tenant_router.get("/leads")
async def list_leads(status: Optional[str] = None, tp: TenantPrincipal = Depends(_staff)):
    q = {"status": status} if status else {}
    rows = await tp.repos().leads.find(q, sort=[("created_at", -1)], limit=200)
    return {"leads": [_clean(unseal(r)) for r in rows], "new": await tp.repos().leads.count({"status": "new"})}


@tenant_router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, body: StatusBody, tp: TenantPrincipal = Depends(_staff)):
    res = await tp.repos().leads.update_one({"_id": lead_id}, {"$set": {"status": body.status, "updated_at": utcnow()}})
    if res.matched_count == 0:
        raise _not_found("Lead not found")
    return {"success": True}


# ─────────────────────────────────────────────
# PHONE NUMBERS, TEAM, TEST CALL, AUDIT
# ─────────────────────────────────────────────

# ⚠ Typical codes — carriers differ and change them. Verify per carrier before giving to customers.
# `countries`: where the row applies (the business's country picks the rows; "*" = everywhere).
# Landlines often want the AI number in national format (0…) instead of +CC…: the note says so.
GSM = {"no_answer": "**61*{n}#", "busy": "**67*{n}#", "unreachable": "**62*{n}#", "cancel": "##002#"}
FORWARDING_GUIDE = [
    {"countries": ["*"], "carrier": "Most mobiles (GSM: Vodafone, EE, O2, Three, Telstra, Optus, T-Mobile…)", **GSM},
    # United Kingdom
    {"countries": ["GB"], "carrier": "BT landline (Call Diversion)",
     "no_answer": "*61*{n}#", "busy": "*67*{n}#", "unreachable": "*21*{n}# (all calls)", "cancel": "#61#  #67#  #21#"},
    {"countries": ["GB"], "carrier": "Virgin Media / Sky / TalkTalk landline",
     "no_answer": "*61*{n}#", "busy": "*67*{n}#", "unreachable": "*21*{n}# (all calls)", "cancel": "#21#"},
    {"countries": ["GB", "IE", "AU", "MT"], "carrier": "Business VoIP / hosted phone system (RingCentral, 8x8, 3CX…)",
     "no_answer": "Set 'forward on no answer' to {n} in your provider's portal", "busy": "Portal setting",
     "unreachable": "Portal setting", "cancel": "Portal setting"},
    # Ireland
    {"countries": ["IE"], "carrier": "eir / Virgin Media Ireland landline",
     "no_answer": "*61*{n}#", "busy": "*67*{n}#", "unreachable": "*21*{n}# (all calls)", "cancel": "#21#"},
    # Australia
    {"countries": ["AU"], "carrier": "Telstra landline (Call Forward)",
     "no_answer": "*61 {n} #", "busy": "*24 {n} #", "unreachable": "*21 {n} # (all calls)", "cancel": "#61#  #24#  #21#"},
    {"countries": ["AU"], "carrier": "Optus / TPG / Aussie Broadband home phone",
     "no_answer": "*61*{n}#", "busy": "*67*{n}#", "unreachable": "*21*{n}# (all calls)", "cancel": "#21#"},
    # United States / Canada
    {"countries": ["US", "CA"], "carrier": "Verizon mobile",
     "no_answer": "*71{n}", "busy": "*71{n}", "unreachable": "*71{n}", "cancel": "*73"},
    {"countries": ["US", "CA"], "carrier": "Most US landlines / VoIP (Comcast Business, RingCentral…)",
     "no_answer": "Set 'forward on no answer' to {n} in your provider's portal", "busy": "*90{n} (varies)",
     "unreachable": "Portal setting", "cancel": "Portal setting"},
    {"countries": ["US"], "carrier": "Google Voice", "no_answer": "Settings → Calls → Forward to {n}", "busy": "—",
     "unreachable": "—", "cancel": "Remove the linked number"},
]


def forwarding_guide_for(country: str, target: str) -> list:
    c = (country or "").upper()
    rows = [r for r in FORWARDING_GUIDE if "*" in r["countries"] or c in r["countries"]]
    if len(rows) == 1:                       # a country without its own rows: show everything
        rows = FORWARDING_GUIDE
    return [{k: (v.replace("{n}", target or "<your AI number>") if isinstance(v, str) else v)
             for k, v in row.items() if k != "countries"} for row in rows]


@tenant_router.get("/phone-numbers")
async def phone_numbers(tp: TenantPrincipal = Depends(_staff)):
    rows = await PhoneNumbersStore(get_db()).for_tenant(tp.tenant.id)
    numbers = [_clean(r) | {"e164": r.get("e164")} for r in rows if r.get("status") != "released"]
    if tp.tenant.is_legacy and settings.TWILIO_PHONE_NUMBER:
        numbers.append({"e164": settings.TWILIO_PHONE_NUMBER, "purpose": "inbound", "status": "active", "provider": "twilio"})
    target = numbers[0]["e164"] if numbers else ""
    guide = forwarding_guide_for(tp.tenant.country, target)
    return {"numbers": numbers, "forwarding": guide,
            "note": "Most businesses keep their existing number and forward to the AI number on no answer, busy, "
                    "or after hours. Codes vary by carrier — check with yours. On a landline, if the number is "
                    "rejected, dial it in national format (starting with 0) instead of +country code."}


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["admin", "staff", "owner", "dentist"] = "staff"
    resource_id: Optional[str] = Field(default=None, max_length=60)    # dentist: whose day they see


class RoleBody(BaseModel):
    role: Literal["owner", "admin", "staff", "dentist"]
    resource_id: Optional[str] = Field(default=None, max_length=60)


async def _dentist_resource(tp: TenantPrincipal, role: str, resource_id: Optional[str]) -> Optional[str]:
    """A dentist must be linked to one of the practice's active practitioners."""
    if role != "dentist":
        return None
    if not resource_id:
        raise HTTPException(422, "Choose which dentist (practitioner) this person is")
    res = await tp.repos().resources.find_one({"_id": resource_id})
    if not res or res.get("active") is False:
        raise HTTPException(422, "That practitioner doesn't exist or is inactive")
    return resource_id


@tenant_router.get("/team")
async def team(tp: TenantPrincipal = Depends(_staff)):
    users = await UsersStore(get_db()).for_tenant(tp.tenant.id)
    members = []
    for u in users:
        mem = next((m for m in u.get("memberships", []) if m.get("tenant_id") == tp.tenant.id), {})
        role = mem.get("role")
        members.append({"id": u["_id"], "email": u.get("email"), "name": u.get("name", ""), "role": role,
                        "resource_id": mem.get("resource_id"),
                        "last_login_at": to_jsonable(u.get("last_login_at")), "disabled": bool(u.get("disabled")),
                        "pending_password": not u.get("password_hash")})
    invites = await get_db().invites.find({"tenant_id": tp.tenant.id}).to_list(100)
    return {"members": members, "invites": [{"email": i["email"], "role": i["role"],
                                             "resource_id": i.get("resource_id"),
                                             "expires_at": to_jsonable(i.get("expires_at"))} for i in invites]}


@tenant_router.post("/team/invite")
async def invite(body: InviteBody, request: Request, tp: TenantPrincipal = Depends(_owner)):
    if tp.tenant.limits.max_users and not tp.user.platform_admin:
        members = len(await UsersStore(get_db()).for_tenant(tp.tenant.id))
        pending = await get_db().invites.count_documents({"tenant_id": tp.tenant.id})
        if members + pending >= tp.tenant.limits.max_users:
            raise HTTPException(402, f"Your plan includes {tp.tenant.limits.max_users} user(s). "
                                     "Upgrade on the Billing page to invite more.")
    resource_id = await _dentist_resource(tp, body.role, body.resource_id)
    token = await auth_service.create_invite(tenant_id=tp.tenant.id, email=body.email, role=body.role,
                                             invited_by=tp.user.user_id, resource_id=resource_id)
    link = auth_service.dashboard_link(f"/accept-invite?token={token}")
    sent = await send_email(body.email, f"You're invited to {tp.tenant.name}",
                            f"{tp.user.name or tp.user.email} invited you to manage {tp.tenant.name}'s AI receptionist.\n\n"
                            f"Accept within 7 days: {link}")
    await _audit(tp, request, "team.invite", "user", body.email, {"role": body.role, "resource_id": resource_id})
    return {"success": True, "emailed": sent, "link": link}


async def _owners(tenant_id: str) -> List[str]:
    users = await UsersStore(get_db()).for_tenant(tenant_id)
    return [u["_id"] for u in users
            if any(m.get("tenant_id") == tenant_id and m.get("role") == "owner" for m in u.get("memberships", []))]


@tenant_router.patch("/team/{user_id}")
async def change_role(user_id: str, body: RoleBody, request: Request, tp: TenantPrincipal = Depends(_owner)):
    owners = await _owners(tp.tenant.id)
    if user_id in owners and body.role != "owner" and len(owners) == 1:
        raise HTTPException(409, "A business needs at least one owner")
    doc = await UsersStore(get_db()).get(user_id)
    if not doc or not any(m.get("tenant_id") == tp.tenant.id for m in doc.get("memberships", [])):
        raise _not_found("Member not found")
    resource_id = await _dentist_resource(tp, body.role, body.resource_id)
    await auth_service.add_membership(user_id, tp.tenant.id, body.role, resource_id)
    await _audit(tp, request, "team.role", "user", user_id, {"role": body.role, "resource_id": resource_id})
    return {"success": True}


@tenant_router.delete("/team/{user_id}")
async def remove_member(user_id: str, request: Request, tp: TenantPrincipal = Depends(_owner)):
    owners = await _owners(tp.tenant.id)
    if user_id in owners and len(owners) == 1:
        raise HTTPException(409, "A business needs at least one owner")
    await auth_service.remove_membership(user_id, tp.tenant.id)
    await get_db().sessions.delete_many({"user_id": user_id, "tenant_id": tp.tenant.id})
    await _audit(tp, request, "team.remove", "user", user_id)
    return {"success": True}


@tenant_router.post("/test-call")
async def test_call(tp: TenantPrincipal = Depends(_staff)):
    """A 15-minute browser-call link to your own assistant (Acceptance: 'place a test call from the browser')."""
    base = (settings.CALL_BASE_URL or settings.WEBHOOK_BASE_URL or "").strip()
    if not base or not settings.LIVEKIT_URL:
        raise HTTPException(503, "Browser calls need LIVEKIT_URL and CALL_BASE_URL")
    room = f"test_{uuid.uuid4().hex[:16]}"
    return {"url": build_call_link(base, room, None if tp.tenant.is_legacy else tp.tenant.id), "room": room}


@tenant_router.get("/audit")
async def audit_log(limit: int = Query(100, ge=1, le=500), tp: TenantPrincipal = Depends(_admin)):
    rows = await tp.repos().audit.find({}, sort=[("at", -1)], limit=limit)
    return {"entries": [_clean(r) for r in rows]}


# ─────────────────────────────────────────────
# INTEGRATIONS (Task 2.8)
# ─────────────────────────────────────────────

@tenant_router.get("/integrations")
async def integrations(tp: TenantPrincipal = Depends(_staff)):
    from core.integrations import google_calendar as gcal
    from core.services import calendar_sync

    g = calendar_sync.google_settings(tp.tenant)
    return {"google_calendar": {
        "available": gcal.configured(), "connected": calendar_sync.connected(tp.tenant),
        "calendar_id": g.get("calendar_id"), "last_sync_at": to_jsonable(g.get("last_sync_at")),
        "last_error": g.get("last_error"), "connected_at": to_jsonable(g.get("connected_at")),
    }, "whatsapp": {"phone_number_id": tp.tenant.integrations.whatsapp.phone_number_id or None},
       "pms": _pms_status(tp.tenant)}


def _pms_status(t: TenantConfig) -> Dict[str, Any]:
    from core.services import pms_sync
    from core.services import scheduling_provider as sp

    s = sp.pms_settings(t)
    return {"available": pms_sync.available_providers(), "provider": sp.provider_name(t),
            "connected": sp.connected(t), "account": s.get("account") or {},
            "last_sync_at": to_jsonable(s.get("last_sync_at")), "last_error": s.get("last_error"),
            "connected_at": to_jsonable(s.get("connected_at"))}


class PMSBody(BaseModel):
    provider: Literal["cliniko", "dentally"]
    api_key: str = Field(min_length=10, max_length=500)


def _pms_http_error(e: Exception) -> HTTPException:
    return HTTPException(422 if not getattr(e, "transient", False) else 502, f"The practice system said: {e}")


@tenant_router.get("/reactivation")
async def reactivation_status(tp: TenantPrincipal = Depends(_staff)):
    """Dental patient recall: settings, whether it can run, and the last 30 days."""
    from core.services import reactivation

    t = tp.tenant
    since = utcnow() - timedelta(days=30)
    open_rows = await tp.repos().recalls.count({"status": {"$in": list(reactivation.OPEN_STATUSES)}})
    return {"available": reactivation.available(t), "settings": t.reactivation.model_dump(),
            "default_template": t.reactivation.effective_template if not t.reactivation.template else None,
            "queued": open_rows, "last_30_days": await reactivation.stats(t, since, utcnow())}


@tenant_router.post("/reactivation/preview")
async def reactivation_preview(tp: TenantPrincipal = Depends(_admin)):
    """How many patients would get a recall text now (asks the practice's system; nothing is sent)."""
    from core.services import reactivation
    from core.services.scheduling_provider import PMSError

    if not reactivation.available(tp.tenant):
        raise HTTPException(409, "Patient recall needs a dental practice in Australia or the UK with Cliniko or "
                                 "Dentally connected")
    try:
        return await reactivation.preview(tp.tenant)
    except PMSError as e:
        raise _pms_http_error(e)


@tenant_router.put("/integrations/pms")
async def pms_connect(body: PMSBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    """Connect Cliniko (AU) or Dentally (UK): the key is checked against the PMS, stored encrypted, and
    the dentists and appointment types are copied in."""
    from core.services import pms_sync
    from core.services.scheduling_provider import PMSError

    if tp.tenant.is_legacy:
        raise HTTPException(409, "Run migration m0003 first")
    try:
        result = await pms_sync.connect(tp.tenant, body.provider, body.api_key, user_id=tp.user.user_id)
    except PMSError as e:
        raise _pms_http_error(e)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await _audit(tp, request, "integration.connect", body.provider, tp.tenant.id, {})
    return {"success": True, **to_jsonable(result)}


@tenant_router.post("/integrations/pms/test")
async def pms_test(tp: TenantPrincipal = Depends(_admin)):
    from core.services import pms_sync
    from core.services.scheduling_provider import PMSError

    try:
        return {"success": True, "account": to_jsonable(await pms_sync.test(tp.tenant))}
    except PMSError as e:
        raise _pms_http_error(e)
    except ValueError as e:
        raise HTTPException(409, str(e))


@tenant_router.post("/integrations/pms/sync")
async def pms_sync_now(tp: TenantPrincipal = Depends(_admin)):
    from core.services import pms_sync
    from core.services import scheduling_provider as sp

    if not sp.connected(tp.tenant):
        raise HTTPException(409, "Connect a practice-management system first")
    return {"success": True, **(await pms_sync.sync(tp.tenant))}


@tenant_router.delete("/integrations/pms")
async def pms_disconnect(request: Request, tp: TenantPrincipal = Depends(_admin)):
    from core.services import pms_sync
    from core.services import scheduling_provider as sp

    name = sp.provider_name(tp.tenant)
    await pms_sync.disconnect(tp.tenant)
    await _audit(tp, request, "integration.disconnect", name, tp.tenant.id, {})
    return {"success": True}


@tenant_router.post("/integrations/google/connect")
async def google_connect(tp: TenantPrincipal = Depends(_admin)):
    from core.integrations import google_calendar as gcal

    if not gcal.configured():
        raise HTTPException(503, "Google Calendar isn't set up on this server (GOOGLE_OAUTH_CLIENT_ID)")
    if tp.tenant.is_legacy:
        raise HTTPException(409, "Run migration m0003 first")
    return {"url": gcal.auth_url(tp.tenant.id, tp.user.user_id)}


class CalendarBody(BaseModel):
    calendar_id: str = Field(min_length=1, max_length=200)


@tenant_router.patch("/integrations/google")
async def google_settings(body: CalendarBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    from core.services import calendar_sync

    if not calendar_sync.connected(tp.tenant):
        raise HTTPException(409, "Connect Google Calendar first")
    await calendar_sync._set_status(tp.tenant.id, calendar_id=body.calendar_id)
    await _audit(tp, request, "integration.update", "google_calendar", tp.tenant.id, {"calendar_id": body.calendar_id})
    return {"success": True}


@tenant_router.post("/integrations/google/sync")
async def google_sync(tp: TenantPrincipal = Depends(_admin)):
    from core.services import calendar_sync

    if not calendar_sync.connected(tp.tenant):
        raise HTTPException(409, "Connect Google Calendar first")
    counts = await calendar_sync.sync_busy(tp.tenant)
    return {"success": bool(counts) or counts == {}, "busy_blocks": counts}


@tenant_router.post("/integrations/google/disconnect")
async def google_disconnect(request: Request, tp: TenantPrincipal = Depends(_admin)):
    from core.services import calendar_sync

    await calendar_sync.disconnect(tp.tenant.id)
    await _audit(tp, request, "integration.disconnect", "google_calendar", tp.tenant.id)
    return {"success": True}


# ─────────────────────────────────────────────
# OUTGOING WEBHOOKS (Task 2.8 escape hatch)
# ─────────────────────────────────────────────

MAX_WEBHOOKS = 10


def _webhook_view(hook: Any) -> Dict[str, Any]:
    doc = hook.model_dump() if hasattr(hook, "model_dump") else dict(hook)
    doc.pop("secret", None)
    return to_jsonable(doc)


async def _fresh_webhooks(tenant_id: str) -> List[Dict[str, Any]]:
    """Read the list from the database, not the 60 s cache, so two quick edits never clobber each other."""
    from core.repositories.repos import TenantsStore

    doc = await TenantsStore(get_db()).get(tenant_id) or {}
    return list(((doc.get("integrations") or {}).get("webhooks")) or [])


async def _save_webhooks(tp: TenantPrincipal, hooks: List[Dict[str, Any]]) -> None:
    from core.repositories.repos import TenantsStore
    from core.tenancy import invalidate

    await TenantsStore(get_db()).update(tp.tenant.id, {"integrations.webhooks": hooks})
    invalidate(tp.tenant.id)


@tenant_router.get("/integrations/webhooks")
async def list_webhooks(tp: TenantPrincipal = Depends(_admin)):
    from core.integrations.webhooks import WEBHOOK_EVENTS

    return {"endpoints": [_webhook_view(h) for h in tp.tenant.integrations.webhooks], "events": list(WEBHOOK_EVENTS)}


class WebhookBody(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    events: List[str] = Field(default_factory=list)


@tenant_router.post("/integrations/webhooks")
async def create_webhook(body: WebhookBody, request: Request, tp: TenantPrincipal = Depends(_admin)):
    from core.integrations.webhooks import new_secret
    from core.tenancy.models import WebhookEndpoint

    if tp.tenant.is_legacy:
        raise HTTPException(409, "Run migration m0003 first")
    url = _public_http_url(body.url)
    hooks = await _fresh_webhooks(tp.tenant.id)
    if len(hooks) >= MAX_WEBHOOKS:
        raise HTTPException(409, f"At most {MAX_WEBHOOKS} webhooks per business")
    try:
        hook = WebhookEndpoint(url=url, secret=new_secret(), events=body.events, created_at=utcnow())
    except ValueError as e:
        raise HTTPException(422, str(e)[:200])
    hooks.append(hook.model_dump())
    await _save_webhooks(tp, hooks)
    await _audit(tp, request, "webhook.create", "webhook", hook.id, {"url": hook.url, "events": hook.events})
    return {"endpoint": _webhook_view(hook), "secret": hook.secret}


@tenant_router.delete("/integrations/webhooks/{hook_id}")
async def delete_webhook(hook_id: str, request: Request, tp: TenantPrincipal = Depends(_admin)):
    hooks = await _fresh_webhooks(tp.tenant.id)
    kept = [h for h in hooks if h.get("id") != hook_id]
    if len(kept) == len(hooks):
        raise _not_found("No such webhook")
    await _save_webhooks(tp, kept)
    await _audit(tp, request, "webhook.delete", "webhook", hook_id)
    return {"success": True}


@tenant_router.post("/integrations/webhooks/{hook_id}/test")
async def test_webhook(hook_id: str, tp: TenantPrincipal = Depends(_admin)):
    """Deliver a `ping` right now and report the HTTP status (not queued, so the owner sees the result)."""
    from core.integrations import webhooks as wh

    hook = next((h for h in tp.tenant.integrations.webhooks if h.id == hook_id), None)
    if hook is None:
        raise _not_found("No such webhook")
    result = await wh.deliver(hook, wh.envelope(tp.tenant, "ping", {"message": "Hello from your AI receptionist"}))
    await wh._record(tp.tenant.id, hook.id, "ping", result)
    return result


# ─────────────────────────────────────────────
# VOICES (Task 2.10: voice pick with audio previews)
# ─────────────────────────────────────────────

@tenant_router.get("/voices")
async def voices(provider: str = Query("elevenlabs", max_length=20), tp: TenantPrincipal = Depends(_staff)):
    from core.integrations import elevenlabs

    if provider == "deepgram":
        from core.voices import AURA_DEFAULT_FOR_COUNTRY, aura_catalogue

        return {"provider": provider, "available": bool(settings.DEEPGRAM_API_KEY), "voices": aura_catalogue(),
                "default_voice_id": AURA_DEFAULT_FOR_COUNTRY.get(tp.tenant.country.upper())
                or settings.DEEPGRAM_TTS_MODEL,
                "note": None if settings.DEEPGRAM_API_KEY else "Deepgram isn't set up on this server yet."}
    if provider != "elevenlabs":
        return {"provider": provider, "available": False, "voices": [],
                "note": "Voice previews are available for ElevenLabs voices. Enter the voice ID for other providers."}
    if not elevenlabs.configured():
        return {"provider": provider, "available": False, "voices": [],
                "note": "Voice previews aren't available yet, so calls use the standard voice. "
                        "Paste an ElevenLabs voice ID to choose a different one."}
    return {"provider": provider, "available": True, "voices": await elevenlabs.list_voices(),
            "default_voice_id": settings.ELEVENLABS_VOICE_ID or None}


# Re-exported for the availability page.
spoken_slot = av.spoken
