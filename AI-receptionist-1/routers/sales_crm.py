"""
The built-in CRM API (SALES_SUITE_PLAN.md wave 3) — prefix /platform/sales, the sales team only.

GET    /platform/sales/deals   POST …   PATCH …/{id}           deals (won or lost, never deleted)
GET    /platform/sales/board                                   the deal board: stages, totals, weighted value
POST   /platform/sales/leads/{id}/contacts   PATCH …/{cid}     people at a lead (archived, never deleted)
GET    /platform/sales/crm/duplicates   POST /leads/{id}/merge records of the same business
GET    /platform/sales/me/work                                 my tasks, deals and leads
GET    /platform/sales/views   POST …   PATCH …/{id}           saved filters
GET    /platform/sales/fields  POST …   PATCH …/{id}           custom fields
PATCH  /platform/sales/leads/{id}/custom                       custom field values
GET    /platform/sales/search?q=                               leads and deals
POST   /platform/sales/leads/bulk                              assign, stage, tag, queue into a campaign
POST   /platform/sales/leads/{id}/email | draft-email | email-consent | sequence/stop
POST   /platform/sales/offers/{id}/insights                    learn from real calls
GET    /platform/sales/reports                                 pipeline, results, meetings, activity, funnel
GET    /platform/sales/audit   GET …/audit.csv   GET …/audit/verify      the sales audit trail
POST   /public/sales/inbound-email                             replies to sales email (inbound-parse webhook)
"""

from __future__ import annotations

import csv
import hmac
import io
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from config import settings
from core.auth import Principal
from core.outbound import crm, followup, offer_ai
from core.outbound import models as m
from core.outbound import offers as of
from core.tenancy.ids import new_id
from database.mongodb import get_db
from routers.sales import _audit, _lead, _offer, _out, caller, manager, utcnow, viewer
from utils.serialize import to_jsonable

sales_crm_router = APIRouter(prefix="/platform/sales", tags=["Sales CRM"])
public_crm_router = APIRouter(prefix="/public/sales", tags=["Sales CRM"])


# ── Deals and the board ──────────────────────────────────────────────────────

@sales_crm_router.get("/deals")
async def list_deals(stage: Optional[str] = None, owner_id: Optional[str] = None, lead_id: Optional[str] = None,
                     admin: Principal = Depends(viewer)):
    q: Dict[str, Any] = {}
    if stage:
        q["stage"] = stage
    if owner_id:
        q["owner_id"] = owner_id
    if lead_id:
        q["lead_id"] = lead_id
    rows = await get_db()[crm.DEALS].find(q).sort("updated_at", -1).limit(500).to_list(500)
    return to_jsonable({"deals": [m.with_id(d) for d in rows]})


@sales_crm_router.post("/deals")
async def create_deal(body: crm.DealInput, request: Request, admin: Principal = Depends(caller)):
    await _lead(body.lead_id)
    doc = await crm.create_deal(get_db(), body, by=admin.user_id, now=utcnow())
    await _audit(admin, request, "sales.deal.create", "deal", doc["_id"], {"lead_id": body.lead_id, "amount": body.amount})
    return to_jsonable({"deal": m.with_id(doc)})


@sales_crm_router.patch("/deals/{deal_id}")
async def update_deal(deal_id: str, body: crm.DealPatch, request: Request, admin: Principal = Depends(caller)):
    if body.custom:
        try:
            body.custom = await crm.custom_values(get_db(), "deal", body.custom)
        except ValueError as e:
            raise HTTPException(422, str(e))
    try:
        doc = await crm.update_deal(get_db(), deal_id, body, by=admin.user_id, now=utcnow())
    except ValueError as e:
        raise HTTPException(422, str(e))
    if doc is None:
        raise HTTPException(404, "Deal not found")
    await _audit(admin, request, "sales.deal.update", "deal", deal_id, body.model_dump(exclude_none=True))
    return to_jsonable({"deal": m.with_id(doc)})


@sales_crm_router.get("/board")
async def board(owner_id: Optional[str] = None, admin: Principal = Depends(viewer)):
    """Deals by stage, with each column's total and weighted value, joined to their leads."""
    db = get_db()
    q: Dict[str, Any] = {"owner_id": owner_id} if owner_id else {}
    deals = await db[crm.DEALS].find(q).sort("updated_at", -1).limit(2000).to_list(2000)
    leads = {lead["_id"]: lead for lead in await db[m.LEADS].find(
        {"_id": {"$in": list({d["lead_id"] for d in deals})}},
        {"business_name": 1, "contact_name": 1, "phone": 1, "city": 1, "country": 1, "owner_id": 1}).to_list(2000)}
    columns: Dict[str, List[Any]] = {s: [] for s in crm.DEAL_STAGES}
    totals = await crm.stage_totals(db, q)                  # every deal, not just the ones on the board
    for d in deals:
        s = d.get("stage", "qualified")
        columns.setdefault(s, [])
        if len(columns[s]) < 200:
            columns[s].append({**m.with_id(d), "lead": m.with_id(leads.get(d["lead_id"]) or {"_id": d["lead_id"]})})
    return to_jsonable({"columns": columns, "totals": totals, "stages": list(crm.DEAL_STAGES),
                        "probability": crm.PROBABILITY})


@sales_crm_router.get("/team/people")
async def team_people(admin: Principal = Depends(viewer)):
    """Names for owner pickers: everyone who can work leads (callers, managers, platform admins)."""
    rows = await get_db().users.find({"$or": [{"sales_role": {"$in": ["caller", "manager"]}}, {"is_platform_admin": True}],
                                      "disabled": {"$ne": True}}, {"name": 1, "email": 1, "sales_role": 1}).to_list(500)
    return {"people": [{"id": r["_id"], "name": r.get("name") or r.get("email"), "role": r.get("sales_role") or "admin"} for r in rows]}


# ── Contacts ─────────────────────────────────────────────────────────────────

@sales_crm_router.post("/leads/{lead_id}/contacts")
async def add_contact(lead_id: str, body: crm.ContactInput, request: Request, admin: Principal = Depends(caller)):
    await _lead(lead_id)
    doc = await crm.add_contact(get_db(), lead_id, body, by=admin.user_id, now=utcnow())
    await _audit(admin, request, "sales.contact.add", "sales_lead", lead_id, {"contact": doc["id"]})
    return to_jsonable({"contact": doc})


class ContactPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    role: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=200, pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$")
    phone: Optional[str] = Field(default=None, max_length=40)
    primary: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=500)
    archived: Optional[bool] = None


@sales_crm_router.patch("/leads/{lead_id}/contacts/{contact_id}")
async def update_contact(lead_id: str, contact_id: str, body: ContactPatch, request: Request, admin: Principal = Depends(caller)):
    if not await crm.update_contact(get_db(), lead_id, contact_id, body.model_dump(exclude_none=True), now=utcnow()):
        raise HTTPException(404, "Contact not found")
    await _audit(admin, request, "sales.contact.update", "sales_lead", lead_id, {"contact": contact_id, **body.model_dump(exclude_none=True)})
    return {"success": True}


# ── Duplicates and merge ─────────────────────────────────────────────────────

@sales_crm_router.get("/crm/duplicates")
async def duplicates(admin: Principal = Depends(viewer)):
    return to_jsonable({"groups": [{"reason": g["reason"], "leads": [m.with_id(x) for x in g["leads"]]}
                                   for g in await crm.duplicates(get_db())]})


class MergeBody(BaseModel):
    merge_id: str = Field(min_length=2, max_length=60)


@sales_crm_router.post("/leads/{lead_id}/merge")
async def merge_leads(lead_id: str, body: MergeBody, request: Request, admin: Principal = Depends(manager)):
    """Fold another record of the same business into this one. The stricter compliance state wins."""
    try:
        doc = await crm.merge(get_db(), lead_id, body.merge_id, by=admin.user_id, now=utcnow())
    except ValueError as e:
        raise HTTPException(409 if "already" in str(e) else 422, str(e))
    await _audit(admin, request, "sales.lead.merge", "sales_lead", lead_id, {"merged": body.merge_id, "status": doc.get("status")})
    return {"lead": _out(doc)}


# ── My work, views, fields, search, bulk ─────────────────────────────────────

@sales_crm_router.get("/me/work")
async def my_work(admin: Principal = Depends(viewer)):
    return to_jsonable(await crm.my_work(get_db(), admin.user_id, now=utcnow()))


@sales_crm_router.get("/views")
async def list_views(kind: Literal["leads", "deals"] = "leads", admin: Principal = Depends(viewer)):
    return to_jsonable({"views": [m.with_id(v) for v in await crm.views(get_db(), admin.user_id, kind)]})


@sales_crm_router.post("/views")
async def create_view(body: crm.ViewInput, admin: Principal = Depends(caller)):
    if body.shared and not admin.can_sell("manager"):
        raise HTTPException(403, "Only a sales manager can share a view with everyone")
    doc = {"_id": new_id("view"), **body.model_dump(), "owner": admin.user_id, "archived": False, "created_at": utcnow()}
    await get_db()[crm.VIEWS].insert_one(doc)
    return to_jsonable({"view": m.with_id(doc)})


class ViewPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    filters: Optional[Dict[str, Any]] = None
    archived: Optional[bool] = None


@sales_crm_router.patch("/views/{view_id}")
async def update_view(view_id: str, body: ViewPatch, admin: Principal = Depends(caller)):
    doc = await get_db()[crm.VIEWS].find_one({"_id": view_id})
    if not doc:
        raise HTTPException(404, "View not found")
    if doc["owner"] != admin.user_id and not admin.can_sell("manager"):
        raise HTTPException(403, "That view belongs to someone else")
    sets = body.model_dump(exclude_none=True)
    if "filters" in sets:
        sets["filters"] = crm.ViewInput(name=doc["name"], filters=sets["filters"]).filters
    await get_db()[crm.VIEWS].update_one({"_id": view_id}, {"$set": sets})
    return {"success": True}


@sales_crm_router.get("/fields")
async def list_fields(admin: Principal = Depends(viewer)):
    rows = await get_db()[crm.FIELDS].find({"archived": {"$ne": True}}).sort("label", 1).to_list(200)
    return to_jsonable({"fields": [m.with_id(f) for f in rows]})


@sales_crm_router.post("/fields")
async def create_field(body: crm.CustomFieldInput, request: Request, admin: Principal = Depends(manager)):
    if await get_db()[crm.FIELDS].find_one({"key": body.key, "applies_to": body.applies_to}):
        raise HTTPException(409, "A field with that key already exists")
    doc = {"_id": new_id("fld"), **body.model_dump(), "archived": False, "created_at": utcnow()}
    await get_db()[crm.FIELDS].insert_one(doc)
    await _audit(admin, request, "sales.field.create", "field", doc["_id"], body.model_dump())
    return to_jsonable({"field": m.with_id(doc)})


class FieldPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=2, max_length=60)
    options: Optional[List[str]] = Field(default=None, max_length=30)
    archived: Optional[bool] = None


@sales_crm_router.patch("/fields/{field_id}")
async def update_field(field_id: str, body: FieldPatch, request: Request, admin: Principal = Depends(manager)):
    res = await get_db()[crm.FIELDS].update_one({"_id": field_id}, {"$set": body.model_dump(exclude_none=True)})
    if not res.matched_count:
        raise HTTPException(404, "Field not found")
    await _audit(admin, request, "sales.field.update", "field", field_id, body.model_dump(exclude_none=True))
    return {"success": True}


@sales_crm_router.patch("/leads/{lead_id}/custom")
async def set_custom(lead_id: str, body: Dict[str, Any], request: Request, admin: Principal = Depends(caller)):
    await _lead(lead_id)
    try:
        values = await crm.custom_values(get_db(), "lead", body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if values:
        await get_db()[m.LEADS].update_one({"_id": lead_id}, {"$set": {**{f"custom.{k}": v for k, v in values.items()},
                                                                       "updated_at": utcnow()}})
    await _audit(admin, request, "sales.lead.custom", "sales_lead", lead_id, values)
    return {"success": True}


@sales_crm_router.get("/search")
async def search(q: str = Query(min_length=2, max_length=80), admin: Principal = Depends(viewer)):
    out = await crm.search(get_db(), q)
    return to_jsonable({"leads": [m.with_id(x) for x in out["leads"]], "deals": [m.with_id(x) for x in out["deals"]]})


class BulkBody(BaseModel):
    lead_ids: List[str] = Field(min_length=1, max_length=5000)
    action: Literal["assign", "stage", "tag", "queue"]
    value: Any = None                 # owner id, stage, tag, or campaign id


@sales_crm_router.post("/leads/bulk")
async def bulk(body: BulkBody, request: Request, admin: Principal = Depends(manager)):
    db = get_db()
    if body.action == "queue":
        from core.outbound import dialer

        campaign = await db[m.CAMPAIGNS].find_one({"_id": str(body.value or "")})
        if not campaign or campaign.get("status") == "completed":
            raise HTTPException(404, "Pick a campaign that isn't completed")
        result: Dict[str, Any] = await dialer.queue_leads(db, campaign, {"_id": {"$in": body.lead_ids}}, now=utcnow(), picked=True)
    else:
        try:
            result = {"updated": await crm.bulk(db, body.lead_ids, body.action, body.value, by=admin.user_id, now=utcnow())}
        except ValueError as e:
            raise HTTPException(422, str(e))
    await _audit(admin, request, f"sales.leads.bulk.{body.action}", "sales_leads", "*",
                 {"count": len(body.lead_ids), "value": body.value, **result})
    return result


# ── Email: compose, AI draft, consent, sequences ─────────────────────────────

class EmailBody(BaseModel):
    to: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=2, max_length=150)
    text: str = Field(min_length=2, max_length=6000)


@sales_crm_router.post("/leads/{lead_id}/email")
async def send_email(lead_id: str, body: EmailBody, request: Request, admin: Principal = Depends(caller)):
    """A one-off email from the CRM: only to someone who agreed to email or wrote to us. Footer and
    unsubscribe are added; it is logged on the timeline and retried from the outbox if it fails."""
    lead = await _lead(lead_id)
    why = crm.email_permission(lead, body.to)
    if not why:
        raise HTTPException(409, "No consent to email this address. It needs consent given on a call, a reply from "
                                 "them, or consent a manager records with evidence.")
    if await followup.is_suppressed(get_db(), body.to):
        raise HTTPException(409, "That address unsubscribed from sales email.")
    doc = await followup.send_sales_email(get_db(), to=body.to, subject=body.subject, text=body.text, kind="manual",
                                          lead_id=lead_id, now=utcnow(), by=admin.user_id)
    await _audit(admin, request, "sales.email.send", "sales_lead", lead_id, {"to": body.to, "basis": why, "status": doc["status"]})
    return to_jsonable({"email": {k: v for k, v in doc.items() if k != "attachments"}, "basis": why})


@sales_crm_router.post("/leads/{lead_id}/draft-email")
async def draft_email(lead_id: str, body: offer_ai.DraftEmailRequest, request: Request, admin: Principal = Depends(caller)):
    """An AI draft for the person to edit before sending. Nothing is sent."""
    db = get_db()
    lead = await _lead(lead_id)
    deal = await db[crm.DEALS].find_one({"_id": body.deal_id, "lead_id": lead_id}) if body.deal_id else None
    campaign = await db[m.CAMPAIGNS].find_one({"_id": lead.get("campaign_id")}) if lead.get("campaign_id") else None
    offer = await of.offer_for_campaign(db, campaign or {})
    notes = [a.get("text") for a in await followup.activity(db, lead_id, limit=6)]
    try:
        await offer_ai.guard(db, admin.user_id, "compose")
        out = await offer_ai.draft_email(db, lead, offer, purpose=body.purpose, deal=deal, notes=notes,
                                         instruction=body.instruction, market=body.market)
    except offer_ai.AiError as e:
        raise HTTPException(e.status, str(e))
    await _audit(admin, request, "sales.email.ai_draft", "sales_lead", lead_id, {"purpose": body.purpose, "usd": out["cost_usd"]})
    return out


class ConsentBody(BaseModel):
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    note: str = Field(min_length=5, max_length=300)        # the evidence: "Asked for pricing by email at the expo, 12 Nov"


@sales_crm_router.post("/leads/{lead_id}/email-consent")
async def record_email_consent(lead_id: str, body: ConsentBody, request: Request, admin: Principal = Depends(manager)):
    if not await crm.record_email_consent(get_db(), lead_id, body.email, note=body.note, by=admin.user_id, now=utcnow()):
        raise HTTPException(404, "No lead or contact with that email")
    await _audit(admin, request, "sales.email.consent", "sales_lead", lead_id, {"email": body.email, "note": body.note})
    return {"success": True}


@sales_crm_router.post("/leads/{lead_id}/sequence/stop")
async def stop_sequence(lead_id: str, request: Request, admin: Principal = Depends(caller)):
    n = await followup.stop_sequences(get_db(), lead_id, reason=f"stopped by {admin.name or admin.email}", now=utcnow())
    await _audit(admin, request, "sales.sequence.stop", "sales_lead", lead_id, {"cancelled": n})
    return {"cancelled": n}


@public_crm_router.post("/inbound-email")
async def inbound_email(request: Request, x_inbound_secret: str = Header(default="", alias="X-Inbound-Secret")):
    """Replies to sales email, from the email provider's inbound-parse webhook. Off until
    SALES_INBOUND_EMAIL_SECRET is set; the reply-to address names the lead, signed."""
    secret = settings.SALES_INBOUND_EMAIL_SECRET
    if not secret:
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(x_inbound_secret.encode(), secret.encode()):
        raise HTTPException(401, "Bad signature")
    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
    to = " ".join(str(data.get(k) or "") for k in ("to", "To", "recipient", "envelope_to"))
    sender = str(data.get("from") or data.get("From") or data.get("sender") or "")
    match = re.search(r"[^<\s]+@[^>\s]+", sender)
    lead_id = followup.lead_from_reply_address(to)
    if not lead_id or not match:
        return {"accepted": False}
    if not await get_db()[m.LEADS].find_one({"_id": lead_id}, {"_id": 1}):
        return {"accepted": False}
    await followup.record_reply(get_db(), lead_id, sender=match.group(0), subject=str(data.get("subject") or data.get("Subject") or "")[:200],
                                text=str(data.get("text") or data.get("TextBody") or data.get("plain") or "")[:5000], now=utcnow())
    return {"accepted": True}


# ── Learn from calls, reports, the audit trail ───────────────────────────────

class InsightsBody(BaseModel):
    days: Literal[7, 30, 90] = 30
    version: Optional[int] = Field(default=None, ge=1)     # one published version only (default: all)


@sales_crm_router.post("/offers/{offer_id}/insights")
async def offer_insights(offer_id: str, body: InsightsBody, request: Request, admin: Principal = Depends(manager)):
    from core.outbound import insights

    doc = await _offer(offer_id)
    offer = {**of.settings_of(doc).model_dump(), "id": offer_id, "version": doc.get("published_version")}
    use_ai = not offer_ai.unavailable_reason()
    try:
        if use_ai:
            await offer_ai.guard(get_db(), admin.user_id, "insights")
        out = await insights.insights(get_db(), offer, days=body.days, now=utcnow(), use_ai=use_ai,
                                      version=body.version)
    except offer_ai.AiError as e:
        raise HTTPException(e.status, str(e))
    await _audit(admin, request, "sales.offer.insights", "offer", offer_id, {"days": body.days, "answered": out["answered"]})
    return to_jsonable(out)


@sales_crm_router.get("/reports")
async def reports(days: int = Query(90, ge=7, le=365), admin: Principal = Depends(viewer)):
    from core.outbound import reports as rep

    return to_jsonable(await rep.report(get_db(), days=days, now=utcnow()))


def _audit_query(action: Optional[str], target_type: Optional[str], target_id: Optional[str], user: Optional[str],
                 since: Optional[str], until: Optional[str]) -> Dict[str, Any]:
    if action and not action.startswith("sales."):          # the sales audit only: never the rest of the platform
        raise HTTPException(422, "Only sales actions (sales.…) can be searched here.")
    q: Dict[str, Any] = {"tenant_id": "platform", "action": {"$regex": "^" + re.escape(action or "sales.")}}
    if target_type:
        q["target_type"] = target_type
    if target_id:
        q["target_id"] = target_id
    if user:
        q["user_id"] = {"$in": [user, f"platform:{user}"]}
    rng: Dict[str, Any] = {}
    from datetime import datetime, timezone

    for key, value in (("$gte", since), ("$lte", until)):
        if value:
            try:
                rng[key] = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(422, "Dates are YYYY-MM-DD")
    if rng:
        q["at"] = rng
    return q


async def _audit_rows(q: Dict[str, Any], limit: int, skip: int = 0) -> List[Dict[str, Any]]:
    # scoped: q always carries tenant_id "platform" (_audit_query)
    rows = await get_db().audit_log.find(q, {"hash": 0, "prev_hash": 0}).sort("at", -1).skip(skip).limit(limit).to_list(limit)
    ids = {str(r.get("user_id") or "").removeprefix("platform:") for r in rows}
    names = {u["_id"]: u.get("name") or u.get("email") for u in await get_db().users.find({"_id": {"$in": list(ids)}},
                                                                                           {"name": 1, "email": 1}).to_list(500)}
    for r in rows:
        r["user_name"] = names.get(str(r.get("user_id") or "").removeprefix("platform:"), r.get("user_id"))
    return rows


@sales_crm_router.get("/audit")
async def audit(action: Optional[str] = Query(None, max_length=80), target_type: Optional[str] = Query(None, max_length=40),
                target_id: Optional[str] = Query(None, max_length=80), user: Optional[str] = Query(None, max_length=80),
                since: Optional[str] = None, until: Optional[str] = None, page: int = Query(1, ge=1),
                admin: Principal = Depends(manager)):
    """Who did what in the sales dialer, newest first. Entries are hash-chained and never edited."""
    q = _audit_query(action, target_type, target_id, user, since, until)
    total = await get_db().audit_log.count_documents(q)  # scoped: tenant_id "platform" (_audit_query)
    rows = await _audit_rows(q, 50, (page - 1) * 50)
    return to_jsonable({"entries": rows, "total": total, "page": page, "pages": max(1, -(-total // 50))})


@sales_crm_router.get("/audit.csv")
async def audit_csv(request: Request, action: Optional[str] = Query(None, max_length=80), since: Optional[str] = None,
                    until: Optional[str] = None, admin: Principal = Depends(manager)):
    rows = await _audit_rows(_audit_query(action, None, None, None, since, until), 20_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "user", "action", "target_type", "target_id", "ip", "diff"])
    for r in rows:
        w.writerow([r.get("at"), r.get("user_name"), r.get("action"), r.get("target_type"), r.get("target_id"), r.get("ip"),
                    str(r.get("diff") or "")[:2000]])
    await _audit(admin, request, "sales.audit.export", "audit_log", "*", {"rows": len(rows)})
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="sales-audit.csv"'})


@sales_crm_router.get("/audit/verify")
async def audit_verify(admin: Principal = Depends(manager)):
    """Walks the whole chain: an edited, deleted or inserted entry breaks it."""
    from core.compliance import audit_chain

    ok, problems = await audit_chain.verify(get_db(), "platform")
    return {"intact": ok, "problems": problems[:20]}
