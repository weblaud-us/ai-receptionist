"""
Dentally (UK practice-management system) — REST API, behind core.services.scheduling_provider.

- Base URL settings.DENTALLY_API_BASE_URL (production https://api.dentally.co/v1; the sandbox is
  https://api.sandbox.dentally.co/v1). Bearer token from the practice's OAuth2 / API access,
  stored encrypted (core/crypto.py) like every other credential.
- Dentally books by practitioner, start and finish time with a free-text reason. There is no
  appointment-type list to map, so the business's own services (name, duration) are used as they
  are (`requires_type_mapping = False`); only practitioners are mirrored.
- Access needs an approved Dentally partner application, so the provider is offered only when
  settings.PMS_DENTALLY_ENABLED is true.

⚠ VERIFY every endpoint and field name here against the Dentally API docs and the sandbox before
enabling it for a customer. The shapes below follow the public docs as they were written; the
adapter is built so that fixing a field name is a one-line change.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings
from core.services.scheduling_provider import (
    RECALL_LOOKBACK_DAYS, AppointmentType, CreatedAppointment, LapsedPatient, Patient, PMSError, Practitioner,
    ProviderSlot, lapsed_from_visits, request_json, same_phone, split_name,
)

logger = logging.getLogger(__name__)

MAX_DAYS = 14
MAX_PRACTITIONERS = 6


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


class DentallyProvider:
    name = "dentally"
    requires_type_mapping = False

    def __init__(self, token: str, *, site_id: str = "", timezone: str = "Europe/London") -> None:
        self.token = token.strip()
        self.site_id = site_id
        self.timezone = timezone
        self.base = settings.DENTALLY_API_BASE_URL.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}"}

    async def _call(self, method: str, path: str, *, params: Any = None, json_body: Any = None,
                    write: bool = False) -> Any:
        return await request_json(method, f"{self.base}{path}", headers=self._headers(), params=params,
                                  json_body=json_body, write=write)

    async def test_connection(self) -> Dict[str, Any]:
        data = await self._call("GET", "/sites")
        sites = data.get("sites") or []
        if not sites:
            raise PMSError("this Dentally account has no sites", transient=False)
        chosen = next((s for s in sites if str(s.get("id")) == self.site_id), sites[0])
        self.site_id = str(chosen.get("id"))
        return {"site_id": self.site_id, "site_name": chosen.get("name"), "sites": len(sites)}

    async def list_practitioners(self) -> List[Practitioner]:
        data = await self._call("GET", "/practitioners", params={"active": "true"})
        out = []
        for r in data.get("practitioners") or []:
            user = r.get("user") or {}
            name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or r.get("name") or ""
            out.append(Practitioner(id=str(r["id"]), name=name.strip() or "Practitioner",
                                    active=r.get("active", True) is not False))
        return out

    async def list_appointment_types(self) -> List[AppointmentType]:
        return []                                        # Dentally books by duration and reason

    async def get_available_slots(self, service: Dict[str, Any], date_from: date, date_to: date,
                                  practitioner_ids: List[str]) -> List[ProviderSlot]:
        if not practitioner_ids:
            return []
        zone = ZoneInfo(self.timezone or "Europe/London")
        last = min(date_to, date_from + timedelta(days=MAX_DAYS - 1))
        duration = int(service.get("duration_min") or 30)
        data = await self._call("GET", "/appointments/availability", params={
            "practitioner_ids[]": practitioner_ids[:MAX_PRACTITIONERS],
            "start_time": _iso(datetime.combine(date_from, time.min, tzinfo=zone)),
            "finish_time": _iso(datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone)),
            "duration": duration,
        })
        out: List[ProviderSlot] = []
        for r in data.get("availability") or []:
            start, pid = r.get("start_time"), r.get("practitioner_id")
            if start and pid is not None:
                s = _parse(start)
                end = _parse(r["finish_time"]) if r.get("finish_time") else s + timedelta(minutes=duration)
                # A free block can be longer than the appointment: offer its start.
                out.append(ProviderSlot(start=s, end=min(end, s + timedelta(minutes=duration)),
                                        practitioner_id=str(pid)))
        out.sort(key=lambda x: (x.start, x.practitioner_id))
        return out

    async def _find_patient(self, patient: Patient) -> Optional[str]:
        _, last = split_name(patient.name)
        data = await self._call("GET", "/patients", params={"query": last})
        for r in data.get("patients") or []:
            phones = [r.get("mobile_phone") or "", r.get("home_phone") or "", r.get("work_phone") or ""]
            if any(same_phone(p, patient.phone) for p in phones):
                return str(r["id"])
        return None

    async def _create_patient(self, patient: Patient) -> str:
        first, last = split_name(patient.name)
        body: Dict[str, Any] = {"patient": {"first_name": first, "last_name": last, "mobile_phone": patient.phone,
                                            "site_id": self.site_id or None}}
        if patient.email:
            body["patient"]["email_address"] = patient.email
        data = await self._call("POST", "/patients", json_body=body, write=True)
        return str((data.get("patient") or data)["id"])

    async def _existing_appointment(self, patient_id: str, start: datetime) -> Optional[str]:
        data = await self._call("GET", "/appointments", params={"patient_id": patient_id, "on": start.date().isoformat()})
        for r in data.get("appointments") or []:
            if r.get("start_time") and _parse(r["start_time"]) == start.astimezone(timezone.utc):
                return str(r["id"])
        return None

    async def create_appointment(self, patient: Patient, service: Dict[str, Any], start: datetime, end: datetime,
                                 practitioner_id: str, notes: str) -> CreatedAppointment:
        patient_id = await self._find_patient(patient) or await self._create_patient(patient)
        body = {"appointment": {"patient_id": patient_id, "practitioner_id": practitioner_id,
                                "start_time": _iso(start), "finish_time": _iso(end),
                                "reason": str(service.get("name") or "Appointment")[:100],
                                "notes": (notes or "")[:1000]}}
        try:
            data = await self._call("POST", "/appointments", json_body=body, write=True)
        except PMSError as e:
            if not e.transient:
                raise
            existing = await self._existing_appointment(patient_id, start)
            if existing:
                return CreatedAppointment(id=existing, patient_id=patient_id)
            raise
        return CreatedAppointment(id=str((data.get("appointment") or data)["id"]), patient_id=patient_id)

    async def cancel_appointment(self, appointment_id: str, reason: str) -> None:
        await self._call("PUT", f"/appointments/{appointment_id}/cancel", write=True,
                         json_body={"reason": (reason or "Cancelled by phone")[:200]})

    # ── Recall (patient reactivation) ─────────────────────────────────────

    async def list_lapsed_patients(self, since_date: datetime, exclude_with_future_booking: bool = True,
                                   *, now: Optional[datetime] = None) -> List[LapsedPatient]:
        """Appointments since the recall lookback, grouped per patient. ⚠ VERIFY the appointment states
        (`completed` / `did_not_attend` / `cancelled`), paging and the patient fields against the sandbox."""
        now = now or datetime.now(timezone.utc)
        visits = []
        page = 1
        while page <= 60:
            data = await self._call("GET", "/appointments", params={
                "start_time": _iso(now - timedelta(days=RECALL_LOOKBACK_DAYS)), "per_page": 100, "page": page})
            rows = data.get("appointments") or []
            for r in rows:
                if not r.get("start_time") or r.get("patient_id") is None:
                    continue
                state = str(r.get("state") or "").lower()
                visits.append({"id": r.get("id"), "patient_id": str(r["patient_id"]), "start": _parse(r["start_time"]),
                               "attended": state not in ("did_not_attend", "dna", "failed"),
                               "cancelled": state == "cancelled" or bool(r.get("cancelled_at"))})
            if len(rows) < 100:
                break
            page += 1
        out: List[LapsedPatient] = []
        for pid, facts in lapsed_from_visits(visits, since_date, now, exclude_with_future_booking).items():
            data = await self._call("GET", f"/patients/{pid}")
            patient = data.get("patient") or data
            if patient.get("archived") or patient.get("deceased"):
                continue
            marketing = patient.get("marketing") if isinstance(patient.get("marketing"), bool) else None
            out.append(LapsedPatient(pms_patient_id=pid, first_name=str(patient.get("first_name") or "").strip(),
                                     mobile=str(patient.get("mobile_phone") or ""), sms_marketing_consent=marketing,
                                     **facts))
        return out
