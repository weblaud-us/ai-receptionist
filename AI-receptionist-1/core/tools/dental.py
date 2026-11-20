"""
Dental vertical tools (core/prompts/verticals/dental.py).

    triage_dental_emergency   an urgent patient: saved as an urgent lead, the practice is alerted,
                              and the reply carries the escalation (the emergency number comes from
                              code, never from the model)
    qualify_cosmetic_lead     an implant or cosmetic enquiry: saved as a qualified lead with its
                              estimated pipeline value, taken from the tenant's services

Both write to the tenant's `leads` collection, the same one `capture_lead` uses, so the dashboard
Leads page, the `lead.created` webhook and data-subject export and erasure (keyed on `phone`)
cover them. A repeat call within the same phone call updates the lead instead of adding a second
one, so a model that calls a tool twice never creates a duplicate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.integrations import webhooks
from core.notifications import fire, notify_owner
from core.prompts.builder import emergency_number
from core.sensitive import LEAD_TEXT_FIELDS, seal
from core.tenancy.ids import new_id
from core.timeutils import business_now

if TYPE_CHECKING:
    from core.repositories import Repos
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

SEVERITIES = ("life_threatening", "urgent", "routine")
TREATMENTS = ("implants", "all_on_4", "clear_aligners", "veneers", "composite_bonding", "whitening", "other_cosmetic")
PATIENT_STATUSES = ("new", "existing", "unknown")
TIMEFRAMES = ("asap", "within_1_month", "1_3_months", "researching")
FUNDING = ("health_fund", "finance", "both", "none", "unknown")

TREATMENT_LABELS = {
    "implants": "Dental implants", "all_on_4": "All-on-4 / full-arch implants", "clear_aligners": "Clear aligners",
    "veneers": "Veneers", "composite_bonding": "Composite bonding", "whitening": "Teeth whitening",
    "other_cosmetic": "Cosmetic treatment",
}
# Words in a service name that mean it belongs to a treatment (used to find its value).
_TREATMENT_WORDS = {
    "implants": ("implant",), "all_on_4": ("all-on-4", "all on 4", "full arch", "full-arch"),
    "clear_aligners": ("invisalign", "aligner", "clear"), "veneers": ("veneer",),
    "composite_bonding": ("bonding", "composite"), "whitening": ("whitening", "whiten"), "other_cosmetic": (),
}

MAX_SUMMARY = 300
MAX_NOTES = 500

TRIAGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "triage_dental_emergency",
        "description": (
            "Record an urgent dental problem so the practice calls back first and is alerted now. Use for "
            "red-flag emergencies (after telling the caller the emergency number) and for urgent problems such "
            "as severe toothache, a broken tooth or a lost crown. Read the callback number back first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symptom_summary": {"type": "string",
                                    "description": "The problem in a few words, e.g. 'severe pain lower left, 2 days'"},
                "severity_level": {"type": "string", "enum": list(SEVERITIES)},
                "swelling_or_bleeding_red_flag": {
                    "type": "boolean",
                    "description": "True if there is spreading facial swelling, swelling affecting breathing, "
                                   "swallowing or an eye, bleeding that won't stop, or a head injury",
                },
                "patient_name": {"type": "string"},
                "callback_phone": {"type": "string", "description": "Callback number with country code"},
            },
            "required": ["symptom_summary", "severity_level", "swelling_or_bleeding_red_flag"],
        },
    },
}

QUALIFY_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "qualify_cosmetic_lead",
        "description": (
            "Record an implant or cosmetic treatment enquiry for the treatment coordinator, once you know the "
            "treatment, whether they're a new or existing patient, their timeframe and how they'd pay. Then "
            "offer to book the consultation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "treatment_category": {"type": "string", "enum": list(TREATMENTS)},
                "patient_status": {"type": "string", "enum": list(PATIENT_STATUSES)},
                "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                "health_fund_or_finance_interest": {"type": "string", "enum": list(FUNDING)},
                "service_name": {"type": "string",
                                 "description": "The matching treatment or consultation from the facts, if any"},
                "caller_name": {"type": "string"},
                "callback_phone": {"type": "string", "description": "Callback number with country code"},
                "notes": {"type": "string", "description": "Anything else useful for the coordinator, briefly"},
            },
            "required": ["treatment_category", "patient_status", "timeframe", "health_fund_or_finance_interest"],
        },
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pick(value: Any, allowed: tuple, default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


async def _save_lead(ctx: "ToolContext", repos: "Repos", kind: str, lead: Dict[str, Any]) -> tuple[str, bool]:
    """Insert the lead, or update this call's earlier one of the same kind. → (lead_id, created).
    Symptoms and notes are stored encrypted (core/sensitive.seal) for clinical-encryption tenants."""
    if ctx.tenant_cfg.clinical_encryption:
        lead = seal(lead, LEAD_TEXT_FIELDS)
    if ctx.call_id:
        existing = await repos.leads.find_one({"call_id": ctx.call_id, "kind": kind}, {"_id": 1})
        if existing:
            await repos.leads.update_one({"_id": existing["_id"]}, {"$set": {**lead, "updated_at": _utcnow()}})
            return existing["_id"], False
    doc = {"_id": new_id("lead"), "kind": kind, "status": "new", "channel": ctx.channel, "call_id": ctx.call_id,
           "created_at": _utcnow(), **lead}
    await repos.leads.insert_one(doc)
    return doc["_id"], True


def _emit(ctx: "ToolContext", lead_id: str, kind: str, lead: Dict[str, Any]) -> None:
    webhooks.emit(ctx.tenant, "lead.created", {"lead_id": lead_id, "kind": kind, "channel": ctx.channel,
                                               "call_id": ctx.call_id, **lead})


# ── triage_dental_emergency ──────────────────────────────────────────────────

async def triage_dental_emergency(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    t = ctx.tenant_cfg
    summary = " ".join(str(args.get("symptom_summary") or "").split())[:MAX_SUMMARY]
    if not summary:
        return {"error": "symptom_summary_required", "message": "Ask briefly what the problem is."}
    severity = _pick(args.get("severity_level"), SEVERITIES, "urgent")
    red_flag = bool(args.get("swelling_or_bleeding_red_flag"))
    if red_flag:
        severity = "life_threatening"
    name = str(args.get("patient_name") or "").strip()[:120]
    phone = str(args.get("callback_phone") or ctx.caller_phone or "").strip()[:32]

    number = emergency_number(t.country)
    country = (t.country or "").upper()
    if severity == "life_threatening":
        escalation = "emergency_services"
        guidance = (f"Make sure they have been told to call {number} now, or go to the nearest emergency "
                    "department. Don't book an appointment. The practice has been alerted.")
    elif severity == "urgent":
        escalation = "same_day"
        guidance = ("Saved as urgent and the practice has been alerted. Offer the earliest emergency appointment "
                    "with check_availability; if none suits, say the team will call back as soon as possible.")
        if country == "GB" and not t.is_open(business_now(t.timezone, ctx.now)):
            guidance += " While the practice is closed they can also call NHS 111 for urgent dental advice."
    else:
        escalation = "routine"
        guidance = "Saved. Offer the next available appointment with check_availability."

    if not phone and severity != "life_threatening":
        return {"error": "phone_required", "message": "Ask for a callback number, then try again."}

    repos = ctx.repos()
    contact_id = None
    if phone:
        contact = await repos.contacts.upsert_by_phone(phone, {"name": name})
        contact_id = contact.get("_id")
    lead = {
        "contact_id": contact_id, "name": name or "Unknown", "phone": phone, "address": "",
        "problem": summary,
        "urgency": "emergency" if severity == "life_threatening" else "today" if severity == "urgent" else "this_week",
        "preferred_time": "", "severity": severity, "red_flag": red_flag, "escalation": escalation,
    }
    lead_id, created = await _save_lead(ctx, repos, "dental_emergency", dict(lead))
    if created:
        urgent = severity != "routine"
        label = "EMERGENCY (told to call " + number + ")" if severity == "life_threatening" else (
            "Urgent dental problem" if urgent else "Dental problem")
        text = f"{'🔴 ' if urgent else ''}{label}\nFrom: {lead['name']} — {phone or 'no number'}\n\n{summary}"
        fire(notify_owner(t, "on_message", text, subject="Urgent dental call", urgent=urgent))
        _emit(ctx, lead_id, "dental_emergency", lead)
    logger.info("Dental triage lead %s (%s)", lead_id, severity)
    out: Dict[str, Any] = {"success": True, "lead_id": lead_id, "escalation": escalation, "message": guidance}
    if severity == "life_threatening":
        out["emergency_number"] = number
    return out


# ── qualify_cosmetic_lead ────────────────────────────────────────────────────

def estimate_value(services: List[Dict[str, Any]], treatment: str, service_name: Optional[str]) -> Optional[int]:
    """Pipeline value of a lead, in minor units: the named service's value, else the best match by
    treatment words, else the first service in the right category (implant / cosmetic). None = unknown."""
    live = [s for s in services if s.get("active") is not False and isinstance(s.get("estimated_value_minor"), int)]
    wanted = (service_name or "").strip().lower()
    if wanted:
        for s in live:
            if str(s.get("name") or "").strip().lower() == wanted:
                return int(s["estimated_value_minor"])
    category = "implant" if treatment in ("implants", "all_on_4") else "cosmetic"
    in_category = [s for s in live if s.get("category") == category]
    for word in _TREATMENT_WORDS.get(treatment, ()):
        for s in in_category or live:
            if word in str(s.get("name") or "").lower():
                return int(s["estimated_value_minor"])
    return int(in_category[0]["estimated_value_minor"]) if in_category else None


async def qualify_cosmetic_lead(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    t = ctx.tenant_cfg
    treatment = _pick(args.get("treatment_category"), TREATMENTS, "other_cosmetic")
    status = _pick(args.get("patient_status"), PATIENT_STATUSES, "unknown")
    timeframe = _pick(args.get("timeframe"), TIMEFRAMES, "researching")
    funding = _pick(args.get("health_fund_or_finance_interest"), FUNDING, "unknown")
    name = str(args.get("caller_name") or "").strip()[:120]
    phone = str(args.get("callback_phone") or ctx.caller_phone or "").strip()[:32]
    notes = " ".join(str(args.get("notes") or "").split())[:MAX_NOTES]
    service_name = str(args.get("service_name") or "").strip()[:120]
    if not phone:
        return {"error": "phone_required", "message": "Ask for a callback number, then try again."}

    repos = ctx.repos()
    services = await repos.services.find({"active": {"$ne": False}}, limit=200,
                                         projection={"name": 1, "category": 1, "estimated_value_minor": 1,
                                                     "active": 1})
    value = estimate_value(services, treatment, service_name)
    high = timeframe in ("asap", "within_1_month")
    contact = await repos.contacts.upsert_by_phone(phone, {"name": name})
    label = TREATMENT_LABELS[treatment]
    lead = {
        "contact_id": contact.get("_id"), "name": name or "Unknown", "phone": phone, "address": "",
        "problem": f"{label} enquiry" + (f": {notes}" if notes else ""),
        "urgency": "today" if high else "this_week", "preferred_time": "",
        "treatment_category": treatment, "patient_status": status, "timeframe": timeframe,
        "funding_interest": funding, "service_name": service_name or None, "notes": notes,
        "estimated_value_minor": value, "currency": t.currency, "priority": "high" if high else "normal",
    }
    lead_id, created = await _save_lead(ctx, repos, "cosmetic_consult", dict(lead))
    if created:
        text = (f"{'⭐ ' if high else ''}New {label.lower()} enquiry ({status} patient, "
                f"{timeframe.replace('_', ' ')})\nFrom: {lead['name']} — {phone}")
        if funding not in ("none", "unknown"):
            text += f"\nInterested in: {funding.replace('_', ' ')}"
        if notes:
            text += f"\n\n{notes}"
        fire(notify_owner(t, "on_message", text, subject=f"New {label.lower()} enquiry"))
        _emit(ctx, lead_id, "cosmetic_consult", lead)
    logger.info("Cosmetic lead %s (%s, %s)", lead_id, treatment, timeframe)
    return {"success": True, "lead_id": lead_id,
            "message": ("Saved for the treatment coordinator. Offer to book the consultation now with "
                        "check_availability, or say the coordinator will call them back"
                        + (" today." if high else " soon."))}
