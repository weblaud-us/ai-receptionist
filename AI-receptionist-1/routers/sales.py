"""
Outbound sales dialer API (Roadmap Task 4.6) — prefix /platform/sales, the sales team (viewer < caller < manager).

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
POST   /platform/sales/live/{attempt}/hangup  end it; one that hasn't rung yet is cancelled
GET    /platform/sales/attempts   GET …/{id}  PATCH …/{id}      results; correct an outcome; label AMD
GET    /platform/sales/funnel
GET    /platform/sales/offers   POST …   GET/PATCH …/{id}       what the dialer sells (SALES_DIALER_PLAN.md)
POST   /platform/sales/offers/{id}/publish | archive | unarchive | duplicate | preview
POST   /platform/sales/offers/{id}/test-call  talk to the agent in the browser (nothing is booked, sent or dialled)
POST   /platform/sales/leads/{id}/research   read the lead's website now
POST   /platform/sales/leads/{id}/notes      GET /platform/sales/pipeline   GET /platform/sales/meetings
GET    /platform/sales/tasks   POST …   PATCH …/{id}         follow-ups (done or cancelled, never deleted)
GET    /platform/sales/outbox  POST …/{id}/retry | mark-sent    sales email that didn't go out
GET    /platform/sales/leads.csv   GET /platform/sales/attempts.csv
GET    /public/sales/unsubscribe              one-click unsubscribe from sales email

There is deliberately no endpoint that skips the compliance gate, removes a do-not-call entry, or
deletes an offer (offers are archived; every call can be traced to the exact offer version it used).
The one way to ring a number a person chose (POST /platform/sales/calls, routers/sales_calls.py) goes
to a lead, through the full gate, only after a person approved a review of exactly that call, and the
approval is kept on the attempt.

Public (no sign-in), for the "get a call from our AI" landing page:
GET    /public/demo-call/consent              the exact consent wording to show next to the checkbox
POST   /public/demo-call                      records written consent and queues the call
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, ValidationError
from pymongo import ReturnDocument

from config import settings
from core import ratelimit
from core.auth import Principal, require_platform_admin
from core.auth.deps import require_sales
from core.outbound import (dialer, followup, funnel, gate, importer, lookup, outcomes, persona, phones, registry, research,
                           single_call)
from core.outbound import models as m
from core.outbound import offer_ai
from core.outbound import offers as of
from core.repositories import Repos
from core.tenancy.ids import new_id
from database.mongodb import get_db
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

sales_router = APIRouter(prefix="/platform/sales", tags=["Sales dialer"])
public_sales_router = APIRouter(prefix="/public", tags=["Sales dialer"])

AUDIT_SCOPE = "platform"

# Who may use what (SALES_SUITE_PLAN.md Phase 2): viewer < caller < manager < platform admin.
viewer = require_sales("viewer")
caller = require_sales("caller")
manager = require_sales("manager")
PAGE_SIZE = 50

TEST_CALLS = of.TEST_CALLS
TEST_CALL_MAX_S = of.TEST_CALL_MAX_S
TEST_CALL_IDENTITY = "lead"                 # the agent listens to the participant called "lead"

CONSENT_VERSION = "2026-11-v1"        # the built-in offer's wording; an offer's own wording is versioned with it


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


SINGLE_CALLS = "single"            # ?campaign_id=single: calls placed one at a time, without a campaign


def _by_campaign(campaign_id: Optional[str]) -> Dict[str, Any]:
    """Attempts of one campaign, or of single calls (`campaign_id` None, so the campaign index serves it)."""
    if not campaign_id:
        return {}
    if campaign_id == SINGLE_CALLS:
        return {"campaign_id": None, "kind": "single"}
    return {"campaign_id": campaign_id}


# ── Status ───────────────────────────────────────────────────────────────────

@sales_router.get("/status")
async def status(admin: Principal = Depends(viewer)):
    db = get_db()
    legacy = persona.legacy_offer()
    problems = await dialer.setup_problems(db)
    return to_jsonable({
        "ready": not problems,
        "problems": problems,
        "dialer_enabled": settings.OUTBOUND_DIALER_ENABLED,
        "line_type_lookup": lookup.configured(),
        "sales_tenant_id": settings.SALES_TENANT_ID or None,
        "company": settings.SALES_COMPANY_NAME,
        "registries": await registry.registry_overview(db),
        "registry_max_age_days": settings.SALES_DNC_REGISTRY_MAX_AGE_DAYS,
        "au_dncr_wash_max_age_days": settings.SALES_AU_DNCR_WASH_MAX_AGE_DAYS,
        "blocked_us_states": sorted(settings.sales_blocked_us_states),
        "modes": [{"key": mode, "countries": sorted(_countries(mode))} for mode in m.MODES],
        "personas": [{"id": v["id"], "label": v["label"],
                      "opener": persona.opener(legacy, v, of.SAMPLE_LEAD, "ai_cold_b2b_us")}
                     for v in legacy["variants"]],
        "consent_campaign_id": settings.SALES_CONSENT_CAMPAIGN_ID or None,
        "au_dncr_needs_wash": len(await registry.needs_wash(db, [r["phone"] for r in await db[m.LEADS].find(
            {"owner": m.OWNER_PLATFORM, "country": "AU", "status": {"$nin": ["dnc", "bad_number"]}}, {"phone": 1}).to_list(50_000)],
            "au_dncr", utcnow())),
        "ai_assist": await offer_ai.status(db, utcnow()),
        "learning": {**await offer_ai.learning_status(db, utcnow()), "coach": settings.SALES_COACH_ENABLED,
                     "memory": settings.SALES_LEAD_MEMORY_ENABLED, "playbooks": settings.SALES_PLAYBOOKS_ENABLED,
                     "autopilot": settings.SALES_LEARNING_AUTOPILOT},
        "research": {"enabled": research.enabled(), "spent_today_usd": round(await research.spent_today(db, utcnow()), 4),
                     "daily_cap_usd": settings.SALES_RESEARCH_DAILY_USD,
                     "pending": await db[m.LEADS].count_documents({"research.status": {"$in": ["pending", "running"]}})},
        "single_calls": await single_call.status(db),
        "counts": {"leads": await db[m.LEADS].count_documents({}), "dnc": await db[m.DNC].count_documents({})},
    })


# ── Team: who may use the sales dialer (SALES_SUITE_PLAN.md Phase 2) ────────

class TeamMemberBody(BaseModel):
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(default="", max_length=120)
    role: Literal["viewer", "caller", "manager"]
    takes_leads: bool = False                     # new interested leads and deals are shared out among these


class TeamPatchBody(BaseModel):
    role: Optional[Literal["viewer", "caller", "manager", "none"]] = None      # "none" takes access away
    takes_leads: Optional[bool] = None


def _member(doc: Mapping[str, Any]) -> Dict[str, Any]:
    return {"id": doc["_id"], "email": doc.get("email"), "name": doc.get("name") or "",
            "role": "admin" if doc.get("is_platform_admin") else doc.get("sales_role"),
            "takes_leads": bool(doc.get("sales_takes_leads")), "disabled": bool(doc.get("disabled")),
            "mfa_enabled": bool((doc.get("mfa") or {}).get("enabled")), "last_login_at": doc.get("last_login_at")}


@sales_router.get("/team")
async def team(admin: Principal = Depends(require_platform_admin)):
    rows = await get_db().users.find({"$or": [{"sales_role": {"$in": ["viewer", "caller", "manager"]}},
                                              {"is_platform_admin": True}]}).sort("email", 1).to_list(500)
    return to_jsonable({"members": [_member(r) for r in rows]})


@sales_router.post("/team")
async def add_team_member(body: TeamMemberBody, request: Request, admin: Principal = Depends(require_platform_admin)):
    """Give someone the sales dialer. New people get a one-time set-password link (not e-mailed)."""
    from core.auth import service as auth_service
    from core.repositories.repos import UsersStore

    users = UsersStore(get_db())
    email = body.email.strip().lower()
    existing = await users.by_email(email)
    link = None
    if existing and existing.get("is_platform_admin"):
        raise HTTPException(409, "That person is a platform admin and can already use everything.")
    if existing:
        user = existing
    else:
        user = await auth_service.create_user(email=email, password=None, name=body.name)
        token = await auth_service.start_password_reset(email)
        base = (settings.PLATFORM_DASHBOARD_URL or settings.DASHBOARD_URL or "").rstrip("/")
        link = f"{base}/reset-password?token={token}" if token else None
    await users.update(user["_id"], {"$set": {"sales_role": body.role, "sales_takes_leads": body.takes_leads}})
    await _audit(admin, request, "sales.team.add", "user", user["_id"], {"email": email, "role": body.role})
    return to_jsonable({"member": _member({**user, "sales_role": body.role, "sales_takes_leads": body.takes_leads}),
                        "set_password_link": link})


@sales_router.patch("/team/{user_id}")
async def update_team_member(user_id: str, body: TeamPatchBody, request: Request,
                             admin: Principal = Depends(require_platform_admin)):
    doc = await get_db().users.find_one({"_id": user_id})
    if not doc:
        raise HTTPException(404, "User not found")
    if doc.get("is_platform_admin"):
        raise HTTPException(409, "Platform admins can't be given a narrower sales role.")
    update: Dict[str, Any] = {}
    if body.role == "none":
        update["$unset"] = {"sales_role": "", "sales_takes_leads": ""}
    else:
        sets: Dict[str, Any] = {}
        if body.role:
            sets["sales_role"] = body.role
        if body.takes_leads is not None:
            sets["sales_takes_leads"] = body.takes_leads
        if sets:
            update["$set"] = sets
    if update:
        await get_db().users.update_one({"_id": user_id}, update)
    await _audit(admin, request, "sales.team.update", "user", user_id, body.model_dump(exclude_none=True))
    return to_jsonable({"member": _member(await get_db().users.find_one({"_id": user_id}) or doc)})


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
    research: bool = False          # read each new lead's website before it is called (SALES_RESEARCH_ENABLED)


@sales_router.post("/leads/import")
async def import_leads(body: ImportBody, request: Request, admin: Principal = Depends(manager)):
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
        if body.research:
            result["research_pending"] = await research.mark_pending(
                get_db(), {"owner": m.OWNER_PLATFORM, "source": f"csv:{body.source.strip()}", "status": {"$ne": "dnc"}},
                now=utcnow())
    return {**result, "fields": list(importer.FIELDS)}


@sales_router.get("/leads")
async def list_leads(status: Optional[str] = None, campaign_id: Optional[str] = None, q: Optional[str] = None,
                     source: Optional[str] = None, page: int = Query(1, ge=1),
                     owner_id: Optional[str] = None, tag: Optional[str] = None, country: Optional[str] = None,
                     min_fit: Optional[int] = Query(None, ge=0, le=100), stage: Optional[str] = None,
                     sort: Literal["newest", "fit", "updated"] = "newest",
                     admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {"owner": m.OWNER_PLATFORM}
    if status:
        query["status"] = status
    else:
        query["status"] = {"$ne": "merged"}
    if owner_id:
        query["owner_id"] = "" if owner_id == "none" else (admin.user_id if owner_id == "me" else owner_id)
    if tag:
        query["tags"] = tag
    if country:
        query["country"] = country.upper()[:2]
    if min_fit is not None:
        query["fit_score"] = {"$gte": min_fit}
    if stage:
        query["stage"] = stage
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
    order = {"newest": [("created_at", -1)], "fit": [("fit_score", -1), ("created_at", -1)], "updated": [("updated_at", -1)]}[sort]
    rows = await db[m.LEADS].find(query, {"gate_log": 0}).sort(order).skip((page - 1) * PAGE_SIZE) \
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
async def queue_leads(body: QueueBody, request: Request, admin: Principal = Depends(manager)):
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
    result = await dialer.queue_leads(get_db(), campaign, query, picked=bool(body.lead_ids))
    # Leads about to be called get researched first, when research is on (the dial never waits for it).
    result["research_pending"] = await research.mark_pending(
        get_db(), {"campaign_id": campaign["_id"], "status": "queued"}, now=utcnow())
    await _audit(admin, request, "sales.leads.queue", "campaign", campaign["_id"], result)
    return result


@sales_router.post("/leads/{lead_id}/research")
async def research_lead_now(lead_id: str, request: Request, admin: Principal = Depends(manager)):
    """Read the lead's website now (about 10–40 s). Counts towards the daily research budget."""
    lead = await _lead(lead_id)
    if not lead.get("website"):
        raise HTTPException(422, "Add the lead's website first.")
    if not settings.OPENAI_API_KEY:
        raise HTTPException(503, "Research needs OPENAI_API_KEY.")
    db = get_db()
    if await research.budget_left(db, utcnow()) <= 0:
        raise HTTPException(429, "Today's research budget (SALES_RESEARCH_DAILY_USD) is used up.")
    campaign = await db[m.CAMPAIGNS].find_one({"_id": lead.get("campaign_id")}) if lead.get("campaign_id") else None
    offer = await of.offer_for_campaign(db, campaign or {})
    doc = await research.research_lead(db, lead, offer, now=utcnow())
    await _audit(admin, request, "sales.lead.research", "sales_lead", lead_id, {"status": doc["status"]})
    return to_jsonable({"research": doc})


@sales_router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, admin: Principal = Depends(viewer)):
    lead = await _lead(lead_id)
    attempts = await get_db()[m.ATTEMPTS].find({"lead_id": lead_id}).sort("created_at", -1).limit(50).to_list(50)
    out = _out(lead)
    out["gate_log"] = to_jsonable(list(reversed(lead.get("gate_log") or [])))
    out["stage"] = followup.stage_of(lead)
    db = get_db()
    tasks = await db[followup.TASKS].find({"lead_id": lead_id}).sort("created_at", -1).limit(30).to_list(30)
    from core.outbound import crm

    deals = await db[crm.DEALS].find({"lead_id": lead_id}).sort("updated_at", -1).to_list(50)
    scheduled = await db[followup.OUTBOX].find({"lead_id": lead_id, "status": "scheduled"},
                                               {"subject": 1, "send_after": 1, "step": 1, "sequence_trigger": 1}).sort("send_after", 1).to_list(20)
    out["memory"] = await _memory_of(lead)
    return {"lead": out, "attempts": [_out(a) for a in attempts], "deals": [_out(d) for d in deals],
            "scheduled_emails": [_out(e) for e in scheduled], "email_allowed": crm.email_permission(lead, lead.get("email") or "") is not None,
            "activity": [_out(a) for a in await followup.activity(db, lead_id)],
            "tasks": [_out(t) for t in tasks],
            "consent": to_jsonable(await db[m.CONSENT].find({"phone": lead["phone"]}, {"_id": 0})
                                   .sort("at", -1).limit(10).to_list(10))}


async def _memory_of(lead: Mapping[str, Any]) -> Dict[str, Any]:
    """What the agent will know on the next call to this lead (SALES_LEARNING_PLAN.md Phase 1)."""
    from core.outbound import memory

    db = get_db()
    campaign = await db[m.CAMPAIGNS].find_one({"_id": lead.get("campaign_id")}) if lead.get("campaign_id") else None
    offer = await of.offer_for_campaign(db, campaign or {})
    return to_jsonable({"lines": await memory.lead_brief(db, lead, offer, now=utcnow()),
                        "cleared_at": lead.get("memory_cleared_at"), "enabled": settings.SALES_LEAD_MEMORY_ENABLED,
                        "days": settings.SALES_MEMORY_DAYS})


@sales_router.post("/leads/{lead_id}/memory/forget")
async def forget_lead_memory(lead_id: str, request: Request, admin: Principal = Depends(caller)):
    """The agent stops using earlier calls with this business. Nothing is deleted: the calls stay on
    record; the memory just starts again from now."""
    lead = await _lead(lead_id)
    now = utcnow()
    await get_db()[m.LEADS].update_one({"_id": lead_id}, {"$set": {"memory_cleared_at": now, "updated_at": now}})
    await _audit(admin, request, "sales.lead.memory_forget", "sales_lead", lead_id, {})
    return {"memory": await _memory_of({**lead, "memory_cleared_at": now})}


class LeadPatch(BaseModel):
    business_name: Optional[str] = Field(default=None, max_length=200)
    contact_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)
    agent_notes: Optional[str] = Field(default=None, max_length=600)   # what the agent should know about them
    vertical: Optional[str] = Field(default=None, max_length=60)        # what kind of business, e.g. "dental clinic"
    tags: Optional[List[str]] = Field(default=None, max_length=20)
    timezone: Optional[str] = Field(default=None, max_length=64)
    region_code: Optional[str] = Field(default=None, max_length=40)     # "TX", "QLD", or the full name
    website: Optional[str] = Field(default=None, max_length=200)
    no_website: Optional[bool] = None                              # they have no website of their own
    stage: Optional[str] = Field(default=None, max_length=20)     # "" = back to the stage the calls say
    owner_id: Optional[str] = Field(default=None, max_length=60)  # who works this lead ("" = nobody)


@sales_router.patch("/leads/{lead_id}")
async def patch_lead(lead_id: str, body: LeadPatch, request: Request, admin: Principal = Depends(caller)):
    lead = await _lead(lead_id)
    sets: Dict[str, Any] = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "stage" in sets:
        if sets["stage"] and sets["stage"] not in followup.PIPELINE_STAGES:
            raise HTTPException(422, f"Stage is one of {', '.join(followup.PIPELINE_STAGES)}")
        sets["stage"] = sets["stage"] or None
        if sets["stage"] != lead.get("stage"):
            await followup.add_activity(get_db(), lead_id, "stage",
                                        f"Stage: {sets['stage'] or 'back to automatic'}", by=admin.user_id, now=utcnow())
    if "email" in sets and sets["email"] and "@" not in sets["email"]:
        raise HTTPException(422, "That doesn't look like an email address")
    if "timezone" in sets:
        if not phones.valid_timezone(sets["timezone"]):
            raise HTTPException(422, "Unknown timezone (use an IANA name such as America/Chicago)")
        sets["timezones"] = [sets["timezone"]]
    if "region_code" in sets:
        country = str(lead.get("country") or "US")
        sets["region_code"] = phones.normalize_region(country, sets["region_code"])
        if not sets["region_code"]:
            raise HTTPException(422, f"Unknown state for {country}")
        info = phones.parse(lead["phone"])
        if info is not None and "timezone" not in sets:          # the state narrows down where they are
            zones = phones.timezones_for(info, state=sets["region_code"])
            if zones:
                sets["timezones"] = list(zones)
                sets["timezone"] = zones[0] if len(zones) == 1 else ""
    if sets:
        sets["updated_at"] = utcnow()
        await get_db()[m.LEADS].update_one({"_id": lead_id}, {"$set": sets})
        await _audit(admin, request, "sales.lead.update", "sales_lead", lead_id, {k: v for k, v in sets.items() if k != "updated_at"})
    return {"lead": _out(await _lead(lead_id))}


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@sales_router.post("/leads/{lead_id}/notes")
async def add_note(lead_id: str, body: NoteBody, admin: Principal = Depends(caller)):
    await _lead(lead_id)
    doc = await followup.add_activity(get_db(), lead_id, "note", body.text.strip(), by=admin.user_id, now=utcnow())
    return {"activity": _out(doc)}


# ── Pipeline, tasks, meetings, the outbox ────────────────────────────────────

_PIPELINE_FIELDS = {"business_name": 1, "contact_name": 1, "phone": 1, "email": 1, "status": 1, "stage": 1,
                    "attempts": 1, "history": 1, "campaign_id": 1, "demo": 1, "qualification": 1, "updated_at": 1,
                    "last_outcome": 1, "country": 1}


@sales_router.get("/pipeline")
async def pipeline(admin: Principal = Depends(viewer)):
    """Leads past the first call: interested, meeting booked, proposal, won, lost (most recent first)."""
    db = get_db()
    rows = await db[m.LEADS].find(
        {"owner": m.OWNER_PLATFORM, "$or": [{"stage": {"$in": list(followup.PIPELINE_STAGES)}},
                                            {"status": {"$in": ["interested", "demo_booked", "not_interested"]}}]},
        _PIPELINE_FIELDS).sort("updated_at", -1).limit(1000).to_list(1000)
    open_tasks: Dict[str, int] = {}
    async for t in db[followup.TASKS].find({"done_at": None, "cancelled": False}, {"lead_id": 1}):
        open_tasks[t["lead_id"]] = open_tasks.get(t["lead_id"], 0) + 1
    columns: Dict[str, List[Any]] = {s: [] for s in ("interested", "meeting_booked", "proposal", "won", "lost")}
    for row in rows:
        stage = followup.stage_of(row)
        if stage in columns and len(columns[stage]) < 200:
            columns[stage].append({**_out(row), "stage": stage, "open_tasks": open_tasks.get(row["_id"], 0)})
    return {"columns": columns, "counts": {k: len(v) for k, v in columns.items()}}


@sales_router.get("/tasks")
async def list_tasks(status: Literal["open", "done", "all"] = "open", lead_id: Optional[str] = None,
                     admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {}
    if status == "open":
        query.update(done_at=None, cancelled=False)
    elif status == "done":
        query["done_at"] = {"$ne": None}
    if lead_id:
        query["lead_id"] = lead_id
    db = get_db()
    rows = await db[followup.TASKS].find(query).sort([("due_at", 1), ("created_at", 1)]).limit(500).to_list(500)
    leads = {lead["_id"]: lead async for lead in db[m.LEADS].find(
        {"_id": {"$in": list({r["lead_id"] for r in rows})}}, {"business_name": 1, "contact_name": 1, "phone": 1})}
    return {"tasks": [{**_out(r), "lead": _out(leads.get(r["lead_id"]))} for r in rows]}


class TaskBody(BaseModel):
    lead_id: str
    title: str = Field(min_length=2, max_length=200)
    due_at: Optional[datetime] = None
    owner: str = Field(default="", max_length=80)


@sales_router.post("/tasks")
async def create_task(body: TaskBody, admin: Principal = Depends(caller)):
    await _lead(body.lead_id)
    doc = await followup.create_task(get_db(), body.lead_id, body.title.strip(), due_at=body.due_at, owner=body.owner,
                                     by=admin.user_id, now=utcnow())
    return {"task": _out(doc)}


class TaskPatch(BaseModel):
    done: Optional[bool] = None
    cancelled: Optional[bool] = None
    title: Optional[str] = Field(default=None, min_length=2, max_length=200)
    due_at: Optional[datetime] = None


@sales_router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskPatch, admin: Principal = Depends(caller)):
    db = get_db()
    task = await db[followup.TASKS].find_one({"_id": task_id})
    if not task:
        raise HTTPException(404, "Task not found")
    now = utcnow()
    sets: Dict[str, Any] = {}
    if body.done is not None:
        sets["done_at"] = now if body.done else None
    if body.cancelled is not None:
        sets["cancelled"] = body.cancelled
    if body.title:
        sets["title"] = body.title.strip()
    if "due_at" in body.model_fields_set:
        sets["due_at"] = body.due_at
    if sets:
        await db[followup.TASKS].update_one({"_id": task_id}, {"$set": sets})
        if body.done:
            await followup.add_activity(db, task["lead_id"], "task", f"Done: {task['title']}", by=admin.user_id, now=now)
    return {"task": _out(await db[followup.TASKS].find_one({"_id": task_id}))}


@sales_router.get("/meetings")
async def meetings(days_ahead: int = Query(30, ge=1, le=180), days_back: int = Query(7, ge=0, le=90),
                   admin: Principal = Depends(viewer)):
    """Meetings the dialer booked, from the sales business's calendar, with the lead each one is for."""
    if not settings.SALES_TENANT_ID:
        return {"meetings": []}
    now = utcnow()
    appts = await Repos(get_db(), settings.SALES_TENANT_ID).appointments.find(
        {"source": "outbound_sales", "start": {"$gte": now - timedelta(days=days_back), "$lte": now + timedelta(days=days_ahead)}},
        sort=[("start", 1)], limit=500)
    ids = [a.get("appointment_id") for a in appts]
    leads = {lead["demo"]["appointment_id"]: lead async for lead in get_db()[m.LEADS].find(
        {"demo.appointment_id": {"$in": ids}}, {"business_name": 1, "contact_name": 1, "phone": 1, "email": 1, "demo": 1})}
    return to_jsonable({"meetings": [
        {"appointment_id": a.get("appointment_id"), "start": a.get("start"), "end": a.get("end"), "status": a.get("status"),
         "name": a.get("name"), "email": a.get("email"), "service_name": a.get("service_name"),
         "lead": _out(leads.get(a.get("appointment_id")))} for a in appts]})


@sales_router.get("/outbox")
async def outbox(status: Literal["needs_attention", "all"] = "needs_attention", admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {"status": "failed"} if status == "needs_attention" else {}
    rows = await get_db()[followup.OUTBOX].find(query, {"attachments": 0}).sort("created_at", -1).limit(200).to_list(200)
    return {"emails": [_out(r) for r in rows]}


@sales_router.post("/outbox/{email_id}/retry")
async def retry_email(email_id: str, admin: Principal = Depends(manager)):
    db = get_db()
    row = await db[followup.OUTBOX].find_one({"_id": email_id})
    if not row:
        raise HTTPException(404, "Email not found")
    if row.get("status") != "failed":                 # sent, scheduled or being sent: never send it twice
        raise HTTPException(409, "Only an email that failed to send can be sent again.")
    if await followup.is_suppressed(db, row["to"]):
        raise HTTPException(409, "That address unsubscribed.")
    claimed = await db[followup.OUTBOX].find_one_and_update({"_id": email_id, "status": "failed"},
                                                            {"$set": {"status": "pending", "claimed_at": utcnow()}})
    if not claimed:
        raise HTTPException(409, "Someone is already sending it again.")
    done = await followup._try(db, {**row, "tries": 0}, utcnow())
    return {"email": _out({k: v for k, v in done.items() if k != "attachments"})}


@sales_router.post("/outbox/{email_id}/mark-sent")
async def mark_email_sent(email_id: str, request: Request, admin: Principal = Depends(manager)):
    res = await get_db()[followup.OUTBOX].update_one({"_id": email_id}, {"$set": {"status": "manual_sent", "sent_at": utcnow()}})
    if not res.matched_count:
        raise HTTPException(404, "Email not found")
    await _audit(admin, request, "sales.email.mark_sent", "sales_email", email_id)
    return {"ok": True}


# ── Exports ──────────────────────────────────────────────────────────────────

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    """Spreadsheet-safe: a value that starts like a formula is prefixed with a quote."""
    text = "" if value is None else (", ".join(map(str, value)) if isinstance(value, list) else str(value))
    return "'" + text if text.startswith(_FORMULA) else text


def _csv(rows: List[Dict[str, Any]], cols: List[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for row in rows:
        w.writerow([_cell(row.get(c)) for c in cols])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@sales_router.get("/leads.csv")
async def export_leads(request: Request, status: Optional[str] = None, campaign_id: Optional[str] = None,
                       source: Optional[str] = None, stage: Optional[str] = None,
                       admin: Principal = Depends(manager)):
    query: Dict[str, Any] = {"owner": m.OWNER_PLATFORM}
    for key, value in (("status", status), ("campaign_id", campaign_id), ("source", source)):
        if value:
            query[key] = value
    rows = await get_db()[m.LEADS].find(query, {"gate_log": 0}).sort("created_at", -1).limit(20_000).to_list(20_000)
    out = []
    for row in rows:
        row_stage = followup.stage_of(row)
        if stage and row_stage != stage:
            continue
        q = row.get("qualification") or {}
        out.append({**row, "stage": row_stage, "interest": q.get("interest"), "pains": q.get("pains"),
                    "next_step": q.get("next_step"), "meeting": (row.get("demo") or {}).get("start")})
    await _audit(admin, request, "sales.leads.export", "sales_leads", "*", {"rows": len(out), "status": status, "stage": stage})
    return _csv(out, ["business_name", "contact_name", "phone", "email", "website", "country", "region_code", "status",
                      "stage", "attempts", "last_outcome", "interest", "pains", "next_step", "meeting", "source", "tags",
                      "notes", "created_at"], "sales-leads.csv")


@sales_router.get("/dncr-wash.csv")
async def dncr_wash_export(request: Request, admin: Principal = Depends(require_platform_admin)):
    """Australian leads that need washing against the Do Not Call Register before they can be called
    (no result, or one older than SALES_AU_DNCR_WASH_MAX_AGE_DAYS). Load the result with
    `python -m scripts.import_dnc_registry au_dncr --wash-result <file> --country AU`."""
    db = get_db()
    rows = await db[m.LEADS].find({"owner": m.OWNER_PLATFORM, "country": "AU",
                                   "status": {"$nin": ["dnc", "bad_number"]}}, {"phone": 1}).to_list(50_000)
    due = await registry.needs_wash(db, [r["phone"] for r in rows], "au_dncr", utcnow())
    await _audit(admin, request, "sales.dncr.export", "sales_leads", "au_dncr", {"rows": len(due)})
    # National format (0412345678), as the register's washing service takes it.
    return _csv([{"number": "0" + p[3:]} for p in due], ["number"], "dncr-wash.csv")


@sales_router.get("/attempts.csv")
async def export_attempts(request: Request, campaign_id: Optional[str] = None, days: int = Query(90, ge=1, le=365),
                          admin: Principal = Depends(manager)):
    query: Dict[str, Any] = {"status": "done", "created_at": {"$gte": utcnow() - timedelta(days=days)},
                             **_by_campaign(campaign_id)}
    rows = await get_db()[m.ATTEMPTS].find(query, {"gate": 0}).sort("created_at", -1).limit(50_000).to_list(50_000)
    await _audit(admin, request, "sales.attempts.export", "call_attempts", "*", {"rows": len(rows), "days": days})
    return _csv(rows, ["created_at", "campaign_id", "kind", "to", "from", "offer_id", "offer_version", "variant_id", "mode",
                       "outcome", "amd", "duration_sec", "cost_usd", "summary", "next_step"], "sales-calls.csv")


class CheckBody(BaseModel):
    campaign_id: Optional[str] = None
    mode: Literal["ai_cold_b2b_us", "ai_consented", "human_assisted"] = "ai_cold_b2b_us"


@sales_router.post("/leads/{lead_id}/check")
async def check_lead(lead_id: str, body: CheckBody, admin: Principal = Depends(manager)):
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
async def lead_dnc(lead_id: str, body: DncBody, request: Request, admin: Principal = Depends(caller)):
    lead = await _lead(lead_id)
    await registry.add_to_dnc(get_db(), lead["phone"], reason="manual", source=f"platform:{admin.user_id}", note=body.note)
    await _audit(admin, request, "sales.dnc.add", "sales_lead", lead_id)
    return {"success": True}


@sales_router.get("/dnc")
async def list_dnc(q: Optional[str] = None, page: int = Query(1, ge=1),
                   admin: Principal = Depends(viewer)):
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
async def add_dnc(body: DncBody, request: Request, admin: Principal = Depends(caller)):
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
async def list_campaigns(admin: Principal = Depends(viewer)):
    rows = await get_db()[m.CAMPAIGNS].find({}).sort("created_at", -1).limit(200).to_list(200)
    from core.outbound import timing

    splits = {d["campaign_id"]: d async for d in get_db()[timing.STATS].find(
        {"_id": {"$in": [f"scripts:{c['_id']}" for c in rows]}}, {"alloc": 1, "how": 1, "p_best": 1, "campaign_id": 1})}
    return {"campaigns": [{**_out(c), "progress": await _progress(c["_id"]),
                           "scripts": to_jsonable({k: v for k, v in (splits.get(c["_id"]) or {}).items() if k != "_id"}) or None}
                          for c in rows]}


async def _check_offer(settings_doc: Dict[str, Any], *, previous: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """A campaign may only name a published offer version and that version's variants. An archived
    offer can't be newly attached, but campaigns already using it keep working."""
    offer_id, version = settings_doc.get("offer_id"), settings_doc.get("offer_version")
    if not offer_id:
        return settings_doc
    db = get_db()
    offer = await db[of.OFFERS].find_one({"_id": offer_id}, {"status": 1})
    snap = await db[of.OFFER_VERSIONS].find_one({"_id": f"{offer_id}@{version}"})
    if offer is None or snap is None:
        raise HTTPException(422, "Pick a published version of an existing offer.")
    if offer.get("status") == "archived" and (previous or {}).get("offer_id") != offer_id:
        raise HTTPException(422, "That offer is archived. Unarchive it first.")
    known = [v["id"] for v in snap["settings"]["variants"]]
    chosen = settings_doc.get("variant_ids") or known[:1]
    unknown = [v for v in chosen if v not in known]
    if unknown:
        raise HTTPException(422, f"Version {version} of that offer has no script {', '.join(unknown)}.")
    return {**settings_doc, "variant_ids": chosen}


@sales_router.post("/campaigns")
async def create_campaign(body: m.CampaignSettings, request: Request, admin: Principal = Depends(manager)):
    now = utcnow()
    settings_doc = await _check_offer(body.model_dump())
    doc = {"_id": new_id("cmp"), **settings_doc, "status": "draft", "legal_review": None,
           "created_by": admin.user_id, "created_at": now, "updated_at": now}
    await get_db()[m.CAMPAIGNS].insert_one(doc)
    await _audit(admin, request, "sales.campaign.create", "campaign", doc["_id"],
                 {"name": body.name, "mode": body.mode, "offer_id": body.offer_id, "offer_version": body.offer_version})
    return {"campaign": _out(doc)}


@sales_router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, admin: Principal = Depends(viewer)):
    campaign = await _campaign(campaign_id)
    return {"campaign": _out(campaign), "progress": await _progress(campaign_id),
            "problems": dialer.campaign_problems(campaign)}


@sales_router.patch("/campaigns/{campaign_id}")
async def patch_campaign(campaign_id: str, body: Dict[str, Any], request: Request,
                         admin: Principal = Depends(manager)):
    campaign = await _campaign(campaign_id)
    current = {k: campaign.get(k) for k in m.CampaignSettings.model_fields if k in campaign}
    patch = {k: v for k, v in body.items() if k in m.CampaignSettings.model_fields}
    if "mode" in patch and patch["mode"] != campaign["mode"] and campaign["status"] != "draft":
        raise HTTPException(409, "The mode can only be changed while the campaign is a draft.")
    try:
        merged = m.CampaignSettings.model_validate({**current, **patch})
    except ValueError as e:
        raise HTTPException(422, str(e)[:500])
    sets = {**await _check_offer(merged.model_dump(), previous=campaign), "updated_at": utcnow()}
    # Only a real change to what is said or how the call is made needs a fresh lawyer's review;
    # saving the form with the same script, or renaming the campaign, keeps the sign-off.
    changed = m.material_changes(campaign, sets)
    if changed and campaign.get("status") == "running":
        raise HTTPException(409, "Pause the campaign before changing its call type, script, caller IDs or "
                                 "voicemail: those changes need a fresh legal review.")
    if changed:
        sets["legal_review"] = None
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id}, {"$set": sets})
    await _audit(admin, request, "sales.campaign.update", "campaign", campaign_id,
                 {**patch, "material_changes": changed})
    return {"campaign": _out(await _campaign(campaign_id))}


class SuggestBody(BaseModel):
    offer_id: str = Field(min_length=2, max_length=40)
    source: Optional[str] = Field(default=None, max_length=120)
    tag: Optional[str] = Field(default=None, max_length=60)
    lead_ids: Optional[List[str]] = Field(default=None, max_length=5000)


@sales_router.post("/campaigns/suggest")
async def suggest_campaign(body: SuggestBody, admin: Principal = Depends(manager)):
    """Campaign drafts for an offer and a set of leads, from the rules (SALES_SUITE_PLAN.md Phase 9). Nothing is created."""
    from core.outbound import campaign_setup

    doc = await _offer(body.offer_id)
    if not doc.get("published_version"):
        raise HTTPException(409, "Publish the offer first.")
    if doc.get("status") == "archived":
        raise HTTPException(409, "That offer is archived.")
    snap = await of.get_version(get_db(), body.offer_id, int(doc["published_version"]))
    query: Dict[str, Any] = {}
    if body.lead_ids:
        query["_id"] = {"$in": body.lead_ids}
    if body.source:
        query["source"] = body.source
    if body.tag:
        query["tags"] = body.tag
    result = await campaign_setup.suggest(get_db(), {**doc, "id": doc["_id"]}, snap or {}, query, now=utcnow())
    return to_jsonable(result)


@sales_router.get("/campaigns/{campaign_id}/preflight")
async def campaign_preflight(campaign_id: str, admin: Principal = Depends(viewer)):
    """The launch checklist: what must be true before this campaign starts (SALES_SUITE_PLAN.md Phase 10)."""
    from core.outbound import preflight

    items = await preflight.checks(get_db(), await _campaign(campaign_id), now=utcnow())
    return to_jsonable({"checks": items, "can_start": not preflight.failed(items)})


# ── Caller ID health (SALES_SUITE_PLAN.md Phase 12) ──────────────────────────

@sales_router.get("/numbers/health")
async def numbers_health(admin: Principal = Depends(viewer)):
    from core.outbound import number_health

    return to_jsonable({"numbers": await number_health.health(get_db(), now=utcnow()),
                        "min_dials": number_health.MIN_DIALS, "rest_hours": number_health.REST_HOURS})


class RestBody(BaseModel):
    hours: int = Field(default=72, ge=1, le=24 * 30)
    reason: str = Field(default="Rested by hand", max_length=200)


def _e164(number: str) -> str:
    info = phones.parse(number)
    if not info:
        raise HTTPException(422, "Not a phone number")
    return info.e164


@sales_router.post("/numbers/{number}/rest")
async def rest_number(number: str, body: RestBody, request: Request, admin: Principal = Depends(manager)):
    from core.outbound import number_health

    e164 = _e164(number)
    await number_health.rest(get_db(), e164, hours=body.hours, reason=body.reason, by=admin.user_id, now=utcnow())
    await _audit(admin, request, "sales.number.rest", "phone", e164[-4:], {"hours": body.hours, "reason": body.reason})
    return {"success": True}


@sales_router.post("/numbers/{number}/reactivate")
async def reactivate_number(number: str, request: Request, admin: Principal = Depends(manager)):
    from core.outbound import number_health

    e164 = _e164(number)
    await number_health.reactivate(get_db(), e164, by=admin.user_id, now=utcnow())
    await _audit(admin, request, "sales.number.reactivate", "phone", e164[-4:])
    return {"success": True}


class StartBody(BaseModel):
    legal_review_confirmed: bool = False
    note: str = Field(default="", max_length=300)
    # "Let the agent use lessons from earlier calls" (openers, prices, claims and consent never change).
    # None = leave what the sign-off already says.
    allow_learning: Optional[bool] = None


def learning_consent(admin: Principal, allowed: bool, note: str = "") -> Dict[str, Any]:
    from core.outbound.playbook import LEARNING_POLICY_VERSION

    return {"allowed": bool(allowed), "policy": LEARNING_POLICY_VERSION, "by": admin.user_id, "at": utcnow(),
            "note": note[:300]}


@sales_router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: str, body: StartBody, request: Request,
                         admin: Principal = Depends(manager)):
    campaign = await _campaign(campaign_id)
    if campaign["status"] == "completed":
        raise HTTPException(409, "That campaign is completed. Create a new one.")
    problems = dialer.campaign_problems(campaign)
    if problems:
        raise HTTPException(409, " ".join(problems))
    from core.outbound import preflight

    checklist = await preflight.checks(get_db(), campaign, now=utcnow())
    blocking = preflight.failed(checklist)
    if blocking:
        raise HTTPException(409, {"code": "preflight_failed", "checks": checklist,
                                  "message": "preflight_failed: " + "; ".join(c["label"] + (f" ({c['detail']})" if c["detail"] else "")
                                                                               for c in blocking)})
    sets: Dict[str, Any] = {"status": "running", "updated_at": utcnow(), "paused_reason": None,
                            "last_preflight": {"at": utcnow(), "by": admin.user_id, "checks": checklist}}
    if not m.legal_review_valid(campaign):
        if not body.legal_review_confirmed:
            raise HTTPException(409, "legal_review_required: confirm that a telemarketing lawyer has reviewed "
                                     "this campaign's mode, opener and lead list before the first call.")
        # The snapshot is what the lawyer saw: a later change to any of it voids the sign-off.
        sets["legal_review"] = {"confirmed_by": admin.user_id, "at": utcnow(), "note": body.note,
                                "material": m.material_snapshot(campaign), "preflight": checklist}
        if body.allow_learning is not None:
            sets["legal_review"]["learning"] = learning_consent(admin, body.allow_learning)
    elif body.allow_learning is not None:
        sets["legal_review.learning"] = learning_consent(admin, body.allow_learning)
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id}, {"$set": sets})
    await _audit(admin, request, "sales.campaign.start", "campaign", campaign_id,
                 {"legal_review": bool(body.legal_review_confirmed), "allow_learning": body.allow_learning})
    return {"campaign": _out(await _campaign(campaign_id))}


@sales_router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, request: Request, admin: Principal = Depends(manager)):
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
async def live_calls(admin: Principal = Depends(viewer)):
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
async def join_call(attempt_id: str, body: JoinBody, request: Request, admin: Principal = Depends(caller)):
    attempt = await _active_attempt(attempt_id)
    takeover = body.mode == "takeover"
    if takeover:
        # One person speaks on a call: never silently replace someone who already took it over.
        claimed = await get_db()[m.ATTEMPTS].update_one(
            {"_id": attempt_id, "status": {"$in": list(m.ACTIVE_ATTEMPT_STATUSES)},
             "operator": {"$in": [None, admin.user_id]}},
            {"$set": {"operator": admin.user_id, "taken_over_at": utcnow()}})
        if not claimed.matched_count:
            raise HTTPException(409, "Someone else is already on this call. You can still listen in.")
    await _audit(admin, request, f"sales.call.{body.mode}", "call_attempt", attempt_id)
    return _room_token(attempt["call_id"], takeover=takeover, user=admin)


@sales_router.post("/live/{attempt_id}/hangup")
async def hang_up_call(attempt_id: str, request: Request, admin: Principal = Depends(caller)):
    attempt = await _active_attempt(attempt_id)
    from livekit import api

    if attempt["status"] == "dispatched":
        # It hasn't rung yet (the agent is starting, or no sales worker picked it up): cancel it. Closed
        # first, so the agent's claim (dispatched → dialing) fails and nothing ever rings; then the room
        # goes, which drops the waiting dispatch. Uncounted: the lead goes back as it was.
        who = (admin.name or "someone")[:60]
        if await outcomes.finish_attempt(get_db(), attempt_id, "dispatch_failed", only_from=("dispatched",),
                                         detail=f"Ended by {who} before it rang"):
            lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            try:
                await lk.room.delete_room(api.DeleteRoomRequest(room=attempt["call_id"]))
            except Exception:                            # often there is no room yet: nothing to drop
                logger.info("No room to drop for cancelled call %s", attempt_id, exc_info=True)
            finally:
                await lk.aclose()
            await _audit(admin, request, "sales.call.cancel", "call_attempt", attempt_id)
            return {"success": True, "cancelled": True}
        # The agent claimed it a moment ago: it's ringing now, so hang up as usual.

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
async def dial_next(campaign_id: str, request: Request, admin: Principal = Depends(caller)):
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
            "join": _room_token(result["room"], takeover=True, user=admin),
            "context": await _call_context(await get_db()[m.ATTEMPTS].find_one({"_id": result["attempt_id"]}))}


# ── Results ──────────────────────────────────────────────────────────────────

@sales_router.get("/attempts")
async def list_attempts(campaign_id: Optional[str] = None, outcome: Optional[str] = None,
                        unlabelled: bool = False, flagged: bool = False, page: int = Query(1, ge=1),
                        admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {"status": "done", **_by_campaign(campaign_id)}
    if outcome:
        query["outcome"] = outcome
    if unlabelled:
        query.update({"amd": {"$ne": None}, "amd_label": None})
    if flagged:                                   # a fixed-rule check or the coach found something
        query["$or"] = [{"checks.severe.0": {"$exists": True}}, {"checks.other.0": {"$exists": True}},
                        {"review.flags.0": {"$exists": True}}]
    db = get_db()
    total = await db[m.ATTEMPTS].count_documents(query)
    rows = await db[m.ATTEMPTS].find(query, {"gate": 0}).sort("created_at", -1).skip((page - 1) * PAGE_SIZE) \
        .limit(PAGE_SIZE).to_list(PAGE_SIZE)
    leads = {l["_id"]: l async for l in db[m.LEADS].find(
        {"_id": {"$in": [r["lead_id"] for r in rows]}}, {"business_name": 1, "contact_name": 1})}
    return {"attempts": [{**_out(r), "business_name": (leads.get(r["lead_id"]) or {}).get("business_name")} for r in rows],
            "total": total, "page": page, "pages": _pages(total, PAGE_SIZE)}


@sales_router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str, admin: Principal = Depends(viewer)):
    db = get_db()
    attempt = await db[m.ATTEMPTS].find_one({"_id": attempt_id})
    if not attempt:
        raise HTTPException(404, "Call not found")
    call = await db.calls.find_one({"call_id": attempt["call_id"]},  # cross-tenant: the platform's own sales call, by its unique id
                                   {"transcript": 1, "transcript_enc": 1, "summary": 1, "latency": 1, "duration_s": 1})
    if call:
        from core.sensitive import unseal

        call = unseal(call, ("transcript",))      # sealed when the sales tenant encrypts call content
    lead = await db[m.LEADS].find_one({"_id": attempt["lead_id"]}, {"gate_log": 0})
    return {"attempt": _out(attempt), "lead": _out(lead),
            "transcript": to_jsonable((call or {}).get("transcript") or [])}


async def _call_context(attempt: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the person on a human-assisted call needs in front of them: who, what we know, what to say."""
    if not attempt:
        return {}
    db = get_db()
    lead = await db[m.LEADS].find_one({"_id": attempt["lead_id"]}, {"gate_log": 0}) or {}
    offer = await of.offer_for(db, attempt.get("offer_id"), attempt.get("offer_version"))
    variant = of.variant(offer, attempt.get("variant_id") or attempt.get("persona_id"))
    values = of.opener_values(offer, lead)
    earlier = await db[m.ATTEMPTS].find({"lead_id": attempt["lead_id"], "_id": {"$ne": attempt["_id"]}, "status": "done"},
                                        {"outcome": 1, "summary": 1, "created_at": 1}).sort("created_at", -1).limit(5).to_list(5)
    pricing = offer.get("pricing") or {}
    from core.outbound import memory

    remembered = await memory.lead_brief(db, lead, offer, now=utcnow(), exclude_attempt_id=attempt["_id"]) if lead else []
    return to_jsonable({
        "attempt": m.with_id(dict(attempt)), "lead": m.with_id(dict(lead)) if lead else None,
        "research": lead.get("research") if (lead.get("research") or {}).get("status") == "done" else None,
        "earlier_calls": [m.with_id(e) for e in earlier],
        "memory": remembered,
        "cheat_sheet": {
            "offer": offer.get("name"), "company": offer["company_name"],
            "opener": of.render(variant.get("human_opener") or variant["cold_opener"], values),
            "first_question": of.render(variant.get("first_question") or "", values),
            "pitch": of.render(variant.get("pitch") or "", values),
            "discovery_questions": offer.get("discovery_questions") or [],
            "qualification_questions": offer.get("qualification_questions") or [],
            "value_props": offer.get("value_props") or [], "proof_points": offer.get("proof_points") or [],
            "objections": offer.get("objections") or [],
            "pricing": pricing.get("text") if pricing.get("mode") == "range" else "Don't quote a price: the meeting covers it.",
            "meeting": persona.meeting_phrase(offer),
        }})


@sales_router.get("/attempts/{attempt_id}/context")
async def attempt_context(attempt_id: str, admin: Principal = Depends(viewer)):
    attempt = await get_db()[m.ATTEMPTS].find_one({"_id": attempt_id}, {"gate": 0})
    if not attempt:
        raise HTTPException(404, "Call not found")
    return await _call_context(attempt)


class WrapUpTask(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    due_at: Optional[datetime] = None


class WrapUp(BaseModel):
    outcome: str
    confirm_dnc: bool = False
    notes: str = Field(default="", max_length=2000)
    next_step: str = Field(default="", max_length=200)
    callback_at: Optional[datetime] = None
    stage: Optional[str] = Field(default=None, max_length=20)
    task: Optional[WrapUpTask] = None


@sales_router.post("/attempts/{attempt_id}/wrap-up")
async def wrap_up(attempt_id: str, body: WrapUp, request: Request, admin: Principal = Depends(caller)):
    """The person's verdict on a call they took. Works before or after the call record is closed: the
    outcome is stored on the attempt first, and whichever side closes it uses this one."""
    if body.outcome == "dnc" and not body.confirm_dnc:
        raise HTTPException(422, "Adding a number to the do-not-call list is permanent; confirm it (confirm_dnc).")
    if body.outcome != "dnc" and body.outcome not in m.MANUAL_OUTCOMES:
        raise HTTPException(422, f"Pick one of {', '.join(m.MANUAL_OUTCOMES)}.")
    if body.outcome == "callback" and not body.callback_at:
        raise HTTPException(422, "When should we call back?")
    if body.stage and body.stage not in followup.PIPELINE_STAGES:
        raise HTTPException(422, "Unknown stage")
    db = get_db()
    now = utcnow()
    callback_at = body.callback_at.astimezone(timezone.utc) if body.callback_at and body.callback_at.tzinfo else body.callback_at
    wrapup = {"outcome": body.outcome, "notes": body.notes.strip(), "next_step": body.next_step.strip(),
              "callback_at": callback_at, "by": admin.user_id, "at": now}
    attempt = await db[m.ATTEMPTS].find_one_and_update(
        {"_id": attempt_id}, {"$set": {"wrapup": wrapup, **({"next_step": wrapup["next_step"]} if wrapup["next_step"] else {})}},
        return_document=ReturnDocument.AFTER)
    if attempt is None:
        raise HTTPException(404, "Call not found")
    if attempt.get("status") == "done":                      # the agent already closed it: correct the outcome
        if body.outcome == "dnc":
            await registry.add_to_dnc(db, attempt["to"], reason="asked_on_call", call_id=attempt.get("call_id"),
                                      source="operator", now=now)
        await outcomes.relabel_attempt(db, attempt_id, body.outcome, now=now, by=admin.user_id, callback_at=callback_at)
    elif body.outcome == "dnc":                              # an opt-out takes effect now, not when the call ends
        await registry.add_to_dnc(db, attempt["to"], reason="asked_on_call", call_id=attempt.get("call_id"),
                                  source="operator", now=now)
    lead_id = attempt["lead_id"]
    if wrapup["notes"]:
        await followup.add_activity(db, lead_id, "note", wrapup["notes"], by=admin.user_id, now=now)
    if body.stage:
        await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"stage": body.stage, "updated_at": now}})
        await followup.add_activity(db, lead_id, "stage", f"Stage: {body.stage}", by=admin.user_id, now=now)
    if body.task:
        await followup.create_task(db, lead_id, body.task.title, due_at=body.task.due_at, source="wrapup",
                                   attempt_id=attempt_id, by=admin.user_id, now=now)
    await _audit(admin, request, "sales.attempt.wrap_up", "call_attempt", attempt_id, {"outcome": body.outcome})
    return {"attempt": _out(await db[m.ATTEMPTS].find_one({"_id": attempt_id}, {"gate": 0}))}


class AttemptPatch(BaseModel):
    outcome: Optional[str] = None
    amd_label: Optional[Literal["human", "machine"]] = None
    confirm_dnc: bool = False            # "dnc" is permanent: the person must say they mean it


@sales_router.patch("/attempts/{attempt_id}")
async def patch_attempt(attempt_id: str, body: AttemptPatch, request: Request,
                        admin: Principal = Depends(manager)):
    db = get_db()
    if not await db[m.ATTEMPTS].find_one({"_id": attempt_id}, {"_id": 1}):
        raise HTTPException(404, "Call not found")
    if body.outcome is not None:
        if body.outcome == "dnc":
            if not body.confirm_dnc:
                raise HTTPException(422, "Adding a number to the do-not-call list is permanent; confirm it (confirm_dnc).")
        elif body.outcome not in m.MANUAL_OUTCOMES:
            raise HTTPException(422, f"{body.outcome} cannot be set by hand; use one of {', '.join(m.MANUAL_OUTCOMES)}.")
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


@sales_router.post("/attempts/{attempt_id}/review")
async def review_attempt(attempt_id: str, request: Request, admin: Principal = Depends(manager)):
    """Ask the AI coach to review (or review again) one finished call (SALES_LEARNING_PLAN.md Phase 2).
    Charged to the learning budget."""
    from core.outbound import coach, offer_ai

    db = get_db()
    attempt = await db[m.ATTEMPTS].find_one({"_id": attempt_id})
    if not attempt or attempt.get("status") != "done":
        raise HTTPException(404, "Finished call not found")
    if not settings.OPENAI_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY is not set, so calls can't be reviewed.")
    if await offer_ai.learning_budget_left(db, utcnow()) <= 0:
        raise HTTPException(429, "Today's learning budget (SALES_LEARNING_DAILY_USD) is used up.")
    try:
        review = await coach.review_attempt(db, attempt, now=utcnow())
    except offer_ai.AiError as e:
        raise HTTPException(e.status, str(e))
    tries = int((attempt.get("review") or {}).get("tries") or 0) + 1
    await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"review": {**review, "tries": tries}}})
    await _audit(admin, request, "sales.attempt.review", "call_attempt", attempt_id,
                 {"status": review["status"], "overall": review.get("overall")})
    return {"attempt": _out(await db[m.ATTEMPTS].find_one({"_id": attempt_id}))}


FUNNEL_MAX_CALLS = 50_000


@sales_router.get("/funnel")
async def get_funnel(campaign_id: Optional[str] = None, days: int = Query(30, ge=1, le=365),
                     admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {"status": "done", "created_at": {"$gte": utcnow() - timedelta(days=days)},
                             **_by_campaign(campaign_id)}
    rows = await get_db()[m.ATTEMPTS].find(
        query, {"outcome": 1, "amd": 1, "amd_label": 1, "duration_sec": 1, "cost_usd": 1, "persona_id": 1,
                "offer_id": 1, "offer_version": 1, "variant_id": 1, "status": 1,
                **funnel.NEXT_STEP_FIELDS}).sort("created_at", -1).limit(FUNNEL_MAX_CALLS).to_list(FUNNEL_MAX_CALLS)
    result = funnel.funnel(rows)
    result["by_variant"] = await _variant_labels(funnel.by_variant(rows))
    # Say so when the window has more calls than are counted, rather than show partial numbers as whole.
    truncated = len(rows) >= FUNNEL_MAX_CALLS
    return {"funnel": result, "amd": funnel.amd_accuracy(rows), "days": days,
            "counted_calls": len(rows), "truncated": truncated,
            "outcomes": list(m.OUTCOMES), "manual_outcomes": list(m.MANUAL_OUTCOMES),
            "outcome_labels": m.OUTCOME_LABELS}


# ── Offers: what the dialer sells ────────────────────────────────────────────

async def _offer(offer_id: str) -> Dict[str, Any]:
    doc = await get_db()[of.OFFERS].find_one({"_id": offer_id})
    if not doc:
        raise HTTPException(404, "Offer not found")
    return doc


async def _offer_summary(doc: Mapping[str, Any]) -> Dict[str, Any]:
    db = get_db()
    published = None
    if doc.get("published_version"):
        snap = await db[of.OFFER_VERSIONS].find_one({"_id": f"{doc['_id']}@{doc['published_version']}"})
        if snap:
            published = {"version": snap["version"], "published_at": snap["published_at"],
                         "variants": [{"id": v["id"], "label": v["label"]} for v in snap["settings"]["variants"]]}
    campaigns = await db[m.CAMPAIGNS].count_documents({"offer_id": doc["_id"]})
    running = await db[m.CAMPAIGNS].count_documents({"offer_id": doc["_id"], "status": "running"})
    return {"id": doc["_id"], "name": doc.get("name"), "brand": doc.get("brand"), "company_name": doc.get("company_name"),
            "status": doc.get("status"), "published_version": doc.get("published_version"), "published": published,
            "campaigns": campaigns, "running_campaigns": running, "updated_at": doc.get("updated_at"),
            "learning": await _learning_chip(doc["_id"])}


async def _learning_chip(offer_id: str) -> Optional[Dict[str, Any]]:
    """For the Offers list: the live playbook and the test running (SALES_LEARNING_PLAN.md)."""
    from core.outbound import learning
    from core.outbound import playbook as pb
    from core.outbound.experiments import next_look

    db = get_db()
    state = await db[pb.LEARNING].find_one({"_id": offer_id})
    if not state:
        return None
    live = await db[pb.PLAYBOOKS].find_one({"_id": state.get("live_playbook_id")}, {"n": 1}) if state.get("live_playbook_id") else None
    exp = await db[learning.EXPERIMENTS].find_one({"_id": state.get("experiment_id")}) if state.get("experiment_id") else None
    testing = None
    if exp:
        challenger = await db[pb.PLAYBOOKS].find_one({"_id": exp.get("challenger_id")}, {"n": 1}) or {}
        ev = exp.get("last_eval") or {}
        testing = {"n": challenger.get("n"), "done": ((ev.get("challenger") or {}).get("decided") or 0),
                   "next": next_look(exp.get("looks_done") or []), "status": exp.get("status")}
    return {"mode": state.get("mode"), "live_n": (live or {}).get("n"), "testing": testing}


async def _variant_labels(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Name each A/B row "offer · vN · script" for the Results page."""
    db = get_db()
    names: Dict[str, str] = {}
    for row in rows:
        oid = row.get("offer_id") or of.LEGACY_OFFER_ID
        if oid not in names:
            doc = await db[of.OFFERS].find_one({"_id": oid}, {"name": 1})
            names[oid] = (doc or {}).get("name") or ("LaudLine (built-in)" if oid == of.LEGACY_OFFER_ID else oid)
        variant_label = row.get("variant_id") or "?"
        if row.get("offer_version"):
            snap = await db[of.OFFER_VERSIONS].find_one({"_id": f"{oid}@{row['offer_version']}"}, {"settings.variants": 1})
            for v in ((snap or {}).get("settings") or {}).get("variants") or []:
                if v.get("id") == row.get("variant_id"):
                    variant_label = v.get("label") or variant_label
        elif row.get("variant_id") in persona.PERSONAS:
            variant_label = persona.PERSONAS[row["variant_id"]].label
        row["label"] = f"{names[oid]} · v{row.get('offer_version') or 0} · {variant_label}"
    return rows


@sales_router.get("/offers")
async def list_offers(include_archived: bool = False, admin: Principal = Depends(viewer)):
    query: Dict[str, Any] = {} if include_archived else {"status": {"$ne": "archived"}}
    rows = await get_db()[of.OFFERS].find(query).sort("updated_at", -1).limit(200).to_list(200)
    return to_jsonable({"offers": [await _offer_summary(r) for r in rows]})


class OfferDraftBody(of.OfferSettings):
    """A draft save: the offer, plus AI-written facts still to be confirmed and, for autosave and two
    editors, the `updated_at` this editor loaded (a stale one is refused with 409)."""

    ai_unconfirmed: Optional[List[str]] = Field(default=None, max_length=20)
    ai_confirmed: List[str] = Field(default_factory=list, max_length=20)    # facts a person just confirmed
    expected_updated_at: Optional[str] = Field(default=None, max_length=40)
    autosave: bool = False

    def settings(self) -> of.OfferSettings:
        return of.OfferSettings.model_validate(self.model_dump(exclude={"ai_unconfirmed", "ai_confirmed",
                                                                         "expected_updated_at", "autosave"}))


def _offer_issues(doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    s = of.settings_of(doc)
    return [i.model_dump() for i in [*of.validate_issues(s), *of.unconfirmed_issues(doc.get("ai_unconfirmed") or [], s)]]


def _stale(e: of.StaleDraft) -> HTTPException:
    return HTTPException(409, {"code": "stale_draft", "updated_at": to_jsonable(e.updated_at), "by": e.by,
                               "message": "Someone else saved this offer after you opened it."})


# ── The AI offer copilot (SALES_SUITE_PLAN.md Phases 3, 4, 6) ────────────────

def _ai_http(e: offer_ai.AiError) -> HTTPException:
    if e.problems:
        return HTTPException(e.status, {"message": str(e), "problems": e.problems})
    return HTTPException(e.status, str(e))


def _ai_stream_error(exc: BaseException) -> Dict[str, Any]:
    from core.services.site_import import SiteImportError

    if isinstance(exc, offer_ai.AiError):
        return {"status": exc.status, "message": str(exc)}
    if isinstance(exc, SiteImportError):
        return {"status": exc.status, "message": str(exc)}
    logger.warning("Offer draft failed", exc_info=exc)
    return {"status": 500, "message": "Couldn't draft the offer right now. Try again in a minute."}


@sales_router.post("/offers/generate")
async def generate_offer(body: offer_ai.GenerateRequest, request: Request, admin: Principal = Depends(manager)):
    """Draft a whole offer from a website and a brief. Nothing is saved: the editor opens it unsaved."""
    try:
        await offer_ai.guard(get_db(), admin.user_id, "generate")
        result = await offer_ai.generate_offer(get_db(), body)
    except offer_ai.AiError as e:
        raise _ai_http(e)
    await _audit(admin, request, "sales.offer.ai.generate", "offer", "new",
                 {"website": body.website, "brief": body.brief[:200], "usd": result["cost_usd"]})
    return to_jsonable(result)


@sales_router.post("/offers/generate/stream")
async def generate_offer_stream(body: offer_ai.GenerateRequest, request: Request, admin: Principal = Depends(manager)):
    """The same draft, streamed as server-sent events (utils/sse.py): reading the site, then each part."""
    from utils.sse import progress_response

    try:
        await offer_ai.guard(get_db(), admin.user_id, "generate")
    except offer_ai.AiError as e:
        raise _ai_http(e)

    async def job(emit: Any) -> Dict[str, Any]:
        result = await offer_ai.generate_offer(get_db(), body, on_progress=emit)
        await _audit(admin, request, "sales.offer.ai.generate", "offer", "new",
                     {"website": body.website, "brief": body.brief[:200], "usd": result["cost_usd"]})
        return result

    return progress_response(job, result=lambda r: {"draft_result": to_jsonable(r)}, on_error=_ai_stream_error)


@sales_router.post("/offers/assist")
async def assist_offer(body: offer_ai.AssistRequest, request: Request, admin: Principal = Depends(manager)):
    """A suggestion for one section of the offer. The editor shows it before/after; nothing is saved."""
    try:
        await offer_ai.guard(get_db(), admin.user_id, "assist")
        result = await offer_ai.assist(get_db(), body)
    except offer_ai.AiError as e:
        raise _ai_http(e)
    await _audit(admin, request, "sales.offer.ai.assist", "offer", str(body.offer.get("id") or "new"),
                 {"section": body.section, "action": body.action, "usd": result["cost_usd"]})
    return to_jsonable(result)


class ReviewBody(BaseModel):
    offer: Optional[Dict[str, Any]] = None        # unsaved edits; default: the saved draft
    market: Literal["AU", "GB", "US", "CA"] = "AU"


@sales_router.post("/offers/{offer_id}/review")
async def review_offer(offer_id: str, body: ReviewBody, request: Request, admin: Principal = Depends(manager)):
    """An AI review of the draft: advisory only (publishing is decided by the fixed checks)."""
    saved = None if offer_id == "new" else await _offer(offer_id)
    draft = body.offer if body.offer is not None else (saved or {})
    try:
        await offer_ai.guard(get_db(), admin.user_id, "review")
        result = await offer_ai.review(get_db(), draft, market=body.market)
    except offer_ai.AiError as e:
        raise _ai_http(e)
    if saved is not None:
        await get_db()[of.OFFERS].update_one({"_id": offer_id}, {"$set": {"last_review": {
            "at": utcnow(), "by": admin.user_id, "checksum": result["checksum"], "result": result}}})
    await _audit(admin, request, "sales.offer.ai.review", "offer", offer_id,
                 {"score": result["score"], "findings": len(result["findings"]), "usd": result["cost_usd"]})
    return to_jsonable(result)


# ── Automated test calls (SALES_SUITE_PLAN.md Phase 8) ───────────────────────

class SimulateBody(BaseModel):
    offer: Optional[of.OfferSettings] = None      # a draft to test; default: the version, else the saved draft
    version: Optional[int] = Field(default=None, ge=1)
    variant_ids: List[str] = Field(default_factory=list, max_length=4)


@sales_router.post("/offers/{offer_id}/simulate")
async def simulate_offer(offer_id: str, body: SimulateBody, request: Request, admin: Principal = Depends(manager)):
    """Queue simulated test calls: the real agent, a simulated prospect, a dry run. Results arrive in a few minutes."""
    from core.outbound import simulate

    if body.version:
        snap = await of.get_version(get_db(), offer_id, body.version)
        if not snap:
            raise HTTPException(404, "Version not found")
        settings_doc = snap["settings"]
    elif body.offer is not None:
        settings_doc = body.offer.model_dump()
    else:
        settings_doc = of.settings_of(await _offer(offer_id)).model_dump()
    problems, _ = of.validate_offer(of.OfferSettings.model_validate(settings_doc))
    if problems:
        raise HTTPException(422, "Fix these first: " + "; ".join(problems))
    if offer_ai.unavailable_reason():
        raise HTTPException(503, offer_ai.unavailable_reason())
    if await offer_ai.spent_today(get_db(), utcnow()) >= settings.SALES_AI_ASSIST_DAILY_USD:
        raise HTTPException(429, "Today's AI budget is used up.")
    if await get_db()[simulate.SIMULATIONS].count_documents({"offer_id": offer_id, "status": {"$in": ["queued", "running"]},
                                                              "source": {"$ne": "learning"}}):
        raise HTTPException(409, "Test calls for this offer are already running.")
    doc = await simulate.queue(get_db(), offer_id=offer_id, settings_doc=settings_doc, variant_ids=body.variant_ids,
                               version=body.version, by=admin.user_id, now=utcnow())
    await _audit(admin, request, "sales.offer.simulate", "offer", offer_id,
                 {"simulation": doc["_id"], "version": body.version, "variants": doc["variant_ids"]})
    return to_jsonable({"simulation": m.with_id({k: v for k, v in doc.items() if k != "settings"}),
                        "scenarios": [s.label for s in simulate.scenarios(settings_doc)]})


@sales_router.get("/offers/{offer_id}/simulations")
async def offer_simulations(offer_id: str, admin: Principal = Depends(viewer)):
    from core.outbound import simulate

    rows = await get_db()[simulate.SIMULATIONS].find({"offer_id": offer_id}, {"settings": 0, "results.transcript": 0}) \
        .sort("created_at", -1).limit(20).to_list(20)
    return to_jsonable({"simulations": [m.with_id(r) for r in rows]})


@sales_router.get("/simulations/{simulation_id}")
async def get_simulation(simulation_id: str, admin: Principal = Depends(viewer)):
    from core.outbound import simulate

    doc = await get_db()[simulate.SIMULATIONS].find_one({"_id": simulation_id}, {"settings": 0})
    if not doc:
        raise HTTPException(404, "Test run not found")
    return to_jsonable({"simulation": m.with_id(doc)})


@sales_router.post("/offers")
async def create_offer(body: OfferDraftBody, request: Request, admin: Principal = Depends(manager)):
    s = body.settings()
    doc = await of.create_offer(get_db(), s, by=admin.user_id, offer_id=new_id("off"), now=utcnow(),
                                ai_unconfirmed=body.ai_unconfirmed or [])
    await _audit(admin, request, "sales.offer.create", "offer", doc["_id"],
                 {"name": s.name, "ai_unconfirmed": doc.get("ai_unconfirmed") or []})
    return to_jsonable({"offer": m.with_id(doc), "issues": _offer_issues(doc)})


@sales_router.get("/offers/{offer_id}")
async def get_offer(offer_id: str, admin: Principal = Depends(viewer)):
    doc = await _offer(offer_id)
    problems, warnings = of.validate_offer(of.settings_of(doc))
    campaigns = await get_db()[m.CAMPAIGNS].find({"offer_id": offer_id}, {"name": 1, "status": 1, "offer_version": 1}) \
        .sort("created_at", -1).to_list(100)
    return to_jsonable({"offer": m.with_id(doc), "summary": await _offer_summary(doc),
                        "versions": [m.with_id(v) for v in await of.versions(get_db(), offer_id)],
                        "campaigns": [m.with_id(c) for c in campaigns], "problems": problems, "warnings": warnings,
                        "issues": _offer_issues(doc), "ai_unconfirmed": doc.get("ai_unconfirmed") or [],
                        "last_review": doc.get("last_review"),
                        "last_review_current": bool(doc.get("last_review")) and (doc.get("last_review") or {}).get(
                            "checksum") == of.checksum(of.settings_of(doc).model_dump())})


@sales_router.patch("/offers/{offer_id}")
async def update_offer(offer_id: str, body: OfferDraftBody, request: Request,
                       admin: Principal = Depends(manager)):
    """Edits the draft. Campaigns keep the version they were signed off on until moved to a new one."""
    doc = await _offer(offer_id)
    if doc.get("status") == "archived":
        raise HTTPException(409, "Unarchive the offer before editing it.")
    s = body.settings()
    try:
        updated = await of.update_offer(get_db(), offer_id, s, now=utcnow(), by=admin.user_id,
                                        ai_unconfirmed=body.ai_unconfirmed, ai_confirmed=body.ai_confirmed,
                                        expected_updated_at=body.expected_updated_at)
    except of.StaleDraft as e:
        raise _stale(e)
    if updated is None:
        raise HTTPException(404, "Offer not found")
    changed = of.changed_keys(doc, s)
    confirmed = sorted(set(doc.get("ai_unconfirmed") or []) & set(body.ai_confirmed))
    if changed or body.ai_unconfirmed is not None or confirmed:
        await _audit(admin, request, "sales.offer.autosave" if body.autosave else "sales.offer.update", "offer",
                     offer_id, {"name": s.name, "changed": changed, "ai_confirmed": confirmed,
                                "ai_unconfirmed": updated.get("ai_unconfirmed") or []})
    return to_jsonable({"offer": m.with_id(updated), "issues": _offer_issues(updated)})


@sales_router.get("/offers/{offer_id}/versions/{version}")
async def get_offer_version(offer_id: str, version: int, admin: Principal = Depends(viewer)):
    """One published version in full, for the History tab's compare."""
    snap = await of.get_version(get_db(), offer_id, version)
    if not snap:
        raise HTTPException(404, "Version not found")
    campaigns = await get_db()[m.CAMPAIGNS].find({"offer_id": offer_id, "offer_version": version},
                                                 {"name": 1, "status": 1}).to_list(100)
    return to_jsonable({"version": m.with_id(snap), "campaigns": [m.with_id(c) for c in campaigns]})


class RestoreBody(BaseModel):
    version: int = Field(ge=1)
    expected_updated_at: Optional[str] = Field(default=None, max_length=40)


@sales_router.post("/offers/{offer_id}/restore")
async def restore_offer_version(offer_id: str, body: RestoreBody, request: Request,
                                admin: Principal = Depends(manager)):
    """Copy a published version into the draft. Published versions themselves never change."""
    try:
        doc = await of.restore_version(get_db(), offer_id, body.version, by=admin.user_id, now=utcnow(),
                                       expected_updated_at=body.expected_updated_at)
    except of.StaleDraft as e:
        raise _stale(e)
    except of.OfferError as e:
        raise HTTPException(404 if "not found" in str(e) else 409, str(e))
    await _audit(admin, request, "sales.offer.restore", "offer", offer_id, {"version": body.version})
    return to_jsonable({"offer": m.with_id(doc), "issues": _offer_issues(doc)})


@sales_router.post("/offers/{offer_id}/publish")
async def publish_offer(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    try:
        snap = await of.publish(get_db(), offer_id, by=admin.user_id, by_name=admin.name or admin.email, now=utcnow())
    except of.OfferError as e:
        if str(e) == "Offer not found":
            raise HTTPException(404, str(e))
        raise HTTPException(422, f"{e}: " + "; ".join(e.problems) if e.problems else str(e))
    await _audit(admin, request, "sales.offer.publish", "offer", offer_id, {"version": snap["version"]})
    return to_jsonable({"version": snap["version"], "summary": await _offer_summary(await _offer(offer_id))})


async def _set_offer_status(offer_id: str, status: str) -> Dict[str, Any]:
    doc = await _offer(offer_id)
    if status == "archived" and await get_db()[m.CAMPAIGNS].count_documents({"offer_id": offer_id, "status": "running"}):
        raise HTTPException(409, "Pause the campaigns using this offer before archiving it.")
    new_status = status if status == "archived" else ("active" if doc.get("published_version") else "draft")
    await get_db()[of.OFFERS].update_one({"_id": offer_id}, {"$set": {"status": new_status, "updated_at": utcnow()}})
    return await _offer_summary(await _offer(offer_id))


@sales_router.post("/offers/{offer_id}/archive")
async def archive_offer(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    summary = await _set_offer_status(offer_id, "archived")
    await _audit(admin, request, "sales.offer.archive", "offer", offer_id)
    return to_jsonable({"summary": summary})


@sales_router.post("/offers/{offer_id}/unarchive")
async def unarchive_offer(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    summary = await _set_offer_status(offer_id, "active")
    await _audit(admin, request, "sales.offer.unarchive", "offer", offer_id)
    return to_jsonable({"summary": summary})


@sales_router.post("/offers/{offer_id}/duplicate")
async def duplicate_offer(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    source = of.settings_of(await _offer(offer_id))
    copy = source.model_copy(update={"name": f"{source.name} (copy)"[:80]})
    doc = await of.create_offer(get_db(), copy, by=admin.user_id, offer_id=new_id("off"), now=utcnow())
    await _audit(admin, request, "sales.offer.duplicate", "offer", doc["_id"], {"from": offer_id})
    return to_jsonable({"offer": m.with_id(doc)})


class PreviewBody(BaseModel):
    # unsaved edits, checked here rather than by FastAPI so a half-filled draft gets a checklist,
    # not a 422; default: the saved draft
    offer: Optional[Dict[str, Any]] = None
    ai_unconfirmed: List[str] = Field(default_factory=list, max_length=20)
    variant_id: Optional[str] = None
    lead_id: Optional[str] = None                 # preview for a real lead; default: a sample one
    mode: m.CampaignMode = "ai_cold_b2b_us"
    playbook_id: Optional[str] = Field(default=None, max_length=80)   # include this playbook's lessons


@sales_router.post("/offers/{offer_id}/preview")
async def preview_offer(offer_id: str, body: PreviewBody, admin: Principal = Depends(viewer)):
    """Exactly what the agent would hear and say, for the editor's live preview."""
    unconfirmed = list(body.ai_unconfirmed)
    if body.offer is None:
        saved = await _offer(offer_id)
        draft = of.settings_of(saved)
        unconfirmed = unconfirmed or list(saved.get("ai_unconfirmed") or [])
    else:
        try:
            draft = of.OfferSettings.model_validate({k: v for k, v in body.offer.items()
                                                     if k not in ("ai_unconfirmed", "expected_updated_at")})
        except ValidationError as e:
            issues = of.draft_issues(e)
            return {"opener": "", "consented_opener": "", "callback_opener": "", "voicemail": "", "consent_text": "",
                    "prompt": "",
                    "approx_tokens": 0, "problems": [i.message for i in issues], "warnings": [],
                    "issues": [i.model_dump() for i in issues]}
    all_issues = [*of.validate_issues(draft), *of.unconfirmed_issues(unconfirmed, draft)]
    problems = [i.message for i in all_issues if i.level == "problem"]
    warnings = [i.message for i in all_issues if i.level == "warning"]
    offer = {**draft.model_dump(), "id": offer_id, "version": 0}
    lead: Dict[str, Any] = {**of.SAMPLE_LEAD, "timezone": "Australia/Sydney", "timezones": ["Australia/Sydney"],
                            "country": "AU", "city": "Sydney"}
    if body.lead_id:
        lead = {k: v for k, v in (await _lead(body.lead_id)).items() if k != "gate_log"}
    variant = of.variant(offer, body.variant_id)
    campaign = {"mode": body.mode, "notes": ""}
    remembered: List[str] = []
    if body.lead_id:
        from core.outbound import memory

        remembered = await memory.lead_brief(get_db(), lead, offer, now=utcnow())
    lessons = await _playbook_lines(offer_id, body.playbook_id, offer)
    prompt = persona.build_prompt(offer, variant, lead, campaign, research=(lead.get("research") or None),
                                  now=utcnow(), can_transfer=False, memory=remembered, playbook=lessons)
    return {"opener": persona.opener(offer, variant, lead, "ai_cold_b2b_us"),
            "consented_opener": persona.opener(offer, variant, lead, "ai_consented") if offer.get("consented_opener") else "",
            "callback_opener": persona.opener(offer, variant, lead, body.mode, callback=True)
            if offer.get("callback_opener") else "",
            "voicemail": persona.voicemail_message(offer) if offer.get("voicemail") else "",
            "consent_text": persona.consent_text(offer), "prompt": prompt, "approx_tokens": len(prompt) // 4,
            "problems": problems, "warnings": warnings, "issues": [i.model_dump() for i in all_issues]}



async def _playbook_lines(offer_id: str, playbook_id: Optional[str], offer: Mapping[str, Any]) -> Optional[List[str]]:
    """A playbook's prompt lines for this offer text (only that offer's own playbooks)."""
    if not playbook_id:
        return None
    from core.outbound import playbook as pbm

    doc = await pbm.get(get_db(), playbook_id)
    if not doc or doc.get("offer_id") != offer_id:
        raise HTTPException(404, "Playbook not found for this offer")
    kept, _ = pbm.validate(pbm.entries_of(doc), offer)
    return pbm.render(kept)


class TestCallBody(BaseModel):
    offer: Optional[of.OfferSettings] = None      # unsaved edits; default: the saved draft
    variant_id: Optional[str] = Field(default=None, max_length=32)
    business_name: str = Field(default="Harbour Dental Studio", min_length=1, max_length=120)
    contact_name: str = Field(default="", max_length=80)
    timezone: str = Field(default="Australia/Sydney", max_length=64)
    mode: Literal["ai_cold_b2b_us", "ai_consented"] = "ai_cold_b2b_us"
    playbook_id: Optional[str] = Field(default=None, max_length=80)   # hear a playbook's lessons in use


@sales_router.post("/offers/{offer_id}/test-call")
async def test_call_offer(offer_id: str, body: TestCallBody, request: Request,
                          admin: Principal = Depends(manager)):
    """Talk to the sales agent in the browser, as a prospect would hear it. A dry run: no phone call,
    no compliance gate, nothing booked, emailed or written to a lead (agent/sales.py test_call)."""
    if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        raise HTTPException(503, "LiveKit is not configured.")
    draft = body.offer or of.settings_of(await _offer(offer_id))
    problems, _ = of.validate_offer(draft)
    if problems:
        raise HTTPException(422, "Fix these first: " + "; ".join(problems))
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(422, "Unknown time zone")
    test_id = new_id("stc")
    room = f"salestest-{test_id}"
    now = utcnow()
    lessons = await _playbook_lines(offer_id, body.playbook_id, {**draft.model_dump(), "id": offer_id})
    await get_db()[TEST_CALLS].insert_one({
        "_id": test_id, "offer_id": offer_id, "settings": draft.model_dump(), "variant_id": body.variant_id,
        "mode": body.mode, "room": room, "created_by": admin.user_id, "created_at": now,
        "playbook_lines": lessons or [],
        "lead": {"business_name": body.business_name, "contact_name": body.contact_name,
                 "timezone": body.timezone, "timezones": [body.timezone]}})
    await _audit(admin, request, "sales.offer.test_call", "offer", offer_id, {"room": room})

    from livekit import api

    token = (api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
             .with_identity(TEST_CALL_IDENTITY).with_name(admin.name or "Tester")
             .with_ttl(timedelta(seconds=TEST_CALL_MAX_S + 120))
             .with_grants(api.VideoGrants(room_join=True, room=room, can_subscribe=True, can_publish=True,
                                          can_publish_data=False))
             # joining asks LiveKit to send the sales agent in, with only the test id to go on
             .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(
                 agent_name=settings.SALES_AGENT_NAME, metadata=json.dumps({"test_call_id": test_id}))]))
             .to_jwt())
    return {"token": token, "url": settings.LIVEKIT_URL, "room": room, "mode": "takeover",
            "max_seconds": TEST_CALL_MAX_S}


# ── Public: unsubscribe from sales email ─────────────────────────────────────

_UNSUB_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
               "<title>Unsubscribed</title><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
               "<h1>{title}</h1><p>{text}</p></body>")


@public_sales_router.get("/sales/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(e: str = Query(max_length=200), sig: str = Query(max_length=64)):
    """One click from the link in every sales email: no more sales email to this address, ever."""
    if not followup.verify_unsubscribe(e, sig):
        return HTMLResponse(_UNSUB_PAGE.format(title="That link didn't work",
                                               text="Reply to the email with “unsubscribe” and we'll take you off."),
                            status_code=400)
    await followup.suppress(get_db(), e, source="link", now=utcnow())
    return HTMLResponse(_UNSUB_PAGE.format(title="You're unsubscribed",
                                           text="You won't get any more sales emails from us at this address."))


# ── Public: "get a call from our AI" ─────────────────────────────────────────

async def _consent_offer(campaign: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return await of.offer_for_campaign(get_db(), campaign) if campaign else persona.legacy_offer()


def consent_version(offer: Mapping[str, Any]) -> str:
    """Changes whenever the wording could have: the built-in wording keeps the original version."""
    if offer.get("id") == of.LEGACY_OFFER_ID and not offer.get("version"):
        return CONSENT_VERSION
    return f"{CONSENT_VERSION}:{offer['id']}@{offer['version']}"


async def _consent_campaign() -> Optional[Dict[str, Any]]:
    if not (settings.SALES_CONSENT_CAMPAIGN_ID and settings.OUTBOUND_DIALER_ENABLED):
        return None
    campaign = await get_db()[m.CAMPAIGNS].find_one({"_id": settings.SALES_CONSENT_CAMPAIGN_ID})
    if not campaign or campaign.get("mode") != "ai_consented" or campaign.get("status") != "running":
        return None
    return campaign


@public_sales_router.get("/demo-call/consent")
async def demo_call_consent():
    campaign = await _consent_campaign()
    offer = await _consent_offer(campaign)
    return {"version": consent_version(offer), "text": persona.consent_text(offer), "company": offer["company_name"],
            "available": campaign is not None}


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


# Lead statuses a form request may put back in the queue. Anything else is already being worked.
DEMO_CALL_REQUEUE_FROM = frozenset({"new", "queued", "exhausted", "not_interested", "callback"})


@public_sales_router.post("/demo-call")
async def request_demo_call(body: DemoCallBody, request: Request):
    """Written consent → consent record → the lead joins the consented campaign and is called."""
    if body.website:
        return {"ok": True}                                # a bot: accept and do nothing
    if not body.consent:
        raise HTTPException(422, "Please tick the consent box to get a call.")
    campaign = await _consent_campaign()
    if campaign is None:
        raise HTTPException(503, "Calls on request aren't available right now.")
    offer = await _consent_offer(campaign)
    if body.consent_version != consent_version(offer):        # they saw other wording: show them this one
        raise HTTPException(422, "Please tick the consent box to get a call.")
    ip = ratelimit.client_ip(request)
    if not (await ratelimit.allow("democall:all", settings.PUBLIC_DEMO_CALL_MAX_PER_DAY, 86400)
            and await ratelimit.allow(f"democall:ip:{ip}", settings.PUBLIC_DEMO_CALL_PER_IP_HOUR, 3600)):
        raise HTTPException(429, "Too many requests. Please try again later.")
    info = phones.parse(body.phone, body.country.upper())
    if info is None:
        raise HTTPException(422, "That doesn't look like a valid phone number.")
    db = get_db()
    if await registry.on_internal_dnc(db, info.e164):
        raise HTTPException(409, "This number asked us not to call it. Email us if you'd like that changed.")

    thanks = {"ok": True, "message": "Thanks! Our AI assistant will call you shortly, during business hours."}
    # One request per number a day: a form can't be used to have the AI ring someone over and over.
    if not await ratelimit.allow(f"democall:phone:{info.e164}", 1, 86400):
        return thanks
    existing = await db[m.LEADS].find_one({"owner": m.OWNER_PLATFORM, "phone": info.e164},
                                          {"status": 1, "contact_name": 1, "business_name": 1, "email": 1})

    now = utcnow()
    source = f"form:{body.page.strip() or 'demo-call'}"
    consent = await registry.record_consent(
        db, phone=info.e164, type="written", language_shown=persona.consent_text(offer), source=source, ip=ip,
        user_agent=request.headers.get("user-agent", ""), now=now,
        scope={"company": offer["company_name"], "offer_id": offer["id"]})
    zones = phones.timezones_for(info)
    email = body.email.strip().lower()
    sets: Dict[str, Any] = {"updated_at": now,
                            "consent": {"type": "written", "at": now, "source": source, "record_id": consent["_id"]}}
    # A lead already being worked (on a call, interested, meeting booked, bad number…) keeps its place:
    # the form only records the consent. Names and email are filled in, never written over.
    if existing is None or existing.get("status") in DEMO_CALL_REQUEUE_FROM:
        sets.update(campaign_id=campaign["_id"], status="queued", next_eligible_at=now, attempts=0)
    for key, value in (("contact_name", body.name.strip()), ("business_name", body.business_name.strip()),
                       ("email", email if "@" in email else "")):
        if existing is None or not existing.get(key):
            sets[key] = value
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
    return thanks
