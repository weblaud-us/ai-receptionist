"""
Cancellation waitlist (plan 4.2).

    join(...)                  the assistant puts a caller on the list (core/tools/waitlist.py)
    offer_freed_slot(t, appt)  a cancelled / moved booking's time goes to the first OFFER_TO matching
                               patients by WhatsApp, each with a signed "take it" link
    accept(t, offer_id)        the link: books through SchedulingService.book, which re-checks the
                               time, so the first patient to tap gets it and the rest hear it's gone

Nothing about a patient's health is in the offer: practice, treatment name, day and time.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from core.repositories import Repos
from core.signed_links import sign_ref, verify_ref
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

OFFER_TO = 3                       # patients offered one freed time at once
OFFER_FOR = timedelta(hours=3)     # an offer lapses after this, or 2 h before the visit
PARTS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def offer_url(tenant_id: str, offer_id: str) -> str:
    from services.calendar import _public_base_url

    base = _public_base_url()
    if not base:
        return ""
    return f"{base}/public/waitlist/{tenant_id}/{offer_id}?sig={sign_ref('waitlist', f'{tenant_id}:{offer_id}')}"


def verify(tenant_id: str, offer_id: str, sig: str) -> bool:
    return verify_ref("waitlist", f"{tenant_id}:{offer_id}", sig)


async def join(repos: Repos, *, name: str, phone: str, service: Optional[Dict[str, Any]],
               resource: Optional[Dict[str, Any]], date_from: date, date_to: date, part_of_day: str,
               call_id: Optional[str]) -> str:
    entry_id = new_id("wl")
    await repos.waitlist.insert_one({
        "_id": entry_id, "name": name[:120], "phone": phone[:32], "status": "waiting",
        "service_id": (service or {}).get("_id"), "service_name": (service or {}).get("name"),
        "resource_id": (resource or {}).get("_id"), "resource_name": (resource or {}).get("name"),
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "part_of_day": part_of_day,
        "call_id": call_id, "created_at": utcnow()})
    return entry_id


def _matches(entry: Dict[str, Any], t: TenantConfig, freed: Dict[str, Any]) -> bool:
    start = _aware(freed.get("start"))
    if start is None:
        return False
    local = start.astimezone(ZoneInfo(t.timezone))
    if not (entry.get("date_from", "") <= local.date().isoformat() <= entry.get("date_to", "")):
        return False
    if entry.get("service_id") and entry["service_id"] != freed.get("service_id"):
        return False
    if entry.get("resource_id") and entry["resource_id"] != freed.get("resource_id"):
        return False
    lo, hi = PARTS.get(entry.get("part_of_day") or "", (0, 24))
    return lo <= local.hour < hi


async def offer_freed_slot(t: TenantConfig, freed: Dict[str, Any], now: Optional[datetime] = None) -> int:
    """Offer a freed time to the first matching patients. Returns how many were sent an offer."""
    from core.integrations import whatsapp as wa_api
    from core.notifications import notify_contact
    from core.services import availability as av

    now = now or utcnow()
    start = _aware(freed.get("start"))
    if start is None or start - now < timedelta(hours=2):
        return 0                               # too close to offer to someone who must travel
    repos = Repos(get_db(), t.id)
    waiting = await repos.waitlist.find({"status": "waiting", "date_to": {"$gte": now.date().isoformat()}},
                                        sort=[("created_at", 1)], limit=200)
    chosen = [e for e in waiting if _matches(e, t, freed)][:OFFER_TO]
    if not chosen or not wa_api.configured(t.integrations.whatsapp.phone_number_id or None):
        return 0
    expires = min(now + OFFER_FOR, start - timedelta(hours=2))
    when = av.spoken(start, t.timezone, today=now.astimezone(ZoneInfo(t.timezone)).date())
    slot_key = f"{freed.get('resource_id')}:{start.isoformat()}"
    sent = 0
    for e in chosen:
        offer_id = new_id("wlo")
        await repos.waitlist_offers.insert_one({
            "_id": offer_id, "entry_id": e["_id"], "slot_key": slot_key, "resource_id": freed.get("resource_id"),
            "service_id": freed.get("service_id"), "start": start, "end": _aware(freed.get("end")),
            "status": "open", "expires_at": expires, "created_at": now})
        link = offer_url(t.id, offer_id)
        what = freed.get("service_name") or e.get("service_name") or "appointment"
        who = f" with {freed['resource_name']}" if freed.get("resource_name") else ""
        text = (f"{t.name}: a {what} time has come up {when}{who}. "
                + (f"Tap to take it, first come first served: {link}" if link else "Call us to take it.")
                + f" Not for you? Ignore this message.")
        if await notify_contact(t, e["phone"], text):
            sent += 1
            await repos.waitlist.update_one({"_id": e["_id"]}, {"$set": {"last_offered_at": now}})
    if sent:
        logger.info("Offered a freed time to %d waitlisted patients for %s", sent, t.id)
    return sent


async def accept(t: TenantConfig, offer_id: str, now: Optional[datetime] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
    """('booked', appointment) | ('gone', None) | ('expired', None) | ('unknown', None)."""
    from core.services.scheduling import SchedulingError, SchedulingService

    now = now or utcnow()
    repos = Repos(get_db(), t.id)
    offer = await repos.waitlist_offers.find_one({"_id": offer_id})
    if offer is None:
        return "unknown", None
    if offer.get("status") != "open":
        return ("booked", None) if offer.get("status") == "accepted" else ("gone", None)
    if (_aware(offer.get("expires_at")) or now) < now:
        await repos.waitlist_offers.update_one({"_id": offer_id}, {"$set": {"status": "expired"}})
        return "expired", None
    entry = await repos.waitlist.find_one({"_id": offer["entry_id"]})
    if entry is None or entry.get("status") != "waiting":
        return "gone", None
    svc = SchedulingService(repos, t)
    service = await repos.services.find_one({"_id": offer.get("service_id")}) if offer.get("service_id") else None
    try:
        appt = await svc.book(resource_id=offer.get("resource_id") or "any", start=_aware(offer["start"]) or now,
                              service=service, name=entry.get("name") or "", phone=entry.get("phone") or "",
                              source="waitlist", now=now)
    except SchedulingError:
        await repos.waitlist_offers.update_one({"_id": offer_id}, {"$set": {"status": "taken"}})
        return "gone", None
    await repos.waitlist_offers.update_one({"_id": offer_id}, {"$set": {"status": "accepted", "accepted_at": now,
                                                                        "appointment_id": appt["appointment_id"]}})
    await repos.waitlist_offers.update_many({"slot_key": offer["slot_key"], "status": "open"}, {"$set": {"status": "taken"}})
    await repos.waitlist.update_one({"_id": entry["_id"]}, {"$set": {"status": "booked",
                                                                     "appointment_id": appt["appointment_id"]}})
    return "booked", appt
