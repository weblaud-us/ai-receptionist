"""
Outbound sales dialer API (Roadmap Task 4.6) — prefix /platform/sales, platform admins only.

GET    /platform/sales/status                 readiness, do-not-call lists loaded, personas, counts
GET    /platform/sales/leads                  search / filter
POST   /platform/sales/leads/import           CSV (dry_run=true previews the column mapping and counts)
POST   /platform/sales/leads/queue            put new leads into a campaign's queue
GET    /platform/sales/leads/{id}             lead, every gate decision, every attempt
PATCH  /platform/sales/leads/{id}
POST   /platform/sales/leads/{id}/check       run the compliance gate now (does a paid line-type lookup)
POST   /platform/sales/leads/{id}/dnc         manual do-not-call
GET    /platform/sales/dnc    POST /platform/sales/dnc          the internal list (no delete: it is permanent)
GET    /platform/sales/campaigns   POST …     PATCH …/{id}      POST …/{id}/start   POST …/{id}/pause
POST   /platform/sales/campaigns/{id}/dial-next                 human-assisted: ring the next lead for me
GET    /platform/sales/live                   calls in progress
POST   /platform/sales/live/{attempt}/join    a LiveKit token to listen in (hidden) or take over
POST   /platform/sales/live/{attempt}/hangup
GET    /platform/sales/attempts   GET …/{id}  PATCH …/{id}      results; correct an outcome; label AMD
GET    /platform/sales/funnel

There is deliberately no endpoint that skips the compliance gate, removes a do-not-call entry, or
dials an arbitrary number.

Public (no sign-in), for the "get a call from our AI" landing page:
GET    /public/demo-call/consent              the exact consent wording to show next to the checkbox
POST   /public/demo-call                      records written consent and queues the call
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config import settings
from core import ratelimit
from core.auth import Principal, require_platform_admin
from core.outbound import dialer, funnel, gate, importer, lookup, outcomes, persona, phones, registry
from core.outbound import models as m
from core.repositories import Repos
from core.tenancy.ids import new_id
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

sales_router = APIRouter(prefix="/platform/sales", tags=["Sales dialer"])
public_sales_router = APIRouter(prefix="/public", tags=["Sales dialer"])

AUDIT_SCOPE = "platform"
PAGE_SIZE = 50

CONSENT_VERSION = "2026-11-v1"
CONSENT_TEXT = (
    "By ticking this box and pressing the button, I agree that {company} may call the phone number I entered "
    "using an artificial (AI-generated) voice and automated dialling technology, to tell me about its AI "
    "receptionist service. My consent is not a condition of buying anything, and I can opt out at any time "
    "by saying so on a call."
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _out(doc: Optional[Dict[str, Any]]) -> Any:
    return to_jsonable(m.with_id(doc))


async def _audit(user: Principal, request: Request, action: str, target_type: str, target_id: str,
                 diff: Optional[Dict[str, Any]] = None) -> None:
    await Repos(get_db(), AUDIT_SCOPE).audit.record(
        user_id=f"platform:{user.user_id}", action=action, target_type=target_type, target_id=target_id,
        diff=to_jsonable(diff or {}), ip=request.client.host if request.client else "")


async def _campaign(campaign_id: str) -> Dict[str, Any]:
    doc = await get_db()[m.CAMPAIGNS].find_one({"_id": campaign_id})
    if not doc:
        raise HTTPException(404, "Campaign not found")
    return doc


async def _lead(lead_id: str) -> Dict[str, Any]:
    doc = await get_db()[m.LEADS].find_one({"_id": lead_id})
    if not doc:
        raise HTTPException(404, "Lead not found")
    return doc


def _pages(total: int, size: int) -> int:
    return max(1, -(-total // size))


# ── Status ───────────────────────────────────────────────────────────────────

@sales_router.get("/status")
async def status(admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    return to_jsonable({
        "ready": not dialer.readiness(),
        "problems": dialer.readiness(),
        "dialer_enabled": settings.OUTBOUND_DIALER_ENABLED,
        "line_type_lookup": lookup.configured(),
        "sales_tenant_id": settings.SALES_TENANT_ID or None,
        "company": settings.SALES_COMPANY_NAME,
        "registries": await registry.registry_overview(db),
        "registry_max_age_days": settings.SALES_DNC_REGISTRY_MAX_AGE_DAYS,
        "blocked_us_states": sorted(settings.sales_blocked_us_states),
        "modes": [{"key": mode, "countries": sorted(_countries(mode))} for mode in m.MODES],
        "personas": [{"id": p.id, "label": p.label,
                      "opener": persona.opener(p, {"business_name": "Joe's Plumbing"}, "ai_cold_b2b_us")}
                     for p in persona.PERSONAS.values()],
        "consent_campaign_id": settings.SALES_CONSENT_CAMPAIGN_ID or None,
        "counts": {"leads": await db[m.LEADS].count_documents({}), "dnc": await db[m.DNC].count_documents({})},
    })


def _countries(mode: str) -> frozenset[str]:
    from core.outbound import rules

    return rules.allowed_countries(mode)


# ── Leads ────────────────────────────────────────────────────────────────────

class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=importer.MAX_BYTES)
    mapping: Optional[Dict[str, str]] = None
    source: str = Field(min_length=2, max_length=120, description="where this list came from")
    default_country: str = Field(default="US", min_length=2, max_length=2)
    vertical: str = Field(default="", max_length=60)
    tags: List[str] = Field(default_factory=list, max_length=10)
    campaign_id: Optional[str] = None
    mode: Literal["ai_cold_b2b_us", "ai_consented", "human_assisted"] = "ai_cold_b2b_us"
    dry_run: bool = False


@sales_router.post("/leads/import")
async def import_leads(body: ImportBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    campaign = await _campaign(body.campaign_id) if body.campaign_id else None
    try:
        result = await importer.import_leads(
            get_db(), text=body.csv, mapping=body.mapping, source=f"csv:{body.source.strip()}",
            default_country=body.default_country, vertical=body.vertical, tags=[t.strip()[:40] for t in body.tags if t.strip()],
            campaign=campaign, mode=body.mode, dry_run=body.dry_run)
    except ValueError as e:
        raise HTTPException(422, str(e)[:300])
    if not body.dry_run and result.get("imported"):
        await _audit(admin, request, "sales.leads.import", "sales_leads", body.source,
                     {k: result[k] for k in ("rows", "imported", "invalid", "customers", "blocked")})
    return {**result, "fields": list(importer.FIELDS)}


@sales_router.get("/leads")
async def list_leads(status: Optional[str] = None, campaign_id: Optional[str] = None, q: Optional[str] = None,
                     source: Optional[str] = None, page: int = Query(1, ge=1),
                     admin: Principal = Depends(require_platform_admin)):
    query: Dict[str, Any] = {"owner": m.OWNER_PLATFORM}
    if status:
        query["status"] = status
    if campaign_id:
        query["campaign_id"] = None if campaign_id == "none" else campaign_id
    if source:
        query["source"] = source
    if q and q.strip():
        text = q.strip()
        info = phones.parse(text)
        query["$or"] = [{"phone": info.e164}] if info else [
            {"business_name": {"$regex": _escape(text), "$options": "i"}},
            {"contact_name": {"$regex": _escape(text), "$options": "i"}}]
    db = get_db()
    total = await db[m.LEADS].count_documents(query)
    rows = await db[m.LEADS].find(query, {"gate_log": 0}).sort("created_at", -1).skip((page - 1) * PAGE_SIZE) \
        .limit(PAGE_SIZE).to_list(PAGE_SIZE)
    by_status: Dict[str, int] = {}
    async for row in db[m.LEADS].aggregate([{"$match": {"owner": m.OWNER_PLATFORM}},
                                            {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        by_status[str(row["_id"])] = row["n"]
    sources = await db[m.LEADS].distinct("source", {"owner": m.OWNER_PLATFORM})
    return {"leads": [_out(r) for r in rows], "total": total, "page": page, "pages": _pages(total, PAGE_SIZE),
            "by_status": by_status, "sources": sorted(s for s in sources if s)}


def _escape(text: str) -> str:
    import re

    return re.escape(text[:80])


class QueueBody(BaseModel):
    campaign_id: str
    source: Optional[str] = None
    tag: Optional[str] = None
    lead_ids: Optional[List[str]] = Field(default=None, max_length=5000)


@sales_router.post("/leads/queue")
async def queue_leads(body: QueueBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    campaign = await _campaign(body.campaign_id)
    if campaign["status"] == "completed":
        raise HTTPException(409, "That campaign is completed. Create a new one.")
    query: Dict[str, Any] = {}
    if body.lead_ids:
        query["_id"] = {"$in": body.lead_ids}
    if body.source:
        query["source"] = body.source
    if body.tag:
        query["tags"] = body.tag
    if not query:
        raise HTTPException(422, "Choose which leads to queue: a source, a tag, or specific leads.")
    result = await dialer.queue_leads(get_db(), campaign, query)
    await _audit(admin, request, "sales.leads.queue", "campaign", campaign["_id"], result)
    return result


@sales_router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, admin: Principal = Depends(require_platform_admin)):
    lead = await _lead(lead_id)
    attempts = await get_db()[m.ATTEMPTS].find({"lead_id": lead_id}).sort("created_at", -1).limit(50).to_list(50)
    out = _out(lead)
    out["gate_log"] = to_jsonable(list(reversed(lead.get("gate_log") or [])))
    return {"lead": out, "attempts": [_out(a) for a in attempts],
            "consent": to_jsonable(await get_db()[m.CONSENT].find({"phone": lead["phone"]}, {"_id": 0})
                                   .sort("at", -1).limit(10).to_list(10))}


class LeadPatch(BaseModel):
    business_name: Optional[str] = Field(default=None, max_length=200)
    contact_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)
    tags: Optional[List[str]] = Field(default=None, max_length=20)
    timezone: Optional[str] = Field(default=None, max_length=64)
    region_code: Optional[str] = Field(default=None, max_length=2)


@sales_router.patch("/leads/{lead_id}")
async def patch_lead(lead_id: str, body: LeadPatch, admin: Principal = Depends(require_platform_admin)):
    await _lead(lead_id)
    sets: Dict[str, Any] = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "timezone" in sets:
        if not phones.valid_timezone(sets["timezone"]):
            raise HTTPException(422, "Unknown timezone (use an IANA name such as America/Chicago)")
        sets["timezones"] = [sets["timezone"]]
    if "region_code" in sets:
        sets["region_code"] = phones.normalize_state(sets["region_code"])
        if not sets["region_code"]:
            raise HTTPException(422, "Unknown US state code")
    if sets:
        sets["updated_at"] = utcnow()
        await get_db()[m.LEADS].update_one({"_id": lead_id}, {"$set": sets})
    return {"lead": _out(await _lead(lead_id))}


class CheckBody(BaseModel):
    campaign_id: Optional[str] = None
    mode: Literal["ai_cold_b2b_us", "ai_consented", "human_assisted"] = "ai_cold_b2b_us"


@sales_router.post("/leads/{lead_id}/check")
async def check_lead(lead_id: str, body: CheckBody, admin: Principal = Depends(require_platform_admin)):
    """Run the gate now and show every check. Changes nothing about whether the lead is dialled."""
    lead = await _lead(lead_id)
    campaign_id = body.campaign_id or lead.get("campaign_id")
    campaign = await _campaign(campaign_id) if campaign_id else {"mode": body.mode, "max_attempts": 3, "calling_window": {}}
    decision = await gate.run_gate(get_db(), lead, campaign, utcnow())
    return {"decision": to_jsonable(decision.to_doc()), "lead": _out(await _lead(lead_id))}


class DncBody(BaseModel):
    phone: Optional[str] = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=300)


@sales_router.post("/leads/{lead_id}/dnc")
async def lead_dnc(lead_id: str, body: DncBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    lead = await _lead(lead_id)
    await registry.add_to_dnc(get_db(), lead["phone"], reason="manual", source=f"platform:{admin.user_id}", note=body.note)
    await _audit(admin, request, "sales.dnc.add", "sales_lead", lead_id)
    return {"success": True}


@sales_router.get("/dnc")
async def list_dnc(q: Optional[str] = None, page: int = Query(1, ge=1),
                   admin: Principal = Depends(require_platform_admin)):
    query: Dict[str, Any] = {}
    if q and q.strip():
        info = phones.parse(q.strip())
        query["phone"] = info.e164 if info else q.strip()
    db = get_db()
    total = await db[m.DNC].count_documents(query)
    rows = await db[m.DNC].find(query, {"_id": 0}).sort("added_at", -1).skip((page - 1) * PAGE_SIZE) \
        .limit(PAGE_SIZE).to_list(PAGE_SIZE)
    return {"entries": to_jsonable(rows), "total": total, "page": page, "pages": _pages(total, PAGE_SIZE)}


@sales_router.post("/dnc")
async def add_dnc(body: DncBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    info = phones.parse(body.phone or "")
    if info is None:
        raise HTTPException(422, "Enter the number in international format, e.g. +15125551234")
    added = await registry.add_to_dnc(get_db(), info.e164, reason="manual", source=f"platform:{admin.user_id}", note=body.note)
    await _audit(admin, request, "sales.dnc.add", "phone", info.e164[-4:])
    return {"success": True, "added": added, "phone": info.e164}


# ── Campaigns ────────────────────────────────────────────────────────────────

async def _progress(campaign_id: str) -> Dict[str, Any]:
    db = get_db()
    leads: Dict[str, int] = {}
    async for row in db[m.LEADS].aggregate([{"$match": {"campaign_id": campaign_id}},
                                            {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        leads[str(row["_id"])] = row["n"]
    now = utcnow()
    return {"leads": leads, "total_leads": sum(leads.values()),
            "to_dial": leads.get("queued", 0) + leads.get("callback", 0),
            "active_calls": await dialer.active_count(db, campaign_id, now),
            "dialled_24h": await dialer.dialled_since(db, {"campaign_id": campaign_id}, now - timedelta(hours=24)),
            "demos": leads.get("demo_booked", 0)}


@sales_router.get("/campaigns")
async def list_campaigns(admin: Principal = Depends(require_platform_admin)):
    rows = await get_db()[m.CAMPAIGNS].find({}).sort("created_at", -1).limit(200).to_list(200)
    return {"campaigns": [{**_out(c), "progress": await _progress(c["_id"])} for c in rows]}


@sales_router.post("/campaigns")
async def create_campaign(body: m.CampaignSettings, request: Request, admin: Principal = Depends(require_platform_admin)):
    now = utcnow()
    doc = {"_id": new_id("cmp"), **body.model_dump(), "status": "draft", "legal_review": None,
           "created_by": admin.user_id, "created_at": now, "updated_at": now}
    await get_db()[m.CAMPAIGNS].insert_one(doc)
    await _audit(admin, request, "sales.campaign.create", "campaign", doc["_id"], {"name": body.name, "mode": body.mode})
    return {"campaign": _out(doc)}


@sales_router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, admin: Principal = Depends(require_platform_admin)):
    campaign = await _campaign(campaign_id)
    return {"campaign": _out(campaign), "progress": await _progress(campaign_id),
            "problems": dialer.campaign_problems(campaign)}


@sales_router.patch("/campaigns/{campaign_id}")
async def patch_campaign(campaign_id: str, body: Dict[str, Any], request: Request,
                         admin: Principal = Depends(require_platform_admin)):
    campaign = await _campaign(campaign_id)
    current = {k: campaign.get(k) for k in m.CampaignSettings.model_fields if k in campaign}
    patch = {k: v for k, v in body.items() if k in m.CampaignSettings.model_fields}
    if "mode" in patch and patch["mode"] != campaign["mode"] and campaign["status"] != "draft":
        raise HTTPException(409, "The mode can only be changed while the campaign is a draft.")
    try:
        merged = m.CampaignSettings.model_validate({**current, **patch})
    except ValueError as e:
        raise HTTPException(422, str(e)[:500])
    sets = {**merged.model_dump(), "updated_at": utcnow()}
    if any(k in patch for k in ("mode", "persona_ids")):
        sets["legal_review"] = None               # a different script or call type needs reviewing again
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id}, {"$set": sets})
    await _audit(admin, request, "sales.campaign.update", "campaign", campaign_id, patch)
    return {"campaign": _out(await _campaign(campaign_id))}


class StartBody(BaseModel):
    legal_review_confirmed: bool = False
    note: str = Field(default="", max_length=300)


@sales_router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: str, body: StartBody, request: Request,
                         admin: Principal = Depends(require_platform_admin)):
    campaign = await _campaign(campaign_id)
    if campaign["status"] == "completed":
        raise HTTPException(409, "That campaign is completed. Create a new one.")
    problems = dialer.campaign_problems(campaign)
    if problems:
        raise HTTPException(409, " ".join(problems))
    sets: Dict[str, Any] = {"status": "running", "updated_at": utcnow()}
    if not campaign.get("legal_review"):
        if not body.legal_review_confirmed:
            raise HTTPException(409, "legal_review_required: confirm that a telemarketing lawyer has reviewed "
                                     "this campaign's mode, opener and lead list before the first call.")
        sets["legal_review"] = {"confirmed_by": admin.user_id, "at": utcnow(), "note": body.note}
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id}, {"$set": sets})
    await _audit(admin, request, "sales.campaign.start", "campaign", campaign_id, {"legal_review": bool(body.legal_review_confirmed)})
    return {"campaign": _out(await _campaign(campaign_id))}


@sales_router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, request: Request, admin: Principal = Depends(require_platform_admin)):
    """Stops new dials at once. Calls already in progress finish."""
    await _campaign(campaign_id)
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id, "status": "running"},
                                           {"$set": {"status": "paused", "updated_at": utcnow()}})
    await _audit(admin, request, "sales.campaign.pause", "campaign", campaign_id)
    return {"campaign": _out(await _campaign(campaign_id))}


# ── Live calls: listen in, take over, human-assisted dialling ────────────────

def _room_token(room: str, *, takeover: bool, user: Principal) -> Dict[str, Any]:
    if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        raise HTTPException(503, "LiveKit is not configured.")
    from livekit import api

    identity = f"{'op' if takeover else 'listen'}_{uuid.uuid4().hex[:8]}"
    token = (api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
             .with_identity(identity).with_name(user.name or "Operator").with_ttl(timedelta(minutes=30))
             .with_grants(api.VideoGrants(room_join=True, room=room, can_subscribe=True, can_publish=takeover,
                                          can_publish_data=False, hidden=not takeover)))
    if takeover:
        token = token.with_attributes({"role": "takeover"})      # the sales agent goes silent when it sees this
    return {"token": token.to_jwt(), "url": settings.LIVEKIT_URL, "room": room, "identity": identity,
            "mode": "takeover" if takeover else "listen"}


@sales_router.get("/live")
async def live_calls(admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    now = utcnow()
    rows = await db[m.ATTEMPTS].find(
        {"status": {"$in": list(m.ACTIVE_ATTEMPT_STATUSES)},
         "created_at": {"$gte": now - timedelta(minutes=settings.SALES_MAX_CALL_MINUTES + 5)}},
        {"gate": 0}).sort("created_at", -1).limit(50).to_list(50)
    leads = {l["_id"]: l async for l in db[m.LEADS].find(
        {"_id": {"$in": [r["lead_id"] for r in rows]}}, {"business_name": 1, "contact_name": 1, "region_code": 1})}
    return {"calls": [{**_out(r), "business_name": (leads.get(r["lead_id"]) or {}).get("business_name"),
                       "contact_name": (leads.get(r["lead_id"]) or {}).get("contact_name")} for r in rows],
            "total": len(rows)}


class JoinBody(BaseModel):
    mode: Literal["listen", "takeover"] = "listen"


async def _active_attempt(attempt_id: str) -> Dict[str, Any]:
    attempt = await get_db()[m.ATTEMPTS].find_one({"_id": attempt_id})
    if not attempt:
        raise HTTPException(404, "Call not found")
    if attempt["status"] not in m.ACTIVE_ATTEMPT_STATUSES:
        raise HTTPException(409, "That call has ended.")
    return attempt


@sales_router.post("/live/{attempt_id}/join")
async def join_call(attempt_id: str, body: JoinBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    attempt = await _active_attempt(attempt_id)
    takeover = body.mode == "takeover"
    if takeover:
        await get_db()[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"operator": admin.user_id, "taken_over_at": utcnow()}})
    await _audit(admin, request, f"sales.call.{body.mode}", "call_attempt", attempt_id)
    return _room_token(attempt["call_id"], takeover=takeover, user=admin)


@sales_router.post("/live/{attempt_id}/hangup")
async def hang_up_call(attempt_id: str, request: Request, admin: Principal = Depends(require_platform_admin)):
    attempt = await _active_attempt(attempt_id)
    from livekit import api

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=attempt["call_id"]))     # drops the phone leg too
    except Exception:
        logger.warning("Could not end call %s", attempt_id, exc_info=True)
        raise HTTPException(502, "Could not end the call.")
    finally:
        await lk.aclose()
    await _audit(admin, request, "sales.call.hangup", "call_attempt", attempt_id)
    return {"success": True}


@sales_router.post("/campaigns/{campaign_id}/dial-next")
async def dial_next(campaign_id: str, request: Request, admin: Principal = Depends(require_platform_admin)):
    """Human-assisted dialling: ring the next lead that passes the gate and put me in the call."""
    campaign = await _campaign(campaign_id)
    if campaign["mode"] != "human_assisted":
        raise HTTPException(409, "Only human-assisted campaigns are dialled by hand.")
    if campaign["status"] != "running":
        raise HTTPException(409, "Start the campaign first.")
    problems = dialer.campaign_problems(campaign)
    if problems:
        raise HTTPException(409, " ".join(problems))
    result = await dialer.dial_next(get_db(), campaign, operator=admin.user_id)
    if not result["dialed"]:
        return {"dialed": False, "reason": result["reason"]}
    lead = await _lead(result["lead_id"])
    await _audit(admin, request, "sales.call.dial_next", "call_attempt", result["attempt_id"])
    return {"dialed": True, "attempt_id": result["attempt_id"], "lead": _out({k: v for k, v in lead.items() if k != "gate_log"}),
            "join": _room_token(result["room"], takeover=True, user=admin)}


# ── Results ──────────────────────────────────────────────────────────────────

@sales_router.get("/attempts")
async def list_attempts(campaign_id: Optional[str] = None, outcome: Optional[str] = None,
                        unlabelled: bool = False, page: int = Query(1, ge=1),
                        admin: Principal = Depends(require_platform_admin)):
    query: Dict[str, Any] = {"status": "done"}
    if campaign_id:
        query["campaign_id"] = campaign_id
    if outcome:
        query["outcome"] = outcome
    if unlabelled:
        query.update({"amd": {"$ne": None}, "amd_label": None})
    db = get_db()
    total = await db[m.ATTEMPTS].count_documents(query)
    rows = await db[m.ATTEMPTS].find(query, {"gate": 0}).sort("created_at", -1).skip((page - 1) * PAGE_SIZE) \
        .limit(PAGE_SIZE).to_list(PAGE_SIZE)
    leads = {l["_id"]: l async for l in db[m.LEADS].find(
        {"_id": {"$in": [r["lead_id"] for r in rows]}}, {"business_name": 1, "contact_name": 1})}
    return {"attempts": [{**_out(r), "business_name": (leads.get(r["lead_id"]) or {}).get("business_name")} for r in rows],
            "total": total, "page": page, "pages": _pages(total, PAGE_SIZE)}


@sales_router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str, admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    attempt = await db[m.ATTEMPTS].find_one({"_id": attempt_id})
    if not attempt:
        raise HTTPException(404, "Call not found")
    call = await db.calls.find_one({"call_id": attempt["call_id"]},  # cross-tenant: the platform's own sales call, by its unique id
                                   {"transcript": 1, "summary": 1, "latency": 1, "duration_s": 1})
    lead = await db[m.LEADS].find_one({"_id": attempt["lead_id"]}, {"gate_log": 0})
    return {"attempt": _out(attempt), "lead": _out(lead),
            "transcript": to_jsonable((call or {}).get("transcript") or [])}


class AttemptPatch(BaseModel):
    outcome: Optional[str] = None
    amd_label: Optional[Literal["human", "machine"]] = None


@sales_router.patch("/attempts/{attempt_id}")
async def patch_attempt(attempt_id: str, body: AttemptPatch, request: Request,
                        admin: Principal = Depends(require_platform_admin)):
    db = get_db()
    if not await db[m.ATTEMPTS].find_one({"_id": attempt_id}, {"_id": 1}):
        raise HTTPException(404, "Call not found")
    if body.amd_label:
        await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"amd_label": body.amd_label}})
    if body.outcome:
        try:
            if await outcomes.relabel_attempt(db, attempt_id, body.outcome, by=admin.user_id) is None:
                raise HTTPException(409, "The call has not finished yet.")
        except ValueError as e:
            raise HTTPException(422, str(e))
        await _audit(admin, request, "sales.attempt.outcome", "call_attempt", attempt_id, {"outcome": body.outcome})
    return {"attempt": _out(await db[m.ATTEMPTS].find_one({"_id": attempt_id}))}


@sales_router.get("/funnel")
async def get_funnel(campaign_id: Optional[str] = None, days: int = Query(30, ge=1, le=365),
                     admin: Principal = Depends(require_platform_admin)):
    query: Dict[str, Any] = {"status": "done", "created_at": {"$gte": utcnow() - timedelta(days=days)}}
    if campaign_id:
        query["campaign_id"] = campaign_id
    rows = await get_db()[m.ATTEMPTS].find(
        query, {"outcome": 1, "amd": 1, "amd_label": 1, "duration_sec": 1, "cost_usd": 1, "persona_id": 1,
                "status": 1}).limit(50_000).to_list(50_000)
    return {"funnel": funnel.funnel(rows), "amd": funnel.amd_accuracy(rows), "days": days,
            "outcomes": list(m.OUTCOMES)}


# ── Public: "get a call from our AI" ─────────────────────────────────────────

def consent_text() -> str:
    return CONSENT_TEXT.format(company=settings.SALES_COMPANY_NAME)


async def _consent_campaign() -> Optional[Dict[str, Any]]:
    if not (settings.SALES_CONSENT_CAMPAIGN_ID and settings.OUTBOUND_DIALER_ENABLED):
        return None
    campaign = await get_db()[m.CAMPAIGNS].find_one({"_id": settings.SALES_CONSENT_CAMPAIGN_ID})
    if not campaign or campaign.get("mode") != "ai_consented" or campaign.get("status") != "running":
        return None
    return campaign


@public_sales_router.get("/demo-call/consent")
async def demo_call_consent():
    return {"version": CONSENT_VERSION, "text": consent_text(), "available": await _consent_campaign() is not None}


class DemoCallBody(BaseModel):
    phone: str = Field(min_length=7, max_length=32)
    name: str = Field(default="", max_length=120)
    business_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=200)
    country: str = Field(default="US", min_length=2, max_length=2)
    consent: bool = False
    consent_version: str = Field(default="", max_length=40)
    page: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=200)      # honeypot: people never see or fill this field


@public_sales_router.post("/demo-call")
async def request_demo_call(body: DemoCallBody, request: Request):
    """Written consent → consent record → the lead joins the consented campaign and is called."""
    if body.website:
        return {"ok": True}                                # a bot: accept and do nothing
    if not body.consent or body.consent_version != CONSENT_VERSION:
        raise HTTPException(422, "Please tick the consent box to get a call.")
    ip = ratelimit.client_ip(request)
    if not (await ratelimit.allow("democall:all", settings.PUBLIC_DEMO_CALL_MAX_PER_DAY, 86400)
            and await ratelimit.allow(f"democall:ip:{ip}", settings.PUBLIC_DEMO_CALL_PER_IP_HOUR, 3600)):
        raise HTTPException(429, "Too many requests. Please try again later.")
    campaign = await _consent_campaign()
    if campaign is None:
        raise HTTPException(503, "Calls on request aren't available right now.")
    info = phones.parse(body.phone, body.country.upper())
    if info is None:
        raise HTTPException(422, "That doesn't look like a valid phone number.")
    db = get_db()
    if await registry.on_internal_dnc(db, info.e164):
        raise HTTPException(409, "This number asked us not to call it. Email us if you'd like that changed.")

    now = utcnow()
    source = f"form:{body.page.strip() or 'demo-call'}"
    consent = await registry.record_consent(
        db, phone=info.e164, type="written", language_shown=consent_text(), source=source, ip=ip,
        user_agent=request.headers.get("user-agent", ""), now=now)
    zones = phones.timezones_for(info)
    email = body.email.strip().lower()
    sets = {"contact_name": body.name.strip(), "business_name": body.business_name.strip(),
            "email": email if "@" in email else "", "campaign_id": campaign["_id"], "status": "queued",
            "next_eligible_at": now, "attempts": 0, "updated_at": now,
            "consent": {"type": "written", "at": now, "source": source, "record_id": consent["_id"]}}
    await db[m.LEADS].update_one(
        {"owner": m.OWNER_PLATFORM, "phone": info.e164},
        {"$set": {k: v for k, v in sets.items() if v not in ("", None)},
         "$setOnInsert": {"_id": new_id("lead"), "owner": m.OWNER_PLATFORM, "phone": info.e164, "source": source,
                          "country": info.country, "region_code": info.region_code, "timezones": list(zones),
                          "timezone": zones[0] if len(zones) == 1 else "", "vertical": "", "website": "",
                          "address": {}, "line_type": "", "line_type_checked_at": None, "last_attempt_at": None,
                          "dnc": {"national": False, "state": False, "internal": False, "checked_at": None},
                          "notes": "", "tags": ["form"], "gate": None, "gate_log": [], "created_at": now}},
        upsert=True)
    logger.info("Demo call requested from %s (consent %s)", source, consent["_id"])
    return {"ok": True, "message": "Thanks! Our AI assistant will call you shortly, during business hours."}
