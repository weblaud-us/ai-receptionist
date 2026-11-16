"""
Scheduling (Roadmap Task 2.7): find slots, book, reschedule, cancel — for any vertical.

Double booking is impossible by construction: an appointment claims one `slot_locks` row per
resource per 5-minute block, under a unique (tenant_id, resource_id, block) index. If two callers
pick the same slot, the database refuses the second claim and that caller is offered the next
slot. Reads happen in ≤ 3 round trips (services+resources, appointments, exceptions); nothing
waits on an external calendar inside a call (Task 2.8 caches busy times in `resources.busy_cache`).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pymongo.errors import BulkWriteError, DuplicateKeyError

from core.integrations import webhooks
from core.repositories import Repos
from core.repositories.base import utcnow
from core.services import availability as av
from core.tenancy.ids import new_id
from core.tenancy.models import HoursEntry, TenantConfig

logger = logging.getLogger(__name__)

UTC = timezone.utc
ACTIVE_STATUSES = ["pending", "confirmed"]


class SchedulingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _hours(raw: Any) -> Optional[List[HoursEntry]]:
    if raw is None:
        return None
    out: List[HoursEntry] = []
    for h in raw:
        try:
            out.append(HoursEntry.model_validate(h))
        except Exception:
            continue
    return out


def _intervals(raw: Any) -> List[av.Interval]:
    out: List[av.Interval] = []
    for r in raw or []:
        s, e = r.get("start"), r.get("end")
        if isinstance(s, datetime) and isinstance(e, datetime) and e > s:
            out.append(av.Interval(_aware(s), _aware(e)))
    return out


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("&", "and").split())


class SchedulingService:
    def __init__(self, repos: Repos, tenant: TenantConfig) -> None:
        self.repos = repos
        self.tenant = tenant
        self.tz = tenant.timezone
        self.zone = ZoneInfo(tenant.timezone) if tenant.timezone else ZoneInfo("UTC")

    # ── Lookups ────────────────────────────────────────────────────────────

    async def services(self) -> List[Dict[str, Any]]:
        return await self.repos.services.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)])

    async def resources(self) -> List[Dict[str, Any]]:
        return await self.repos.resources.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)])

    @staticmethod
    def match_service(services: List[Dict[str, Any]], wanted: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fuzzy, forgiving match on what the caller said ('a haircut' → 'Haircut & blow-dry')."""
        if not services:
            return None
        if not wanted:
            return services[0] if len(services) == 1 else None
        w = _norm(wanted)
        for s in services:
            if _norm(s.get("name", "")) == w or s.get("_id") == wanted:
                return s
        for s in services:
            n = _norm(s.get("name", ""))
            if w in n or n in w:
                return s
        words = set(w.split())
        best, score = None, 0
        for s in services:
            overlap = len(words & set(_norm(s.get("name", "")).split()))
            if overlap > score:
                best, score = s, overlap
        return best

    @staticmethod
    def match_resource(resources: List[Dict[str, Any]], wanted: Optional[str]) -> Optional[Dict[str, Any]]:
        if not wanted or _norm(wanted) in ("any", "anyone", "whoever", "no preference", "first available"):
            return None
        w = _norm(wanted)
        for r in resources:
            if _norm(r.get("name", "")) == w or r.get("_id") == wanted:
                return r
        for r in resources:
            if w in _norm(r.get("name", "")):
                return r
        return None

    # ── Availability ───────────────────────────────────────────────────────

    async def find_slots(
        self,
        *,
        service: Optional[Dict[str, Any]],
        day_from: date,
        day_to: Optional[date] = None,
        resource: Optional[Dict[str, Any]] = None,
        requested_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
        limit: int = 3,
        party_size: Optional[int] = None,
    ) -> List[av.Slot]:
        now = _aware(now or utcnow())
        sched = self.tenant.scheduling
        day_to = day_to or day_from
        last_allowed = now.astimezone(self.zone).date() + timedelta(days=sched.max_days_ahead)
        if day_to > last_allowed:
            day_to = last_allowed
        if day_from > day_to:
            return []

        duration = timedelta(minutes=int((service or {}).get("duration_min") or sched.default_duration_minutes))
        buffer = timedelta(minutes=int((service or {}).get("buffer_min") or 0))

        all_resources = await self.resources()
        wanted_ids = set((service or {}).get("resource_ids") or [])
        candidates = [r for r in all_resources if not wanted_ids or r["_id"] in wanted_ids]
        if resource is not None:
            candidates = [r for r in candidates if r["_id"] == resource["_id"]]
        if party_size:
            candidates = [r for r in candidates if int(r.get("capacity") or 1) >= party_size]
        if not candidates:
            if all_resources or resource is not None or wanted_ids:
                return []
            candidates = [{"_id": "any", "name": "", "hours": None}]   # business with no staff model

        window_start = datetime.combine(day_from, datetime.min.time(), tzinfo=self.zone).astimezone(UTC) - timedelta(days=1)
        window_end = datetime.combine(day_to + timedelta(days=2), datetime.min.time(), tzinfo=self.zone).astimezone(UTC)

        appointments = await self.repos.appointments.find(
            {"status": {"$in": ACTIVE_STATUSES}, "start": {"$lt": window_end}, "end": {"$gt": window_start}},
            projection={"resource_id": 1, "start": 1, "end": 1}, limit=5000)
        exceptions = await self.repos.exceptions.find(
            {"start": {"$lt": window_end}, "end": {"$gt": window_start}}, limit=500)

        by_resource: Dict[str, List[Dict[str, Any]]] = {}
        for a in appointments:
            by_resource.setdefault(str(a.get("resource_id")), []).append(a)
        business_exc = _intervals([e for e in exceptions if not e.get("resource_id")])

        windows: List[av.ResourceWindow] = []
        for r in candidates:
            rid = r["_id"]
            busy = av.appointment_busy(by_resource.get(rid, []), buffer)
            busy += _intervals(r.get("busy_cache") or [])            # external calendar (Task 2.8)
            windows.append(av.ResourceWindow(
                resource_id=rid,
                hours=_hours(r.get("hours")),
                busy=busy,
                exceptions=_intervals([e for e in exceptions if e.get("resource_id") == rid]),
            ))

        slots = av.free_slots(
            business_hours=self.tenant.hours, tz=self.tz, resources=windows, duration=duration,
            day_from=day_from, day_to=day_to, now=now,
            min_notice=timedelta(minutes=sched.min_notice_minutes),
            step=timedelta(minutes=sched.slot_step_minutes), business_exceptions=business_exc,
        )
        return av.rank(slots, requested_at, limit)

    # ── Booking ────────────────────────────────────────────────────────────

    async def book(
        self,
        *,
        resource_id: str,
        start: datetime,
        service: Optional[Dict[str, Any]],
        name: str,
        phone: str,
        notes: str = "",
        source: str = "voice",
        call_id: Optional[str] = None,
        party_size: Optional[int] = None,
        email: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Claim the slot (unique index) then write the appointment. Raises SchedulingError('taken')."""
        now = _aware(now or utcnow())
        start = _aware(start)
        sched = self.tenant.scheduling
        duration = timedelta(minutes=int((service or {}).get("duration_min") or sched.default_duration_minutes))
        end = start + duration
        if start < now + timedelta(minutes=sched.min_notice_minutes):
            raise SchedulingError("too_soon", "That time is too soon or already past. Offer the next available slot.")

        # Is the slot still inside opening hours / not blocked? Re-run availability for that day.
        resource_doc = None
        if resource_id != "any":
            resource_doc = await self.repos.resources.find_one({"_id": resource_id})
            if not resource_doc:
                raise SchedulingError("unknown_resource", "That staff member or resource doesn't exist. Check availability again.")
        local_day = start.astimezone(self.zone).date()
        open_slots = await self.find_slots(service=service, day_from=local_day, resource=resource_doc,
                                           requested_at=start, now=now, limit=50, party_size=party_size)
        if not any(s.start == start and s.resource_id == resource_id for s in open_slots):
            raise SchedulingError("taken", "That time was just taken or isn't available. Offer the next slot.")

        appointment_id = new_id("apt")
        contact = await self.repos.contacts.upsert_by_phone(phone, {"name": name, "email": email})
        # Lock the service duration only. The buffer is enforced by availability (it is advice for
        # the schedule, not a claim), so every slot availability offers can always be locked.
        blocks = av.lock_blocks(start, end)
        try:
            await self.repos.slot_locks.insert_many(
                [{"resource_id": resource_id, "block": b, "appointment_id": appointment_id} for b in blocks])
        except (DuplicateKeyError, BulkWriteError):
            await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
            raise SchedulingError("taken", "That time was just booked by someone else. Offer the next slot.")

        doc = {
            "_id": appointment_id,
            "appointment_id": appointment_id,
            "contact_id": contact.get("_id"),
            "phone": phone,
            "name": name,
            "email": email,
            "service_id": (service or {}).get("_id"),
            "service_name": (service or {}).get("name"),
            "resource_id": resource_id,
            "resource_name": (resource_doc or {}).get("name"),
            "start": start, "end": end,
            "status": "confirmed",
            "source": source,
            "call_id": call_id,
            "notes": (notes or "")[:1000],
            "party_size": party_size,
            "price": (service or {}).get("price"),
            "external": {},
            "reminders": {"day_before": {"sent_at": None}, "hour_before": {"sent_at": None}},
            "cancelled_at": None, "cancel_reason": None,
        }
        try:
            await self.repos.appointments.insert_one(doc)
        except Exception:
            await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
            raise
        await self.repos.contacts.update_one({"_id": contact.get("_id")}, {"$inc": {"stats.appointments": 1}})
        logger.info("Appointment %s booked (%s, %s)", appointment_id, doc["service_name"], start.isoformat())
        self._calendar("push", doc)
        webhooks.emit(self.tenant, "appointment.created", self._public(doc))
        return doc

    @staticmethod
    def _public(appointment: Dict[str, Any]) -> Dict[str, Any]:
        """The appointment as sent to webhooks: no internal ids beyond the appointment's own."""
        keep = ("appointment_id", "name", "phone", "email", "service_id", "service_name", "resource_id",
                "resource_name", "start", "end", "status", "source", "call_id", "notes", "party_size", "price",
                "cancel_reason", "rescheduled_from", "rescheduled_to")
        return {k: appointment.get(k) for k in keep if k in appointment}

    def _calendar(self, action: str, appointment: Dict[str, Any]) -> None:
        """Mirror to the owner's Google Calendar in the background (Task 2.8). Never blocks a booking."""
        from core.services import calendar_sync

        if not calendar_sync.connected(self.tenant):
            return
        from core.notifications import fire

        fn = calendar_sync.push_booking if action == "push" else calendar_sync.remove_booking
        fire(fn(self.tenant, dict(appointment)))

    async def cancel(self, appointment_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        appt = await self.repos.appointments.find_one({"appointment_id": appointment_id})
        if not appt:
            raise SchedulingError("not_found", "No such appointment.")
        if appt.get("status") in ("cancelled", "completed", "no_show"):
            raise SchedulingError("already_closed", f"That appointment is already {appt.get('status')}.")
        await self.repos.appointments.update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": "cancelled", "cancelled_at": utcnow(), "cancel_reason": (reason or "")[:300],
                      "updated_at": utcnow()}})
        await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
        appt["status"] = "cancelled"
        appt["cancel_reason"] = (reason or "")[:300]
        self._calendar("remove", appt)
        webhooks.emit(self.tenant, "appointment.cancelled", self._public(appt))
        return appt

    async def reschedule(self, appointment_id: str, *, resource_id: str, start: datetime,
                         now: Optional[datetime] = None) -> Dict[str, Any]:
        """Atomic enough: lock the new slot first; only then release the old one."""
        appt = await self.repos.appointments.find_one({"appointment_id": appointment_id})
        if not appt or appt.get("status") not in ACTIVE_STATUSES:
            raise SchedulingError("not_found", "No active appointment with that ID.")
        service = await self.repos.services.find_one({"_id": appt.get("service_id")}) if appt.get("service_id") else None
        new = await self.book(resource_id=resource_id, start=start, service=service, name=appt.get("name", ""),
                              phone=appt.get("phone", ""), notes=appt.get("notes", ""), source=appt.get("source", "voice"),
                              call_id=appt.get("call_id"), party_size=appt.get("party_size"), email=appt.get("email"),
                              now=now)
        await self.repos.appointments.update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": "cancelled", "cancelled_at": utcnow(), "cancel_reason": "rescheduled",
                      "rescheduled_to": new["appointment_id"], "updated_at": utcnow()}})
        await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
        self._calendar("remove", appt)
        webhooks.emit(self.tenant, "appointment.cancelled",
                      self._public({**appt, "status": "cancelled", "cancel_reason": "rescheduled",
                                    "rescheduled_to": new["appointment_id"]}))
        await self.repos.appointments.update_one({"appointment_id": new["appointment_id"]},
                                                 {"$set": {"rescheduled_from": appointment_id}})
        await self.repos.contacts.update_one({"_id": new.get("contact_id")}, {"$inc": {"stats.appointments": -1}})
        new["rescheduled_from"] = appointment_id
        return new

    async def upcoming_for_phone(self, phone_variants: List[str], *, now: Optional[datetime] = None,
                                 limit: int = 5) -> List[Dict[str, Any]]:
        now = _aware(now or utcnow())
        return await self.repos.appointments.find(
            {"phone": {"$in": phone_variants}, "status": {"$in": ACTIVE_STATUSES}, "end": {"$gte": now}},
            sort=[("start", 1)], limit=limit)

    # ── Speech helpers ─────────────────────────────────────────────────────

    def describe(self, slot: av.Slot, resources_by_id: Dict[str, Dict[str, Any]], *, today: Optional[date] = None) -> str:
        text = av.spoken(slot.start, self.tz, today=today)
        name = (resources_by_id.get(slot.resource_id) or {}).get("name")
        return f"{text} with {name}" if name else text

    def local_today(self, now: Optional[datetime] = None) -> date:
        return _aware(now or utcnow()).astimezone(self.zone).date()

    def parse_local(self, day: date, hhmm: Optional[str]) -> Optional[datetime]:
        """'15:30' on a business-local day → UTC datetime (None when no time given)."""
        if not hhmm:
            return None
        try:
            h, m = (int(x) for x in hhmm.strip().split(":")[:2])
            return datetime.combine(day, datetime.min.time().replace(hour=h, minute=m), tzinfo=self.zone).astimezone(UTC)
        except (ValueError, AttributeError):
            return None
