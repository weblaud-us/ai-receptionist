"""One repository per tenant collection, plus the platform-level (unscoped) ones."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.repositories.base import TenantRepo, utcnow
from core.tenancy.ids import new_id


class ServicesRepo(TenantRepo):
    collection = "services"


class ResourcesRepo(TenantRepo):
    collection = "resources"


class ScheduleExceptionsRepo(TenantRepo):
    collection = "schedule_exceptions"


class AppointmentsRepo(TenantRepo):
    collection = "appointments"


class SlotLocksRepo(TenantRepo):
    collection = "slot_locks"


class ContactsRepo(TenantRepo):
    collection = "contacts"

    async def upsert_by_phone(self, phone: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        now = utcnow()
        sets = {k: v for k, v in fields.items() if v not in (None, "")}
        sets["last_seen_at"] = now
        await self.update_one(
            {"phone": phone},
            # String _id like every other dashboard entity — the API looks contacts up by the id it returned.
            {"$set": sets, "$setOnInsert": {"_id": new_id("con"), "phone": phone, "first_seen_at": now, "created_at": now,
                                            "tags": [], "do_not_contact": False,
                                            "stats": {"calls": 0, "appointments": 0, "no_shows": 0}}},
            upsert=True,
        )
        return await self.find_one({"phone": phone}) or {}


class CallsRepo(TenantRepo):
    collection = "calls"


class MessagesRepo(TenantRepo):
    collection = "messages"


class KnowledgeRepo(TenantRepo):
    collection = "knowledge_items"


class UnansweredRepo(TenantRepo):
    collection = "unanswered_questions"


class LeadsRepo(TenantRepo):
    collection = "leads"


class ConversationsRepo(TenantRepo):
    collection = "conversations"


class UsageEventsRepo(TenantRepo):
    collection = "usage_events"


class AuditLogRepo(TenantRepo):
    collection = "audit_log"

    async def record(self, *, user_id: Optional[str], action: str, target_type: str, target_id: str = "",
                     diff: Optional[Dict[str, Any]] = None, ip: str = "") -> None:
        await self.insert_one({"user_id": user_id, "action": action, "target_type": target_type,
                               "target_id": target_id, "diff": diff or {}, "ip": ip, "at": utcnow()})


class PhoneNumbersRepo(TenantRepo):
    collection = "phone_numbers"


class OwnerFeedbackRepo(TenantRepo):
    collection = "owner_feedback"          # Phase 7: the owner's 1–10 scores


class RecallsRepo(TenantRepo):
    collection = "recalls"                 # dental patient recall: one row per patient per recall cycle


# Hotel vertical plug-in collections (pre-Phase-2 shapes, now with tenant_id)
class RoomsRepo(TenantRepo):
    collection = "rooms"


class BookingsRepo(TenantRepo):
    collection = "bookings"


class PmsCompensationsRepo(TenantRepo):
    collection = "pms_compensations"       # PMS bookings we must still cancel (audit P0-09); see pms_compensation.py


class WaOutboxRepo(TenantRepo):
    collection = "wa_outbox"               # WhatsApp messages staff sent from the dashboard + Meta delivery status


class Repos:
    """All of one tenant's repositories, built once per request / call."""

    def __init__(self, db: Any, tenant_id: str) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.services = ServicesRepo(db, tenant_id)
        self.resources = ResourcesRepo(db, tenant_id)
        self.exceptions = ScheduleExceptionsRepo(db, tenant_id)
        self.appointments = AppointmentsRepo(db, tenant_id)
        self.slot_locks = SlotLocksRepo(db, tenant_id)
        self.contacts = ContactsRepo(db, tenant_id)
        self.calls = CallsRepo(db, tenant_id)
        self.messages = MessagesRepo(db, tenant_id)
        self.knowledge = KnowledgeRepo(db, tenant_id)
        self.unanswered = UnansweredRepo(db, tenant_id)
        self.leads = LeadsRepo(db, tenant_id)
        self.conversations = ConversationsRepo(db, tenant_id)
        self.usage = UsageEventsRepo(db, tenant_id)
        self.audit = AuditLogRepo(db, tenant_id)
        self.phone_numbers = PhoneNumbersRepo(db, tenant_id)
        self.feedback = OwnerFeedbackRepo(db, tenant_id)
        self.recalls = RecallsRepo(db, tenant_id)
        self.rooms = RoomsRepo(db, tenant_id)
        self.bookings = BookingsRepo(db, tenant_id)
        self.wa_outbox = WaOutboxRepo(db, tenant_id)
        self.pms_compensations = PmsCompensationsRepo(db, tenant_id)


# ── Platform-level collections (not tenant-scoped) ───────────────────────────

class TenantsStore:
    def __init__(self, db: Any) -> None:
        self._c = db.tenants

    async def get(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        return await self._c.find_one({"_id": tenant_id})

    async def by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return await self._c.find_one({"slug": slug.lower()})

    async def list(self, *, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"status": status} if status else {}
        return await self._c.find(q).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def insert(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        now = utcnow()
        doc.setdefault("created_at", now)
        doc["updated_at"] = now
        await self._c.insert_one(doc)
        return doc

    async def update(self, tenant_id: str, sets: Dict[str, Any]) -> bool:
        sets = {k: v for k, v in sets.items() if k not in ("_id",)}
        sets["updated_at"] = utcnow()
        res = await self._c.update_one({"_id": tenant_id}, {"$set": sets})
        return res.matched_count > 0

    async def count(self) -> int:
        return await self._c.count_documents({})


class UsersStore:
    def __init__(self, db: Any) -> None:
        self._c = db.users

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self._c.find_one({"_id": user_id})

    async def by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return await self._c.find_one({"email": email.strip().lower()})

    async def for_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        return await self._c.find({"memberships.tenant_id": tenant_id}).to_list(length=500)

    async def insert(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["email"] = doc["email"].strip().lower()
        doc.setdefault("created_at", utcnow())
        await self._c.insert_one(doc)
        return doc

    async def update(self, user_id: str, update: Dict[str, Any]) -> bool:
        res = await self._c.update_one({"_id": user_id}, update)
        return res.matched_count > 0


class PhoneNumbersStore:
    """Routing table: number → tenant. Platform-level because numbers move between tenants."""

    def __init__(self, db: Any) -> None:
        self._c = db.phone_numbers

    async def get(self, e164: str) -> Optional[Dict[str, Any]]:
        return await self._c.find_one({"e164": e164})

    async def for_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        return await self._c.find({"tenant_id": tenant_id}).to_list(length=100)

    async def assign(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc.setdefault("assigned_at", utcnow())
        doc.setdefault("status", "active")
        await self._c.update_one({"e164": doc["e164"]}, {"$set": doc}, upsert=True)
        return doc

    async def release(self, e164: str) -> bool:
        res = await self._c.update_one({"e164": e164}, {"$set": {"status": "released", "released_at": utcnow()}})
        return res.matched_count > 0

    async def list(self, limit: int = 500) -> List[Dict[str, Any]]:
        return await self._c.find({}).sort("assigned_at", -1).limit(limit).to_list(length=limit)
