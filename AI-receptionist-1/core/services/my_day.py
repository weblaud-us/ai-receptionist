"""
A dentist's day (plan E): who they're seeing today or tomorrow, with what the practice knows about
each patient, for the "My day" page.

    day = await day_view(repos, tenant, resource, local_day)

Rows come from two places, merged by the PMS appointment id:
- appointments booked here (by the AI or the dashboard), with the caller's history: the last call's
  summary, an emergency triage or a cosmetic enquiry, no-shows, first visit;
- with Cliniko / Dentally connected, the practice diary itself, so bookings made at the front desk
  show too. Those rows are read live (cached in this process for DIARY_TTL_S) and never stored here.

A dentist (role "dentist") only ever sees their own practitioner's day; the router enforces that.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.repositories import Repos
from core.sensitive import HAS_SUMMARY, LEAD_TEXT_FIELDS, SUMMARY_PROJECTION, unseal
from core.services import scheduling_provider as sp
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

DIARY_TTL_S = 180
SHOWN_STATUSES = ("pending", "confirmed", "completed", "no_show")
LEAD_KINDS = ("dental_emergency", "cosmetic_consult")
AI_SOURCES = ("voice", "web", "browser", "whatsapp")        # booked by the assistant

_diary: Dict[Tuple[str, str, str], Tuple[float, List[sp.ProviderAppointment]]] = {}


def reset_for_tests() -> None:
    _diary.clear()


def _bounds(tenant: TenantConfig, day: date) -> Tuple[datetime, datetime]:
    zone = ZoneInfo(tenant.timezone or "UTC")
    start = datetime.combine(day, dtime.min, tzinfo=zone).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _pms_rows(tenant: TenantConfig, resource: Dict[str, Any], day: date) -> Tuple[List[sp.ProviderAppointment], Optional[str]]:
    """The practice diary for this dentist and day. → (rows, warning). Never raises."""
    pms_id = str(resource.get("pms_id") or "")
    provider = sp.provider_for(tenant)
    if provider is None or not pms_id or resource.get("source") != provider.name:
        return [], None
    key = (tenant.id, pms_id, day.isoformat())
    hit = _diary.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1], None
    try:
        rows = list(await provider.list_appointments(day, day, [pms_id]))
    except sp.PMSError as e:
        logger.warning("Diary read for %s failed: %s", tenant.id, e)
        return [], f"The {provider.name.title()} diary isn't answering, so only bookings made here are shown."
    _diary[key] = (time.monotonic() + DIARY_TTL_S, rows)
    if len(_diary) > 2000:
        now = time.monotonic()
        for k in [k for k, (t, _) in _diary.items() if t <= now]:
            _diary.pop(k, None)
    return rows, None


async def _context(repos: Repos, appt: Dict[str, Any]) -> Dict[str, Any]:
    """What the practice already knows about this patient, from calls and enquiries."""
    out: Dict[str, Any] = {"no_shows": 0, "first_visit": None, "last_call_summary": None, "triage": None,
                           "enquiry": None}
    contact = await repos.contacts.find_one({"_id": appt["contact_id"]}) if appt.get("contact_id") else None
    if contact:
        stats = contact.get("stats") or {}
        out["no_shows"] = int(stats.get("no_shows") or 0)
        out["first_visit"] = int(stats.get("appointments") or 0) <= 1
    phone = appt.get("phone")
    if phone:
        calls = await repos.calls.find({"caller_phone": phone, **HAS_SUMMARY}, sort=[("started_at", -1)], limit=1,
                                       projection={**SUMMARY_PROJECTION, "started_at": 1})
        if calls:
            out["last_call_summary"] = unseal(calls[0], ("summary",)).get("summary")
    q: Dict[str, Any] = {"kind": {"$in": list(LEAD_KINDS)}}
    if appt.get("contact_id"):
        q["contact_id"] = appt["contact_id"]
    elif phone:
        q["phone"] = phone
    else:
        return out
    leads = await repos.leads.find(q, sort=[("created_at", -1)], limit=5)
    for raw in leads:
        lead = unseal(raw, LEAD_TEXT_FIELDS)
        if lead.get("kind") == "dental_emergency" and out["triage"] is None:
            out["triage"] = {"severity": lead.get("severity"), "problem": lead.get("problem"),
                             "red_flag": bool(lead.get("red_flag"))}
        elif lead.get("kind") == "cosmetic_consult" and out["enquiry"] is None:
            out["enquiry"] = {"treatment": lead.get("treatment_category"), "timeframe": lead.get("timeframe")}
    return out


async def day_view(repos: Repos, tenant: TenantConfig, resource: Dict[str, Any], day: date) -> Dict[str, Any]:
    start, end = _bounds(tenant, day)
    local = await repos.appointments.find(
        {"resource_id": resource["_id"], "start": {"$gte": start, "$lt": end}, "status": {"$in": list(SHOWN_STATUSES)}},
        sort=[("start", 1)], limit=200)
    rows: List[Dict[str, Any]] = []
    seen_external = set()
    for a in local:
        ext = a.get("external") or {}
        if ext.get("id"):
            seen_external.add(str(ext["id"]))
        rows.append({
            "appointment_id": a["appointment_id"],
            "source": "ai" if a.get("source") in AI_SOURCES else "dashboard",
            "booked_here": True, "start": _aware(a["start"]), "end": _aware(a.get("end")),
            "patient_name": a.get("name") or "Patient", "phone": a.get("phone") or "",
            "service": a.get("service_name") or "", "status": a.get("status"), "notes": a.get("notes") or "",
            **await _context(repos, a)})
    diary, warning = await _pms_rows(tenant, resource, day)
    for p in diary:
        if p.id in seen_external or p.status == "cancelled":
            continue
        if not (start <= p.start < end):
            continue
        rows.append({"appointment_id": None, "source": "pms", "booked_here": False, "start": p.start, "end": p.end,
                     "patient_name": p.patient_name, "phone": p.patient_phone, "service": p.type_name,
                     "status": "no_show" if p.status == "did_not_attend" else p.status, "notes": "",
                     "no_shows": 0, "first_visit": None, "last_call_summary": None, "triage": None, "enquiry": None})
    rows.sort(key=lambda r: r["start"] or start)
    return {"day": day.isoformat(), "resource": {"id": resource["_id"], "name": resource.get("name")},
            "timezone": tenant.timezone, "appointments": rows, "warning": warning,
            "pms": sp.provider_name(tenant) if sp.connected(tenant) else None}
