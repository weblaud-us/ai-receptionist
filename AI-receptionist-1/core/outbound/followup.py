"""
After the call: the pipeline, follow-up tasks, the lead's timeline, and sales email that can't be
lost or sent to someone who unsubscribed (SALES_DIALER_PLAN.md Phase 3).

    sales_activity           one row per thing that happened to a lead (call, note, email, stage, task)
    sales_tasks              follow-ups with a due date; done or cancelled, never deleted
    sales_outbox             every sales email: sent, failed (retried), or marked sent by hand
    sales_email_suppression  addresses that unsubscribed — no sales email goes to them again

Pipeline stages are worked out from the lead's status (`derived_stage`); a person can move a lead
on (proposal, won, lost) and that choice is kept (`stage_of`).

Every sales email gets the legal footer (SALES_EMAIL_FOOTER: who we are and where) and a working
one-click unsubscribe link. That is required for commercial email under the AU Spam Act 2003 and
good practice under PECR and CAN-SPAM.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import quote

from config import settings
from core.integrations.email import send_email
from core.outbound import models as m
from core.signed_links import sign_ref, verify_ref
from core.tenancy.ids import new_id

logger = logging.getLogger(__name__)

ACTIVITY = "sales_activity"
TASKS = "sales_tasks"
OUTBOX = "sales_outbox"
SUPPRESSION = "sales_email_suppression"

PIPELINE_STAGES: tuple[str, ...] = ("new", "contacted", "interested", "meeting_booked", "proposal", "won", "lost")
MANUAL_STAGES = frozenset({"proposal", "won", "lost"})          # a person's call: never overridden by a dial
ACTIVITY_TYPES = ("call", "note", "email", "stage", "task", "meeting")
OUTBOX_MAX_TRIES = 3
STRANDED_AFTER = timedelta(minutes=10)         # a "pending" email nobody tried for this long is retried
FOLLOW_UP_DAYS = 2
UNSUB_KIND = "sales_unsub"
REPLY_KIND = "sales_reply"
SEQUENCE_TRIGGERS = ("info_sent", "meeting_booked")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Pipeline ─────────────────────────────────────────────────────────────────

def derived_stage(lead: Mapping[str, Any]) -> str:
    status = str(lead.get("status") or "new")
    if status == "demo_booked":
        return "meeting_booked"
    if status == "interested":
        return "interested"
    if status in ("not_interested", "dnc", "bad_number"):
        return "lost"
    if int(lead.get("attempts") or 0) > 0 or (lead.get("history") or []):
        return "contacted"
    return "new"


def stage_of(lead: Mapping[str, Any]) -> str:
    """The stage shown: a person's choice when it is further on (or final), else the derived one."""
    manual, derived = lead.get("stage"), derived_stage(lead)
    if manual in MANUAL_STAGES:
        return str(manual)
    if manual in PIPELINE_STAGES and PIPELINE_STAGES.index(str(manual)) > PIPELINE_STAGES.index(derived):
        return str(manual)
    return derived


# ── Timeline ─────────────────────────────────────────────────────────────────

async def add_activity(db: Any, lead_id: str, type: str, text: str, *, by: str = "system",
                       meta: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    doc = {"_id": new_id("act"), "lead_id": lead_id, "type": type if type in ACTIVITY_TYPES else "note",
           "text": str(text or "")[:2000], "meta": meta or {}, "by": by, "at": now or utcnow()}
    await db[ACTIVITY].insert_one(doc)
    return doc


async def activity(db: Any, lead_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    return await db[ACTIVITY].find({"lead_id": lead_id}).sort("at", -1).limit(limit).to_list(limit)


# ── Tasks ────────────────────────────────────────────────────────────────────

async def create_task(db: Any, lead_id: str, title: str, *, due_at: Optional[datetime] = None, owner: str = "",
                      source: str = "manual", attempt_id: Optional[str] = None, by: str = "system",
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    doc = {"_id": new_id("tsk"), "lead_id": lead_id, "title": str(title or "")[:200], "due_at": due_at,
           "owner": owner[:80], "source": source, "attempt_id": attempt_id, "done_at": None, "cancelled": False,
           "created_by": by, "created_at": now}
    await db[TASKS].insert_one(doc)
    await add_activity(db, lead_id, "task", f"Task: {doc['title']}", by=by, meta={"task_id": doc["_id"]}, now=now)
    return doc


async def auto_follow_up(db: Any, lead: Mapping[str, Any], attempt: Mapping[str, Any], outcome: str,
                         *, now: datetime) -> Optional[Dict[str, Any]]:
    """Interested on a call (or asked for information) → a follow-up task for a person, once per attempt."""
    if outcome != "interested":
        return None
    if await db[TASKS].find_one({"attempt_id": attempt.get("_id"), "source": "agent"}, {"_id": 1}):
        return None
    who = str(lead.get("contact_name") or lead.get("business_name") or "the lead")
    return await create_task(db, str(lead["_id"]), f"Follow up with {who}", due_at=now + timedelta(days=FOLLOW_UP_DAYS),
                             source="agent", attempt_id=attempt.get("_id"), now=now)


# ── Email: suppression, unsubscribe, outbox ──────────────────────────────────

def _norm(email: str) -> str:
    return str(email or "").strip().lower()


def unsubscribe_link(email: str) -> str:
    base = (settings.WEBHOOK_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    address = _norm(email)
    return f"{base}/public/sales/unsubscribe?e={quote(address)}&sig={sign_ref(UNSUB_KIND, address)}"


def verify_unsubscribe(email: str, sig: Optional[str]) -> bool:
    return verify_ref(UNSUB_KIND, _norm(email), sig)


async def is_suppressed(db: Any, email: str) -> bool:
    return bool(await db[SUPPRESSION].find_one({"_id": _norm(email)}, {"_id": 1}))


async def suppress(db: Any, email: str, *, source: str, now: Optional[datetime] = None) -> None:
    await db[SUPPRESSION].update_one({"_id": _norm(email)}, {"$setOnInsert": {"source": source, "at": now or utcnow()}},
                                     upsert=True)


def with_unsubscribe(text: str, email: str) -> str:
    link = unsubscribe_link(email)
    line = f"Don't want emails from us? Unsubscribe: {link}" if link else "Don't want emails from us? Reply with \"unsubscribe\"."
    return f"{text}\n\n{line}"


def ics_attachment(ics: str, filename: str = "invite.ics") -> Dict[str, str]:
    return {"filename": filename, "content": base64.b64encode(ics.encode("utf-8")).decode("ascii"),
            "content_type": "text/calendar; charset=utf-8; method=REQUEST"}


def reply_address(lead_id: Optional[str]) -> str:
    """reply+<lead>.<sig>@SALES_INBOUND_DOMAIN, so a reply lands on the right lead (Phase 16). "" when off."""
    domain = (settings.SALES_INBOUND_DOMAIN or "").strip().lstrip("@")
    if not domain or not lead_id:
        return ""
    return f"reply+{lead_id}.{sign_ref(REPLY_KIND, lead_id)[:16]}@{domain}"


def lead_from_reply_address(address: str) -> Optional[str]:
    import re

    match = re.search(r"reply\+([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]+)@", str(address or ""))
    if not match:
        return None
    lead_id, sig = match.group(1), match.group(2)
    return lead_id if sign_ref(REPLY_KIND, lead_id)[:16] == sig else None


async def send_sales_email(db: Any, *, to: str, subject: str, text: str, kind: str, lead_id: Optional[str] = None,
                           attempt_id: Optional[str] = None, attachments: Optional[List[Dict[str, str]]] = None,
                           now: Optional[datetime] = None, by: str = "system") -> Dict[str, Any]:
    """Send (or refuse, for an unsubscribed address) and record it. A failed send stays in the outbox
    and is retried by `retry_outbox`; after OUTBOX_MAX_TRIES a person sees it as "needs sending"."""
    now = now or utcnow()
    to = _norm(to)
    doc: Dict[str, Any] = {"_id": new_id("eml"), "lead_id": lead_id, "attempt_id": attempt_id, "kind": kind, "to": to,
                           "subject": subject[:200], "text": with_unsubscribe(text, to), "attachments": attachments or [],
                           "reply_to": reply_address(lead_id), "by": by,
                           "status": "pending", "tries": 0, "error": "", "created_at": now, "sent_at": None}
    if await is_suppressed(db, to):
        doc.update(status="suppressed", error="unsubscribed")
        await db[OUTBOX].insert_one(doc)
        return doc
    await db[OUTBOX].insert_one(doc)
    return await _try(db, doc, now)


async def _try(db: Any, doc: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    ok = await send_email(doc["to"], doc["subject"], doc["text"], attachments=doc.get("attachments") or None,
                          reply_to=doc.get("reply_to") or "")
    tries = int(doc.get("tries") or 0) + 1
    sets: Dict[str, Any] = {"tries": tries, "last_try_at": now}
    if ok:
        sets.update(status="sent", sent_at=now, error="")
    else:
        sets.update(status="failed" if tries >= OUTBOX_MAX_TRIES else "pending", error="send failed")
    await db[OUTBOX].update_one({"_id": doc["_id"]}, {"$set": sets})
    if ok and doc.get("lead_id"):
        await add_activity(db, doc["lead_id"], "email", f"Email sent: {doc['subject']}", meta={"email_id": doc["_id"]}, now=now)
    return {**doc, **sets}


async def retry_outbox(db: Any, *, now: Optional[datetime] = None, limit: int = 20) -> Dict[str, int]:
    now = now or utcnow()
    stats = {"sent": 0, "failed": 0}
    seq = await send_due(db, now=now, limit=limit)
    stats["sent"] += seq["sent"]
    stats["failed"] += seq["failed"]
    stranded = now - STRANDED_AFTER
    rows = await db[OUTBOX].find({"status": "pending", "$or": [
        {"tries": {"$gte": 1}, "last_try_at": {"$lte": now - timedelta(minutes=5)}},
        # Claimed (or written) but never tried: the process died before the send. Not lost for good.
        {"tries": {"$in": [0, None]}, "claimed_at": {"$lte": stranded}},
        {"tries": {"$in": [0, None]}, "claimed_at": None, "created_at": {"$lte": stranded}},
    ]}).limit(limit).to_list(limit)
    for row in rows:
        if await is_suppressed(db, row["to"]):
            await db[OUTBOX].update_one({"_id": row["_id"]}, {"$set": {"status": "suppressed", "error": "unsubscribed"}})
            continue
        done = await _try(db, row, now)
        stats["sent" if done["status"] == "sent" else "failed"] += 1
    return stats


def lead_query_for_stage(stage: str) -> Dict[str, Any]:
    """A first cut of the leads that can be in `stage` (refined with `stage_of` in Python)."""
    statuses = {"interested": ["interested"], "meeting_booked": ["demo_booked"],
                "lost": ["not_interested", "dnc", "bad_number"]}.get(stage)
    manual = {"stage": stage}
    return {"$or": [manual, {"status": {"$in": statuses}}]} if statuses else manual



# ── Follow-up sequences (SALES_SUITE_PLAN.md Phase 17) ───────────────────────
#
# An offer's `follow_ups` (up to 3 emails, each N days after the trigger) start only for someone who
# agreed to email: they gave consent on the call ("send me the info") or booked a meeting. Never for
# no-answer or exhausted leads: that would be cold email, which stays off until counsel says otherwise.
# A sequence stops on unsubscribe, a reply, a meeting booked (for the info sequence), won or lost,
# do-not-call, not interested, or by hand.

STOP_STATUSES = ("dnc", "not_interested", "bad_number", "merged")


async def schedule_sequence(db: Any, lead: Mapping[str, Any], offer: Mapping[str, Any], trigger: str, *,
                            attempt_id: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """Queue the offer's follow-ups for this lead. Returns how many were scheduled (0 without consent)."""
    from core.outbound import crm
    from core.outbound import offers as of

    if trigger not in SEQUENCE_TRIGGERS:
        return 0
    now = now or utcnow()
    steps = list(offer.get("follow_ups") or [])
    to = _norm(lead.get("email") or "")
    if not steps or not to or not crm.email_permission(lead, to) or await is_suppressed(db, to):
        return 0
    sequence_id = f"seq_{lead['_id']}_{offer.get('id') or 'offer'}_{trigger}"
    if await db[OUTBOX].find_one({"sequence_id": sequence_id}, {"_id": 1}):
        return 0                                                         # once per lead, offer and trigger
    if trigger == "meeting_booked":
        await stop_sequences(db, str(lead["_id"]), reason="meeting booked", now=now, kinds=("info_sent",))
    first = str(lead.get("contact_name") or "").split(" ")[0] or "there"
    values = {"name": lead.get("contact_name") or "", "first_name": first, "business": lead.get("business_name") or "",
              "company": offer.get("company_name") or "", "persona": offer.get("persona_name") or "",
              "meeting_label": (offer.get("meeting") or {}).get("label") or "", "meeting_url": (offer.get("meeting") or {}).get("url") or "",
              "when": "", "host": (offer.get("meeting") or {}).get("host_name") or settings.SALES_FOUNDER_NAME,
              "website": offer.get("website") or ""}
    n = 0
    for i, step in enumerate(steps[:3]):
        doc = {"_id": new_id("eml"), "lead_id": lead["_id"], "attempt_id": attempt_id, "kind": f"sequence:{trigger}", "to": to,
               "subject": of.render(step.get("subject") or "", values)[:200],
               "text": with_unsubscribe(of.render(step.get("body") or "", values), to), "attachments": [],
               "reply_to": reply_address(str(lead["_id"])), "by": "sequence",
               "status": "scheduled", "send_after": now + timedelta(days=int(step.get("after_days") or 1)),
               "sequence_id": sequence_id, "sequence_trigger": trigger, "step": i + 1,
               "tries": 0, "error": "", "created_at": now, "sent_at": None}
        await db[OUTBOX].insert_one(doc)
        n += 1
    await add_activity(db, str(lead["_id"]), "email", f"Follow-up emails scheduled: {n} ({trigger.replace('_', ' ')})",
                       meta={"sequence_id": sequence_id}, now=now)
    return n


async def stop_sequences(db: Any, lead_id: str, *, reason: str, now: Optional[datetime] = None,
                         kinds: Optional[Sequence[str]] = None) -> int:
    query: Dict[str, Any] = {"lead_id": lead_id, "status": "scheduled"}
    if kinds:
        query["sequence_trigger"] = {"$in": list(kinds)}
    res = await db[OUTBOX].update_many(query, {"$set": {"status": "cancelled", "error": reason[:120], "cancelled_at": now or utcnow()}})
    if res.modified_count:
        await add_activity(db, lead_id, "email", f"Follow-up emails stopped: {reason}", now=now)
    return int(res.modified_count)


async def send_due(db: Any, *, now: Optional[datetime] = None, limit: int = 20) -> Dict[str, int]:
    """Send scheduled follow-ups that are due, unless something since says not to."""
    now = now or utcnow()
    stats = {"sent": 0, "failed": 0, "cancelled": 0}
    rows = await db[OUTBOX].find({"status": "scheduled", "send_after": {"$lte": now}}).sort("send_after", 1).limit(limit).to_list(limit)
    for row in rows:
        lead = await db[m.LEADS].find_one({"_id": row["lead_id"]}, {"status": 1, "stage": 1, "replied_at": 1}) or {}
        why = ""
        if await is_suppressed(db, row["to"]):
            why = "unsubscribed"
        elif lead.get("status") in STOP_STATUSES:
            why = f"lead is {lead.get('status')}"
        elif lead.get("stage") in ("won", "lost"):
            why = f"deal {lead.get('stage')}"
        elif lead.get("replied_at") and lead["replied_at"] >= row["created_at"].replace(tzinfo=lead["replied_at"].tzinfo):
            why = "they replied"
        if why:
            await db[OUTBOX].update_one({"_id": row["_id"]}, {"$set": {"status": "cancelled", "error": why}})
            stats["cancelled"] += 1
            continue
        claimed = await db[OUTBOX].find_one_and_update({"_id": row["_id"], "status": "scheduled"},
                                                       {"$set": {"status": "pending", "claimed_at": now}})
        if not claimed:
            continue
        done = await _try(db, {**row, "status": "pending"}, now)
        stats["sent" if done["status"] == "sent" else "failed"] += 1
    return stats


async def record_reply(db: Any, lead_id: str, *, sender: str, subject: str, text: str,
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """A reply to a sales email (Phase 16): on the timeline, sequences stopped, a task for a person."""
    now = now or utcnow()
    sender = _norm(sender)
    await db[m.LEADS].update_one({"_id": lead_id}, {"$set": {"replied_at": now, "updated_at": now},
                                                          "$addToSet": {"replied_from": sender}})
    if "unsubscribe" in f"{subject} {text[:200]}".lower():
        await suppress(db, sender, source="reply", now=now)
    entry = await add_activity(db, lead_id, "email", f"Reply from {sender}: {subject[:120]}\n\n{text[:1500]}",
                               by=sender, meta={"inbound": True}, now=now)
    await stop_sequences(db, lead_id, reason="they replied", now=now)
    lead = await db[m.LEADS].find_one({"_id": lead_id}, {"owner_id": 1}) or {}
    await create_task(db, lead_id, f"Reply from {sender}", due_at=now, owner=str(lead.get("owner_id") or ""),
                      source="reply", now=now)
    return entry

