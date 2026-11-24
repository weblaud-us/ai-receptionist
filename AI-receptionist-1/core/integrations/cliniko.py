"""
Cliniko (Australian practice-management system) — REST API v1, behind core.services.scheduling_provider.

- Base URL is per shard: https://api.{shard}.cliniko.com/v1. The shard is the API key's suffix
  (…-au1, …-au2, …-uk1); keys without one are au1.
- HTTP Basic auth: the API key as user name, blank password. Cliniko requires a User-Agent with the
  app name and a contact e-mail (settings.CLINIKO_USER_AGENT).
- Lists page with `links.next` (per_page ≤ 100). `available_times` takes at most one week per
  request, so longer ranges are asked for week by week (⚠ VERIFY the limit against current docs).
- A booking creates an `individual_appointment` for a patient found by name and phone, or a new
  one. If the create request times out, we look for that patient's appointment at that time
  before reporting a failure, so a slow Cliniko never produces a double booking.

⚠ VERIFY field names (starts_at / ends_at, the cancel endpoint and its reason code) against the
Cliniko API docs and a test account before the first customer goes live.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings
from core.services import pms_cache
from core.services.scheduling_provider import (
    RECALL_LOOKBACK_DAYS, AppointmentType, CreatedAppointment, LapsedPatient, Patient, PMSError, Practitioner,
    ProviderAppointment, ProviderSlot, lapsed_from_visits, pick_patient, request_json, split_name,
)

logger = logging.getLogger(__name__)

DEFAULT_SHARD = "au1"
_SHARD_RE = re.compile(r"-([a-z]{2}\d{1,2})$")
MAX_PAGES = 10
RECALL_MAX_PAGES = 60            # appointments scanned for recall (100 per page); runs in the jobs worker
MAX_WEEKS = 2                    # a call asks about a day or a few; never scan months live
MAX_PRACTITIONERS = 6            # "anyone": ask about at most this many diaries at once
PARALLEL_REQUESTS = 4            # availability requests in flight at once, per lookup
CANCEL_REASON_OTHER = 50         # ⚠ VERIFY: Cliniko's cancellation reason code for "Other"


def shard_of(api_key: str) -> str:
    m = _SHARD_RE.search((api_key or "").strip())
    return m.group(1) if m else ((settings.CLINIKO_SHARD or "").strip().lower() or DEFAULT_SHARD)


def base_url(api_key: str) -> str:
    return f"https://api.{shard_of(api_key)}.cliniko.com/v1"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class ClinikoProvider:
    name = "cliniko"
    requires_type_mapping = True

    def __init__(self, api_key: str, *, business_id: str = "", timezone: str = "UTC") -> None:
        self.api_key = api_key.strip()
        self.business_id = business_id
        self.timezone = timezone
        self.base = base_url(self.api_key)

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json",
                "User-Agent": settings.CLINIKO_USER_AGENT.strip() or "LaudLine (support)"}

    async def _call(self, method: str, path: str, *, params: Any = None, json_body: Any = None,
                    write: bool = False) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        if not await pms_cache.spend(self.name, self.api_key):
            raise PMSError("too many diary requests this minute", status=429)
        return await request_json(method, url, headers=self._headers(), auth=httpx.BasicAuth(self.api_key, ""),
                                  params=params, json_body=json_body, write=write)

    async def _list(self, path: str, key: str, params: Optional[Dict[str, Any]] = None,
                    max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        url: Optional[str] = path
        query: Any = {"per_page": 100, **(params or {})}
        for _ in range(max_pages):
            if not url:
                break
            page = await self._call("GET", url, params=query)
            out.extend(page.get(key) or [])
            url = ((page.get("links") or {}).get("next")) or None
            query = None                              # the next link carries its own query
        return out

    # ── Setup ─────────────────────────────────────────────────────────────

    async def test_connection(self) -> Dict[str, Any]:
        businesses = await self._list("/businesses", "businesses")
        if not businesses:
            raise PMSError("this Cliniko account has no businesses", transient=False)
        chosen = next((b for b in businesses if str(b.get("id")) == self.business_id), businesses[0])
        self.business_id = str(chosen.get("id"))
        return {"business_id": self.business_id, "business_name": chosen.get("business_name") or chosen.get("label"),
                "businesses": len(businesses), "shard": shard_of(self.api_key)}

    async def list_practitioners(self) -> List[Practitioner]:
        rows = await self._list("/practitioners", "practitioners")
        out = []
        for r in rows:
            name = r.get("display_name") or " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x)
            out.append(Practitioner(id=str(r["id"]), name=(name or "Practitioner").strip(),
                                    active=r.get("active", True) is not False))
        return out

    async def list_appointment_types(self) -> List[AppointmentType]:
        rows = await self._list("/appointment_types", "appointment_types")
        return [AppointmentType(id=str(r["id"]), name=str(r.get("name") or "Appointment"),
                                duration_min=int(r.get("duration_in_minutes") or 30),
                                active=not r.get("archived_at"))
                for r in rows]

    # ── Availability ──────────────────────────────────────────────────────

    async def _ensure_business(self) -> str:
        if not self.business_id:
            await self.test_connection()
        return self.business_id

    async def get_available_slots(self, service: Dict[str, Any], date_from: date, date_to: date,
                                  practitioner_ids: List[str]) -> List[ProviderSlot]:
        type_id = service.get("pms_id")
        if not type_id:
            raise PMSError("this service isn't linked to a Cliniko appointment type", transient=False)
        business = await self._ensure_business()
        duration = timedelta(minutes=int(service.get("duration_min") or 30))
        last = min(date_to, date_from + timedelta(days=7 * MAX_WEEKS - 1))
        # Cliniko answers one practitioner and one week per request. Asked one after another, six
        # dentists over two weeks is ~12 round trips (longer than the tool's timeout), so ask in
        # parallel, a few at a time.
        weeks: List[Tuple[date, date]] = []
        start = date_from
        while start <= last:
            end = min(last, start + timedelta(days=6))
            weeks.append((start, end))
            start = end + timedelta(days=1)
        gate = asyncio.Semaphore(PARALLEL_REQUESTS)

        async def one(pid: str, week_from: date, week_to: date) -> List[ProviderSlot]:
            async with gate:
                rows = await self._list(
                    f"/businesses/{business}/practitioners/{pid}/appointment_types/{type_id}/available_times",
                    "available_times", {"from": week_from.isoformat(), "to": week_to.isoformat()})
            out = []
            for r in rows:
                value = r.get("appointment_start") or r.get("starts_at")
                if value:
                    s = _parse(value)
                    out.append(ProviderSlot(start=s, end=s + duration, practitioner_id=str(pid)))
            return out

        parts = await asyncio.gather(*(one(pid, a, b) for pid in practitioner_ids[:MAX_PRACTITIONERS]
                                       for a, b in weeks), return_exceptions=True)
        failed = [p for p in parts if isinstance(p, BaseException)]
        if failed:
            raise failed[0]                       # every request has finished; none is left running
        slots: List[ProviderSlot] = [s for part in parts if not isinstance(part, BaseException) for s in part]
        slots.sort(key=lambda x: (x.start, x.practitioner_id))
        return slots

    # ── Patients and appointments ─────────────────────────────────────────

    async def _find_patient(self, patient: Patient) -> Optional[str]:
        first, last = split_name(patient.name)
        rows = await self._list("/patients", "patients", {"q[]": [f"last_name:={last}"]})
        return pick_patient([{"id": r["id"], "first_name": r.get("first_name"), "date_of_birth": r.get("date_of_birth"),
                              "phones": [n.get("number", "") for n in r.get("patient_phone_numbers") or []]}
                             for r in rows], patient)

    async def _create_patient(self, patient: Patient) -> str:
        first, last = split_name(patient.name)
        body: Dict[str, Any] = {"first_name": first, "last_name": last,
                                "patient_phone_numbers": [{"phone_type": "Mobile", "number": patient.phone}]}
        if patient.email:
            body["email"] = patient.email
        if patient.date_of_birth:
            body["date_of_birth"] = patient.date_of_birth.isoformat()
        row = await self._call("POST", "/patients", json_body=body, write=True)
        return str(row["id"])

    async def _existing_appointment(self, patient_id: str, start: datetime) -> Optional[str]:
        rows = await self._list("/individual_appointments", "individual_appointments",
                                {"q[]": [f"patient_id:={patient_id}", f"starts_at:={_iso(start)}"]})
        return str(rows[0]["id"]) if rows else None

    async def create_appointment(self, patient: Patient, service: Dict[str, Any], start: datetime, end: datetime,
                                 practitioner_id: str, notes: str) -> CreatedAppointment:
        business = await self._ensure_business()
        patient_id = await self._find_patient(patient) or await self._create_patient(patient)
        body = {"business_id": business, "patient_id": patient_id, "practitioner_id": practitioner_id,
                "appointment_type_id": service.get("pms_id"), "starts_at": _iso(start), "ends_at": _iso(end),
                "notes": (notes or "")[:1000]}
        try:
            row = await self._call("POST", "/individual_appointments", json_body=body, write=True)
        except PMSError as e:
            if not e.transient:
                raise
            # It may have gone through before the connection dropped: look before reporting a failure.
            existing = await self._existing_appointment(patient_id, start)
            if existing:
                logger.info("Cliniko create timed out but the appointment exists (%s)", existing)
                return CreatedAppointment(id=existing, patient_id=patient_id)
            raise
        return CreatedAppointment(id=str(row["id"]), patient_id=patient_id)

    async def cancel_appointment(self, appointment_id: str, reason: str) -> None:
        await self._call("PATCH", f"/individual_appointments/{appointment_id}/cancel", write=True,
                         json_body={"cancellation_reason": CANCEL_REASON_OTHER,
                                    "cancellation_note": (reason or "Cancelled by phone")[:200]})

    # ── A dentist's day (plan E2) ─────────────────────────────────────────

    async def list_appointments(self, date_from: date, date_to: date,
                                practitioner_ids: List[str]) -> List[ProviderAppointment]:
        """Individual appointments for these practitioners between two local days. ⚠ VERIFY the
        `practitioner_id` filter, `patient_name` and the `appointment_type` link against the Cliniko API
        docs with a real account (the field names follow the ones recall already reads)."""
        zone = ZoneInfo(self.timezone or "Australia/Sydney")
        start = datetime.combine(date_from, time.min, tzinfo=zone)
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
        out: List[ProviderAppointment] = []
        for pid in practitioner_ids[:MAX_PRACTITIONERS]:
            rows = await self._list("/individual_appointments", "individual_appointments", {"q[]": [
                f"starts_at:>={_iso(start)}", f"starts_at:<{_iso(end)}", f"practitioner_id:={pid}"]})
            for r in rows:
                begins = r.get("starts_at") or r.get("appointment_start")
                if not begins:
                    continue
                s = _parse(begins)
                finish = _parse(r["ends_at"]) if r.get("ends_at") else s + timedelta(minutes=30)
                status = ("cancelled" if r.get("cancelled_at") else "did_not_attend" if r.get("did_not_arrive")
                          else "booked")
                out.append(ProviderAppointment(
                    id=str(r.get("id")), start=s, end=finish, practitioner_id=str(pid),
                    patient_name=str(r.get("patient_name") or "").strip() or "Patient",
                    type_name=str(r.get("appointment_type_name") or ""), status=status))
        out.sort(key=lambda a: (a.start, a.practitioner_id))
        return out

    # ── Recall (patient reactivation) ─────────────────────────────────────

    async def list_lapsed_patients(self, since_date: datetime, exclude_with_future_booking: bool = True,
                                   *, now: Optional[datetime] = None) -> List[LapsedPatient]:
        """Every individual appointment from the recall lookback onwards (attended, did-not-arrive and
        cancelled are told apart), grouped per patient. ⚠ VERIFY the filter and field names
        (`did_not_arrive`, `cancelled_at`, `patient_phone_numbers[].phone_type`,
        `accepted_sms_marketing`) against the Cliniko API docs."""
        now = now or datetime.now(timezone.utc)
        earliest = now - timedelta(days=RECALL_LOOKBACK_DAYS)
        rows = await self._list("/individual_appointments", "individual_appointments",
                                {"q[]": [f"starts_at:>{_iso(earliest)}"]}, max_pages=RECALL_MAX_PAGES)
        visits = []
        for r in rows:
            start = r.get("starts_at") or r.get("appointment_start")
            patient = r.get("patient_id") or ((r.get("patient") or {}).get("links") or {}).get("self", "").rsplit("/", 1)[-1]
            if not start or not patient:
                continue
            visits.append({"id": r.get("id"), "patient_id": str(patient), "start": _parse(start),
                           "attended": not r.get("did_not_arrive"), "cancelled": bool(r.get("cancelled_at"))})
        lapsed = lapsed_from_visits(visits, since_date, now, exclude_with_future_booking)
        out: List[LapsedPatient] = []
        for pid, facts in lapsed.items():
            patient = await self._call("GET", f"/patients/{pid}")
            if patient.get("archived_at") or patient.get("deceased"):
                continue
            numbers = patient.get("patient_phone_numbers") or []
            mobile = next((n.get("number", "") for n in numbers if str(n.get("phone_type", "")).lower() == "mobile"), "")
            marketing = patient.get("accepted_sms_marketing")
            out.append(LapsedPatient(pms_patient_id=pid, first_name=str(patient.get("first_name") or "").strip(),
                                     mobile=mobile, sms_marketing_consent=marketing if isinstance(marketing, bool) else None,
                                     **facts))
        return out
