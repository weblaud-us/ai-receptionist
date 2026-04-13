"""
Booking reminder background service — single-tenant.
Sends one WhatsApp reminder one day before check-in.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

from config import settings
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from services.calendar import build_booking_calendar_links

logger = logging.getLogger(__name__)


def _meta_base_url() -> str:
    version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
    return f"https://graph.facebook.com/{version}"


async def _send_meta_whatsapp(phone: str, message: str, phone_number_id: Optional[str]) -> bool:
    token = (settings.META_WA_ACCESS_TOKEN or "").strip()
    if not token:
        logger.warning("Reminder skipped: META_WA_ACCESS_TOKEN missing")
        return False
    pnid = (phone_number_id or settings.META_WA_PHONE_NUMBER_ID or "").strip()
    if not pnid:
        logger.warning("Reminder skipped: phone_number_id missing")
        return False

    url = f"{_meta_base_url()}/{pnid}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": phone.replace("+", ""),
        "type": "text",
        "text": {"body": message},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.error("Reminder WhatsApp send failed: %s %s", resp.status_code, resp.text)
                return False
        return True
    except Exception as e:
        logger.error("Reminder WhatsApp send error: %s", e, exc_info=True)
        return False


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
        f"Sir, this is a friendly reminder from {hotel.get('name', settings.HOTEL_NAME)}.",
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
        "Reply here if you need any changes, sir.",
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
        hotel = get_hotel_profile()
        try:
            await self._process_hotel(hotel)
        except Exception as e:
            logger.error("Reminder processing failed: %s", e, exc_info=True)

    async def _process_hotel(self, hotel: Dict[str, Any]) -> None:
        db = get_db()
        tz_name = hotel.get("timezone") or settings.HOTEL_TIMEZONE or "UTC"
        try:
            local_today = datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            local_today = datetime.utcnow().date()

        reminder_date = (local_today + timedelta(days=1)).isoformat()
        bookings = await db.bookings.find(
            {
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
            await db.bookings.update_one(
                {"booking_id": booking.get("booking_id")},
                {"$set": update},
            )


_service: Optional[BookingReminderService] = None


def get_booking_reminder_service() -> BookingReminderService:
    global _service
    if _service is None:
        _service = BookingReminderService()
    return _service
