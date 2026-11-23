"""
Landing-page live demo ("Talk to it now") and the platform's Demo leads page.

Public (the landing page; CORS_ORIGINS must list its origin):
    GET   /public/web-demo/config      is the demo on, how long a call may last
    POST  /public/web-demo/start       Google credential → lead upserted → LiveKit token for the demo practice
    PATCH /public/web-demo/lead        Google credential + practice name (the after-call card)

Platform admins:
    GET    /platform/demo-leads             list (status, search, days)
    GET    /platform/demo-leads.csv         the same list as a CSV
    GET    /platform/demo-leads/{id}        one lead with its demo calls and their transcripts
    PATCH  /platform/demo-leads/{id}        status / notes (audited)
    DELETE /platform/demo-leads/{id}        erase the lead and its demo calls (audited)
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import timedelta
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from config import settings
from core import ratelimit
from core.auth import Principal, require_platform_admin
from core.auth import google_id
from core.repositories import Repos
from core.repositories.base import utcnow
from core.sensitive import unseal
from core.services import web_demo as wd
from core.tenancy import resolve_by_id
from database.mongodb import get_db
from routers.livekit import livekit_configured, mint_browser_token
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

public_web_demo_router = APIRouter(prefix="/public/web-demo", tags=["Web demo"])
demo_leads_router = APIRouter(prefix="/platform/demo-leads", tags=["Web demo"])

AUDIT_SCOPE = "platform"
DAY = 86_400
# The token outlives the call cap a little, so a slow connect doesn't lose the last seconds.
TOKEN_GRACE_S = 120


def _available(region: str) -> bool:
    return bool(settings.WEB_DEMO_ENABLED and settings.web_demo_tenant(region) and livekit_configured()
                and settings.google_signin_client_id)


# ── Public ──────────────────────────────────────────────────────────────────

@public_web_demo_router.get("/config")
async def demo_config():
    return {"enabled": {r: _available(r) for r in wd.REGIONS}, "max_seconds": settings.WEB_DEMO_MAX_SECONDS}


class StartBody(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)
    region: Literal["au", "uk"] = "au"
    page: str = Field(default="", max_length=120)
    referrer: str = Field(default="", max_length=200)
    utm_source: str = Field(default="", max_length=80)
    utm_medium: str = Field(default="", max_length=80)
    utm_campaign: str = Field(default="", max_length=80)


async def _identity(credential: str) -> google_id.GoogleIdentity:
    try:
        return await google_id.verify(credential)
    except google_id.GoogleTokenError as e:
        raise HTTPException(401, str(e))


async def _within_limits(request: Request, sub: str) -> Optional[str]:
    """None = go ahead; else the message for the visitor. The account limit is checked first so a
    visitor who used up their demos is told so, rather than "busy"."""
    if not await ratelimit.allow(f"webdemo:acct:{sub}", settings.WEB_DEMO_PER_ACCOUNT_DAY, DAY):
        return "You've had a few demos today. Come back tomorrow, or sign up to try it on your own number."
    if not await ratelimit.allow(f"webdemo:ip:{ratelimit.client_ip(request)}", settings.WEB_DEMO_PER_IP_HOUR, 3600):
        return "Too many demos from this network. Please try again in an hour."
    if not await ratelimit.allow("webdemo:all", settings.WEB_DEMO_MAX_PER_DAY, DAY):
        return "The demo is very busy today. Please try again tomorrow."
    return None


@public_web_demo_router.post("/start")
async def start_demo(body: StartBody, request: Request):
    if not _available(body.region):
        raise HTTPException(503, "The live demo isn't available right now. Try the sample calls below.")
    ident = await _identity(body.credential)
    problem = await _within_limits(request, ident.sub)
    if problem:
        raise HTTPException(429, problem)
    tenant = await resolve_by_id(settings.web_demo_tenant(body.region))
    if tenant is None:
        logger.error("Web demo practice for %s is missing (WEB_DEMO_TENANT_*)", body.region)
        raise HTTPException(503, "The live demo isn't available right now. Try the sample calls below.")

    db = get_db()
    lead = await wd.upsert_lead(db, ident, region=body.region, page=body.page, referrer=body.referrer,
                                utm={"source": body.utm_source, "medium": body.utm_medium,
                                     "campaign": body.utm_campaign})
    room = f"demo-{uuid.uuid4().hex[:12]}"
    token, _identity_ = mint_browser_token(tenant, room, {"web_demo_lead": lead["_id"]},
                                           ttl_s=settings.WEB_DEMO_MAX_SECONDS + TOKEN_GRACE_S)
    logger.info("Web demo started: lead %s, %s, room %s", lead["_id"], body.region, room)
    return {"token": token, "url": settings.LIVEKIT_URL, "room": room,
            "max_seconds": settings.WEB_DEMO_MAX_SECONDS, "practice_name": tenant.name,
            "agent_name": tenant.agent.name, "first_name": (ident.name.split(" ")[0] if ident.name else ""),
            "lead_practice_name": lead.get("practice_name", "")}


class LeadBody(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)
    practice_name: str = Field(min_length=1, max_length=200)


@public_web_demo_router.patch("/lead")
async def update_lead(body: LeadBody):
    ident = await _identity(body.credential)
    if not await wd.set_practice_name(get_db(), ident, body.practice_name):
        raise HTTPException(404, "Start a demo first.")
    return {"ok": True}


# ── Platform admins ─────────────────────────────────────────────────────────

async def _audit(user: Principal, request: Request, action: str, target_id: str,
                 diff: Optional[Dict[str, Any]] = None) -> None:
    await Repos(get_db(), AUDIT_SCOPE).audit.record(
        user_id=f"platform:{user.user_id}", action=action, target_type="web_demo_lead", target_id=target_id,
        diff=to_jsonable(diff or {}), ip=request.client.host if request.client else "")


def _query(status: Optional[str], q: Optional[str], days: int) -> Dict[str, Any]:
    query: Dict[str, Any] = {"last_seen_at": {"$gte": utcnow() - timedelta(days=days)}}
    if status:
        query["status"] = status
    if q and q.strip():
        pattern = re.escape(q.strip()[:80])
        query["$or"] = [{f: {"$regex": pattern, "$options": "i"}} for f in ("name", "email", "practice_name")]
    return query


async def _leads(status: Optional[str], q: Optional[str], days: int, limit: int) -> list[Dict[str, Any]]:
    # Sessions are capped (wd.MAX_SESSIONS), so whole documents are cheap; lead_row keeps only the last call.
    docs = await get_db()[wd.DEMO_LEADS].find(_query(status, q, days)).sort("last_seen_at", -1).limit(limit).to_list(limit)
    return [wd.lead_row(d) for d in docs]


StatusQ = Query(default=None, pattern="^(" + "|".join(wd.STATUSES) + ")$")


@demo_leads_router.get("")
async def list_demo_leads(status: Optional[str] = StatusQ, q: Optional[str] = Query(default=None, max_length=80),
                          days: int = Query(90, ge=1, le=730), _: Principal = Depends(require_platform_admin)):
    leads = await _leads(status, q, days, 500)
    counts: Dict[str, int] = {s: 0 for s in wd.STATUSES}
    for row in await get_db()[wd.DEMO_LEADS].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]).to_list(20):
        counts[row["_id"]] = row["n"]
    return {"leads": to_jsonable(leads), "counts": counts, "statuses": list(wd.STATUSES),
            "enabled": {r: _available(r) for r in wd.REGIONS}}


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    """Spreadsheet-safe: a value that starts like a formula is prefixed with a quote."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA) else text


@demo_leads_router.get(".csv")
async def export_demo_leads(request: Request, status: Optional[str] = StatusQ,
                            q: Optional[str] = Query(default=None, max_length=80),
                            days: int = Query(365, ge=1, le=730), admin: Principal = Depends(require_platform_admin)):
    leads = await _leads(status, q, days, 10_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["name", "email", "practice_name", "region", "status", "demo_count", "talk_seconds", "first_seen_at",
            "last_seen_at", "utm_source", "utm_medium", "utm_campaign", "last_summary", "notes"]
    w.writerow(cols)
    for lead in leads:
        utm = lead.get("utm") or {}
        row = {**lead, "utm_source": utm.get("source"), "utm_medium": utm.get("medium"),
               "utm_campaign": utm.get("campaign")}
        w.writerow([_cell(row.get(c)) for c in cols])
    await _audit(admin, request, "demo_leads.export", "*", {"rows": len(leads), "status": status, "days": days})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="demo-leads.csv"',
                             "Cache-Control": "no-store"})


async def _lead(lead_id: str) -> Dict[str, Any]:
    doc = await get_db()[wd.DEMO_LEADS].find_one({"_id": lead_id})
    if not doc:
        raise HTTPException(404, "Lead not found")
    return doc


@demo_leads_router.get("/{lead_id}")
async def get_demo_lead(lead_id: str, _: Principal = Depends(require_platform_admin)):
    doc = await _lead(lead_id)
    sessions = []
    for s in reversed(doc.get("sessions") or []):          # newest first
        call = await Repos(get_db(), s["tenant_id"]).calls.find_one(
            {"call_id": s["call_id"]}, {"transcript": 1, "transcript_enc": 1, "ended_reason": 1})
        transcript = (unseal(call, ("transcript",)).get("transcript") or []) if call else []
        sessions.append({**wd.session_view(s), "transcript": [{"role": t.get("role"), "text": t.get("text"), "at": t.get("at")}
                                             for t in transcript if isinstance(t, dict)],
                         "transcript_available": call is not None,
                         "ended_reason": (call or {}).get("ended_reason")})
    return {"lead": to_jsonable(wd.lead_row(doc)), "sessions": to_jsonable(sessions)}


class LeadPatch(BaseModel):
    status: Optional[Literal["new", "contacted", "qualified", "not_a_fit"]] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


@demo_leads_router.patch("/{lead_id}")
async def patch_demo_lead(lead_id: str, body: LeadPatch, request: Request,
                          admin: Principal = Depends(require_platform_admin)):
    await _lead(lead_id)
    sets = {k: v for k, v in body.model_dump().items() if v is not None}
    if not sets:
        raise HTTPException(422, "Nothing to change")
    await get_db()[wd.DEMO_LEADS].update_one({"_id": lead_id}, {"$set": {**sets, "updated_at": utcnow()}})
    await _audit(admin, request, "demo_lead.update", lead_id, {k: v for k, v in sets.items() if k != "notes"}
                 | ({"notes": "changed"} if "notes" in sets else {}))
    return {"lead": to_jsonable(wd.lead_row(await _lead(lead_id)))}


@demo_leads_router.delete("/{lead_id}")
async def delete_demo_lead(lead_id: str, request: Request, admin: Principal = Depends(require_platform_admin)):
    """Erasure request: the lead and the demo calls it made (transcripts included)."""
    doc = await _lead(lead_id)
    calls = 0
    for s in doc.get("sessions") or []:
        res = await Repos(get_db(), s["tenant_id"]).calls.delete_many({"call_id": s["call_id"]})
        calls += int(getattr(res, "deleted_count", 0) or 0)
    await get_db()[wd.DEMO_LEADS].delete_one({"_id": lead_id})
    await _audit(admin, request, "demo_lead.delete", lead_id, {"calls_deleted": calls})
    return {"ok": True, "calls_deleted": calls}
