"""
What the sales agent can do during a call (Roadmap Task 4.5), as plain async functions.

agent/sales.py wraps each one as a LiveKit tool; they are kept here so they can be tested without
the voice runtime. Each returns a dict the model reads, and records what happened on the
`SalesContext` — the call's outcome is decided from that when the call ends.

Demos are booked with the Phase 2 scheduling engine, with your own business (SALES_TENANT_ID) as
the tenant: its "Demo" service, its opening hours and its connected Google Calendar.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from config import settings
from core.integrations.email import send_email
from core.notifications import fire, notify_owner
from core.outbound import models as m
from core.outbound import registry
from core.repositories import Repos
from core.services import availability as av
from core.services.scheduling import SchedulingError, SchedulingService
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_CALLBACK_DAYS = 30
DEMO_SEARCH_DAYS = 7

# Strongest first: what a call "was" when several things happened on it.
_OUTCOME_ORDER = ("dnc", "wrong_number", "demo_booked", "callback", "interested", "not_interested",
                  "gatekeeper", "no_conversation")
END_CALL_OUTCOMES = ("demo_booked", "interested", "callback", "not_interested", "gatekeeper", "wrong_number")


@dataclass
class SalesContext:
    db: Any
    lead: Dict[str, Any]
    campaign: Mapping[str, Any]
    attempt_id: str
    call_id: str
    tenant: Optional[TenantConfig] = None        # the sales tenant; None = demos cannot be booked
    now: Optional[datetime] = None               # tests pin the clock
    outcome: Optional[str] = None
    callback_at: Optional[datetime] = None
    next_step: Optional[str] = None
    opted_out: bool = False

    def clock(self) -> datetime:
        return self.now or datetime.now(timezone.utc)

    def set_outcome(self, outcome: str) -> None:
        """Keep the strongest outcome: an opt-out beats a booked demo beats "interested"."""
        if outcome not in _OUTCOME_ORDER:
            return
        if self.outcome is None or _OUTCOME_ORDER.index(outcome) < _OUTCOME_ORDER.index(self.outcome):
            self.outcome = outcome

    @property
    def lead_zone(self) -> str:
        zones = self.lead.get("timezones") or []
        return self.lead.get("timezone") or (zones[0] if zones else "") or (self.tenant.timezone if self.tenant else "UTC")

    async def update_lead(self, sets: Dict[str, Any]) -> None:
        self.lead.update(sets)
        await self.db[m.LEADS].update_one({"_id": self.lead["_id"]}, {"$set": {**sets, "updated_at": self.clock()}})


def _scheduler(ctx: SalesContext) -> Optional[SchedulingService]:
    if ctx.tenant is None:
        return None
    return SchedulingService(Repos(ctx.db, ctx.tenant.id), ctx.tenant)


def _valid_email(value: Any) -> str:
    email = str(value or "").strip().lower().rstrip(".")
    return email if _EMAIL.match(email) else ""


def _footer() -> str:
    return f"\n\n--\n{settings.SALES_EMAIL_FOOTER}" if settings.SALES_EMAIL_FOOTER else ""


# ── Demo booking ─────────────────────────────────────────────────────────────

async def check_demo_slots(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    svc = _scheduler(ctx)
    if svc is None:
        return {"error": "unavailable", "message": "The calendar isn't available. Offer to have someone email them times instead."}
    now = ctx.clock()
    today = svc.local_today(now)
    try:
        day = date.fromisoformat(str(args.get("date") or "")[:10])
    except ValueError:
        day = today
    day = max(day, today)
    service = svc.match_service(await svc.services(), settings.SALES_DEMO_SERVICE)
    slots = await svc.find_slots(service=service, day_from=day, day_to=day + timedelta(days=DEMO_SEARCH_DAYS),
                                 requested_at=svc.parse_local(day, args.get("time")), now=now)
    if not slots:
        return {"available": False, "slots": [],
                "message": "No demo times are open in the next week. Offer to email them some times instead."}
    zone = ctx.lead_zone
    local_today = now.astimezone(ZoneInfo(zone)).date()
    return {"available": True,
            "slots": [{"slot_id": s.slot_id, "say": av.spoken(s.start, zone, today=local_today)} for s in slots],
            "message": "Offer these times in words; they are in the business's local time. "
                       "When they pick one, confirm their name and email, then call book_demo."}


async def book_demo(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    svc = _scheduler(ctx)
    if svc is None:
        return {"error": "unavailable", "message": "The calendar isn't available. Offer to have someone email them instead."}
    decoded = av.decode_slot_id(str(args.get("slot_id") or ""))
    if not decoded:
        return {"error": "bad_slot", "message": "Use a slot_id from check_demo_slots."}
    resource_id, start = decoded
    name = str(args.get("name") or ctx.lead.get("contact_name") or "").strip()[:120]
    if not name:
        return {"error": "name_required", "message": "Ask for their name."}
    email = _valid_email(args.get("email")) or _valid_email(ctx.lead.get("email"))
    if not email:
        return {"error": "email_required", "message": "Ask for the email address to send the invite to, and read it back."}
    service = svc.match_service(await svc.services(), settings.SALES_DEMO_SERVICE)
    business = ctx.lead.get("business_name") or "a lead"
    try:
        appt = await svc.book(resource_id=resource_id, start=start, service=service, name=name,
                              phone=ctx.lead["phone"], email=email, source="outbound_sales", call_id=ctx.call_id,
                              notes=f"Demo for {business}. Booked by the sales assistant.", now=ctx.clock())
    except SchedulingError as e:
        return {"error": e.code, "message": e.message}

    when = av.spoken(appt["start"], ctx.lead_zone)
    await ctx.update_lead({"status": "demo_booked", "contact_name": name, "email": email,
                           "demo": {"appointment_id": appt["appointment_id"], "start": appt["start"]}})
    ctx.set_outcome("demo_booked")
    ctx.next_step = f"Demo {when}"
    founder = settings.SALES_FOUNDER_NAME or settings.SALES_COMPANY_NAME
    link = f"\nJoin here: {settings.SALES_DEMO_MEETING_URL}" if settings.SALES_DEMO_MEETING_URL else ""
    fire(send_email(email, f"Your {settings.SALES_COMPANY_NAME} demo — {when}",
                    f"Hi {name},\n\nYour 15-minute demo with {founder} is booked for {when}.{link}\n\n"
                    f"Reply to this email if you need to change the time.{_footer()}"))
    assert ctx.tenant is not None
    fire(notify_owner(ctx.tenant, "on_new_booking",
                      f"Demo booked by the sales assistant: {business}\n{name} — {ctx.lead['phone']} — {email}\n{when}",
                      subject="Demo booked"))
    return {"success": True, "appointment_id": appt["appointment_id"], "when": when,
            "message": f"Booked for {when}. Tell them the invite is on its way to {email}, then wrap up."}


# ── Follow-ups ───────────────────────────────────────────────────────────────

async def send_info_email(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    email = _valid_email(args.get("email")) or _valid_email(ctx.lead.get("email"))
    if not email:
        return {"error": "email_required", "message": "Ask for the email address and read it back to confirm."}
    await ctx.update_lead({"email": email, "info_email_requested_at": ctx.clock()})
    ctx.set_outcome("interested")
    ctx.next_step = ctx.next_step or f"Sent information to {email}"
    company = settings.SALES_COMPANY_NAME
    demo_line = f"\n\nHear it for yourself: call our demo line on {settings.SALES_DEMO_LINE}." if settings.SALES_DEMO_LINE else ""
    sent = await send_email(
        email, f"{company}: an AI receptionist for {ctx.lead.get('business_name') or 'your business'}",
        f"Hi,\n\nThanks for taking our call. {company} answers every call to your business, day or night: "
        "it answers questions, books jobs and appointments into your calendar, takes messages, and hands the "
        f"caller to you whenever they ask for a person.{demo_line}\n\n"
        f"Reply to this email to set up a 15-minute demo.{_footer()}")
    if not sent:
        # Stored on the lead, so a person can send it by hand.
        return {"success": True, "sent": False,
                "message": "Noted. Tell them the information will be emailed today. Then offer a demo time."}
    return {"success": True, "sent": True, "message": f"Sent to {email}. Offer a demo time anyway."}


async def schedule_callback(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    raw = str(args.get("when") or "").strip()
    try:
        local = datetime.fromisoformat(raw)
    except ValueError:
        return {"error": "bad_time", "message": "Give the day and time as YYYY-MM-DDTHH:MM in their local time."}
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(ctx.lead_zone))
    when = local.astimezone(timezone.utc)
    now = ctx.clock()
    if when <= now:
        return {"error": "in_past", "message": "That time has passed. Ask for a time later today or another day."}
    if when > now + timedelta(days=MAX_CALLBACK_DAYS):
        return {"error": "too_far", "message": "That is more than a month away. Offer to email them instead."}
    ctx.callback_at = when
    ctx.set_outcome("callback")
    note = str(args.get("note") or "").strip()[:200]
    await ctx.update_lead({"callback_note": note, "callback_requested_at": now})
    spoken = av.spoken(when, ctx.lead_zone)
    ctx.next_step = f"Call back {spoken}" + (f" — {note}" if note else "")
    return {"success": True, "when": spoken,
            "message": f"Noted: call back {spoken}. It will only be placed inside permitted calling hours. Say goodbye."}


async def add_to_dnc(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Immediate and irreversible from the agent's side."""
    await registry.add_to_dnc(ctx.db, ctx.lead["phone"], reason=str(args.get("reason") or "requested_on_call"),
                              call_id=ctx.call_id, source="sales_agent", now=ctx.clock())
    ctx.lead["status"] = "dnc"
    ctx.opted_out = True
    ctx.set_outcome("dnc")
    return {"success": True, "message": "They are on the do-not-call list. Apologise once, say goodbye, and call end_call."}


async def mark_wrong_number(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    ctx.set_outcome("wrong_number")
    return {"success": True, "message": "Noted. Apologise for the mistake and call end_call."}
