"""
Data subject requests for one person, by phone number, inside one business (Roadmap §11.2).

The business is the controller and decides; we (the processor) give it the tool. The owner, or a
platform admin acting on the owner's written instruction, can:

    find(...)     what we hold: counts per kind, newest dates
    export(...)   everything about that number as one JSON document (access / portability)
    erase(...)    delete or anonymise it (erasure)

What erasure does, per collection:
    contacts, messages, leads, conversations, guests   deleted
    calls          transcript, turns, summary removed; caller number replaced by a one-way hash;
                   the recording object is deleted from storage. The call row stays (date, length,
                   outcome, cost) because invoices and usage depend on it — no personal data is left.
    appointments   name, phone, e-mail and notes wiped; the time slot stays so the calendar isn't
                   corrupted. Upcoming confirmed appointments block erasure unless `include_future`
                   (the owner should cancel or move them with the customer first).
    bookings       (hotel) same as appointments.

Optionally the number goes on a suppression list (`consent_events`, opt-out) so the business never
contacts it again — keeping that one number for that purpose is allowed and is what most people want.

Every request is logged in `dsr_requests` with its legal due date (DSR_DEADLINE_DAYS, one month under
GDPR) — the log holds a masked number and a hash, never the number itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from config import settings
from core.phone import digits, mask_phone
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from core.tools.hotel import phone_variants

logger = logging.getLogger(__name__)

REQUESTS = "dsr_requests"
MAX_ROWS = 5_000

# kind → (collection, phone fields). Order = order in the export.
SOURCES: Dict[str, tuple[str, tuple[str, ...]]] = {
    "contacts": ("contacts", ("phone",)),
    "calls": ("calls", ("caller_phone",)),
    "appointments": ("appointments", ("phone",)),
    "messages": ("messages", ("caller_phone", "callback_number")),
    "leads": ("leads", ("phone",)),
    "conversations": ("conversations", ("phone",)),
    "bookings": ("bookings", ("guest_phone",)),
    "guests": ("guests", ("phone",)),
    "recalls": ("recalls", ("phone",)),
    "whatsapp": ("wa_outbox", ("to",)),
}
DELETE_KINDS = ("contacts", "messages", "leads", "conversations", "guests", "recalls", "whatsapp")
FUTURE_STATUSES = ("confirmed", "pending")

RequestKind = Literal["access", "erasure"]


class SubjectError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalise(phone: str) -> str:
    d = digits(phone)
    if len(d) < 7 or len(d) > 15:
        raise SubjectError("Enter the full phone number with country code, e.g. +15125550123")
    return f"+{d}"


def phone_hash(phone: str) -> str:
    """Keyed one-way hash: lets us show "same number as request X" without storing the number."""
    return hmac.new(settings.SECRET_KEY.encode(), digits(phone).encode(), hashlib.sha256).hexdigest()[:32]


def _query(tenant_id: str, fields: tuple[str, ...], variants: List[str]) -> Dict[str, Any]:
    ors = [{f: {"$in": variants}} for f in fields]
    return {"tenant_id": tenant_id, **(ors[0] if len(ors) == 1 else {"$or": ors})}


def _aware(v: Any) -> Any:
    return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v


async def find(db: Any, tenant: TenantConfig, phone: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    number = normalise(phone)
    variants = phone_variants(number)
    counts: Dict[str, int] = {}
    for kind, (coll, fields) in SOURCES.items():
        counts[kind] = await db[coll].count_documents(_query(tenant.id, fields, variants))  # scoped: _query(tenant.id)
    future = await db.appointments.count_documents({**_query(tenant.id, ("phone",), variants),  # scoped: _query(tenant.id)
                                                    "status": {"$in": list(FUTURE_STATUSES)}, "start": {"$gte": now}})
    recordings = await db.calls.count_documents({**_query(tenant.id, ("caller_phone",), variants),  # scoped: _query(tenant.id)
                                                 "recording.path": {"$exists": True}})
    return {"phone": mask_phone(number), "counts": counts, "total": sum(counts.values()),
            "future_appointments": future, "recordings": recordings}


def _strip(doc: Dict[str, Any]) -> Dict[str, Any]:
    from core.sensitive import drop_ciphertext, unseal

    doc = drop_ciphertext(unseal(doc))               # an access request gets the readable text
    out = {k: v for k, v in doc.items() if k not in ("tenant_id", "turns")}
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    return out


async def export(db: Any, tenant: TenantConfig, phone: str) -> Dict[str, Any]:
    """Access request: every record about the number, as plain JSON-able data."""
    number = normalise(phone)
    variants = phone_variants(number)
    data: Dict[str, List[Dict[str, Any]]] = {}
    for kind, (coll, fields) in SOURCES.items():
        rows = await db[coll].find(_query(tenant.id, fields, variants)).limit(MAX_ROWS).to_list(MAX_ROWS)  # scoped: _query(tenant.id)
        if rows:
            data[kind] = [_strip(r) for r in rows]
    return {"business": tenant.name, "subject_phone": number, "exported_at": utcnow().isoformat(),
            "note": "Call recordings are not included; they can be provided separately on request.",
            "data": data}


async def _delete_recording(rec: Dict[str, Any]) -> bool:
    from core.compliance.retention import delete_recording_object

    return await delete_recording_object(rec)


async def erase(db: Any, tenant: TenantConfig, phone: str, *, include_future: bool = False, suppress: bool = True,
                actor: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    number = normalise(phone)
    variants = phone_variants(number)
    future_q = {**_query(tenant.id, ("phone",), variants), "status": {"$in": list(FUTURE_STATUSES)},
                "start": {"$gte": now}}
    if not include_future and await db.appointments.count_documents(future_q):  # scoped: future_q has tenant_id
        raise SubjectError("This number has upcoming appointments. Cancel or move them first, or confirm "
                           "that they should be anonymised too.")
    counts: Dict[str, int] = {}
    for kind in DELETE_KINDS:
        coll, fields = SOURCES[kind]
        res = await db[coll].delete_many(_query(tenant.id, fields, variants))  # scoped: _query(tenant.id)
        counts[kind] = int(getattr(res, "deleted_count", 0) or 0)

    # Calls: keep the row for billing, remove everything personal.
    hashed = f"erased:{phone_hash(number)[:12]}"
    recordings = 0
    calls_q = _query(tenant.id, ("caller_phone",), variants)
    async for call in db.calls.find({**calls_q, "recording.path": {"$exists": True}}, {"recording": 1}):  # scoped: calls_q has tenant_id
        if await _delete_recording(call.get("recording") or {}):
            recordings += 1
    res = await db.calls.update_many(calls_q, {  # scoped: calls_q has tenant_id
        "$set": {"caller_phone": hashed, "erased_at": now},
        "$unset": {"transcript": "", "transcript_enc": "", "turns": "", "summary": "", "recording": ""}})
    counts["calls"] = int(getattr(res, "modified_count", 0) or 0)
    counts["recordings"] = recordings

    wipe = {"name": "", "phone": hashed, "email": "", "notes": "", "erased_at": now}
    res = await db.appointments.update_many(_query(tenant.id, ("phone",), variants), {"$set": wipe})  # scoped: _query(tenant.id)
    counts["appointments"] = int(getattr(res, "modified_count", 0) or 0)
    res = await db.bookings.update_many(_query(tenant.id, ("guest_phone",), variants),  # scoped: _query(tenant.id)
                                        {"$set": {"guest_name": "", "guest_phone": hashed, "guest_email": "",
                                                  "special_requests": "", "erased_at": now}})
    counts["bookings"] = int(getattr(res, "modified_count", 0) or 0)

    if suppress:
        from core.compliance.optout import record_opt_out

        await record_opt_out(db, number, channel="erasure", source=actor or "dsr", tenant_id=tenant.id,
                             note="kept on the suppression list after erasure")
        # The contact row was just deleted; keep only what suppression needs.
        await db.contacts.update_one({"tenant_id": tenant.id, "phone": number},  # scoped: tenant_id
                                     {"$set": {"do_not_contact": True, "do_not_contact_source": "erasure",
                                               "do_not_contact_at": now, "suppression_only": True},
                                      "$setOnInsert": {"tenant_id": tenant.id, "phone": number, "created_at": now,
                                                       "tags": [], "stats": {}}},
                                     upsert=True)
    logger.info("Erased data for %s in %s: %s", mask_phone(number), tenant.id, counts)
    out: Dict[str, Any] = {"phone": mask_phone(number), "counts": counts, "suppressed": suppress}
    from core.services import scheduling_provider

    if scheduling_provider.connected(tenant):
        # We hold only mirrors of the practice's diary; its records are the practice's to erase.
        name = scheduling_provider.provider_name(tenant).capitalize()
        out["practice_system"] = (f"Appointments and the patient record in {name} are not changed by this request; "
                                  f"erase or anonymise them in {name} if the request covers them.")
    return out


async def log_request(db: Any, tenant: TenantConfig, phone: str, kind: RequestKind, *, actor: str,
                      result: Dict[str, Any], received_at: Optional[datetime] = None, note: str = "") -> Dict[str, Any]:
    now = utcnow()
    received = _aware(received_at) or now
    doc = {"_id": new_id("dsr"), "tenant_id": tenant.id, "kind": kind, "phone_masked": mask_phone(normalise(phone)),
           "phone_hash": phone_hash(phone), "received_at": received,
           "due_at": received + timedelta(days=settings.DSR_DEADLINE_DAYS), "completed_at": now,
           "status": "completed", "actor": actor[:80], "note": note[:500],
           "result": json.loads(json.dumps(result.get("counts") or {"records": result.get("total", 0)}, default=str))}
    await db[REQUESTS].insert_one(doc)  # scoped: doc carries tenant_id
    return doc


async def list_requests(db: Any, tenant_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    q = {"tenant_id": tenant_id} if tenant_id else {}
    return await db[REQUESTS].find(q).sort("completed_at", -1).limit(limit).to_list(limit)  # cross-tenant: q is one business, or all for the platform log
