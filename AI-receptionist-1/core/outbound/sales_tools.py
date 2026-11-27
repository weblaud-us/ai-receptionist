"""
What the sales agent can do during a call (Roadmap Task 4.5; SALES_DIALER_PLAN.md Phase 1A), as
plain async functions.

agent/sales.py wraps each one as a LiveKit tool; they are kept here so they can be tested without
the voice runtime. Each returns a dict the model reads, and records what happened on the
`SalesContext` — the call's outcome is decided from that when the call ends.

Meetings are booked with the Phase 2 scheduling engine, with your own business (SALES_TENANT_ID) as
the tenant: the offer's meeting service, its opening hours and its connected Google Calendar. The
emails come from the offer's templates.

`dry_run` (a browser test call of an offer) answers exactly as a real call would but writes nothing:
no booking, no email, no do-not-call entry, no change to a lead.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

from config import settings
from email.utils import parseaddr

from core.notifications import fire, notify_owner
from core.outbound import followup, gate, persona, registry
from core.outbound import models as m
from core.outbound import offers as of
from core.repositories import Repos
from core.services import availability as av
from core.services.appointment_ics import event_ics
from core.services.scheduling import SchedulingError, SchedulingService
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_CALLBACK_DAYS = 30
MEETING_SEARCH_DAYS = 7

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
    tenant: Optional[TenantConfig] = None        # the sales tenant; None = meetings cannot be booked
    now: Optional[datetime] = None               # tests pin the clock
    offer: Dict[str, Any] = field(default_factory=persona.legacy_offer)
    variant_id: Optional[str] = None
    research: Optional[Dict[str, Any]] = None
    dry_run: bool = False                        # a test call: answer as usual, write nothing
    outcome: Optional[str] = None
    callback_at: Optional[datetime] = None
    next_step: Optional[str] = None
    opted_out: bool = False
    not_interested_reason: Optional[str] = None
    email_consent_at: Optional[datetime] = None    # they agreed on the call to get an email from us
    roi_estimate: Optional[Dict[str, Any]] = None  # what estimate_roi told them, for the call record
    # What the learning loop counts (SALES_LEARNING_PLAN.md Phase 0): next steps a tool confirmed.
    events: List[str] = field(default_factory=list)
    info_status: Optional[str] = None              # the info email: sent | queued | suppressed
    whatsapp_consent_at: Optional[datetime] = None
    memory: List[str] = field(default_factory=list)            # <earlier_calls> lines (Phase 1)
    playbook: Optional[Dict[str, Any]] = None                  # the playbook this call uses (Phase 3)

    def clock(self) -> datetime:
        return self.now or datetime.now(timezone.utc)

    @property
    def variant(self) -> Dict[str, Any]:
        return of.variant(self.offer, self.variant_id)

    def add_event(self, event: str) -> None:
        """A next step a tool confirmed (meeting, info_email, info_whatsapp, callback, transfer)."""
        if event not in self.events:
            self.events.append(event)

    def set_outcome(self, outcome: str) -> None:
        """Keep the strongest outcome: an opt-out beats a booked meeting beats "interested"."""
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
        if self.dry_run:
            return
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


def _meeting(ctx: SalesContext) -> Dict[str, Any]:
    return dict(ctx.offer.get("meeting") or {})


def _service_name(ctx: SalesContext) -> str:
    return str(_meeting(ctx).get("service_name") or settings.SALES_DEMO_SERVICE)


def email_values(ctx: SalesContext, *, name: str = "", when: str = "") -> Dict[str, str]:
    meeting = _meeting(ctx)
    url = str(meeting.get("url") or settings.SALES_DEMO_MEETING_URL or "")
    name = name or str(ctx.lead.get("contact_name") or "").strip()
    return {"name": name or "there", "first_name": name.split()[0] if name else "there",
            "business": str(ctx.lead.get("business_name") or "").strip() or "your business",
            "company": str(ctx.offer["company_name"]), "persona": str(ctx.offer.get("persona_name") or ""),
            "meeting_label": str(meeting.get("label") or "demo"), "meeting_url": f"Join here: {url}" if url else "",
            "when": when, "host": persona.host_name(ctx.offer), "website": str(ctx.offer.get("website") or "")}


def render_email(template: Mapping[str, Any], values: Mapping[str, str]) -> tuple[str, str]:
    subject = of.render(str(template.get("subject") or ""), values).replace("\n", " ").strip()
    body = re.sub(r"\n{3,}", "\n\n", of.render(str(template.get("body") or ""), values)).strip()
    return subject, body + _footer()


def meeting_invite(ctx: SalesContext, appt: Mapping[str, Any], *, name: str, email: str) -> str:
    """A calendar invite for the prospect (the meeting is already on our own calendar)."""
    meeting = _meeting(ctx)
    company = str(ctx.offer["company_name"])
    url = str(meeting.get("url") or settings.SALES_DEMO_MEETING_URL or "")
    host = persona.host_name(ctx.offer)
    org_name, org_email = parseaddr(settings.EMAIL_FROM or "")
    label = str(meeting.get("label") or "meeting")
    return event_ics(f"{appt['appointment_id']}@sales", appt["start"], appt["end"],
                     f"{company} {label} with {host}",
                     f"{label.capitalize()} with {host} of {company}." + (f" Join: {url}" if url else "")
                     + " Reply to the confirmation email to change the time.",
                     url=url, organizer_email=org_email, organizer_name=org_name or company,
                     attendee_email=email, attendee_name=name, now=ctx.clock())


def _weekday_problem(day: date, weekday: Any) -> Optional[Dict[str, Any]]:
    """The model names the weekday it means; a date that falls on another day was worked out wrong."""
    said = str(weekday or "").strip().lower()
    if not said:
        return None
    actual = day.strftime("%A")
    if said[:3] != actual.lower()[:3]:
        return {"error": "weekday_mismatch",
                "message": f"{day:%Y-%m-%d} is a {actual}, not a {str(weekday).strip()}. Work the date out again "
                           "from today's date in 'Right now', then try again."}
    return None


# ── Meetings ─────────────────────────────────────────────────────────────────

async def check_meeting_slots(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    svc = _scheduler(ctx)
    label = _meeting(ctx).get("label") or "meeting"
    if svc is None:
        return {"error": "unavailable", "message": f"The calendar isn't available. Offer to have someone email them {label} times instead."}
    now = ctx.clock()
    today = svc.local_today(now)
    try:
        day = date.fromisoformat(str(args.get("date") or "")[:10])
    except ValueError:
        day = today
    else:
        problem = _weekday_problem(day, args.get("weekday"))
        if problem:
            return problem
    day = max(day, today)
    service = svc.match_service(await svc.services(), _service_name(ctx))
    slots = await svc.find_slots(service=service, day_from=day, day_to=day + timedelta(days=MEETING_SEARCH_DAYS),
                                 requested_at=svc.parse_local(day, args.get("time")), now=now)
    if not slots:
        return {"available": False, "slots": [],
                "message": "No times are open in the next week. Offer to email them some times instead."}
    zone = ctx.lead_zone
    local_today = now.astimezone(ZoneInfo(zone)).date()
    return {"available": True,
            "slots": [{"slot_id": s.slot_id, "say": av.spoken(s.start, zone, today=local_today)} for s in slots],
            "message": "Offer two of these times in words; they are in the lead's local time. "
                       "When they pick one, confirm their name and email, then call book_meeting."}


async def book_meeting(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    svc = _scheduler(ctx)
    if svc is None:
        return {"error": "unavailable", "message": "The calendar isn't available. Offer to have someone email them instead."}
    decoded = av.decode_slot_id(str(args.get("slot_id") or ""))
    if not decoded:
        return {"error": "bad_slot", "message": "Use a slot_id from check_meeting_slots."}
    resource_id, start = decoded
    name = str(args.get("name") or ctx.lead.get("contact_name") or "").strip()[:120]
    if not name:
        return {"error": "name_required", "message": "Ask for their name."}
    email = _valid_email(args.get("email")) or _valid_email(ctx.lead.get("email"))
    if not email:
        return {"error": "email_required", "message": "Ask for the email address to send the invite to, and read it back."}
    business = ctx.lead.get("business_name") or "a lead"
    label = str(_meeting(ctx).get("label") or "meeting")
    if ctx.dry_run:
        when = av.spoken(start, ctx.lead_zone)
        ctx.set_outcome("demo_booked")
        ctx.add_event("meeting")
        ctx.next_step = f"{label.capitalize()} {when} (test call: nothing booked)"
        return {"success": True, "appointment_id": "test", "when": when,
                "message": f"Booked for {when}. Tell them the invite is on its way to {email}, then wrap up."}
    service = svc.match_service(await svc.services(), _service_name(ctx))
    try:
        appt = await svc.book(resource_id=resource_id, start=start, service=service, name=name,
                              phone=ctx.lead["phone"], email=email, source="outbound_sales", call_id=ctx.call_id,
                              notes=f"{label.capitalize()} with {business} about {ctx.offer.get('name')}. "
                                    "Booked by the sales assistant.", now=ctx.clock())
    except SchedulingError as e:
        return {"error": e.code, "message": e.message}

    when = av.spoken(appt["start"], ctx.lead_zone)
    meeting = {"appointment_id": appt["appointment_id"], "start": appt["start"], "offer_id": ctx.offer.get("id")}
    await ctx.update_lead({"status": "demo_booked", "contact_name": name, "email": email, "demo": meeting})
    await ctx.db[m.LEADS].update_one({"_id": ctx.lead["_id"]}, {"$push": {"meetings": meeting}})
    await followup.add_activity(ctx.db, ctx.lead["_id"], "meeting", f"{label.capitalize()} booked for {when}",
                                by="sales_agent", meta={"appointment_id": appt["appointment_id"]}, now=ctx.clock())
    ctx.set_outcome("demo_booked")
    ctx.add_event("meeting")
    ctx.email_consent_at = ctx.clock()
    ctx.next_step = f"{label.capitalize()} {when}"
    values = email_values(ctx, name=name, when=when)
    subject, body = render_email(ctx.offer.get("confirmation_email") or {}, values)
    invite = meeting_invite(ctx, appt, name=name, email=email)
    fire(followup.send_sales_email(ctx.db, to=email, subject=subject, text=body, kind="meeting_confirmation",
                                   lead_id=ctx.lead["_id"], attempt_id=ctx.attempt_id,
                                   attachments=[followup.ics_attachment(invite)], now=ctx.clock()))
    assert ctx.tenant is not None
    fire(notify_owner(ctx.tenant, "on_new_booking",
                      f"{label.capitalize()} booked by the sales assistant ({ctx.offer.get('name')}): {business}\n"
                      f"{name} — {ctx.lead['phone']} — {email}\n{when}",
                      subject=f"{label.capitalize()} booked"))
    return {"success": True, "appointment_id": appt["appointment_id"], "when": when,
            "message": f"Booked for {when}. Tell them the invite is on its way to {email}, then wrap up."}


# ── Follow-ups ───────────────────────────────────────────────────────────────

async def send_info_email(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    email = _valid_email(args.get("email")) or _valid_email(ctx.lead.get("email"))
    if not email:
        return {"error": "email_required", "message": "Ask for the email address and read it back to confirm."}
    await ctx.update_lead({"email": email, "info_email_requested_at": ctx.clock()})
    ctx.set_outcome("interested")
    ctx.email_consent_at = ctx.clock()
    ctx.next_step = ctx.next_step or f"Sent information to {email}"
    label = _meeting(ctx).get("label") or "meeting"
    if ctx.dry_run:
        ctx.info_status = "sent"
        ctx.add_event("info_email")
        return {"success": True, "sent": True, "message": f"Sent to {email}. Offer a {label} time anyway."}
    subject, body = render_email(ctx.offer.get("info_email") or {}, email_values(ctx))
    result = await followup.send_sales_email(ctx.db, to=email, subject=subject, text=body, kind="info",
                                             lead_id=ctx.lead["_id"], attempt_id=ctx.attempt_id, now=ctx.clock())
    sent = result["status"] == "sent"
    # Only an email that went (or will go, from the outbox) is a next step: a suppressed one isn't.
    ctx.info_status = "sent" if sent else ("suppressed" if result["status"] == "suppressed" else "queued")
    if ctx.info_status != "suppressed":
        ctx.add_event("info_email")
    if result["status"] == "suppressed":
        return {"success": True, "sent": False,
                "message": "They asked us earlier not to email them, so nothing was sent. Offer a call-back instead."}
    if not sent:
        # Stored on the lead, so a person can send it by hand.
        return {"success": True, "sent": False,
                "message": f"Noted. Tell them the information will be emailed today. Then offer a {label} time."}
    return {"success": True, "sent": True, "message": f"Sent to {email}. Offer a {label} time anyway."}


def _link(url: str) -> str:
    url = str(url or "").strip()
    return url if not url or re.match(r"^https?://", url, re.I) else f"https://{url}"


def whatsapp_params(ctx: SalesContext) -> list[str]:
    """The template's {{1}} {{2}} {{3}}: their first name, our company, the page to book on."""
    name = str(ctx.lead.get("contact_name") or "").strip()
    link = _link(of.booking_url(ctx.offer)) or str(_meeting(ctx).get("url") or settings.SALES_DEMO_MEETING_URL or "")
    return [name.split()[0] if name else "there", str(ctx.offer["company_name"]), link or "-"]


async def send_whatsapp_info(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """They agreed on the call to get the details on WhatsApp: save the number and their OK, send the
    approved template, and leave the team a task. Never in a test call."""
    from core.integrations import whatsapp
    from core.outbound import phones

    channels = ctx.offer.get("channels") or {}
    if not (channels.get("whatsapp") and channels.get("whatsapp_template")):
        return {"error": "off", "message": "WhatsApp isn't available for this offer. Offer email instead."}
    if ctx.lead.get("status") == "dnc" or ctx.opted_out:
        return {"error": "dnc", "message": "They asked not to be contacted. Don't send anything; say goodbye."}
    raw = str(args.get("number") or "").strip()
    same = not raw or raw.lower() in ("this", "this number", "same", "same number", "this one")
    info = phones.parse(ctx.lead.get("phone") if same else raw, str(ctx.lead.get("country") or "US"))
    if info is None:
        return {"error": "bad_number", "message": "That number doesn't look right. Ask for it again, digit by digit, "
                                                  "and read it back."}
    if same and phones.is_fixed_line(info.e164):
        return {"error": "landline", "message": "This is a landline, so it can't get WhatsApp. Ask for their mobile "
                                                "number, or offer email instead."}
    now = ctx.clock()
    await ctx.update_lead({"whatsapp": info.e164, "whatsapp_consent_at": now,
                           "whatsapp_consent_attempt_id": ctx.attempt_id})
    ctx.whatsapp_consent_at = now
    ctx.add_event("info_whatsapp")      # sent now, or by the team today from the task below
    ctx.set_outcome("interested")
    ctx.next_step = ctx.next_step or "Sent details on WhatsApp"
    label = _meeting(ctx).get("label") or "meeting"
    if ctx.dry_run:
        return {"success": True, "sent": True, "message": f"Sent on WhatsApp. Offer a {label} time anyway."}
    result = await whatsapp.send_template(info.e164, str(channels["whatsapp_template"]),
                                          str(channels.get("whatsapp_language") or "en"), whatsapp_params(ctx))
    owner = str(ctx.campaign.get("owner_id") or "")
    if result.ok:
        await followup.add_activity(ctx.db, ctx.lead["_id"], "whatsapp", f"Details sent on WhatsApp to {info.e164}",
                                    by="sales_agent", now=now)
        await followup.create_task(ctx.db, ctx.lead["_id"], "Follow up on WhatsApp", owner=owner, source="sales_agent",
                                   attempt_id=ctx.attempt_id, due_at=now + timedelta(days=1), by="sales_agent", now=now)
        return {"success": True, "sent": True, "message": f"Sent on WhatsApp. Offer a {label} time anyway."}
    logger.warning("Sales WhatsApp template not sent for lead %s: %s", ctx.lead["_id"], result.error)
    await followup.add_activity(ctx.db, ctx.lead["_id"], "whatsapp",
                                f"Agreed to WhatsApp on {info.e164}, but it couldn't be sent automatically: "
                                f"{(result.error or 'unknown error')[:200]}", by="sales_agent", now=now)
    await followup.create_task(ctx.db, ctx.lead["_id"], f"Send the details on WhatsApp to {info.e164}", owner=owner,
                               source="sales_agent", attempt_id=ctx.attempt_id, due_at=now, by="sales_agent", now=now)
    return {"success": True, "sent": False,
            "message": f"Noted. Tell them the team will message them on WhatsApp today. Then offer a {label} time."}


async def estimate_roi(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Their answers × the offer's figures, said as a rough estimate. The model never does the maths."""
    roi = ctx.offer.get("roi") or {}
    inputs = list(roi.get("inputs") or [])
    if not (roi.get("enabled") and inputs):
        return {"error": "off", "message": "There's no estimate for this offer. Carry on without one."}
    answers = args.get("answers")
    answers = list(answers) if isinstance(answers, (list, tuple)) else []
    got = of.roi_estimate(roi, answers)
    if "error" in got:
        spec = inputs[got["index"]]
        if got["error"] == "too_big":
            return {"error": "check", "message": f"That number for {spec['label']} seems too big. Check it with them: "
                                                 f"\"{spec['question']}\""}
        return {"error": "need", "message": f"You still need {spec['label']}. Ask: \"{spec['question']}\""}
    currency = str(ctx.offer.get("deal_currency") or "USD")
    label = str(roi.get("result_label") or "").strip()
    amount = got["amount"]
    if amount <= 0:
        return {"error": "zero", "message": "With those numbers there's nothing to estimate. Move on without a figure."}
    sentence = f"Using your numbers, that's roughly {of.spoken_amount(amount, currency)}"
    if label:
        sentence += (" " if label.lower().startswith(("a ", "an ", "in ", "of ", "per ", "every ")) else " of ") + label
    sentence += "."
    pricing = ctx.offer.get("pricing") or {}
    cost = roi.get("monthly_cost")
    if cost and got["monthly"] and pricing.get("mode") == "range" and amount >= float(cost) * 1.5:
        sentence += f" That's about {of.number_words(round(amount / float(cost)))} times what it costs."
    sentence += " It's only an estimate, not a promise."
    how = "; ".join([f"{x['label']}: {a}" for x, a in zip(inputs, answers)]
                    + [f"{f['label']}: {float(f['value']):g}{'%' if f.get('percent') else ''}" for f in roi.get("factors") or []])
    estimate = {"amount": of.round_figure(amount), "currency": currency, "label": label, "answers": answers[:len(inputs)],
                "at": ctx.clock()}
    await ctx.update_lead({"roi_estimate": estimate})
    ctx.roi_estimate = estimate
    return {"success": True, "say": sentence,
            "how": f"If they ask how: {how}" + (" (week figures are turned into a month)" if any(
                x.get("per") == "week" for x in inputs) else ""),
            "message": "Say the 'say' sentence in your own voice, then connect it to the meeting."}


async def schedule_callback(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    raw = str(args.get("when") or "").strip()
    try:
        local = datetime.fromisoformat(raw)
    except ValueError:
        return {"error": "bad_time", "message": "Give the day and time as YYYY-MM-DDTHH:MM in their local time."}
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(ctx.lead_zone))
    problem = _weekday_problem(local.date(), args.get("weekday"))
    if problem:
        return problem
    when = local.astimezone(timezone.utc)
    now = ctx.clock()
    if when <= now:
        return {"error": "in_past", "message": "That time has passed. Ask for a time later today or another day."}
    if when > now + timedelta(days=MAX_CALLBACK_DAYS):
        return {"error": "too_far", "message": "That is more than a month away. Offer to email them instead."}
    if ctx.campaign.get("calling_window") and not gate.window_open(ctx.lead, ctx.campaign, when):
        nxt = gate.next_window(ctx.lead, ctx.campaign, when)
        suggestion = f" The nearest time we can call is {av.spoken(nxt, ctx.lead_zone)}." if nxt else ""
        return {"error": "outside_hours",
                "message": "We're not allowed to call at that time." + suggestion + " Ask if that, or another "
                           "time inside business hours, works."}
    ctx.callback_at = when
    ctx.set_outcome("callback")
    ctx.add_event("callback")
    note = str(args.get("note") or "").strip()[:200]
    await ctx.update_lead({"callback_note": note, "callback_requested_at": now})
    spoken = av.spoken(when, ctx.lead_zone)
    ctx.next_step = f"Call back {spoken}" + (f" — {note}" if note else "")
    return {"success": True, "when": spoken, "message": f"Noted: call back {spoken}. Say goodbye."}


async def add_to_dnc(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """They asked not to be called. Immediate and irreversible from the agent's side."""
    if not ctx.dry_run:
        await registry.add_to_dnc(ctx.db, ctx.lead["phone"], reason=str(args.get("reason") or "requested_on_call"),
                                  call_id=ctx.call_id, source="sales_agent", now=ctx.clock())
    ctx.lead["status"] = "dnc"
    ctx.opted_out = True
    ctx.set_outcome("dnc")
    return {"success": True, "message": "They are on the do-not-call list. Apologise once, say goodbye, and call end_call."}


async def mark_not_interested(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Not interested in this offer — not a request to stop calling. The offer is not pitched to them
    again and other offers wait (outcomes.decline_offer, when the call ends)."""
    ctx.not_interested_reason = str(args.get("reason") or "").strip()[:200] or None
    ctx.set_outcome("not_interested")
    return {"success": True, "message": "Noted. Thank them for their time, say goodbye, and call end_call."}


async def mark_wrong_number(ctx: SalesContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    ctx.set_outcome("wrong_number")
    return {"success": True, "message": "Noted. Apologise for the mistake and call end_call."}
