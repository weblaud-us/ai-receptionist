"""
The built-in CRM (SALES_SUITE_PLAN.md wave 3): there is no HubSpot or Pipedrive; the platform
dashboard is where sales are run.

    a lead       the company: its main number (the only one the dialer calls, through the gate), and
                 `contacts[]` (people, roles, emails, their own email consent)
    a deal       an opportunity to sell one offer to a lead: amount, stage, probability, owner, expected
                 close, lost reason, proposal. Created by itself when a call ends "interested" or
                 "meeting booked"; moved on by people.
    ownership    leads and deals have an owner; new ones are shared out round-robin among the sales team
                 members with "gets new leads" on, or go to the campaign's owner
    merge        two records of one business become one, always keeping the stricter compliance state:
                 do-not-call wins, declined offers are combined, the longest cooldown is kept. The merged
                 lead is kept (status "merged"), never deleted.
    views        saved filters, for one person or shared
    fields       custom fields on leads and deals (text, number, date, choice)

Nothing here deletes: deals end won or lost, contacts and fields are archived.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from core.outbound import followup
from core.outbound import models as m
from core.tenancy.ids import new_id

DEALS = "sales_deals"
VIEWS = "sales_views"
FIELDS = "sales_custom_fields"
META = "sales_crm_meta"

DEAL_STAGES: tuple[str, ...] = ("qualified", "meeting_booked", "proposal", "won", "lost")
OPEN_STAGES = ("qualified", "meeting_booked", "proposal")
PROBABILITY: Mapping[str, float] = {"qualified": 0.2, "meeting_booked": 0.4, "proposal": 0.6, "won": 1.0, "lost": 0.0}
# The lead's pipeline stage for each deal stage (followup.PIPELINE_STAGES).
LEAD_STAGE = {"qualified": "interested", "meeting_booked": "meeting_booked", "proposal": "proposal", "won": "won", "lost": "lost"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: Any) -> Optional[datetime]:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


# ── Fit (Phase 13): without research, a score from what the lead record says ─

def rules_fit(lead: Mapping[str, Any], campaign: Mapping[str, Any]) -> Dict[str, Any]:
    score, reasons = 40, []
    vertical = str(campaign.get("vertical") or "").strip().lower()
    if vertical and vertical in str(lead.get("vertical") or "").lower():
        score += 25
        reasons.append(f"Is a {vertical} business")
    if lead.get("website"):
        score += 10
        reasons.append("Has a website")
    if lead.get("contact_name"):
        score += 15
        reasons.append("A named contact")
    if lead.get("email"):
        score += 5
    if lead.get("region_code"):
        score += 5
    return {"score": min(100, score), "source": "rules", "reasons": reasons[:3]}


# ── Deals ────────────────────────────────────────────────────────────────────

class DealInput(BaseModel):
    lead_id: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=2, max_length=120)
    offer_id: Optional[str] = Field(default=None, max_length=40)
    amount: float = Field(default=0, ge=0, le=100_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    stage: Literal["qualified", "meeting_booked", "proposal", "won", "lost"] = "qualified"
    probability: Optional[float] = Field(default=None, ge=0, le=1)
    expected_close: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    owner_id: str = Field(default="", max_length=60)


class DealPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    amount: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    stage: Optional[Literal["qualified", "meeting_booked", "proposal", "won", "lost"]] = None
    probability: Optional[float] = Field(default=None, ge=0, le=1)
    expected_close: Optional[str] = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    owner_id: Optional[str] = Field(default=None, max_length=60)
    lost_reason: Optional[str] = Field(default=None, max_length=300)
    proposal: Optional[Dict[str, Any]] = None                   # {sent_at, amount, link}
    custom: Optional[Dict[str, Any]] = None

    @field_validator("proposal")
    @classmethod
    def _proposal(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return v
        link = str(v.get("link") or "")[:500]
        if link and not re.match(r"^https?://", link):
            raise ValueError("the proposal link must start with http(s)://")
        try:
            amount = float(v.get("amount") or 0)
        except (TypeError, ValueError):
            raise ValueError("the proposal amount must be a number")
        return {"sent_at": str(v.get("sent_at") or "")[:30], "amount": amount, "link": link}


async def next_owner(db: Any, campaign: Optional[Mapping[str, Any]] = None) -> str:
    """The campaign's owner, else the next person (round robin) with "gets new leads" on."""
    if campaign and campaign.get("owner_id"):
        return str(campaign["owner_id"])
    people = await db.users.find({"sales_takes_leads": True, "sales_role": {"$in": ["caller", "manager"]},
                                  "disabled": {"$ne": True}}, {"_id": 1}).sort("_id", 1).to_list(200)
    if not people:
        return ""
    meta = await db[META].find_one_and_update({"_id": "round_robin"}, {"$inc": {"n": 1}}, upsert=True,
                                              return_document=ReturnDocument.AFTER)
    return str(people[(int(meta["n"]) - 1) % len(people)]["_id"])


async def create_deal(db: Any, body: DealInput, *, by: str, now: Optional[datetime] = None,
                      campaign_id: Optional[str] = None, source: str = "manual") -> Dict[str, Any]:
    now = now or utcnow()
    doc = {"_id": new_id("deal"), **body.model_dump(), "campaign_id": campaign_id,
           "probability": body.probability if body.probability is not None else PROBABILITY[body.stage],
           "lost_reason": "", "proposal": None, "custom": {}, "source": source,
           "won_at": now if body.stage == "won" else None, "closed_at": now if body.stage in ("won", "lost") else None,
           "created_by": by, "created_at": now, "updated_at": now}
    await db[DEALS].insert_one(doc)
    await followup.add_activity(db, body.lead_id, "stage", f"Deal opened: {body.title} ({body.stage.replace('_', ' ')})",
                                by=by, meta={"deal_id": doc["_id"]}, now=now)
    await sync_lead_stage(db, body.lead_id, now=now)
    return doc


async def update_deal(db: Any, deal_id: str, patch: DealPatch, *, by: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    now = now or utcnow()
    deal = await db[DEALS].find_one({"_id": deal_id})
    if deal is None:
        return None
    sets: Dict[str, Any] = {k: v for k, v in patch.model_dump(exclude_none=True).items() if k != "custom"}
    for k, v in (patch.custom or {}).items():
        sets[f"custom.{k}"] = v
    if patch.stage and patch.stage != deal.get("stage"):
        if patch.stage == "lost" and not (patch.lost_reason or deal.get("lost_reason")):
            raise ValueError("say why it was lost")
        if patch.probability is None:
            sets["probability"] = PROBABILITY[patch.stage]
        sets["won_at"] = now if patch.stage == "won" else None
        sets["closed_at"] = now if patch.stage in ("won", "lost") else None
        await followup.add_activity(db, deal["lead_id"], "stage",
                                    f"Deal {deal.get('title')}: {deal.get('stage', '').replace('_', ' ')} → {patch.stage.replace('_', ' ')}"
                                    + (f" ({patch.lost_reason})" if patch.stage == "lost" and patch.lost_reason else ""),
                                    by=by, meta={"deal_id": deal_id}, now=now)
        if patch.stage in ("won", "lost"):
            await followup.stop_sequences(db, deal["lead_id"], reason=f"deal {patch.stage}", now=now)
    if patch.proposal and not (deal.get("proposal") or {}).get("sent_at") and patch.proposal.get("sent_at"):
        await followup.add_activity(db, deal["lead_id"], "note", f"Proposal sent: {patch.proposal.get('amount') or ''} "
                                    f"{patch.proposal.get('link') or ''}".strip(), by=by, meta={"deal_id": deal_id}, now=now)
    sets["updated_at"] = now
    await db[DEALS].update_one({"_id": deal_id}, {"$set": sets})
    await sync_lead_stage(db, deal["lead_id"], now=now)
    return await db[DEALS].find_one({"_id": deal_id})


async def sync_lead_stage(db: Any, lead_id: str, *, now: Optional[datetime] = None) -> None:
    """The lead's pipeline stage follows its most advanced deal (an open one, else the last closed one)."""
    deals = await db[DEALS].find({"lead_id": lead_id}, {"stage": 1, "updated_at": 1}).to_list(100)
    if not deals:
        return
    open_ = [d for d in deals if d["stage"] in OPEN_STAGES]
    pick = max(open_, key=lambda d: DEAL_STAGES.index(d["stage"])) if open_ else max(
        deals, key=lambda d: _aware(d.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"stage": LEAD_STAGE[pick["stage"]], "updated_at": now or utcnow()}})


async def ensure_deal(db: Any, lead: Mapping[str, Any], attempt: Mapping[str, Any], outcome: str,
                      *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """A call ended interested or with a meeting: open a deal for this offer, or move it on. Idempotent."""
    if outcome not in ("interested", "demo_booked"):
        return None
    now = now or utcnow()
    from core.outbound import offers as of

    offer_id = of.offer_id_of(attempt)
    stage = "meeting_booked" if outcome == "demo_booked" else "qualified"
    existing = await db[DEALS].find_one({"lead_id": lead["_id"], "offer_id": offer_id, "stage": {"$in": list(OPEN_STAGES)}})
    if existing:
        if DEAL_STAGES.index(stage) > DEAL_STAGES.index(existing["stage"]):
            return await update_deal(db, existing["_id"], DealPatch(stage=stage), by="sales_agent", now=now)  # type: ignore[arg-type]
        return existing
    offer = await of.offer_for(db, attempt.get("offer_id"), attempt.get("offer_version"))
    campaign = await db[m.CAMPAIGNS].find_one({"_id": attempt.get("campaign_id")}, {"owner_id": 1}) if attempt.get("campaign_id") else None
    owner = str(lead.get("owner_id") or "") or await next_owner(db, campaign)
    if owner and not lead.get("owner_id"):
        await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"owner_id": owner}})
    body = DealInput(lead_id=lead["_id"], title=f"{lead.get('business_name') or 'Lead'} · {offer.get('name') or offer_id}"[:120],
                     offer_id=offer_id, amount=float(offer.get("deal_value_default") or 0),
                     currency=str(offer.get("deal_currency") or "USD"), stage=stage, owner_id=owner)  # type: ignore[arg-type]
    return await create_deal(db, body, by="sales_agent", now=now, campaign_id=attempt.get("campaign_id"), source="call")


# ── Contacts ─────────────────────────────────────────────────────────────────

class ContactInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=200, pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$")
    phone: str = Field(default="", max_length=40)
    primary: bool = False
    notes: str = Field(default="", max_length=500)


async def add_contact(db: Any, lead_id: str, body: ContactInput, *, by: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    doc = {"id": new_id("con"), **body.model_dump(), "email": body.email.lower(), "email_consent_at": None,
           "archived": False, "created_at": now, "created_by": by}
    if body.primary:
        await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"contacts.$[].primary": False}})
    await db[m.LEADS].update_one({"_id": lead_id}, {"$push": {"contacts": doc}, "$set": {"updated_at": now}})
    await followup.add_activity(db, lead_id, "note", f"Contact added: {body.name}{f' ({body.role})' if body.role else ''}", by=by, now=now)
    return doc


async def update_contact(db: Any, lead_id: str, contact_id: str, patch: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    allowed = {"name", "role", "email", "phone", "primary", "notes", "archived"}
    sets = {f"contacts.$.{k}": (str(v).lower() if k == "email" else v) for k, v in patch.items() if k in allowed}
    if not sets:
        return False
    if patch.get("primary"):
        await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"contacts.$[].primary": False}})
    sets["updated_at"] = now or utcnow()
    res = await db[m.LEADS].update_one({"_id": lead_id, "contacts.id": contact_id}, {"$set": sets})
    return bool(res.matched_count)


# ── Email consent (compose and sequences) ───────────────────────────────────

def email_permission(lead: Mapping[str, Any], to: str) -> Optional[str]:
    """Why we may email `to` (None = we may not): consent given on a call or recorded by a manager, or
    they wrote to us first. AU Spam Act / PECR: commercial email needs consent."""
    to = str(to or "").strip().lower()
    if not to:
        return None
    if lead.get("email_consent_at") and str(lead.get("email") or "").lower() == to:
        return "consent"
    for c in lead.get("contacts") or []:
        if str(c.get("email") or "").lower() == to and c.get("email_consent_at") and not c.get("archived"):
            return "consent"
    if to in [str(x).lower() for x in lead.get("replied_from") or []]:
        return "replied"
    return None


async def record_email_consent(db: Any, lead_id: str, email: str, *, note: str, by: str, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    email = email.strip().lower()
    lead = await db[m.LEADS].find_one({"_id": lead_id}, {"email": 1, "contacts": 1})
    if lead is None:
        return False
    evidence = {"at": now, "by": by, "note": note[:300], "email": email}
    if str(lead.get("email") or "").lower() == email or not lead.get("email"):
        await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"email": email, "email_consent_at": now},
                                                        "$push": {"email_consent_log": evidence}})
    else:
        res = await db[m.LEADS].update_one({"_id": lead_id, "contacts.email": email},
                                           {"$set": {"contacts.$.email_consent_at": now}, "$push": {"email_consent_log": evidence}})
        if not res.matched_count:
            return False
    await followup.add_activity(db, lead_id, "note", f"Email consent recorded for {email}: {note[:200]}", by=by, now=now)
    return True


# ── Duplicates and merge ─────────────────────────────────────────────────────

def _domain(website: Any) -> str:
    site = str(website or "").strip().lower()
    if not site:
        return ""
    host = urlparse(site if "//" in site else f"https://{site}").netloc
    return host.removeprefix("www.")


def _name_key(name: Any) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower()).split()
    stop = {"the", "pty", "ltd", "llc", "inc", "co", "limited", "and", "of"}
    return " ".join(w for w in words if w not in stop)


async def duplicates(db: Any, *, limit: int = 50) -> List[Dict[str, Any]]:
    """Groups of leads that look like the same business: same website domain, or the same name in the same city."""
    rows = await db[m.LEADS].find({"owner": m.OWNER_PLATFORM, "status": {"$ne": "merged"}},
                                  {"business_name": 1, "website": 1, "phone": 1, "city": 1, "status": 1, "created_at": 1}
                                  ).to_list(50_000)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        keys = []
        if _domain(r.get("website")):
            keys.append("d:" + _domain(r.get("website")))
        name = _name_key(r.get("business_name"))
        if name:
            keys.append(f"n:{name}|{str(r.get('city') or '').lower()}")
        for k in keys:
            groups.setdefault(k, []).append(r)
    out, seen = [], set()
    for key, items in groups.items():
        ids = tuple(sorted(i["_id"] for i in items))
        if len(ids) < 2 or ids in seen:
            continue
        seen.add(ids)
        out.append({"reason": "same website" if key.startswith("d:") else "same name and city", "leads": items})
        if len(out) >= limit:
            break
    return out


_STRICTER = ("dnc", "bad_number", "not_interested")


async def merge(db: Any, keep_id: str, merge_id: str, *, by: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Fold `merge_id` into `keep_id`. The stricter compliance state always wins."""
    now = now or utcnow()
    if keep_id == merge_id:
        raise ValueError("pick two different leads")
    keep = await db[m.LEADS].find_one({"_id": keep_id})
    other = await db[m.LEADS].find_one({"_id": merge_id})
    if not keep or not other:
        raise ValueError("lead not found")
    if other.get("status") == "merged" or keep.get("status") == "merged":
        raise ValueError("one of these leads was already merged")
    sets: Dict[str, Any] = {"updated_at": now}
    for s in _STRICTER:                                       # dnc > bad number > not interested > anything else
        if s in (keep.get("status"), other.get("status")):
            if keep.get("status") != s:
                sets["status"] = s
                sets["next_eligible_at"] = None
            break
    if other.get("dnc") and not keep.get("dnc"):
        sets["dnc"] = other["dnc"]
    cool = [c for c in (_aware(keep.get("cooldown_until")), _aware(other.get("cooldown_until"))) if c]
    if cool:
        sets["cooldown_until"] = max(cool)
    sets["declined_offers"] = sorted(set(keep.get("declined_offers") or []) | set(other.get("declined_offers") or []))
    for f in ("email", "website", "contact_name", "owner_id", "agent_notes", "whatsapp", "whatsapp_consent_at"):
        if not keep.get(f) and other.get(f):
            sets[f] = other[f]
    if not keep.get("email_consent_at") and other.get("email_consent_at") and other.get("email") == sets.get("email", keep.get("email")):
        sets["email_consent_at"] = other["email_consent_at"]
    contacts = list(keep.get("contacts") or []) + [c for c in other.get("contacts") or []
                                                   if c.get("email") not in {x.get("email") for x in keep.get("contacts") or []}]
    if other.get("contact_name") or other.get("phone") != keep.get("phone"):
        contacts.append({"id": new_id("con"), "name": other.get("contact_name") or other.get("business_name") or "Merged record",
                         "role": "", "email": str(other.get("email") or "").lower(), "phone": other.get("phone") or "",
                         "primary": False, "notes": "From a merged duplicate", "email_consent_at": other.get("email_consent_at"),
                         "archived": False, "created_at": now, "created_by": by})
    sets["contacts"] = contacts
    push = {"history": {"$each": list(other.get("history") or [])}, "offer_history": {"$each": list(other.get("offer_history") or [])},
            "merged_from": {"lead_id": merge_id, "phone": other.get("phone"), "at": now, "by": by}}
    await db[m.LEADS].update_one({"_id": keep_id}, {"$set": sets, "$push": push})
    for coll in (followup.ACTIVITY, followup.TASKS, DEALS, followup.OUTBOX):
        # scoped: platform-level sales collections only (the sales dialer is the platform's own)
        await db[coll].update_many({"lead_id": merge_id}, {"$set": {"lead_id": keep_id, "merged_from": merge_id}})
    await db[m.ATTEMPTS].update_many({"lead_id": merge_id}, {"$set": {"merged_into_lead": keep_id}})
    await db[m.LEADS].update_one({"_id": merge_id}, {"$set": {"status": "merged", "merged_into": keep_id, "next_eligible_at": None,
                                                              "updated_at": now}})
    await followup.add_activity(db, keep_id, "note", f"Merged with {other.get('business_name') or merge_id} ({other.get('phone')})",
                                by=by, meta={"merged": merge_id}, now=now)
    await sync_lead_stage(db, keep_id, now=now)
    merged = await db[m.LEADS].find_one({"_id": keep_id})
    return merged or {}


# ── My work ──────────────────────────────────────────────────────────────────

async def stage_totals(db: Any, query: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, float]]:
    """Per stage: how many deals, their value and their probability-weighted value, over every deal
    that matches (counted in Mongo, so a big pipeline is never cut short)."""
    out: Dict[str, Dict[str, float]] = {s: {"count": 0, "value": 0.0, "weighted": 0.0} for s in DEAL_STAGES}
    rows = await db[DEALS].aggregate([
        {"$match": query or {}},
        {"$group": {"_id": {"$ifNull": ["$stage", "qualified"]}, "count": {"$sum": 1},
                    "value": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "weighted": {"$sum": {"$multiply": [{"$ifNull": ["$amount", 0]}, {"$ifNull": ["$probability", 0]}]}}}},
    ]).to_list(len(DEAL_STAGES) + 10)
    for r in rows:
        out[str(r["_id"])] = {"count": int(r["count"]), "value": round(float(r["value"] or 0), 2),
                              "weighted": round(float(r["weighted"] or 0), 2)}
    return out


async def my_work(db: Any, user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    end = now + timedelta(days=1)
    tasks = await db[followup.TASKS].find({"owner": user_id, "done_at": None, "cancelled": False,
                                           "$or": [{"due_at": {"$lte": end}}, {"due_at": None}]}).sort("due_at", 1).to_list(200)
    deals = await db[DEALS].find({"owner_id": user_id, "stage": {"$in": list(OPEN_STAGES)}}).sort("updated_at", -1).to_list(200)
    leads = await db[m.LEADS].find({"owner_id": user_id, "status": {"$nin": ["merged", "dnc"]}},
                                   {"business_name": 1, "status": 1, "stage": 1, "phone": 1}).sort("updated_at", -1).limit(100).to_list(100)
    overdue = [t for t in tasks if _aware(t.get("due_at")) and _aware(t["due_at"]) < now]  # type: ignore[operator]
    return {"tasks": tasks, "overdue": len(overdue), "deals": deals, "leads": leads,
            "pipeline_value": round(sum(float(d.get("amount") or 0) * float(d.get("probability") or 0) for d in deals), 2)}


# ── Saved views and custom fields ────────────────────────────────────────────

VIEW_FILTERS = ("status", "stage", "q", "tag", "source", "owner_id", "country", "min_fit", "campaign_id")


class ViewInput(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    kind: Literal["leads", "deals"] = "leads"
    filters: Dict[str, Any] = Field(default_factory=dict)
    shared: bool = False

    @field_validator("filters")
    @classmethod
    def _filters(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return {k: (str(x)[:120] if not isinstance(x, (int, float, bool)) else x) for k, x in v.items() if k in VIEW_FILTERS}


async def views(db: Any, user_id: str, kind: str) -> List[Dict[str, Any]]:
    return await db[VIEWS].find({"kind": kind, "archived": {"$ne": True}, "$or": [{"owner": user_id}, {"shared": True}]}
                                ).sort("name", 1).to_list(200)


class CustomFieldInput(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    label: str = Field(min_length=2, max_length=60)
    type: Literal["text", "number", "date", "select"] = "text"
    options: List[str] = Field(default_factory=list, max_length=30)
    applies_to: Literal["lead", "deal"] = "lead"


def check_custom_value(defn: Mapping[str, Any], value: Any) -> Any:
    if value in (None, ""):
        return None
    kind = defn.get("type")
    if kind == "number":
        return float(value)
    if kind == "date":
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(value)):
            raise ValueError(f"{defn.get('label')}: use YYYY-MM-DD")
        return str(value)
    if kind == "select":
        if str(value) not in (defn.get("options") or []):
            raise ValueError(f"{defn.get('label')}: pick one of {', '.join(defn.get('options') or [])}")
        return str(value)
    return str(value)[:500]


async def custom_values(db: Any, applies_to: str, values: Mapping[str, Any]) -> Dict[str, Any]:
    defs = {d["key"]: d for d in await db[FIELDS].find({"applies_to": applies_to, "archived": {"$ne": True}}).to_list(100)}
    out = {}
    for k, v in values.items():
        if k not in defs:
            raise ValueError(f"unknown field {k}")
        out[k] = check_custom_value(defs[k], v)
    return out


# ── Search ───────────────────────────────────────────────────────────────────

async def search(db: Any, q: str, *, limit: int = 20) -> Dict[str, Any]:
    text = q.strip()[:80]
    if len(text) < 2:
        return {"leads": [], "deals": []}
    rx = {"$regex": re.escape(text), "$options": "i"}
    digits = re.sub(r"\D", "", text)
    ors: List[Dict[str, Any]] = [{"business_name": rx}, {"contact_name": rx}, {"email": rx}, {"contacts.name": rx},
                                 {"contacts.email": rx}, {"website": rx}]
    if len(digits) >= 4:
        ors.append({"phone": {"$regex": re.escape(digits)}})
    leads = await db[m.LEADS].find({"owner": m.OWNER_PLATFORM, "status": {"$ne": "merged"}, "$or": ors},
                                   {"business_name": 1, "contact_name": 1, "phone": 1, "status": 1, "stage": 1, "city": 1}
                                   ).limit(limit).to_list(limit)
    deals = await db[DEALS].find({"title": rx}).limit(limit).to_list(limit)
    return {"leads": leads, "deals": deals}


async def bulk(db: Any, lead_ids: Iterable[str], action: str, value: Any, *, by: str, now: Optional[datetime] = None) -> int:
    """Assign an owner, set the pipeline stage, or add a tag on many leads at once."""
    now = now or utcnow()
    ids = list(lead_ids)[:5000]
    query = {"_id": {"$in": ids}, "status": {"$ne": "merged"}}
    if action == "assign":
        res = await db[m.LEADS].update_many(query, {"$set": {"owner_id": str(value or ""), "updated_at": now}})
    elif action == "stage":
        if value not in followup.PIPELINE_STAGES:
            raise ValueError("unknown stage")
        res = await db[m.LEADS].update_many(query, {"$set": {"stage": value, "updated_at": now}})
    elif action == "tag":
        tag = str(value or "").strip()[:60]
        if not tag:
            raise ValueError("name the tag")
        res = await db[m.LEADS].update_many(query, {"$addToSet": {"tags": tag}, "$set": {"updated_at": now}})
    else:
        raise ValueError("unknown action")
    for lead_id in ids[:200]:
        await followup.add_activity(db, lead_id, "note", f"Bulk {action}: {value}", by=by, now=now)
    return int(res.modified_count)
