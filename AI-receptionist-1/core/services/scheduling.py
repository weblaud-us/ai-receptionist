"""
Scheduling (Roadmap Task 2.7): find slots, book, reschedule, cancel — for any vertical.

Double booking is impossible by construction: an appointment claims one `slot_locks` row per
resource per 5-minute block, under a unique (tenant_id, resource_id, block) index. If two callers
pick the same slot, the database refuses the second claim and that caller is offered the next
slot. Reads happen in ≤ 3 round trips (services+resources, appointments, exceptions); nothing
waits on an external calendar inside a call (Task 2.8 caches busy times in `resources.busy_cache`).

Dental practice-management systems (core/services/scheduling_provider.py): when a business has
connected Cliniko or Dentally, the PMS diary is the system of record. Slots come from the PMS, a
booking is written there first (after the local slot lock, so two of our calls can't race), then
mirrored here with `external: {provider, id, patient_id}`. Cancel and reschedule go to the PMS
too. A slow or failing PMS raises SchedulingError("pms_unavailable"), which tells the assistant
to take a callback instead.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pymongo.errors import BulkWriteError, DuplicateKeyError

from config import settings
from core.integrations import webhooks
from core.repositories import Repos
from core.repositories.base import utcnow
from core.services import availability as av
from core.services import pms_cache
from core.services import scheduling_provider as sp
from core.tenancy.ids import new_id
from core.tenancy.models import HoursEntry, TenantConfig

logger = logging.getLogger(__name__)

UTC = timezone.utc
ACTIVE_STATUSES = ["pending", "confirmed"]
# A booking takes its slot locks, then (with a PMS) waits up to ~8 s for the diary, then writes the
# appointment. Locks older than this with no live appointment were left by a crash (audit P0-16).
LOCK_ORPHAN_AFTER = timedelta(minutes=10)

PMS_DOWN = ("The practice diary isn't answering right now. Don't try again: take their name, number and preferred "
            "time with take_message and say the team will call back to confirm the booking.")
PMS_UNMAPPED = ("That appointment type can't be booked automatically. Take their details with take_message so the "
                "team can book it.")
PMS_NOT_SYNCED = ("No dentists are set up for online booking yet. Take their details with take_message so the team "
                  "can book it.")


def dob_fingerprint(tenant_id: str, dob: date) -> str:
    """HMAC of tenant + date of birth. Proves a caller knows the DOB without storing it: a database
    leak alone doesn't reveal it (the key is SECRET_KEY, not in the database)."""
    msg = f"dob:{tenant_id}:{dob.isoformat()}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


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
        self.pms = sp.provider_for(tenant)          # None = built-in scheduling

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
        fresh: bool = False,
    ) -> List[av.Slot]:
        """Open times. With a PMS, recent diary answers are reused (pms_cache); `fresh` asks the diary
        itself, which booking always does."""
        now = _aware(now or utcnow())
        sched = self.tenant.scheduling
        day_to = day_to or day_from
        last_allowed = now.astimezone(self.zone).date() + timedelta(days=sched.max_days_ahead)
        if day_to > last_allowed:
            day_to = last_allowed
        if day_from > day_to:
            return []
        if self.pms is not None:
            return await self._pms_slots(service=service, day_from=day_from, day_to=day_to, resource=resource,
                                         requested_at=requested_at, now=now, limit=limit, fresh=fresh)

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

    def _pms_service(self, service: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        out = dict(service or {})
        out.setdefault("duration_min", self.tenant.scheduling.default_duration_minutes)
        return out

    async def _pms_slots(self, *, service: Optional[Dict[str, Any]], day_from: date, day_to: date,
                         resource: Optional[Dict[str, Any]], requested_at: Optional[datetime], now: datetime,
                         limit: int, fresh: bool = False) -> List[av.Slot]:
        """Free times from the practice diary, for the dentists mirrored from it (pms_sync)."""
        assert self.pms is not None
        if self.pms.requires_type_mapping and not (service or {}).get("pms_id"):
            raise SchedulingError("pms_unmapped", PMS_UNMAPPED)
        mirrored = [r for r in await self.resources() if r.get("source") == self.pms.name and r.get("pms_id")]
        wanted_ids = set((service or {}).get("resource_ids") or [])
        candidates = [r for r in mirrored if not wanted_ids or r["_id"] in wanted_ids]
        if resource is not None:
            candidates = [r for r in candidates if r["_id"] == resource["_id"]]
            if not candidates:
                return []
        if not candidates:
            raise SchedulingError("pms_not_synced", PMS_NOT_SYNCED)
        try:
            raw = await pms_cache.slots(self.tenant.id, self.pms, self._pms_service(service), day_from, day_to,
                                        [str(r["pms_id"]) for r in candidates], zone=self.zone, fresh=fresh)
        except sp.PMSError as e:
            logger.warning("PMS availability failed for %s: %s", self.tenant.id, e)
            raise SchedulingError("pms_unavailable", PMS_DOWN)
        local_id = {str(r["pms_id"]): r["_id"] for r in candidates}
        earliest = now + timedelta(minutes=self.tenant.scheduling.min_notice_minutes)
        slots = [av.Slot(resource_id=local_id[s.practitioner_id], start=_aware(s.start), end=_aware(s.end))
                 for s in raw if s.practitioner_id in local_id and _aware(s.start) >= earliest]
        # The practice's own hours and closures still apply (split-shift lunch, public holidays).
        window_start = datetime.combine(day_from, datetime.min.time(), tzinfo=self.zone).astimezone(UTC) - timedelta(days=1)
        window_end = datetime.combine(day_to + timedelta(days=2), datetime.min.time(), tzinfo=self.zone).astimezone(UTC)
        exceptions = await self.repos.exceptions.find(
            {"start": {"$lt": window_end}, "end": {"$gt": window_start}}, limit=500)
        slots = av.within_hours(slots, business_hours=self.tenant.hours, tz=self.tz,
                                business_exceptions=_intervals([e for e in exceptions if not e.get("resource_id")]))
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
        date_of_birth: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Claim the slot (unique index) then write the appointment. Raises SchedulingError('taken').
        `date_of_birth` goes to the practice diary to find the right patient. Here only a keyed
        fingerprint is kept (`dob_check`), so a later caller can prove it's their booking (SEC-06)."""
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
                                           requested_at=start, now=now, limit=50, party_size=party_size,
                                           fresh=True)
        if resource_id == "any":
            # "Anyone" (P0-16): resolve to a real person free at that start BEFORE locking, so the lock
            # covers their chair. Plain "any" remains only for a business with no staff at all, and a
            # practice diary (PMS) always needs a real dentist.
            match = next((s for s in open_slots if s.start == start and s.resource_id != "any"), None)
            if match is not None:
                resource_id = match.resource_id
                resource_doc = await self.repos.resources.find_one({"_id": resource_id})
            elif self.pms is not None:
                raise SchedulingError("unknown_resource", "Pick one of the times from check_availability, then book it.")
        if not any(s.start == start and s.resource_id == resource_id for s in open_slots):
            await self._diary_changed()             # what we offered was stale: don't offer it again
            raise SchedulingError("taken", "That time was just taken or isn't available. Offer the next slot.")

        appointment_id = new_id("apt")
        contact = await self.repos.contacts.upsert_by_phone(phone, {"name": name, "email": email})
        # Lock the service duration only. The buffer is enforced by availability (it is advice for
        # the schedule, not a claim), so every slot availability offers can always be locked.
        blocks = av.lock_blocks(start, end)
        await self._claim(resource_id, blocks, appointment_id)

        external: Dict[str, Any] = {}
        if self.pms is not None:
            try:
                created = await self.pms.create_appointment(
                    sp.Patient(name=name, phone=phone, email=email, date_of_birth=date_of_birth),
                    self._pms_service(service), start, end,
                    str((resource_doc or {}).get("pms_id") or ""), notes)
            except sp.PMSError as e:
                await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
                logger.warning("PMS booking failed for %s: %s", self.tenant.id, e)
                if e.transient:
                    raise SchedulingError("pms_unavailable", PMS_DOWN)
                raise SchedulingError("pms_rejected", "The diary wouldn't take that booking. Offer another time, "
                                                      "or take a message so the team books it.")
            external = {"provider": self.pms.name, "id": created.id, "patient_id": created.patient_id}

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
            "external": external,
            "dob_check": dob_fingerprint(self.tenant.id, date_of_birth) if date_of_birth else None,
            "reminders": {"day_before": {"sent_at": None}, "hour_before": {"sent_at": None}},
            "cancelled_at": None, "cancel_reason": None,
        }
        try:
            await self.repos.appointments.insert_one(doc)
        except Exception:
            await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
            if external:
                # Audit P0-09: the PMS holds the booking but we don't. Undo it there, or the diary and
                # this system disagree and the freed slot gets booked twice.
                await self._undo_pms_booking(external, name=name, phone=phone, start=start)
            raise
        await self._diary_changed()
        await self.repos.contacts.update_one({"_id": contact.get("_id")}, {"$inc": {"stats.appointments": 1}})
        logger.info("Appointment %s booked (%s, %s)", appointment_id, doc["service_name"], start.isoformat())
        if self.tenant.vertical == "dental":
            from core.services import reactivation

            # A recalled patient booked: stop the follow-up now, and credit the recall text.
            await reactivation.on_booking(self.tenant, phone, appointment_id, db=self.repos.db)
        self._calendar("push", doc)
        webhooks.emit(self.tenant, "appointment.created", self._public(doc))
        return doc

    async def _diary_changed(self) -> None:
        if self.pms is not None:
            await pms_cache.invalidate(self.tenant.id)

    async def _claim(self, resource_id: str, blocks: List[datetime], appointment_id: str) -> None:
        """Take the slot's lock rows (unique per resource + block). On a clash, free locks a crashed
        booking left behind and try once more; a real booking always wins."""
        rows = [{"resource_id": resource_id, "block": b, "appointment_id": appointment_id} for b in blocks]
        for attempt in (1, 2):
            try:
                await self.repos.slot_locks.insert_many(rows)
                return
            except (DuplicateKeyError, BulkWriteError):
                await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
                if attempt == 2 or not await self._clear_orphan_locks(resource_id, blocks):
                    raise SchedulingError("taken", "That time was just booked by someone else. Offer the next slot.")

    async def _clear_orphan_locks(self, resource_id: str, blocks: List[datetime]) -> bool:
        """Delete locks on these blocks whose appointment is missing or no longer active, once they are
        old enough that the booking can't still be in flight. True when something was freed."""
        held = await self.repos.slot_locks.find({"resource_id": resource_id, "block": {"$in": blocks}}, limit=500)
        cutoff = utcnow() - LOCK_ORPHAN_AFTER
        stale: List[str] = []
        for appt_id in {str(h.get("appointment_id")) for h in held}:
            rows = [h for h in held if str(h.get("appointment_id")) == appt_id]
            if any(isinstance(h.get("created_at"), datetime) and _aware(h["created_at"]) > cutoff for h in rows):
                continue                                    # recent: a booking may be mid-flight
            appt = await self.repos.appointments.find_one({"appointment_id": appt_id}, {"status": 1})
            if appt and appt.get("status") in ACTIVE_STATUSES:
                continue                                    # a real booking holds it
            stale.append(appt_id)
        if not stale:
            return False
        await self.repos.slot_locks.delete_many({"appointment_id": {"$in": stale}})
        logger.warning("Freed orphaned slot locks for %s: %s", self.tenant.id, ", ".join(stale))
        return True

    async def _undo_pms_booking(self, external: Dict[str, Any], *, name: str, phone: str, start: datetime) -> None:
        assert self.pms is not None
        for attempt in (1, 2):
            try:
                await self.pms.cancel_appointment(str(external["id"]), "Booking could not be saved; rolled back")
                logger.warning("Rolled back PMS appointment %s for %s after a failed save", external["id"],
                               self.tenant.id)
                return
            except Exception as e:                          # noqa: BLE001 — any failure: retry, then alert
                logger.error("PMS rollback attempt %d failed for %s (%s): %s", attempt, self.tenant.id,
                             external.get("id"), e)
        from core.services import pms_compensation

        try:     # keep trying in the background for a day (tenant jobs tick); the owner is told now
            await pms_compensation.record(self.repos, external, start)
        except Exception:
            logger.error("Couldn't queue the PMS rollback for %s", self.tenant.id, exc_info=True)
        from core.notifications import fire, notify_owner

        when = start.astimezone(self.zone).strftime("%a %d %b %H:%M")
        fire(notify_owner(self.tenant, "on_new_booking",
                          f"Please check your practice diary: a booking for {name} ({phone}) at {when} was made in "
                          f"{self.pms.name} but couldn't be saved here, and undoing it failed. We'll keep trying to "
                          f"cancel it for 24 hours; cancel it in {self.pms.name} yourself if the patient "
                          "doesn't expect it.",
                          subject="Diary needs checking", urgent=True))

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

        if self.pms is not None or not calendar_sync.connected(self.tenant):
            return                                  # with a PMS, the PMS diary is the calendar
        from core.notifications import fire

        fn = calendar_sync.push_booking if action == "push" else calendar_sync.remove_booking
        fire(fn(self.tenant, dict(appointment)))

    async def cancel(self, appointment_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        appt = await self.repos.appointments.find_one({"appointment_id": appointment_id})
        if not appt:
            raise SchedulingError("not_found", "No such appointment.")
        if appt.get("status") in ("cancelled", "completed", "no_show"):
            raise SchedulingError("already_closed", f"That appointment is already {appt.get('status')}.")
        if not await self._pms_cancel(appt, reason or "Cancelled by phone"):
            raise SchedulingError("pms_unavailable", "The diary isn't answering, so the appointment is NOT cancelled "
                                                     "yet. Take a message so the team can cancel it.")
        await self.repos.appointments.update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": "cancelled", "cancelled_at": utcnow(), "cancel_reason": (reason or "")[:300],
                      "updated_at": utcnow()}})
        await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})
        await self._diary_changed()                 # the freed time can be offered straight away
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
        pms_cancelled = await self._pms_cancel(appt, "Rescheduled by phone")
        await self._diary_changed()
        if not pms_cancelled:
            from core.notifications import fire, notify_owner

            # The new time is booked; the old one is still in the diary. Ask the team to remove it.
            fire(notify_owner(self.tenant, "on_new_booking",
                              f"Please cancel the old appointment in your practice diary: {appt.get('name')} - "
                              f"{appt.get('phone')}, moved to a new time by phone.", subject="Diary needs a tidy-up"))
        self._calendar("remove", appt)
        webhooks.emit(self.tenant, "appointment.cancelled",
                      self._public({**appt, "status": "cancelled", "cancel_reason": "rescheduled",
                                    "rescheduled_to": new["appointment_id"]}))
        await self.repos.appointments.update_one({"appointment_id": new["appointment_id"]},
                                                 {"$set": {"rescheduled_from": appointment_id}})
        await self.repos.contacts.update_one({"_id": new.get("contact_id")}, {"$inc": {"stats.appointments": -1}})
        new["rescheduled_from"] = appointment_id
        return new

    async def _pms_cancel(self, appt: Dict[str, Any], reason: str) -> bool:
        """Cancel the PMS copy of an appointment. True when done (or there is none to cancel)."""
        ext = appt.get("external") or {}
        if not ext.get("provider") or not ext.get("id"):
            return True
        if self.pms is None or self.pms.name != ext["provider"]:
            logger.info("Appointment %s came from %s, which is no longer connected", appt.get("appointment_id"),
                        ext["provider"])
            return True
        try:
            await self.pms.cancel_appointment(str(ext["id"]), reason)
            return True
        except sp.PMSError as e:
            logger.warning("PMS cancel failed for %s: %s", self.tenant.id, e)
            return False

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


# ── Warm the diary cache when a call starts (plan A1.3) ─────────────────────

PREFETCH_DAYS = 7          # most callers ask about this week; the cache is per day, so any day in it hits
PREFETCH_SERVICES = 2      # the services this practice books most


async def prefetch_availability(repos: Repos, tenant: TenantConfig, *, now: Optional[datetime] = None) -> int:
    """Ask the practice diary for the coming week while the greeting plays, so the caller's first
    "when can I come in?" is answered from the cache instead of waiting on Cliniko / Dentally.
    Returns how many open times were cached. Never raises: a failed prefetch only means a live
    lookup later, exactly as before."""
    if not sp.connected(tenant):
        return 0
    now = _aware(now or utcnow())
    try:
        svc = SchedulingService(repos, tenant)
        assert svc.pms is not None
        services = [s for s in await svc.services()
                    if s.get("pms_id") or not svc.pms.requires_type_mapping]
        if not services:
            return 0
        counts = await repos.appointments.aggregate([
            {"$match": {"start": {"$gte": now - timedelta(days=90)}}},
            {"$group": {"_id": "$service_id", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}}, {"$limit": 20}])
        booked = {c["_id"]: int(c["n"]) for c in counts}
        ranked = sorted(enumerate(services), key=lambda e: (-booked.get(e[1].get("_id"), 0), e[0]))
        today = now.astimezone(svc.zone).date()
        total = 0
        for _, service in ranked[:PREFETCH_SERVICES]:
            found = await svc.find_slots(service=service, day_from=today,
                                         day_to=today + timedelta(days=PREFETCH_DAYS - 1), now=now, limit=1000)
            total += len(found)
        return total
    except Exception as e:                              # noqa: BLE001 — a warm-up must never hurt a call
        logger.info("Diary prefetch skipped for %s: %s", tenant.id, e)
        return 0
