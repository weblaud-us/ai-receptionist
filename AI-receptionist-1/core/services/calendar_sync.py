"""
Calendar sync (Roadmap Task 2.8): keep availability aware of the owner's Google Calendar, and put
AI bookings into it.

- `sync_busy(tenant)` — the business calendar's events plus one free/busy query for any staff
  calendars, written where the availability engine already looks: per-staff `resources.busy_cache`,
  and business-wide busy time as `schedule_exceptions` with `source: "google"`. Run by the jobs
  service every few minutes (and on demand from the dashboard); never inside a call.

  The business calendar is read event by event, not with free/busy, because the app writes its own
  bookings there for staff who have no calendar of their own. Those events are skipped (marked with
  PRIVATE_KEY, or known by their stored event id); otherwise one booking would come back as a
  business-wide block and close that time for every staff member. Free ("transparent") and
  cancelled events are skipped too; all-day events block the whole day.
- `push_booking` / `remove_booking` — fire-and-forget after a booking or cancellation.

Tenant config: `tenants.integrations.google_calendar = {connected, calendar_id, refresh_token_enc,
last_sync_at, last_error}`; a staff member's own calendar is `resources.calendar.calendar_id`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from core import crypto
from core.integrations import google_calendar as gcal
from core.repositories import Repos
from core.repositories.repos import TenantsStore
from core.tenancy import invalidate
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

SOURCE = "google"
PRIVATE_KEY = "receptionist_appointment_id"     # on every event this app writes (gcal.insert_event `private`)


def google_settings(tenant: TenantConfig) -> Dict[str, Any]:
    return dict(tenant.integrations.google_calendar or {})


def connected(tenant: TenantConfig) -> bool:
    g = google_settings(tenant)
    return bool(g.get("connected") and g.get("refresh_token_enc"))


def _refresh_token(tenant: TenantConfig) -> str:
    return crypto.decrypt(google_settings(tenant)["refresh_token_enc"])


async def _set_status(tenant_id: str, **fields: Any) -> None:
    await TenantsStore(get_db()).update(tenant_id, {f"integrations.google_calendar.{k}": v for k, v in fields.items()})
    invalidate(tenant_id)


async def save_connection(tenant_id: str, *, refresh_token: str, user_id: str) -> None:
    await _set_status(tenant_id, connected=True, refresh_token_enc=crypto.encrypt(refresh_token),
                      calendar_id="primary", connected_by=user_id, connected_at=datetime.now(timezone.utc),
                      last_error=None)


async def disconnect(tenant_id: str) -> None:
    await TenantsStore(get_db()).update(tenant_id, {"integrations.google_calendar": {"connected": False}})
    repos = Repos(get_db(), tenant_id)
    await repos.exceptions.delete_many({"source": SOURCE})
    await repos.resources.update_many({}, {"$unset": {"busy_cache": ""}})
    invalidate(tenant_id)


def _event_window(event: Dict[str, Any], tz: str) -> Optional[Tuple[datetime, datetime]]:
    start, end = event.get("start") or {}, event.get("end") or {}
    if start.get("dateTime") and end.get("dateTime"):
        return gcal._parse(start["dateTime"]), gcal._parse(end["dateTime"])
    if start.get("date") and end.get("date"):                     # all day: midnight to midnight, business time
        zone = ZoneInfo(tz)
        return (datetime.fromisoformat(start["date"]).replace(tzinfo=zone).astimezone(timezone.utc),
                datetime.fromisoformat(end["date"]).replace(tzinfo=zone).astimezone(timezone.utc))
    return None


def business_busy_from_events(events: List[Dict[str, Any]], tz: str, own_event_ids: Set[str]
                              ) -> List[Tuple[datetime, datetime]]:
    """The owner's busy time on the business calendar, without the app's own bookings."""
    busy: List[Tuple[datetime, datetime]] = []
    for ev in events:
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue
        private = (ev.get("extendedProperties") or {}).get("private") or {}
        if private.get(PRIVATE_KEY) or ev.get("id") in own_event_ids:
            continue
        window = _event_window(ev, tz)
        if window and window[1] > window[0]:
            busy.append(window)
    return busy


async def _own_event_ids(repos: Repos, calendar_id: str, since: datetime) -> Set[str]:
    """Event ids of bookings this app wrote to `calendar_id` (also covers events written before
    they carried PRIVATE_KEY)."""
    rows = await repos.appointments.find(
        {"external.google_event_id": {"$exists": True}, "external.google_calendar_id": calendar_id,
         "end": {"$gte": since - timedelta(days=1)}},
        limit=10_000, projection={"external.google_event_id": 1})
    return {str((r.get("external") or {}).get("google_event_id")) for r in rows}


async def sync_busy(tenant: TenantConfig, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Refresh cached busy times for the next `max_days_ahead` days. Returns counts per calendar."""
    if not connected(tenant):
        return {}
    now = now or datetime.now(timezone.utc)
    end = now + timedelta(days=tenant.scheduling.max_days_ahead + 1)
    repos = Repos(get_db(), tenant.id)
    business_cal = google_settings(tenant).get("calendar_id") or "primary"
    resources = await repos.resources.find({"active": {"$ne": False}}, projection={"calendar": 1})
    staff_cals = {r["_id"]: (r.get("calendar") or {}).get("calendar_id") for r in resources}
    staff_calendars = sorted({c for c in staff_cals.values() if c})
    try:
        token = _refresh_token(tenant)
        events = await gcal.list_events(token, business_cal, now, end)
        busy = await gcal.freebusy(token, staff_calendars, now, end) if staff_calendars else {}
    except (gcal.GoogleError, crypto.DecryptionError) as e:
        logger.warning("Google calendar sync failed for %s: %s", tenant.id, e)
        await _set_status(tenant.id, last_error=str(e)[:300], last_error_at=now)
        return {}
    business = business_busy_from_events(events, tenant.timezone, await _own_event_ids(repos, business_cal, now))

    # Business calendar → business-wide blocks (replaced wholesale each sync).
    await repos.exceptions.delete_many({"source": SOURCE})
    rows: List[Dict[str, Any]] = [{"_id": new_id("exc"), "resource_id": None, "start": s, "end": e, "source": SOURCE,
                                   "reason": "Busy in Google Calendar"} for s, e in business]
    if rows:
        await repos.exceptions.insert_many(rows)
    # Staff with their own calendar → per-resource busy cache.
    for rid, cal in staff_cals.items():
        cache = [{"start": s, "end": e} for s, e in busy.get(cal, [])] if cal else []
        await repos.resources.update_one({"_id": rid}, {"$set": {"busy_cache": cache, "busy_synced_at": now}})
    await _set_status(tenant.id, last_sync_at=now, last_error=None)
    return {business_cal: len(business), **{cal: len(v) for cal, v in busy.items() if cal != business_cal}}


async def _calendar_for(tenant: TenantConfig, resource_id: Optional[str]) -> str:
    if resource_id and resource_id != "any":
        r = await Repos(get_db(), tenant.id).resources.find_one({"_id": resource_id}, {"calendar": 1})
        own = ((r or {}).get("calendar") or {}).get("calendar_id")
        if own:
            return own
    return google_settings(tenant).get("calendar_id") or "primary"


async def push_booking(tenant: TenantConfig, appointment: Dict[str, Any]) -> None:
    """Write a new appointment to Google Calendar. Never raises — a calendar outage can't break booking."""
    if not connected(tenant):
        return
    try:
        cal = await _calendar_for(tenant, appointment.get("resource_id"))
        who = f" with {appointment['resource_name']}" if appointment.get("resource_name") else ""
        event_id = await gcal.insert_event(
            _refresh_token(tenant), cal,
            summary=f"{appointment.get('service_name') or 'Appointment'}{who}: {appointment.get('name', '')}",
            start=appointment["start"], end=appointment["end"], timezone_name=tenant.timezone,
            description=f"Booked by the AI receptionist ({appointment.get('source')}).\n"
                        f"Phone: {appointment.get('phone', '')}\n{appointment.get('notes') or ''}",
            private={PRIVATE_KEY: appointment["appointment_id"]})
        await Repos(get_db(), tenant.id).appointments.update_one(
            {"appointment_id": appointment["appointment_id"]},
            {"$set": {"external.google_event_id": event_id, "external.google_calendar_id": cal}})
    except Exception as e:
        logger.warning("Google event insert failed for %s: %s", appointment.get("appointment_id"), e)


async def remove_booking(tenant: TenantConfig, appointment: Dict[str, Any]) -> None:
    ext = appointment.get("external") or {}
    if not connected(tenant) or not ext.get("google_event_id"):
        return
    try:
        await gcal.delete_event(_refresh_token(tenant), ext.get("google_calendar_id") or "primary", ext["google_event_id"])
    except Exception as e:
        logger.warning("Google event delete failed for %s: %s", appointment.get("appointment_id"), e)
