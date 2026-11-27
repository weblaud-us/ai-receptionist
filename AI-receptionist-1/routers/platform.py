"""
Platform admin API (Roadmap Tasks 2.3.4 and 2.10) — prefix /platform, for you only.

A separate router with its own dependency (`require_platform_admin`); every write is recorded in
the target tenant's `audit_log`.

GET    /platform/verticals               vertical catalog (for the provisioning form; no tenant needed)
GET    /platform/tenants                 every business: status, vertical, calls and minutes (30 d)
POST   /platform/tenants                 provision a business in one step: profile (from the website import),
                                         owner (welcome e-mail), services + FAQs, and a number order
POST   /platform/site-import             read a website → draft profile / hours / services / FAQs (nothing saved)
GET    /platform/numbers/available       numbers we could buy (Twilio search)
GET    /platform/numbers/owned           numbers in our Twilio account no business uses
POST   /platform/tenants/{id}/numbers    buy or connect a number: Twilio trunk → LiveKit trunk → routing → test call
GET    /platform/tenants/{id}            details: config, usage, team, numbers, recent calls, audit trail
PATCH  /platform/tenants/{id}            status, limits, region, integrations (WhatsApp number id)…
POST   /platform/tenants/{id}/members    give someone access (new people get a set-password link)
PATCH  /platform/tenants/{id}/members/{user_id}             change role (a business keeps ≥ 1 owner)
DELETE /platform/tenants/{id}/members/{user_id}             remove access
POST   /platform/tenants/{id}/members/{user_id}/reset-link  one-time password link for a locked-out member
GET    /platform/phone-numbers           the routing table
POST   /platform/phone-numbers           assign a number to a business
DELETE /platform/phone-numbers/{e164}    release a number
POST   /platform/users                   create a platform admin
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config import settings
from core import ratelimit
from core.auth import Principal, require_platform_admin, service as auth_service
from core.integrations.email import send_email
from core.phone import digits
from core.prompts.verticals import vertical_catalog
from core.repositories import Repos
from core.repositories.base import utcnow
from core.repositories.repos import PhoneNumbersStore, TenantsStore, UsersStore
from core.services import numbers as number_service, site_import
from core.services.scheduling import ACTIVE_STATUSES
from core.services.tenant_admin import EDITABLE_FIELDS, PLATFORM_FIELDS, create_tenant, update_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tenancy.models import VERTICALS
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

platform_router = APIRouter(prefix="/platform", tags=["Platform"])


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return to_jsonable(doc)


async def _audit(tenant_id: str, user: Principal, request: Request, action: str, target_type: str,
                 target_id: str, diff: Optional[Dict[str, Any]] = None) -> None:
    await Repos(get_db(), tenant_id).audit.record(user_id=f"platform:{user.user_id}", action=action,
                                                  target_type=target_type, target_id=target_id,
                                                  diff=to_jsonable(diff or {}),
                                                  ip=request.client.host if request.client else "")


class NumberChoice(BaseModel):
    source: Literal["buy", "owned", "none"] = "none"
    e164: str = Field(default="", max_length=20)


class CreateTenantBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    vertical: str = "generic"
    timezone: str = Field(default="America/New_York", max_length=64)
    country: str = Field(default="US", max_length=2)
    currency: str = Field(default="USD", max_length=3)
    phone: str = Field(default="", max_length=32)
    owner_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    owner_name: str = Field(default="", max_length=120)
    inbound_number: Optional[str] = Field(default=None, max_length=32)   # map only (number already on the trunk)
    region: Literal["us", "eu", "me"] = "us"
    # From the website import (all optional): the reviewed draft.
    description: str = Field(default="", max_length=300)
    website: str = Field(default="", max_length=300)
    address: Dict[str, str] = Field(default_factory=dict)
    hours: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    services: List[Dict[str, Any]] = Field(default_factory=list, max_length=60)
    faqs: List[Dict[str, Any]] = Field(default_factory=list, max_length=40)
    links: Dict[str, str] = Field(default_factory=dict)
    # Automation.
    number: NumberChoice = Field(default_factory=NumberChoice)   # buy or connect a number, end to end
    send_invite: bool = True                                     # e-mail the owner their set-password link
    mark_onboarded: bool = True                                  # skip the owner's setup wizard


class AssignNumberBody(BaseModel):
    e164: str = Field(min_length=8, max_length=20)
    tenant_id: str
    purpose: Literal["inbound", "outbound_sales"] = "inbound"
    provider: str = "twilio"
    provider_sid: str = ""
    livekit_inbound_trunk_id: str = ""


class CreateAdminBody(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=256)


def _e164(raw: str) -> str:
    d = digits(raw)
    if len(d) < 8:
        raise HTTPException(422, "Phone number must be in international format, e.g. +15125551234")
    return "+" + d


@platform_router.get("/verticals")
async def verticals(admin: Principal = Depends(require_platform_admin)):
    return {"verticals": vertical_catalog()}


@platform_router.get("/tenants")
async def list_tenants(status: Optional[str] = None, admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    tenants = await TenantsStore(db).list(status=status, limit=1000)
    since = utcnow() - timedelta(days=30)
    stats: Dict[str, Dict[str, Any]] = {}
    async for row in db.calls.aggregate([  # cross-tenant: per-business call counts (platform admin)
        {"$match": {"started_at": {"$gte": since}}},
        {"$group": {"_id": "$tenant_id", "calls": {"$sum": 1}, "seconds": {"$sum": {"$ifNull": ["$duration_s", 0]}}}},
    ]):
        stats[str(row["_id"])] = row
    numbers: Dict[str, list] = {}
    for n in await PhoneNumbersStore(db).list(limit=5000):
        if n.get("status") != "released":
            numbers.setdefault(n["tenant_id"], []).append(n["e164"])
    out = []
    for t in tenants:
        s = stats.get(t["_id"], {})
        out.append({"id": t["_id"], "name": t.get("name"), "slug": t.get("slug"), "vertical": t.get("vertical"),
                    "status": t.get("status"), "region": t.get("region"), "timezone": t.get("timezone"),
                    "calls_30d": s.get("calls", 0), "minutes_30d": round((s.get("seconds") or 0) / 60, 1),
                    "included_minutes": (t.get("limits") or {}).get("included_minutes"),
                    "max_concurrent_calls": (t.get("limits") or {}).get("max_concurrent_calls", 3),
                    "numbers": numbers.get(t["_id"], []), "created_at": to_jsonable(t.get("created_at"))})
    return {"tenants": out, "total": len(out)}


def _numbers_http(exc: number_service.ProvisioningError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _check_number_free(e164: str) -> None:
    existing = await PhoneNumbersStore(get_db()).get(e164)
    if existing and existing.get("status") != "released":
        raise HTTPException(409, "That number already belongs to a business")
    if await get_db().number_orders.find_one({"e164": e164, "status": {"$in": list(number_service.OPEN_STATUSES)}}):
        raise HTTPException(409, "That number is already being set up")


async def _welcome_owner(tenant_name: str, email: str, link: Optional[str]) -> bool:
    if link:
        body = (f"Your AI receptionist for {tenant_name} is set up.\n\n"
                f"Choose your password to sign in (this link works for 7 days):\n{link}\n\n"
                "From the dashboard you can see every call, change what your assistant says, and manage bookings.")
    else:
        body = (f"You now have access to {tenant_name}'s AI receptionist.\n\n"
                f"Sign in with your existing account: {auth_service.dashboard_link('/login')}")
    return await send_email(email, f"LaudLine: your AI receptionist for {tenant_name} is ready", body)


@platform_router.post("/tenants")
async def provision_tenant(body: CreateTenantBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    """Create a business in one step: profile (optionally from the website import), the owner (welcome
    e-mail), its services and FAQs, and its phone number (bought or connected in the background)."""
    if body.vertical not in VERTICALS:
        raise HTTPException(422, f"vertical must be one of {', '.join(VERTICALS)}")
    users = UsersStore(get_db())
    if body.inbound_number:
        await _check_number_free(_e164(body.inbound_number))
    number = _e164(body.number.e164) if body.number.source != "none" else ""
    if number:
        if number_service.mode() == "off":
            raise HTTPException(503, "Automatic numbers are off: " + "; ".join(number_service.setup_problems()))
        await _check_number_free(number)

    extra: Dict[str, Any] = {"notifications": {"owner_email": body.owner_email}}
    for key in ("description", "website"):
        if getattr(body, key).strip():
            extra[key] = getattr(body, key).strip()
    if any(v for v in body.address.values()):
        extra["address"] = {k: v for k, v in body.address.items() if v}
    if body.hours:
        extra["hours"] = body.hours
    if body.links:
        extra["links"] = {k: v for k, v in body.links.items() if v}
    if body.mark_onboarded:
        extra["onboarding"] = {"step": "number", "completed_at": utcnow().isoformat(), "by": "platform"}
    try:                                              # one validated write: a bad field creates nothing
        tenant = await create_tenant(name=body.name, vertical=body.vertical, timezone=body.timezone,
                                     country=body.country.upper(), currency=body.currency.upper(), phone=body.phone,
                                     region=body.region, extra=extra)
    except ValueError as e:
        raise HTTPException(422, str(e)[:300])
    actor = f"platform:{admin.user_id}"
    ip = request.client.host if request.client else ""
    added = {"services": 0, "knowledge": 0}
    if body.services or body.faqs:                    # the website's rows replace the vertical's examples
        added = await site_import.apply_draft(tenant, {"services": body.services, "faqs": body.faqs},
                                              user_id=actor, ip=ip, enforce_plan=False)

    owner = await users.by_email(body.owner_email)
    if owner:
        await auth_service.add_membership(owner["_id"], tenant.id, "owner")
        link = None
    else:
        owner = await auth_service.create_user(email=body.owner_email, password=None, name=body.owner_name,
                                               tenant_id=tenant.id, role="owner")
        token = await auth_service.start_password_reset(body.owner_email, ttl=auth_service.INVITE_TTL)
        link = auth_service.dashboard_link(f"/reset-password?token={token}") if token else None
    emailed = await _welcome_owner(tenant.name, owner["email"], link) if body.send_invite else False

    if body.inbound_number:
        await PhoneNumbersStore(get_db()).assign({"e164": _e164(body.inbound_number), "tenant_id": tenant.id,
                                                  "purpose": "inbound", "provider": "twilio", "country": body.country.upper()})
        invalidate()
    order, number_error = None, None
    if number:
        try:
            order = await number_service.start_order(get_db(), tenant, number, requested_by=actor,
                                                     enforce_limits=False, owned=body.number.source == "owned")
            if order["status"] == "pending":
                await number_service.run_order_in_background(order["_id"])
        except number_service.ProvisioningError as exc:   # the business stays; add the number from its page
            number_error = str(exc)
    await _audit(tenant.id, admin, request, "tenant.create", "tenant", tenant.id,
                 {"name": body.name, "vertical": body.vertical, "from_website": bool(body.website),
                  "number": body.number.source, "invite_emailed": emailed, **added})
    logger.info("Provisioned tenant %s (%s)", tenant.id, body.vertical)
    return {"tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "vertical": tenant.vertical},
            "owner": {"id": owner["_id"], "email": owner["email"]},
            "set_password_link": link, "emailed": emailed, "added": added,
            "order": to_jsonable(number_service.public_order(order)) if order else None,
            "number_error": number_error}


# ── Website import and numbers (the New business wizard) ─────────────────────

class SiteImportBody(BaseModel):
    url: str = Field(min_length=4, max_length=500)


@platform_router.post("/site-import")
async def platform_site_import(body: SiteImportBody, admin: Principal = Depends(require_platform_admin)):
    """Read a business's website and draft its setup. Nothing is saved."""
    if not await ratelimit.allow_fixed(f"site-import:platform:{admin.user_id}", 30, 3600):
        raise HTTPException(429, "Too many website imports. Try again in an hour.")
    try:
        return {"draft": await site_import.import_site(body.url)}
    except site_import.SiteImportError as e:
        raise HTTPException(e.status, str(e))


@platform_router.post("/site-import/stream")
async def platform_site_import_stream(body: SiteImportBody, admin: Principal = Depends(require_platform_admin)):
    """The same import, streamed as server-sent events (see routers/tenant.py)."""
    from routers.tenant import site_import_error
    from utils.sse import progress_response

    if not await ratelimit.allow_fixed(f"site-import:platform:{admin.user_id}", 30, 3600):
        raise HTTPException(429, "Too many website imports. Try again in an hour.")
    return progress_response(lambda emit: site_import.import_site(body.url, on_progress=emit),
                             result=lambda draft: {"draft": draft}, on_error=site_import_error)


@platform_router.get("/numbers/available")
async def available_numbers(country: str = Query("US", min_length=2, max_length=2),
                            area_code: str = Query("", max_length=6), contains: str = Query("", max_length=10),
                            admin: Principal = Depends(require_platform_admin)):
    """Numbers we could buy right now (Twilio search)."""
    mode = number_service.mode()
    if mode == "off":
        return {"numbers": [], "mode": mode, "problems": number_service.setup_problems(), "instant": False}
    try:
        found = await number_service.search(country, area_code=area_code.strip(), contains=contains.strip(), limit=20)
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    return {"numbers": found, "mode": mode, "problems": [],
            "instant": country.upper() in settings.number_instant_countries or mode == "fake"}


@platform_router.get("/numbers/owned")
async def owned_numbers(admin: Principal = Depends(require_platform_admin)):
    """Numbers already in our Twilio account that no business uses."""
    mode = number_service.mode()
    if mode == "off":
        return {"numbers": [], "mode": mode, "problems": number_service.setup_problems()}
    try:
        return {"numbers": await number_service.list_owned(get_db()), "mode": mode, "problems": []}
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)


class OrderNumberBody(BaseModel):
    e164: str = Field(min_length=8, max_length=20)
    source: Literal["buy", "owned"] = "buy"


@platform_router.post("/tenants/{tenant_id}/numbers")
async def order_tenant_number(tenant_id: str, body: OrderNumberBody, request: Request,
                              admin: Principal = Depends(require_platform_admin)):
    """Buy (or connect an owned) number for a business: Twilio trunk, LiveKit trunk and routing, then a test call."""
    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    try:
        order = await number_service.start_order(get_db(), tenant, body.e164, requested_by=f"platform:{admin.user_id}",
                                                 enforce_limits=False, owned=body.source == "owned")
    except number_service.ProvisioningError as exc:
        raise _numbers_http(exc)
    if order["status"] == "pending":
        await number_service.run_order_in_background(order["_id"])
    return {"order": to_jsonable(number_service.public_order(order))}            # start_order audits it


def _role_in(user: Dict[str, Any], tenant_id: str) -> Optional[str]:
    return next((m.get("role") for m in user.get("memberships", []) if m.get("tenant_id") == tenant_id), None)


def _member_view(u: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    return {"id": u["_id"], "email": u["email"], "name": u.get("name", ""), "role": _role_in(u, tenant_id),
            "last_login_at": to_jsonable(u.get("last_login_at")), "disabled": bool(u.get("disabled")),
            "pending_password": not u.get("password_hash"), "mfa": bool((u.get("mfa") or {}).get("enabled"))}


@platform_router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, admin: Principal = Depends(require_platform_admin)):
    """Everything the details page shows: the full config, usage, team, numbers, recent calls and audit trail."""
    db = get_db()
    doc = await TenantsStore(db).get(tenant_id)
    if not doc:
        raise HTTPException(404, "Tenant not found")
    repos = Repos(db, tenant_id)
    now = utcnow()
    usage = await repos.calls.aggregate([
        {"$match": {"started_at": {"$gte": now - timedelta(days=30)}}},
        {"$group": {"_id": None, "calls": {"$sum": 1}, "seconds": {"$sum": {"$ifNull": ["$duration_s", 0]}}}},
    ], limit=1)
    calls = await repos.calls.find(sort=[("started_at", -1)], limit=15, projection={
        "_id": 0, "call_id": 1, "started_at": 1, "duration_s": 1, "outcome": 1, "status": 1, "direction": 1,
        "caller_phone": 1, "summary": 1, "after_hours": 1})
    audit = await repos.audit.find(sort=[("at", -1)], limit=50,
                                   projection={"_id": 0, "tenant_id": 0})
    members = await UsersStore(db).for_tenant(tenant_id)
    invites = await db.invites.find({"tenant_id": tenant_id}).to_list(100)
    u = usage[0] if usage else {}
    stats = {
        "calls_30d": u.get("calls", 0), "minutes_30d": round((u.get("seconds") or 0) / 60, 1),
        "calls_total": await repos.calls.count(),
        "contacts": await repos.contacts.count(),
        "appointments_upcoming": await repos.appointments.count({"start": {"$gte": now},
                                                                 "status": {"$in": ACTIVE_STATUSES}}),
        "leads": await repos.leads.count(),
        "messages": await repos.messages.count(),
        "knowledge": await repos.knowledge.count(),
        "services": await repos.services.count(),
    }
    return {"tenant": _clean(doc), "stats": stats,
            "numbers": [to_jsonable({k: v for k, v in n.items() if k != "_id"})
                        for n in await PhoneNumbersStore(db).for_tenant(tenant_id)],
            "members": [_member_view(m, tenant_id) for m in members],
            "invites": [{"email": i["email"], "role": i["role"], "expires_at": to_jsonable(i.get("expires_at"))}
                        for i in invites],
            "recent_calls": to_jsonable(calls), "audit": to_jsonable(audit),
            "number_orders": [to_jsonable(number_service.public_order(o)) for o in
                              await db.number_orders.find({"tenant_id": tenant_id}).sort("created_at", -1).to_list(10)]}


class AddMemberBody(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(default="", max_length=120)
    role: Literal["owner", "admin", "staff"] = "staff"


class MemberRoleBody(BaseModel):
    role: Literal["owner", "admin", "staff"]


async def _tenant_member(tenant_id: str, user_id: str) -> Dict[str, Any]:
    if not await TenantsStore(get_db()).get(tenant_id):
        raise HTTPException(404, "Tenant not found")
    doc = await UsersStore(get_db()).get(user_id)
    if not doc or _role_in(doc, tenant_id) is None:
        raise HTTPException(404, "Member not found")
    return doc


async def _owner_ids(tenant_id: str) -> list:
    return [u["_id"] for u in await UsersStore(get_db()).for_tenant(tenant_id) if _role_in(u, tenant_id) == "owner"]


@platform_router.post("/tenants/{tenant_id}/members")
async def add_member(tenant_id: str, body: AddMemberBody, request: Request,
                     admin: Principal = Depends(require_platform_admin)):
    """Give someone access to a business. New people get a one-time set-password link (not e-mailed)."""
    if not await TenantsStore(get_db()).get(tenant_id):
        raise HTTPException(404, "Tenant not found")
    users = UsersStore(get_db())
    existing = await users.by_email(body.email)
    link = None
    if existing:
        await auth_service.add_membership(existing["_id"], tenant_id, body.role)
        user = existing
    else:
        user = await auth_service.create_user(email=body.email, password=None, name=body.name,
                                              tenant_id=tenant_id, role=body.role)
        token = await auth_service.start_password_reset(body.email)
        link = auth_service.dashboard_link(f"/reset-password?token={token}") if token else None
    await _audit(tenant_id, admin, request, "team.add", "user", user["_id"], {"email": user["email"], "role": body.role})
    return {"success": True, "member": {"id": user["_id"], "email": user["email"]}, "set_password_link": link}


@platform_router.patch("/tenants/{tenant_id}/members/{user_id}")
async def change_member_role(tenant_id: str, user_id: str, body: MemberRoleBody, request: Request,
                             admin: Principal = Depends(require_platform_admin)):
    await _tenant_member(tenant_id, user_id)
    owners = await _owner_ids(tenant_id)
    if user_id in owners and body.role != "owner" and len(owners) == 1:
        raise HTTPException(409, "A business needs at least one owner")
    await auth_service.add_membership(user_id, tenant_id, body.role)
    await _audit(tenant_id, admin, request, "team.role", "user", user_id, {"role": body.role})
    return {"success": True}


@platform_router.delete("/tenants/{tenant_id}/members/{user_id}")
async def remove_member(tenant_id: str, user_id: str, request: Request,
                        admin: Principal = Depends(require_platform_admin)):
    await _tenant_member(tenant_id, user_id)
    owners = await _owner_ids(tenant_id)
    if user_id in owners and len(owners) == 1:
        raise HTTPException(409, "A business needs at least one owner")
    await auth_service.remove_membership(user_id, tenant_id)
    await get_db().sessions.delete_many({"user_id": user_id, "tenant_id": tenant_id})
    await _audit(tenant_id, admin, request, "team.remove", "user", user_id)
    return {"success": True}


@platform_router.post("/tenants/{tenant_id}/members/{user_id}/reset-link")
async def member_reset_link(tenant_id: str, user_id: str, request: Request,
                            admin: Principal = Depends(require_platform_admin)):
    """A one-time password link for a member who is locked out (valid 1 hour). Returned, not e-mailed."""
    doc = await _tenant_member(tenant_id, user_id)
    if doc.get("disabled"):
        raise HTTPException(409, "This account is disabled")
    token = await auth_service.start_password_reset(doc["email"])
    if not token:
        raise HTTPException(409, "Couldn't create a link for this account")
    await _audit(tenant_id, admin, request, "team.reset_link", "user", user_id)
    return {"link": auth_service.dashboard_link(f"/reset-password?token={token}")}


@platform_router.patch("/tenants/{tenant_id}")
async def patch_tenant(tenant_id: str, body: Dict[str, Any], request: Request,
                       admin: Principal = Depends(require_platform_admin)):
    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    try:
        updated = await update_tenant(tenant, body, allowed=EDITABLE_FIELDS | PLATFORM_FIELDS,
                                      user_id=f"platform:{admin.user_id}",
                                      ip=request.client.host if request.client else "", enforce_plan=False)
    except ValueError as e:
        raise HTTPException(422, str(e)[:500])
    return {"tenant": _clean(updated.to_doc())}


@platform_router.get("/phone-numbers")
async def list_numbers(admin: Principal = Depends(require_platform_admin)):
    rows = await PhoneNumbersStore(get_db()).list(limit=5000)
    return {"numbers": [to_jsonable({k: v for k, v in r.items() if k != "_id"}) for r in rows]}


@platform_router.post("/phone-numbers")
async def assign_number(body: AssignNumberBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    if not await TenantsStore(get_db()).get(body.tenant_id):
        raise HTTPException(404, "Tenant not found")
    e164 = _e164(body.e164)
    store = PhoneNumbersStore(get_db())
    existing = await store.get(e164)
    if existing and existing.get("status") != "released" and existing.get("tenant_id") != body.tenant_id:
        raise HTTPException(409, "That number belongs to another business. Release it first.")
    doc = {**body.model_dump(), "e164": e164, "status": "active", "assigned_at": utcnow()}
    await store.assign(doc)
    invalidate()
    await _audit(body.tenant_id, admin, request, "number.assign", "phone_number", e164)
    return {"success": True, "number": to_jsonable(doc)}


@platform_router.delete("/phone-numbers/{e164}")
async def release_number(e164: str, request: Request, admin: Principal = Depends(require_platform_admin)):
    number = _e164(e164)
    store = PhoneNumbersStore(get_db())
    existing = await store.get(number)
    if not existing:
        raise HTTPException(404, "Number not found")
    await store.release(number)
    invalidate()
    await _audit(existing["tenant_id"], admin, request, "number.release", "phone_number", number)
    return {"success": True}


@platform_router.post("/users")
async def create_platform_admin(body: CreateAdminBody, admin: Principal = Depends(require_platform_admin)):
    doc = await auth_service.create_user(email=body.email, password=body.password, name=body.name, platform_admin=True)
    return {"id": doc["_id"], "email": doc["email"]}
