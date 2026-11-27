"""
"Add to calendar" for appointments (plan 4.5): a signed public .ics link sent with the booking
confirmation.

    url = ics_url(tenant_id, appointment_id)      # "" when the server has no public base URL
    text = ics_text(tenant, appointment)

The link is signed (core/signed_links) per business and appointment, so it can't be guessed. The
event names the practice and the treatment for the patient's own calendar; times are UTC (Z) so any
calendar shows them in its own timezone. A cancelled appointment's link answers 410.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from core.signed_links import sign_ref, verify_ref
from core.tenancy.models import TenantConfig
from services.calendar import _public_base_url

KIND = "appt_ics"


def sign(tenant_id: str, appointment_id: str) -> str:
    return sign_ref(KIND, f"{tenant_id}:{appointment_id}")


def verify(tenant_id: str, appointment_id: str, sig: str) -> bool:
    return verify_ref(KIND, f"{tenant_id}:{appointment_id}", sig)


def ics_url(tenant_id: str, appointment_id: str) -> str:
    base = _public_base_url()
    if not base:
        return ""
    return f"{base}/public/appointments/{tenant_id}/{appointment_id}.ics?sig={sign(tenant_id, appointment_id)}"


def _esc(text: Any) -> str:
    """RFC 5545 TEXT escaping."""
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> List[str]:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545 §3.1)."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return out


def _utc(dt: datetime) -> str:
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_ics(uid: str, start: datetime, end: datetime, summary: str, description: str = "", *,
              location: str = "", url: str = "", organizer_email: str = "", organizer_name: str = "",
              attendee_email: str = "", attendee_name: str = "", now: datetime | None = None) -> str:
    """One event as an invite (METHOD:REQUEST), e.g. a sales meeting a prospect booked on a call: the
    calendar app offers to add it. No alarm: the prospect's own calendar settings apply."""
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WebLaud//Sales meetings//EN", "CALSCALE:GREGORIAN",
        "METHOD:REQUEST", "BEGIN:VEVENT",
        f"UID:{_esc(uid)}",
        f"DTSTAMP:{_utc(now or datetime.now(timezone.utc))}",
        f"DTSTART:{_utc(start)}",
        f"DTEND:{_utc(end)}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(description)}",
        "STATUS:CONFIRMED",
    ]
    if location or url:
        lines.append(f"LOCATION:{_esc(location or url)}")
    if url:
        lines.append(f"URL:{url}")
    if organizer_email:
        lines.append(f"ORGANIZER;CN={_esc(organizer_name or organizer_email)}:mailto:{organizer_email}")
    if attendee_email:
        lines.append(f"ATTENDEE;CN={_esc(attendee_name or attendee_email)};ROLE=REQ-PARTICIPANT;"
                     f"PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:{attendee_email}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"


def ics_text(t: TenantConfig, appt: Dict[str, Any], now: datetime | None = None) -> str:
    what = appt.get("service_name") or "Appointment"
    with_whom = f" with {appt['resource_name']}" if appt.get("resource_name") else ""
    change = f"To change or cancel, call {t.phone}." if t.phone else "To change or cancel, call the practice."
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LaudLine//Appointments//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "BEGIN:VEVENT",
        f"UID:{appt['appointment_id']}@{t.id}",
        f"DTSTAMP:{_utc(now or datetime.now(timezone.utc))}",
        f"DTSTART:{_utc(appt['start'])}",
        f"DTEND:{_utc(appt['end'])}",
        f"SUMMARY:{_esc(f'{what} at {t.name}{with_whom}')}",
        f"DESCRIPTION:{_esc(change)}",
    ]
    if t.address_text:
        lines.append(f"LOCATION:{_esc(t.address_text)}")
    lines += ["BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(what)}", "END:VALARM",
              "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
