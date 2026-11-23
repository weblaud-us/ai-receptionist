"""join_waitlist (plan 4.2): when no time suits, the caller can wait for a cancellation."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Dict

from core.services import waitlist
from core.services.scheduling import SchedulingService

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

MAX_DAYS = 60

JOIN_WAITLIST_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "join_waitlist",
        "description": ("Put the caller on the cancellation waitlist when no offered time suits. If a matching "
                        "time frees up, they get a WhatsApp message and the first to tap the link gets it. Ask "
                        "first; confirm the dates."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Patient's full name"},
            "service": {"type": "string", "description": "The treatment, as the caller said it"},
            "resource": {"type": "string", "description": "Preferred dentist, or 'anyone'"},
            "date_from": {"type": "string", "description": "First acceptable day, YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "Last acceptable day, YYYY-MM-DD (default two weeks on)"},
            "part_of_day": {"type": "string", "enum": ["any", "morning", "afternoon", "evening"]},
            "phone": {"type": "string", "description": "WhatsApp number, if different from the caller's"},
        }, "required": ["name", "date_from"]},
    },
}


def _day(value: Any, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, AttributeError):
        return fallback


async def join_waitlist(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    from core.integrations import whatsapp as wa_api

    t = ctx.tenant_cfg
    if not wa_api.configured(t.integrations.whatsapp.phone_number_id or None):
        return {"error": "waitlist_unavailable",
                "message": "The waitlist isn't available here. Offer take_message so the team can call back."}
    name = str(args.get("name") or "").strip()
    phone = str(args.get("phone") or ctx.caller_phone or "").strip()
    if not name:
        return {"error": "name_required", "message": "Ask for the patient's full name."}
    if not phone:
        return {"error": "phone_required", "message": "Ask for a mobile number with WhatsApp."}
    svc = SchedulingService(ctx.repos(), t)
    today = svc.local_today(ctx.now)
    date_from = max(today, _day(args.get("date_from"), today))
    date_to = min(_day(args.get("date_to"), date_from + timedelta(days=14)), today + timedelta(days=MAX_DAYS))
    if date_to < date_from:
        return {"error": "bad_dates", "message": "Check the dates with the caller: the last day is before the first."}
    service = svc.match_service(await svc.services(), args.get("service"))
    resource = svc.match_resource(await svc.resources(), args.get("resource"))
    part = str(args.get("part_of_day") or "any")
    await waitlist.join(ctx.repos(), name=name, phone=phone, service=service, resource=resource,
                        date_from=date_from, date_to=date_to, part_of_day=part if part in waitlist.PARTS else "any",
                        call_id=ctx.call_id)
    return {"success": True, "until": date_to.isoformat(),
            "message": (f"On the waitlist until {date_to:%A %d %B}. Tell them that if a matching time comes up "
                        "they'll get a WhatsApp message, and the first person to tap the link gets it.")}
