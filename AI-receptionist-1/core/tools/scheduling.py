"""
Generic scheduling tools (Roadmap Task 2.7) — appointments for salons, clinics, restaurants,
home-service estimate visits, professional offices… Thin wrappers over SchedulingService that
return spoken-friendly text plus machine IDs the model passes back.

Ownership (Task 0.9): a caller may see, move or cancel an appointment only if it was booked
with their phone number, or they give its ID and the name on it.

Dental practices (health records, audit SEC-06): caller ID alone is never enough, because families
share one phone. The caller gives the patient's name and date of birth; it must match the booking's
DOB fingerprint, or, for a booking made without a DOB, the name plus the number it was booked with.
"""

from __future__ import annotations

import hmac
import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.notifications import fire, notify_contact, notify_owner
from core.phone import phones_match
from core.services import availability as av
from core.services.scheduling import ACTIVE_STATUSES, SchedulingError, SchedulingService, dob_fingerprint
from core.tools.hotel import _names_match, _norm_name, phone_variants

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

SCHEDULING_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "check_availability",
        "description": ("Find open appointment times. Call before offering any time. Returns the three "
                        "nearest open slots to the requested day/time, each with a slot_id."),
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "The service wanted, as the caller said it"},
            "date": {"type": "string", "description": "Requested day, YYYY-MM-DD (business-local)"},
            "time": {"type": "string", "description": ("Requested time HH:MM, 24-hour, if any. For a part of the day "
                                                       "use morning 09:00, afternoon 13:00, evening 17:00")},
            "date_to": {"type": "string", "description": "Last acceptable day YYYY-MM-DD, for 'any day this week'"},
            "resource": {"type": "string", "description": "Preferred staff member / table / room, or 'anyone'"},
            "party_size": {"type": "integer", "description": "Restaurants: number of people"},
        }, "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "book_appointment",
        "description": ("Book one of the slots returned by check_availability. Read back the day, time, "
                        "service and name and get a clear yes first."),
        "parameters": {"type": "object", "properties": {
            "slot_id": {"type": "string", "description": "slot_id from check_availability"},
            "service": {"type": "string"},
            "name": {"type": "string", "description": "Full name"},
            "phone": {"type": "string", "description": "Contact number with country code, if different from the caller's"},
            "notes": {"type": "string", "description": "Anything the team should know"},
            "party_size": {"type": "integer"},
            "date_of_birth": {"type": "string",
                              "description": "YYYY-MM-DD. Ask existing patients, so the right record is used"},
        }, "required": ["slot_id", "name"]}}},
    {"type": "function", "function": {
        "name": "find_my_appointments",
        "description": ("The caller's upcoming appointments. Found by their phone number; dental practices "
                        "also need the patient's name and date of birth."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Patient's full name (dental: always ask)"},
            "date_of_birth": {"type": "string", "description": "YYYY-MM-DD. Dental: always ask, to confirm it's their booking"},
        }}}},
    {"type": "function", "function": {
        "name": "reschedule_appointment",
        "description": "Move an appointment to a new slot_id from check_availability. Confirm the new time first.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "string"},
            "new_slot_id": {"type": "string"},
            "name": {"type": "string", "description": "Patient's full name (dental: always ask)"},
            "date_of_birth": {"type": "string", "description": "YYYY-MM-DD. Dental: always ask, to confirm it's their booking"},
        }, "required": ["appointment_id", "new_slot_id"]}}},
    {"type": "function", "function": {
        "name": "cancel_appointment",
        "description": "Cancel an appointment. Offer to reschedule first; confirm before cancelling.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "string"},
            "reason": {"type": "string"},
            "name": {"type": "string", "description": "Patient's full name (dental: always ask)"},
            "date_of_birth": {"type": "string", "description": "YYYY-MM-DD. Dental: always ask, to confirm it's their booking"},
        }, "required": ["appointment_id"]}}},
]

NOT_FOUND = {"error": "not_found",
             "message": ("No matching appointment. If they're calling from a different number, ask for "
                         "the appointment ID and the name it was booked under.")}


IDENTITY_REQUIRED = {"error": "identity_required",
                     "message": "First ask for the patient's full name and date of birth, then try again with both."}
NOT_VERIFIED = {"error": "not_verified",
                "message": ("No booking matches those details. Don't guess, and don't say what is or isn't on file. "
                            "Offer take_message so the front desk can call them back.")}


def _identity_checked(ctx: "ToolContext") -> bool:
    """Health practices protect bookings with name + DOB, not caller ID (SEC-06)."""
    return ctx.tenant_cfg.vertical == "dental"


def _name_overlaps(a: Optional[str], b: Optional[str]) -> bool:
    """First name or surname in common: speech-to-text often hears 'Jon' for 'John' on one of them."""
    ta = {w for w in _norm_name(a) if len(w) > 1}
    return bool(ta & {w for w in _norm_name(b) if len(w) > 1})


def _verified(appt: Dict[str, Any], ctx: "ToolContext", name: Optional[str], dob: Optional[date]) -> bool:
    if not _name_overlaps(appt.get("name"), name):
        return False
    fp = appt.get("dob_check")
    if fp:
        return dob is not None and hmac.compare_digest(str(fp), dob_fingerprint(ctx.tenant_cfg.id, dob))
    # Booked without a DOB (from the dashboard, or before DOBs were asked): name + the booking's number.
    return bool(ctx.caller_phone) and phones_match(appt.get("phone"), ctx.caller_phone)


def _service(ctx: "ToolContext") -> SchedulingService:
    return SchedulingService(ctx.repos(), ctx.tenant_cfg)


def _parse_date(value: Any, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, AttributeError):
        return fallback


def _owned(appt: Dict[str, Any], ctx: "ToolContext", name: Optional[str]) -> bool:
    if ctx.caller_phone and phones_match(appt.get("phone"), ctx.caller_phone):
        return True
    return _names_match(appt.get("name"), name)


def _say(svc: SchedulingService, appt: Dict[str, Any], today: date) -> str:
    when = av.spoken(appt["start"], svc.tz, today=today)
    parts = [appt.get("service_name") or "appointment", when]
    if appt.get("resource_name"):
        parts.append(f"with {appt['resource_name']}")
    return " ".join(parts)


async def check_availability(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    svc = _service(ctx)
    today = svc.local_today(ctx.now)
    services = await svc.services()
    service = svc.match_service(services, args.get("service"))
    if services and service is None and args.get("service"):
        names = ", ".join(s["name"] for s in services[:8])
        return {"error": "unknown_service", "message": f"That service isn't offered. The services are: {names}. Ask which one they'd like."}
    if len(services) > 1 and service is None:
        names = ", ".join(s["name"] for s in services[:8])
        return {"error": "service_required", "message": f"Ask which service they want. Options: {names}."}

    day = _parse_date(args.get("date"), today)
    if day < today:
        return {"error": "date_in_past", "message": f"That date has passed. Today is {today.isoformat()}; ask for a new date."}
    day_to = _parse_date(args.get("date_to"), day)
    requested = svc.parse_local(day, args.get("time"))
    resources = await svc.resources()
    resource = svc.match_resource(resources, args.get("resource"))
    if args.get("resource") and resource is None and resources and str(args["resource"]).lower() not in ("anyone", "any"):
        names = ", ".join(r["name"] for r in resources[:8])
        return {"error": "unknown_resource", "message": f"No one by that name. Available: {names}, or anyone."}
    party = args.get("party_size")
    if party and int(party) > ctx.tenant_cfg.scheduling.max_party_size:
        return {"error": "party_too_large",
                "message": f"Parties over {ctx.tenant_cfg.scheduling.max_party_size} need the manager — take a message."}

    try:
        slots = await svc.find_slots(service=service, day_from=day, day_to=day_to, resource=resource,
                                     requested_at=requested, now=ctx.now, party_size=int(party) if party else None)
        later = [] if slots else await svc.find_slots(       # look ahead a week: offer something, not a dead end
            service=service, day_from=day_to + timedelta(days=1), day_to=day_to + timedelta(days=7),
            resource=resource, now=ctx.now, party_size=int(party) if party else None)
    except SchedulingError as e:            # e.g. the practice's diary (PMS) isn't answering
        return {"error": e.code, "message": e.message}
    by_id = {r["_id"]: r for r in resources}
    if not slots:
        return {"available": False, "slots": [],
                "alternatives": [{"slot_id": s.slot_id, "say": svc.describe(s, by_id, today=today)} for s in later],
                "message": "Nothing free then. Offer the alternatives, or another day."}
    from core.prompts.builder import price_text

    return {
        "available": True,
        "service": (service or {}).get("name"),
        "duration_min": (service or {}).get("duration_min"),
        "price": price_text(service or {}, ctx.tenant_cfg.vertical) or None,
        "slots": [{"slot_id": s.slot_id, "say": svc.describe(s, by_id, today=today),
                   "resource": (by_id.get(s.resource_id) or {}).get("name")} for s in slots],
        "message": "Offer these times in words. When they choose, confirm name (and number) then book_appointment.",
    }


def _parse_dob(value: Any, now: Optional[datetime]) -> Optional[date]:
    """A plausible date of birth, or None (a misheard one must never match the wrong record)."""
    try:
        dob = date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None
    today = (now or datetime.now(timezone.utc)).date()
    return dob if date(today.year - 120, 1, 1) <= dob <= today else None


async def book_appointment(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    svc = _service(ctx)
    decoded = av.decode_slot_id(str(args.get("slot_id") or ""))
    if not decoded:
        return {"error": "bad_slot", "message": "Use a slot_id from check_availability."}
    resource_id, start = decoded
    name = str(args.get("name") or "").strip()[:120]
    phone = str(args.get("phone") or ctx.caller_phone or "").strip()
    if not name:
        return {"error": "name_required", "message": "Ask for their full name."}
    if not phone:
        return {"error": "phone_required", "message": "Ask for a contact number."}
    services = await svc.services()
    service = svc.match_service(services, args.get("service"))
    if services and service is None:
        return {"error": "service_required", "message": "Ask which service, then book again."}
    dob = _parse_dob(args.get("date_of_birth"), ctx.now)
    try:
        appt = await svc.book(resource_id=resource_id, start=start, service=service, name=name, phone=phone,
                              notes=str(args.get("notes") or ""), source=ctx.channel if ctx.channel != "browser" else "web",
                              call_id=ctx.call_id, party_size=int(args["party_size"]) if args.get("party_size") else None,
                              now=ctx.now, date_of_birth=dob)
    except SchedulingError as e:
        return {"error": e.code, "message": e.message}
    t = ctx.tenant_cfg
    today = svc.local_today(ctx.now)
    when = av.spoken(appt["start"], svc.tz, today=today)
    label = appt.get("service_name") or "appointment"
    fire(notify_owner(t, "on_new_booking",
                      f"New booking: {label} {when}" + (f" with {appt['resource_name']}" if appt.get("resource_name") else "")
                      + f"\n{name} — {phone}" + (f"\nNotes: {appt['notes']}" if appt.get("notes") else ""),
                      subject="New booking"))
    if phone:
        # The caller gave this number to book: a transactional confirmation. Record the consent basis.
        await ctx.repos().contacts.update_one({"_id": appt.get("contact_id")}, {"$set": {"consent.sms": {
            "granted": True, "at": datetime.now(timezone.utc), "source": f"booking_{ctx.channel}", "call_id": ctx.call_id,
            "scope": "transactional"}}})
        from core.services.appointment_ics import ics_url

        cal = ics_url(t.id, appt["appointment_id"])          # plan 4.5: add it to their calendar
        fire(notify_contact(t, phone, f"{t.name}: your {label} is booked for {when}. Reply here or call {t.phone} "
                                      "to change it." + (f" Add it to your calendar: {cal}" if cal else "")))
    return {"success": True, "appointment_id": appt["appointment_id"], "when": when, "service": label,
            "with": appt.get("resource_name"),
            "message": f"Booked: {label} {when}. Tell them a confirmation is on its way, then ask if there's anything else."}


async def find_my_appointments(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    if _identity_checked(ctx):
        return await _find_verified(ctx, args)
    if not ctx.caller_phone:
        return {"found": False, "message": "The caller's number is unknown. Ask for the appointment ID and the name on it."}
    svc = _service(ctx)
    rows = await svc.upcoming_for_phone(phone_variants(ctx.caller_phone), now=ctx.now)
    if not rows:
        return {"found": False, "message": "No upcoming appointments on this number."}
    today = svc.local_today(ctx.now)
    return {"found": True, "appointments": [
        {"appointment_id": a["appointment_id"], "say": _say(svc, a, today), "status": a.get("status")} for a in rows]}


async def _find_verified(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name or not args.get("date_of_birth"):
        return IDENTITY_REQUIRED
    dob = _parse_dob(args.get("date_of_birth"), ctx.now)
    if dob is None:
        return {"error": "bad_date_of_birth", "message": "Ask for the date of birth again: day, month and year."}
    svc = _service(ctx)
    now = ctx.now or utcnow()
    rows = await ctx.repos().appointments.find(      # booked with this DOB, from any number (a parent's phone)
        {"dob_check": dob_fingerprint(ctx.tenant_cfg.id, dob), "status": {"$in": ACTIVE_STATUSES},
         "end": {"$gte": now}},
        sort=[("start", 1)], limit=5)
    if ctx.caller_phone:
        rows += await svc.upcoming_for_phone(phone_variants(ctx.caller_phone), now=ctx.now)
    seen: set = set()
    mine = []
    for a in sorted(rows, key=lambda r: r["start"]):
        if a["appointment_id"] not in seen and _verified(a, ctx, name, dob):
            seen.add(a["appointment_id"])
            mine.append(a)
    if not mine:
        return {"found": False, **NOT_VERIFIED}
    today = svc.local_today(ctx.now)
    return {"found": True, "appointments": [
        {"appointment_id": a["appointment_id"], "say": _say(svc, a, today), "status": a.get("status")}
        for a in mine[:5]]}


async def _load_owned(ctx: "ToolContext", appointment_id: str, name: Optional[str],
                      dob_raw: Any = None) -> Optional[Dict[str, Any]]:
    appt = await ctx.repos().appointments.find_one({"appointment_id": (appointment_id or "").strip()})
    if not appt:
        return None
    if _identity_checked(ctx):
        return appt if _verified(appt, ctx, name, _parse_dob(dob_raw, ctx.now)) else None
    return appt if _owned(appt, ctx, name) else None


def _not_owned(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    if not _identity_checked(ctx):
        return NOT_FOUND
    return IDENTITY_REQUIRED if not (args.get("name") and args.get("date_of_birth")) else NOT_VERIFIED


async def reschedule_appointment(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    appt = await _load_owned(ctx, str(args.get("appointment_id") or ""), args.get("name"), args.get("date_of_birth"))
    if not appt:
        return _not_owned(ctx, args)
    decoded = av.decode_slot_id(str(args.get("new_slot_id") or ""))
    if not decoded:
        return {"error": "bad_slot", "message": "Use a slot_id from check_availability."}
    svc = _service(ctx)
    try:
        new = await svc.reschedule(appt["appointment_id"], resource_id=decoded[0], start=decoded[1], now=ctx.now)
    except SchedulingError as e:
        return {"error": e.code, "message": e.message}
    today = svc.local_today(ctx.now)
    when = av.spoken(new["start"], svc.tz, today=today)
    t = ctx.tenant_cfg
    fire(notify_owner(t, "on_new_booking", f"Rescheduled: {new.get('service_name') or 'appointment'} → {when}\n{new.get('name')} — {new.get('phone')}",
                      subject="Appointment rescheduled"))
    if new.get("phone"):
        fire(notify_contact(t, new["phone"], f"{t.name}: your appointment has moved to {when}."))
    return {"success": True, "appointment_id": new["appointment_id"], "when": when,
            "message": f"Moved to {when}. Confirm it back to the caller."}


async def cancel_appointment(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    appt = await _load_owned(ctx, str(args.get("appointment_id") or ""), args.get("name"), args.get("date_of_birth"))
    if not appt:
        return _not_owned(ctx, args)
    svc = _service(ctx)
    try:
        await svc.cancel(appt["appointment_id"], args.get("reason"))
    except SchedulingError as e:
        return {"error": e.code, "message": e.message}
    t = ctx.tenant_cfg
    today = svc.local_today(ctx.now)
    fire(notify_owner(t, "on_new_booking", f"Cancelled: {_say(svc, appt, today)}\n{appt.get('name')} — {appt.get('phone')}",
                      subject="Appointment cancelled"))
    policy = t.scheduling.cancellation_policy.strip()
    return {"success": True, "appointment_id": appt["appointment_id"],
            "policy": policy or None,
            "message": "Cancelled. " + (f"Mention the policy: {policy}" if policy else "Offer to book another time.")}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
