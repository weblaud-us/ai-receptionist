"""
Platform admin API (Roadmap Tasks 2.3.4 and 2.10) — prefix /platform, for you only.

A separate router with its own dependency (`require_platform_admin`); every write is recorded in
the target tenant's `audit_log`.

GET    /platform/tenants                 every business: status, vertical, calls and minutes (30 d)
POST   /platform/tenants                 provision a business + owner (owner gets a set-password link)
GET    /platform/tenants/{id}
PATCH  /platform/tenants/{id}            status, limits, region, integrations (WhatsApp number id)…
GET    /platform/phone-numbers           the routing table
POST   /platform/phone-numbers           assign a number to a business
DELETE /platform/phone-numbers/{e164}    release a number
POST   /platform/users                   create a platform admin
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import Principal, require_platform_admin, service as auth_service
from core.phone import digits
from core.repositories import Repos
from core.repositories.base import utcnow
from core.repositories.repos import PhoneNumbersStore, TenantsStore, UsersStore
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


class CreateTenantBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    vertical: str = "generic"
    timezone: str = Field(default="America/New_York", max_length=64)
    country: str = Field(default="US", max_length=2)
    currency: str = Field(default="USD", max_length=3)
    phone: str = Field(default="", max_length=32)
    owner_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    owner_name: str = Field(default="", max_length=120)
    inbound_number: Optional[str] = Field(default=None, max_length=32)
    region: Literal["us", "eu", "me"] = "us"


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


@platform_router.get("/tenants")
async def list_tenants(status: Optional[str] = None, admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    tenants = await TenantsStore(db).list(status=status, limit=1000)
    since = utcnow() - timedelta(days=30)
    stats: Dict[str, Dict[str, Any]] = {}
    async for row in db.calls.aggregate([
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
                    "numbers": numbers.get(t["_id"], []), "created_at": to_jsonable(t.get("created_at"))})
    return {"tenants": out, "total": len(out)}


@platform_router.post("/tenants")
async def provision_tenant(body: CreateTenantBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    if body.vertical not in VERTICALS:
        raise HTTPException(422, f"vertical must be one of {', '.join(VERTICALS)}")
    users = UsersStore(get_db())
    if body.inbound_number and await PhoneNumbersStore(get_db()).get(_e164(body.inbound_number)):
        existing = await PhoneNumbersStore(get_db()).get(_e164(body.inbound_number))
        if existing and existing.get("status") != "released":
            raise HTTPException(409, "That number already belongs to a business")
    try:
        tenant = await create_tenant(name=body.name, vertical=body.vertical, timezone=body.timezone,
                                     country=body.country.upper(), currency=body.currency.upper(), phone=body.phone,
                                     region=body.region,
                                     extra={"notifications": {"owner_email": body.owner_email}})
    except ValueError as e:
        raise HTTPException(422, str(e)[:300])

    owner = await users.by_email(body.owner_email)
    if owner:
        await auth_service.add_membership(owner["_id"], tenant.id, "owner")
        link = None
    else:
        owner = await auth_service.create_user(email=body.owner_email, password=None, name=body.owner_name,
                                               tenant_id=tenant.id, role="owner")
        token = await auth_service.start_password_reset(body.owner_email)
        link = auth_service.dashboard_link(f"/reset-password?token={token}") if token else None
    if body.inbound_number:
        await PhoneNumbersStore(get_db()).assign({"e164": _e164(body.inbound_number), "tenant_id": tenant.id,
                                                  "purpose": "inbound", "provider": "twilio", "country": body.country.upper()})
        invalidate()
    await _audit(tenant.id, admin, request, "tenant.create", "tenant", tenant.id, {"name": body.name, "vertical": body.vertical})
    logger.info("Provisioned tenant %s (%s)", tenant.id, body.vertical)
    return {"tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "vertical": tenant.vertical},
            "owner": {"id": owner["_id"], "email": owner["email"]},
            "set_password_link": link}


@platform_router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, admin: Principal = Depends(require_platform_admin)):
    doc = await TenantsStore(get_db()).get(tenant_id)
    if not doc:
        raise HTTPException(404, "Tenant not found")
    members = await UsersStore(get_db()).for_tenant(tenant_id)
    return {"tenant": _clean(doc), "numbers": [to_jsonable({k: v for k, v in n.items() if k != "_id"})
                                               for n in await PhoneNumbersStore(get_db()).for_tenant(tenant_id)],
            "members": [{"id": u["_id"], "email": u["email"], "name": u.get("name", ""),
                         "role": next((m["role"] for m in u.get("memberships", []) if m["tenant_id"] == tenant_id), None)}
                        for u in members]}


@platform_router.patch("/tenants/{tenant_id}")
async def patch_tenant(tenant_id: str, body: Dict[str, Any], request: Request,
                       admin: Principal = Depends(require_platform_admin)):
    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    try:
        updated = await update_tenant(tenant, body, allowed=EDITABLE_FIELDS | PLATFORM_FIELDS,
                                      user_id=f"platform:{admin.user_id}",
                                      ip=request.client.host if request.client else "")
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
