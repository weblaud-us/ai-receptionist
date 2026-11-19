"""
Legal and privacy API (Roadmap Phase 6, §11).

Business (prefix /t, owners):
GET   /t/legal                           document versions + links, DPA status, sub-processors, retention
POST  /t/legal/dpa/accept                accept the current DPA on behalf of the business
POST  /t/legal/accept                    a member accepts updated terms / privacy / AUP
GET   /t/privacy/subject?phone=          what we hold about one caller (counts only)
GET   /t/privacy/subject/export?phone=   everything about that caller, as JSON (access request)
POST  /t/privacy/subject/erase           delete / anonymise it (erasure request)
GET   /t/privacy/requests                the business's request log (masked numbers)

Public:
GET   /public/legal                      document versions + links + sub-processor list (marketing site)
GET   /public/site                       demo number, status page, support and help links (Phase 7)

Platform (prefix /platform):
GET   /platform/privacy/requests         every business's requests, with due dates
GET   /platform/consent-events           opt-outs / opt-ins across channels (masked)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from config import settings
from core import ratelimit
from core.auth import Principal, TenantPrincipal, require_platform_admin, require_tenant
from core.compliance import legal, optout, subjects
from core.phone import mask_phone
from core.repositories.base import utcnow
from core.tenancy import invalidate
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

compliance_router = APIRouter(prefix="/t", tags=["Legal & privacy"])
public_compliance_router = APIRouter(prefix="/public", tags=["Public"])
platform_compliance_router = APIRouter(prefix="/platform", tags=["Platform"])

_owner = require_tenant("owner", allow_inactive=True)       # a cancelled business can still answer requests
_member = require_tenant("staff", allow_inactive=True)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _actor(tp: TenantPrincipal) -> str:
    return f"platform:{tp.user.user_id}" if tp.user.platform_admin else tp.user.user_id


async def _audit(tp: TenantPrincipal, request: Request, action: str, diff: Dict[str, Any]) -> None:
    await tp.repos().audit.record(user_id=_actor(tp), action=action, target_type="privacy", target_id=tp.tenant_id,
                                  diff=to_jsonable(diff), ip=_ip(request))


async def _limit(tp: TenantPrincipal, what: str, per_hour: int) -> None:
    if not await ratelimit.allow_fixed(f"privacy:{what}:{tp.tenant_id}", per_hour, 3600):
        raise HTTPException(429, "Too many requests — try again in an hour", headers={"Retry-After": "3600"})


# ─────────────────────────────────────────────
# LEGAL
# ─────────────────────────────────────────────

@compliance_router.get("/legal")
async def legal_status(tp: TenantPrincipal = Depends(_member)):
    user = await get_db().users.find_one({"_id": tp.user.user_id}, {"legal_acceptances": 1}) or {}
    a = tp.tenant.agent
    return to_jsonable({
        "entity": settings.LEGAL_ENTITY_NAME,
        "documents": legal.public_documents(),
        "dpa": legal.dpa_status(tp.tenant),
        "outstanding": [] if tp.user.platform_admin else legal.outstanding(user),
        "subprocessors": legal.SUBPROCESSORS,
        "retention": {"transcript_days": a.transcript_retention_days, "recording_days": a.recording.retention_days,
                      "recording_enabled": a.recording.enabled, "after_cancellation_days": settings.CANCEL_RETENTION_DAYS},
        "privacy_contact": settings.PRIVACY_CONTACT_EMAIL or settings.SUPPORT_EMAIL,
        "dsr_deadline_days": settings.DSR_DEADLINE_DAYS,
    })


class DpaAccept(BaseModel):
    version: str = Field(min_length=8, max_length=20)
    signer_name: str = Field(min_length=2, max_length=120)
    signer_title: str = Field(default="", max_length=120)


@compliance_router.post("/legal/dpa/accept")
async def accept_dpa(body: DpaAccept, request: Request, tp: TenantPrincipal = Depends(_owner)):
    current = legal.version_of("dpa")
    if body.version != current:
        raise HTTPException(409, f"The DPA has changed (current version {current}); reload and review it")
    now = utcnow()
    await get_db().tenants.update_one({"_id": tp.tenant_id}, {"$set": {
        "legal.dpa_version": current, "legal.dpa_accepted_at": now, "legal.dpa_accepted_by": _actor(tp),
        "legal.dpa_signer": {"name": body.signer_name, "title": body.signer_title, "ip": _ip(request)},
        "updated_at": now}})
    invalidate(tp.tenant_id)
    await _audit(tp, request, "legal.dpa_accept", {"version": current, "signer": body.signer_name})
    return {"success": True, "version": current, "accepted_at": to_jsonable(now)}


class AcceptBody(BaseModel):
    documents: List[str] = Field(min_length=1, max_length=5)


@compliance_router.post("/legal/accept")
async def accept_documents(body: AcceptBody, request: Request, tp: TenantPrincipal = Depends(_member)):
    keys = [k for k in body.documents if k in legal.SIGNUP_DOCUMENTS]
    if not keys:
        raise HTTPException(422, f"documents must be from {', '.join(legal.SIGNUP_DOCUMENTS)}")
    rows = await legal.record_user_acceptance(get_db(), tp.user.user_id, keys, ip=_ip(request),
                                              user_agent=request.headers.get("user-agent", ""))
    return {"accepted": [{"doc": r["doc"], "version": r["version"]} for r in rows]}


# ─────────────────────────────────────────────
# DATA SUBJECT REQUESTS
# ─────────────────────────────────────────────

def _bad(exc: subjects.SubjectError) -> HTTPException:
    return HTTPException(422, str(exc))


@compliance_router.get("/privacy/subject")
async def subject_lookup(phone: str = Query(min_length=7, max_length=32), tp: TenantPrincipal = Depends(_owner)):
    await _limit(tp, "lookup", 60)
    try:
        return to_jsonable(await subjects.find(get_db(), tp.tenant, phone))
    except subjects.SubjectError as exc:
        raise _bad(exc)


@compliance_router.get("/privacy/subject/export")
async def subject_export(request: Request, phone: str = Query(min_length=7, max_length=32),
                         received_at: Optional[datetime] = None, tp: TenantPrincipal = Depends(_owner)):
    await _limit(tp, "export", 20)
    try:
        data = await subjects.export(get_db(), tp.tenant, phone)
    except subjects.SubjectError as exc:
        raise _bad(exc)
    counts = {k: len(v) for k, v in data["data"].items()}
    await subjects.log_request(get_db(), tp.tenant, phone, "access", actor=_actor(tp), result={"counts": counts},
                               received_at=received_at)
    await _audit(tp, request, "privacy.export", {"phone": mask_phone(phone), "counts": counts})
    body = json.dumps(to_jsonable(data), indent=2)
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="caller-data.json"',
                             "Cache-Control": "no-store"})


class EraseBody(BaseModel):
    phone: str = Field(min_length=7, max_length=32)
    confirm: str = Field(min_length=7, max_length=32)          # the number typed twice
    include_future: bool = False
    suppress: bool = True
    received_at: Optional[datetime] = None
    note: str = Field(default="", max_length=500)


@compliance_router.post("/privacy/subject/erase")
async def subject_erase(body: EraseBody, request: Request, tp: TenantPrincipal = Depends(_owner)):
    await _limit(tp, "erase", 20)
    try:
        if subjects.normalise(body.phone) != subjects.normalise(body.confirm):
            raise HTTPException(422, "The two numbers don't match")
        result = await subjects.erase(get_db(), tp.tenant, body.phone, include_future=body.include_future,
                                      suppress=body.suppress, actor=_actor(tp))
    except subjects.SubjectError as exc:
        raise HTTPException(409 if "upcoming" in str(exc) else 422, str(exc))
    await subjects.log_request(get_db(), tp.tenant, body.phone, "erasure", actor=_actor(tp), result=result,
                               received_at=body.received_at, note=body.note)
    await _audit(tp, request, "privacy.erase", {"phone": mask_phone(body.phone), "counts": result["counts"]})
    return to_jsonable(result)


@compliance_router.get("/privacy/requests")
async def privacy_requests(tp: TenantPrincipal = Depends(_owner)):
    rows = await subjects.list_requests(get_db(), tp.tenant_id)
    return {"requests": [to_jsonable({k: v for k, v in r.items() if k not in ("tenant_id", "phone_hash")}
                                     | {"id": r["_id"]}) for r in rows]}


# ─────────────────────────────────────────────
# PUBLIC
# ─────────────────────────────────────────────

@public_compliance_router.get("/legal")
async def public_legal():
    return {"entity": settings.LEGAL_ENTITY_NAME, "documents": legal.public_documents(),
            "subprocessors": legal.SUBPROCESSORS, "privacy_contact": settings.PRIVACY_CONTACT_EMAIL}


@public_compliance_router.get("/site")
async def public_site():
    """What the marketing site and the dashboard footer show (Phase 7, Task 7.2)."""
    return {"demo_phone_number": settings.DEMO_PHONE_NUMBER, "status_page_url": settings.STATUS_PAGE_URL,
            "support_email": settings.SUPPORT_EMAIL, "help_center_url": settings.HELP_CENTER_URL,
            "signup_enabled": settings.SIGNUP_ENABLED}


# ─────────────────────────────────────────────
# PLATFORM
# ─────────────────────────────────────────────

@platform_compliance_router.get("/privacy/requests")
async def all_privacy_requests(admin: Principal = Depends(require_platform_admin)):
    rows = await subjects.list_requests(get_db(), None, limit=500)
    names = {t["_id"]: t.get("name", "") for t in await get_db().tenants.find({}, {"name": 1}).to_list(10_000)}
    return {"requests": [to_jsonable({k: v for k, v in r.items() if k != "phone_hash"}
                                     | {"id": r["_id"], "business": names.get(r["tenant_id"], "")}) for r in rows]}


@platform_compliance_router.get("/consent-events")
async def consent_events(limit: int = Query(default=200, ge=1, le=1000),
                         admin: Principal = Depends(require_platform_admin)):
    rows = await get_db()[optout.EVENTS].find({}).sort("at", -1).limit(limit).to_list(limit)
    return {"events": [to_jsonable({k: v for k, v in r.items() if k != "_id"} | {"phone": mask_phone(r.get("phone"))})
                       for r in rows]}
