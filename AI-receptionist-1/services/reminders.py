"""
Hotel check-in reminders: one WhatsApp message the day before check-in.

Runs per business: every active hotel tenant's own bookings, with its own name, timezone and
WhatsApp number. Before migration m0003 (no tenants yet) it serves the .env hotel's bookings,
which have no tenant_id. A booking is never sent under another business's name, and a guest who
opted out (STOP) gets nothing.

With Redis the jobs worker calls `run_once()` on a schedule (jobs/worker.py); without it the API
process runs the loop below. Either way each reminder is claimed atomically before it is sent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo


from config import settings
from database.mongodb import get_db
from database.tenancy import get_hotel_profile, tenant_profile
from services.calendar import build_booking_calendar_links

logger = logging.getLogger(__name__)

CLAIM_RETRY_AFTER = timedelta(minutes=55)   # a failed send is retried on a later tick, never in parallel


def _meta_base_url() -> str:
    version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
    return f"https://graph.facebook.com/{version}"


async def _send_meta_whatsapp(phone: str, message: str, phone_number_id: Optional[str]) -> bool:
    from core.integrations.whatsapp import send_text

    return await send_text(phone, message, phone_number_id)


def _build_reminder_message(booking: Dict[str, Any], hotel: Dict[str, Any]) -> str:
    links = build_booking_calendar_links(
        booking_id=booking.get("booking_id", ""),
        calendar_token=booking.get("calendar_token", ""),
        hotel_id=hotel.get("hotel_id", "default"),
        hotel_name=hotel.get("name", settings.HOTEL_NAME),
        hotel_address=hotel.get("address", settings.HOTEL_ADDRESS),
        check_in_date=str(booking.get("check_in_date", "")),
        check_out_date=str(booking.get("check_out_date", "")),
        checkin_time=hotel.get("checkin_time", settings.HOTEL_CHECKIN_TIME),
        checkout_time=hotel.get("checkout_time", settings.HOTEL_CHECKOUT_TIME),
        room_type=str(booking.get("room_type", "standard")),
    )
    lines = [
        f"Hello, this is a friendly reminder from {hotel.get('name', settings.HOTEL_NAME)}.",
        f"Your check-in is tomorrow ({booking.get('check_in_date')}) at {hotel.get('checkin_time', settings.HOTEL_CHECKIN_TIME)}.",
        f"Booking ID: {booking.get('booking_id')}",
        f"Room: {str(booking.get('room_type', '')).title()} (Room {booking.get('room_number', '-')})",
        "",
        "Add to calendar:",
        f"Google Calendar: {links.get('google_calendar_url', '')}",
    ]
    if links.get("ics_url"):
        lines.append(f"Apple/Outlook ICS: {links['ics_url']}")
    lines += [
        "",
        f"Hotel address: {hotel.get('address', settings.HOTEL_ADDRESS)}",
        "Reply here if you need any changes.",
    ]
    return "\n".join(lines)


class BookingReminderService:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="booking-reminders")
        logger.info("Booking reminder service started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Booking reminder service stopped")

    async def _run_loop(self) -> None:
        interval_min = max(1, int(settings.BOOKING_REMINDER_POLL_MINUTES))
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Reminder loop failed: %s", e, exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_min * 60)
            except asyncio.TimeoutError:
                continue


    async def run_once(self) -> None:
        if not settings.ENABLE_ONE_DAY_BOOKING_REMINDER:
            return
        if not settings.ENABLE_WHATSAPP:
            return
        from core.tenancy.resolver import legacy_mode

        if await legacy_mode():
            hotels = [(get_hotel_profile(), None)]            # pre-migration: the .env hotel only
        else:
            hotels = [(tenant_profile(t), t.id) for t in await _hotel_tenants()]
        for hotel, tenant_id in hotels:
            try:
                await self._process_hotel(hotel, tenant_id=tenant_id)
            except Exception as e:
                logger.error("Reminder processing failed for %s: %s", tenant_id or "legacy hotel", e, exc_info=True)

    async def _process_hotel(self, hotel: Dict[str, Any], tenant_id: Optional[str] = None) -> None:
        """One business's reminders. `tenant_id=None`: the pre-migration bookings (no tenant_id)."""
        from core.compliance.optout import is_opted_out

        db = get_db()
        scope: Dict[str, Any] = {"tenant_id": tenant_id} if tenant_id else {"tenant_id": {"$in": [None, "default"]}}
        tz_name = hotel.get("timezone") or settings.HOTEL_TIMEZONE or "UTC"
        try:
            local_today = datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            local_today = datetime.utcnow().date()

        reminder_date = (local_today + timedelta(days=1)).isoformat()
        bookings = await db.bookings.find(  # scoped: one business (or the pre-migration hotel)
            {
                **scope,
                "status": "confirmed",
                "check_in_date": reminder_date,
                "$or": [
                    {"one_day_reminder_sent": {"$exists": False}},
                    {"one_day_reminder_sent": False},
                ],
            }
        ).limit(200).to_list(200)

        for booking in bookings:
            phone = booking.get("guest_phone")
            if not phone:
                continue
            if tenant_id and await is_opted_out(db, tenant_id, phone):
                await db.bookings.update_one(  # scoped: the booking's own business
                    {**scope, "booking_id": booking.get("booking_id")},
                    {"$set": {"one_day_reminder_sent": True, "one_day_reminder_skipped": "opted_out"}})
                continue
            # Claim first (atomic): with several runners, or a retry, only the one whose update
            # matches sends the reminder. A claim older than CLAIM_RETRY_AFTER whose send failed
            # is free to be claimed again.
            now = datetime.utcnow()
            claimed = await db.bookings.find_one_and_update(  # scoped: the booking's own business
                {**scope, "booking_id": booking.get("booking_id"), "status": "confirmed",
                 "one_day_reminder_sent": {"$ne": True},
                 "$or": [{"one_day_reminder_claimed_at": {"$exists": False}},
                         {"one_day_reminder_claimed_at": None},
                         {"one_day_reminder_claimed_at": {"$lt": now - CLAIM_RETRY_AFTER}}]},
                {"$set": {"one_day_reminder_claimed_at": now}})
            if claimed is None:
                continue
            msg = _build_reminder_message(booking, hotel)
            sent = await _send_meta_whatsapp(
                phone=phone,
                message=msg,
                phone_number_id=hotel.get("meta_whatsapp_phone_number_id"),
            )
            update = {
                "updated_at": datetime.utcnow(),
                "one_day_reminder_attempted_at": datetime.utcnow(),
            }
            if sent:
                update["one_day_reminder_sent"] = True
                update["one_day_reminder_sent_at"] = datetime.utcnow()
                logger.info("1-day reminder sent for booking=%s", booking.get("booking_id"))
            else:
                update["one_day_reminder_error"] = "send_failed"
            await db.bookings.update_one(  # scoped: the booking's own business
                {**scope, "booking_id": booking.get("booking_id")},
                {"$set": update},
            )


async def _hotel_tenants() -> list:
    from core.repositories.repos import TenantsStore
    from core.tenancy.models import TenantConfig

    out = []
    for doc in await TenantsStore(get_db()).list(limit=10_000):
        if doc.get("vertical") != "hotel":
            continue
        try:
            t = TenantConfig.from_doc(doc)
        except Exception:
            logger.warning("Skipping invalid tenant %s", doc.get("_id"))
            continue
        if t.active:
            out.append(t)
    return out


_service: Optional[BookingReminderService] = None


def get_booking_reminder_service() -> BookingReminderService:
    global _service
    if _service is None:
        _service = BookingReminderService()
    return _service
