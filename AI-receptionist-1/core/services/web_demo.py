"""
The landing-page live demo ("Talk to it now"): leads, demo-call bookkeeping and clean-up.

A visitor signs in with Google on the landing page and talks to a fictional demo practice in the browser
(WEB_DEMO_TENANT_AU / _GB). Each Google account is one lead in `web_demo_leads`, read by platform
admins in the dashboard's Demo leads page. Nothing is sent to the visitor and nobody is alerted: the
lead just appears there.

    web_demo_leads   _id, google_sub (unique), email, email_domain, name, hosted_domain, region,
                     practice_name, status, notes, demo_count (demos started), call_count (calls that
                     reached the agent), talk_seconds, sessions[] (newest last, ≤ 20),
                     utm {source, medium, campaign}, page, referrer, first_seen_at, last_seen_at

The demo practices are ordinary tenants. Their diary, contacts and messages are cleared after
WEB_DEMO_DIARY_RETENTION_HOURS so the next visitor finds free slots and no visitor's details linger;
their calls (transcripts) after WEB_DEMO_TRANSCRIPT_DAYS. Leads go after WEB_DEMO_LEAD_RETENTION_DAYS
without a visit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import settings
from core.auth.google_id import GoogleIdentity
from core.repositories import Repos
from core.repositories.base import utcnow
from core.sensitive import drop_ciphertext, seal, unseal
from core.tenancy.ids import new_id

logger = logging.getLogger(__name__)

DEMO_LEADS = "web_demo_leads"
STATUSES = ("new", "contacted", "qualified", "not_a_fit")
REGIONS = ("au", "uk")
MAX_SESSIONS = 20
# What a demo practice keeps only briefly: everything a visitor's call can create except the call itself.
DIARY_COLLECTIONS = ("appointments", "slot_locks", "contacts", "messages", "leads", "conversations",
                     "unanswered", "waitlist", "waitlist_offers", "escalations", "wa_outbox")


def demo_tenant_ids() -> set[str]:
    return {t for t in (settings.WEB_DEMO_TENANT_AU.strip(), settings.WEB_DEMO_TENANT_GB.strip()) if t}


def is_demo_tenant(tenant_id: Optional[str]) -> bool:
    return bool(tenant_id) and tenant_id in demo_tenant_ids()


def _clip(value: Optional[str], n: int) -> str:
    return (value or "").strip()[:n]


async def upsert_lead(db: Any, ident: GoogleIdentity, *, region: str, page: str = "", referrer: str = "",
                      utm: Optional[Dict[str, str]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One lead per Google account: created on the first demo, refreshed (and counted) on every one.
    Where they came from (UTM, page, referrer) is the first visit's."""
    now = now or utcnow()
    first_touch = {"utm": {k: _clip((utm or {}).get(k), 80) for k in ("source", "medium", "campaign")},
                   "page": _clip(page, 120), "referrer": _clip(referrer, 200)}
    await db[DEMO_LEADS].update_one(
        {"google_sub": ident.sub},
        {"$set": {"email": ident.email, "email_domain": ident.email.rpartition("@")[2], "name": ident.name,
                  "hosted_domain": ident.hosted_domain, "region": region, "last_seen_at": now},
         "$inc": {"demo_count": 1},
         "$setOnInsert": {"_id": new_id("dml"), "google_sub": ident.sub, "first_seen_at": now, "status": "new",
                          "notes": "", "practice_name": "", "talk_seconds": 0, "call_count": 0, "sessions": [],
                          **first_touch}},
        upsert=True)
    lead = await db[DEMO_LEADS].find_one({"google_sub": ident.sub})
    assert lead is not None
    return lead


async def set_practice_name(db: Any, ident: GoogleIdentity, practice_name: str) -> bool:
    res = await db[DEMO_LEADS].update_one({"google_sub": ident.sub},
                                     {"$set": {"practice_name": _clip(practice_name, 200), "updated_at": utcnow()}})
    return bool(res.matched_count)


async def record_session(db: Any, lead_id: str, *, call_id: str, tenant_id: str, started_at: datetime,
                         duration_s: float, outcome: Optional[str], summary: Optional[str],
                         tools_used: Optional[list] = None) -> None:
    """Called by the worker when a demo call ends: the call goes on the lead's history."""
    session = {"call_id": call_id, "tenant_id": tenant_id, "started_at": started_at,
               "duration_s": round(max(duration_s, 0.0), 1), "outcome": outcome or "completed",
               "summary": _clip(summary, 1200) or None, "tools_used": list(dict.fromkeys(tools_used or []))[:12]}
    # A demo caller may still mention real symptoms: the summary is sealed like a call's (PRIV-08).
    session = seal(session, ("summary",))
    await db[DEMO_LEADS].update_one({"_id": lead_id}, {
        "$push": {"sessions": {"$each": [session], "$slice": -MAX_SESSIONS}},
        "$inc": {"talk_seconds": int(round(max(duration_s, 0.0))), "call_count": 1},
        "$set": {"last_demo_at": started_at}})


async def purge_demo_tenant(db: Any, tenant_id: str, now: Optional[datetime] = None) -> int:
    """A demo practice's short-lived data (see module docstring). Returns rows removed."""
    if not is_demo_tenant(tenant_id):
        return 0
    now = now or utcnow()
    repos = Repos(db, tenant_id)
    diary_cutoff = now - timedelta(hours=settings.WEB_DEMO_DIARY_RETENTION_HOURS)
    removed = 0
    for name in DIARY_COLLECTIONS:
        repo = getattr(repos, name, None)
        if repo is None:
            continue
        # Upserted rows (contacts, locks) may only carry updated_at.
        res = await repo.delete_many({"$or": [{"created_at": {"$lt": diary_cutoff}},
                                              {"created_at": None, "updated_at": {"$lt": diary_cutoff}}]})
        removed += int(getattr(res, "deleted_count", 0) or 0)
    res = await repos.calls.delete_many({"started_at": {"$lt": now - timedelta(days=settings.WEB_DEMO_TRANSCRIPT_DAYS)}})
    removed += int(getattr(res, "deleted_count", 0) or 0)
    if removed:
        logger.info("Web demo: cleared %d old rows from demo practice %s", removed, tenant_id)
    return removed


async def purge_leads(db: Any, now: Optional[datetime] = None) -> int:
    """Leads not seen for WEB_DEMO_LEAD_RETENTION_DAYS are deleted."""
    now = now or utcnow()
    res = await db[DEMO_LEADS].delete_many({"last_seen_at": {"$lt": now - timedelta(days=settings.WEB_DEMO_LEAD_RETENTION_DAYS)}})
    deleted = int(getattr(res, "deleted_count", 0) or 0)
    if deleted:
        logger.info("Web demo: deleted %d leads past retention", deleted)
    return deleted


def session_view(session: Dict[str, Any]) -> Dict[str, Any]:
    """A stored demo call with its summary readable (never the ciphertext)."""
    return drop_ciphertext(unseal(session, ("summary",)))


def lead_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A lead as the platform dashboard lists it (no session bodies)."""
    sessions = doc.get("sessions") or []
    last = session_view(sessions[-1]) if sessions else {}
    return {
        "id": doc["_id"], "name": doc.get("name", ""), "email": doc.get("email", ""),
        "email_domain": doc.get("email_domain", ""), "hosted_domain": doc.get("hosted_domain", ""),
        "practice_name": doc.get("practice_name", ""), "region": doc.get("region", ""),
        "status": doc.get("status", "new"), "notes": doc.get("notes", ""),
        "demo_count": int(doc.get("demo_count") or 0), "talk_seconds": int(doc.get("talk_seconds") or 0),
        "calls": int(doc.get("call_count") or len(sessions)), "last_summary": last.get("summary"),
        "utm": doc.get("utm") or {}, "page": doc.get("page", ""), "referrer": doc.get("referrer", ""),
        "first_seen_at": doc.get("first_seen_at"), "last_seen_at": doc.get("last_seen_at"),
        "last_demo_at": doc.get("last_demo_at"),
    }
