"""
Booking calendar helpers.

- Build secure ICS URL and Google Calendar URL for a booking.
- Generate ICS file content for calendar import.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Dict
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from config import settings


def generate_calendar_token(booking_id: str, guest_phone: str) -> str:
    seed = f"{booking_id}:{guest_phone}:{settings.SECRET_KEY}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _public_base_url() -> str:
    for candidate in (
        settings.WEBHOOK_BASE_URL,
        settings.CALL_BASE_URL,
    ):
        if candidate and candidate.strip():
            return candidate.strip().rstrip("/")
    return ""


def build_booking_calendar_links(
    booking_id: str,
    calendar_token: str,
    hotel_id: str,
    hotel_name: str,
    hotel_address: str,
    check_in_date: str,
    check_out_date: str,
    checkin_time: str,
    checkout_time: str,
    room_type: str,
) -> Dict[str, str]:
    base = _public_base_url()
    ics_url = ""
    if base:
        qs = urlencode({"token": calendar_token, "hotel_id": hotel_id})
        ics_url = f"{base}/bookings/{booking_id}/calendar.ics?{qs}"

    title = f"{hotel_name} stay ({room_type.title()})"
    details = (
        f"Booking ID: {booking_id}\n"
        f"Check-in: {check_in_date} {checkin_time}\n"
        f"Check-out: {check_out_date} {checkout_time}\n"
        "Please bring a valid ID at check-in."
    )
    date_range = (
        f"{check_in_date.replace('-', '')}T{checkin_time.replace(':', '')}00/"
        f"{check_out_date.replace('-', '')}T{checkout_time.replace(':', '')}00"
    )
    google_qs = urlencode(
        {
            "action": "TEMPLATE",
            "text": title,
            "dates": date_range,
            "details": details,
            "location": hotel_address,
        }
    )
    google_url = f"https://calendar.google.com/calendar/render?{google_qs}"

    return {
        "ics_url": ics_url,
        "google_calendar_url": google_url,
    }


def generate_booking_ics(
    booking_id: str,
    guest_name: str,
    hotel_name: str,
    hotel_address: str,
    room_type: str,
    check_in_date: str,
    check_out_date: str,
    checkin_time: str,
    checkout_time: str,
    timezone_name: str,
) -> str:
    tz_name = timezone_name or settings.HOTEL_TIMEZONE or "UTC"
    try:
        ZoneInfo(tz_name)
    except Exception:
        tz_name = "UTC"

    dtstart = f"{check_in_date.replace('-', '')}T{checkin_time.replace(':', '')}00"
    dtend = f"{check_out_date.replace('-', '')}T{checkout_time.replace(':', '')}00"
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    uid = f"{booking_id}@hotel-ai-receptionist"
    summary = f"{hotel_name} stay - {guest_name}"
    description = (
        f"Booking ID: {booking_id}\\n"
        f"Room type: {room_type.title()}\\n"
        f"Check-in: {check_in_date} {checkin_time}\\n"
        f"Check-out: {check_out_date} {checkout_time}"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Hotel AI Receptionist//Booking Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_utc}",
        f"DTSTART;TZID={tz_name}:{dtstart}",
        f"DTEND;TZID={tz_name}:{dtend}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{hotel_address}",
        "BEGIN:VALARM",
        "TRIGGER:-P1D",
        "ACTION:DISPLAY",
        "DESCRIPTION:Hotel check-in reminder",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
