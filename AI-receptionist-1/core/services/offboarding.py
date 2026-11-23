"""
Data portability and offboarding (Roadmap Task 5.5).

Export — one button, one zip (GET /t/export), or each file on its own:
    calls.csv · transcripts.json · contacts.csv · appointments.csv · appointments.ics · messages.csv

Cancellation timeline (driven by core/billing/jobs.py):
    day 0      subscription ends → status cancelled, calls forwarded / "please call back"
    day 0–30   data kept; owner can export or re-subscribe (everything comes back)
    day 27     final warning e-mail
    day 30     numbers released, then every tenant document deleted, recordings deleted from
               storage, members removed; a tombstone tenant row and a `deletion_log` entry remain
               (no personal data) so the deletion can be proven. Backups age out on their own
               rotation (Atlas snapshot retention) — say so in the privacy policy.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from core.repositories.repos import PhoneNumbersStore
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

EXPORT_MAX_ROWS = 50_000
# Every collection that holds a business's own data (tenant_id on each document).
TENANT_COLLECTIONS = (
    "services", "resources", "schedule_exceptions", "appointments", "slot_locks", "contacts", "calls", "messages",
    "knowledge_items", "unanswered_questions", "leads", "conversations", "usage_events", "audit_log", "rooms",
    "bookings", "room_nights", "guests", "call_sessions", "invites", "number_orders",
    # Phase 6/7. `dsr_requests` is kept on purpose: proof that requests were handled (masked, no personal data).
    "consent_events", "owner_feedback", "call_reviews", "eval_cases", "wa_outbox", "pms_compensations",
)
EXPORT_KINDS = ("calls.csv", "transcripts.json", "contacts.csv", "appointments.csv", "appointments.ics",
                "messages.csv")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return "" if value is None else str(value)


def _csv(rows: Iterable[Dict[str, Any]], columns: List[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for row in rows:
        w.writerow([_cell(row.get(c)) for c in columns])
    return buf.getvalue()


def _cell(value: Any) -> str:
    text = _iso(value) if not isinstance(value, (list, dict)) else json.dumps(value, default=_iso)
    # Spreadsheet formula injection: a cell starting with = + - @ is treated as a formula by Excel.
    return "'" + text if text[:1] in ("=", "+", "-", "@") and not text[1:2].isdigit() else text


async def _rows(db: Any, collection: str, tenant_id: str, *, projection: Optional[Dict[str, Any]] = None,
                sort: str = "created_at") -> List[Dict[str, Any]]:
    cur = db[collection].find({"tenant_id": tenant_id}, projection).sort(sort, 1).limit(EXPORT_MAX_ROWS)  # scoped: tenant_id
    return await cur.to_list(EXPORT_MAX_ROWS)


async def calls_csv(db: Any, tenant: TenantConfig) -> str:
    rows = await _rows(db, "calls", tenant.id, projection={"transcript": 0, "turns": 0}, sort="started_at")
    return _csv(rows, ["call_id", "started_at", "ended_at", "duration_s", "channel", "direction", "caller_phone",
                       "dialed_number", "outcome", "summary", "tools_used", "transfer"])


async def transcripts_json(db: Any, tenant: TenantConfig) -> str:
    from core.sensitive import unseal

    rows = [unseal(r) for r in await _rows(db, "calls", tenant.id, sort="started_at", projection={
        "call_id": 1, "started_at": 1, "caller_phone": 1, "transcript": 1, "transcript_enc": 1, "summary": 1})]
    out = [{"call_id": r.get("call_id"), "started_at": _iso(r.get("started_at")), "caller_phone": r.get("caller_phone"),
            "summary": r.get("summary"),
            "transcript": [{"role": t.get("role"), "text": t.get("text"), "at": _iso(t.get("at"))}
                           for t in r.get("transcript") or []]} for r in rows]
    return json.dumps({"business": tenant.name, "exported_at": utcnow().isoformat(), "calls": out}, indent=2)


async def contacts_csv(db: Any, tenant: TenantConfig) -> str:
    rows = await _rows(db, "contacts", tenant.id)
    return _csv(rows, ["phone", "name", "email", "notes", "tags", "do_not_contact", "first_seen_at", "last_seen_at",
                       "stats"])


async def appointments_csv(db: Any, tenant: TenantConfig) -> str:
    rows = await _rows(db, "appointments", tenant.id, sort="start")
    return _csv(rows, ["appointment_id", "start", "end", "status", "service_name", "resource_name", "name", "phone",
                       "email", "notes", "source", "created_at"])


async def messages_csv(db: Any, tenant: TenantConfig) -> str:
    rows = await _rows(db, "messages", tenant.id)
    return _csv(rows, ["message_id", "created_at", "caller_name", "caller_phone", "message", "urgency", "status"])


def _ics_text(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def appointments_ics(db: Any, tenant: TenantConfig) -> str:
    rows = await _rows(db, "appointments", tenant.id, sort="start")
    stamp = _ics_time(utcnow())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AI Receptionist//Export//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_text(tenant.name)}"]
    for a in rows:
        if not isinstance(a.get("start"), datetime):
            continue
        who = a.get("name") or a.get("phone") or ""
        lines += ["BEGIN:VEVENT", f"UID:{a.get('appointment_id') or a.get('_id')}@receptionist", f"DTSTAMP:{stamp}",
                  f"DTSTART:{_ics_time(a['start'])}", f"DTEND:{_ics_time(a.get('end') or a['start'])}",
                  f"SUMMARY:{_ics_text((a.get('service_name') or 'Appointment') + (' — ' + who if who else ''))}",
                  f"DESCRIPTION:{_ics_text(a.get('notes'))}",
                  f"STATUS:{'CANCELLED' if a.get('status') == 'cancelled' else 'CONFIRMED'}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


BUILDERS = {
    "calls.csv": calls_csv, "transcripts.json": transcripts_json, "contacts.csv": contacts_csv,
    "appointments.csv": appointments_csv, "appointments.ics": appointments_ics, "messages.csv": messages_csv,
}
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "json": "application/json", "ics": "text/calendar; charset=utf-8"}


async def export_one(db: Any, tenant: TenantConfig, kind: str) -> str:
    return await BUILDERS[kind](db, tenant)


async def export_zip(db: Any, tenant: TenantConfig) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for kind in EXPORT_KINDS:
            z.writestr(kind, await export_one(db, tenant, kind))
        z.writestr("README.txt", f"Data export for {tenant.name}, {utcnow():%Y-%m-%d %H:%M} UTC.\n"
                                 "Calls are not recorded; call transcripts are in transcripts.json.\n")
    return buf.getvalue()


# ── Deletion ────────────────────────────────────────────────────────────────

async def _delete_recordings(db: Any, tenant_id: str) -> int:
    from core.compliance.retention import delete_recording_object, storage_configured

    if not storage_configured():
        return 0
    deleted = 0
    async for call in db.calls.find({"tenant_id": tenant_id, "recording.path": {"$exists": True}},  # scoped: tenant_id
                                    {"recording": 1}):
        if await delete_recording_object(call.get("recording") or {}):
            deleted += 1
        else:
            logger.warning("A recording for %s could not be deleted", tenant_id)
    return deleted


async def delete_tenant_data(db: Any, tenant: TenantConfig, *, actor: str = "retention") -> Dict[str, int]:
    """Irreversible. Numbers must already be released (the caller does that first)."""
    counts: Dict[str, int] = {"recordings": await _delete_recordings(db, tenant.id)}
    for name in TENANT_COLLECTIONS:
        res = await db[name].delete_many({"tenant_id": tenant.id})  # scoped: tenant_id
        counts[name] = int(getattr(res, "deleted_count", 0) or 0)
    # Members: drop the membership; a user left with no business and no admin rights is deleted.
    removed_users = 0
    async for user in db.users.find({"memberships.tenant_id": tenant.id}):
        await db.users.update_one({"_id": user["_id"]}, {"$pull": {"memberships": {"tenant_id": tenant.id}}})
        others = [m for m in user.get("memberships") or [] if m.get("tenant_id") != tenant.id]
        if not others and not user.get("is_platform_admin"):
            await db.users.delete_one({"_id": user["_id"]})
            await db.sessions.delete_many({"user_id": user["_id"]})
            removed_users += 1
    counts["users"] = removed_users
    await db.sessions.delete_many({"tenant_id": tenant.id})
    for n in await PhoneNumbersStore(db).for_tenant(tenant.id):
        if n.get("status") != "released":
            await PhoneNumbersStore(db).release(n["e164"])
    now = utcnow()
    # Tombstone: ids and billing references only (needed for accounting), no business data.
    await db.tenants.update_one({"_id": tenant.id}, {"$set": {
        "name": "Deleted business", "slug": re.sub(r"[^a-z0-9-]+", "-", f"deleted-{tenant.id.lower()}")[:63].strip("-"), "status": "cancelled",
        "phone": "", "website": "", "description": "", "address": {}, "notifications": {}, "transfer": {},
        "integrations": {}, "policies": {}, "links": {}, "agent": {}, "emergency_instructions": "",
        "billing.deleted_at": now, "billing.verified_phone": "", "updated_at": now,
    }})
    await db.deletion_log.insert_one({"tenant_id": tenant.id, "at": now, "actor": actor, "counts": counts})
    invalidate(tenant.id)
    logger.info("Deleted all data for tenant %s: %s", tenant.id, counts)
    return counts
