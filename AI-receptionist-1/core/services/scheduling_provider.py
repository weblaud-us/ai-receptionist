"""
External practice-management diaries (PMS) behind one interface: Cliniko (AU), Dentally (UK).

The built-in scheduler (core/services/scheduling.SchedulingService) stays the default system of
record, with its Google Calendar mirror. When a business connects a PMS
(`tenants.integrations.pms.provider`), SchedulingService asks the provider for slots and writes
bookings to the provider first, then mirrors them into the local `appointments` collection. The
dashboard, reminders, insights and the AI tools then work as before.

    provider = provider_for(tenant)        # None = built-in scheduling
    await provider.get_available_slots(service, date_from, date_to, practitioner_ids)

Practitioners and appointment types are mirrored into `resources` and `services` (with `source`
and `pms_id`) by core/services/pms_sync.py, so the prompt and the dashboard read local data and no
call waits on a listing request.

Every request has a short timeout (READ_TIMEOUT_S). Reads are retried once on a 5xx. Writes are
never retried blindly: providers check whether the appointment exists before trying again.
Failures raise PMSError, which the scheduling tools turn into "take a callback". A caller is
never dropped because a diary is slow.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Protocol

import httpx

from core import crypto
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

PROVIDERS = ("builtin", "cliniko", "dentally")
CONNECT_TIMEOUT_S = 2.0
READ_TIMEOUT_S = 3.5             # inside the tool's own 4 s read timeout (core/tools/registry.py)
WRITE_TIMEOUT_S = 8.0            # inside the 10 s write timeout
MAX_RETRY_AFTER_S = 2.0

# Tests replace this with httpx.MockTransport(handler).
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


class PMSError(Exception):
    """A diary request failed. `transient`: worth retrying later (timeouts, 5xx, rate limits)."""

    def __init__(self, message: str, *, transient: bool = True, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status


@dataclass(frozen=True)
class Practitioner:
    id: str
    name: str
    active: bool = True


@dataclass(frozen=True)
class AppointmentType:
    id: str
    name: str
    duration_min: int
    active: bool = True


@dataclass(frozen=True)
class ProviderSlot:
    start: datetime              # aware, UTC
    end: datetime
    practitioner_id: str


@dataclass(frozen=True)
class Patient:
    name: str
    phone: str
    email: Optional[str] = None


@dataclass(frozen=True)
class CreatedAppointment:
    id: str
    patient_id: str


@dataclass(frozen=True)
class LapsedPatient:
    """A patient due a recall, from the practice's own system (the only source recall may use)."""
    pms_patient_id: str
    first_name: str
    mobile: str                              # as stored in the PMS; normalised to E.164 by the caller
    last_visit_at: datetime                  # last completed (attended) appointment
    has_future_booking: bool = False
    next_booking_id: Optional[str] = None
    sms_marketing_consent: Optional[bool] = None   # the PMS's own flag, when it keeps one


# How far back recall looks for visits: inferred / soft-opt-in consent doesn't outlive 24 months.
RECALL_LOOKBACK_DAYS = 24 * 31


class SchedulingProvider(Protocol):
    name: str
    # True: an AI-bookable service must be mapped to one of the PMS's appointment types (Cliniko).
    # False: the PMS books by duration and reason, so local services work as they are (Dentally).
    requires_type_mapping: bool

    async def test_connection(self) -> Dict[str, Any]: ...

    async def list_practitioners(self) -> List[Practitioner]: ...

    async def list_appointment_types(self) -> List[AppointmentType]: ...

    async def get_available_slots(self, service: Dict[str, Any], date_from: date, date_to: date,
                                  practitioner_ids: List[str]) -> List[ProviderSlot]: ...

    async def create_appointment(self, patient: Patient, service: Dict[str, Any], start: datetime, end: datetime,
                                 practitioner_id: str, notes: str) -> CreatedAppointment: ...

    async def cancel_appointment(self, appointment_id: str, reason: str) -> None: ...

    async def list_lapsed_patients(self, since_date: datetime, exclude_with_future_booking: bool = True,
                                   *, now: Optional[datetime] = None) -> List[LapsedPatient]:
        """Patients whose last attended visit was before `since_date` (and within the recall lookback),
        optionally without anyone who already has an upcoming booking."""
        ...


def lapsed_from_visits(visits: List[Dict[str, Any]], since_date: datetime, now: datetime,
                       exclude_with_future_booking: bool) -> Dict[str, Dict[str, Any]]:
    """Group appointment rows {patient_id, start, attended, cancelled, id} into per-patient recall facts.
    Returns patient_id → {last_visit_at, has_future_booking, next_booking_id} for lapsed patients."""
    from datetime import timedelta, timezone

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    earliest = now - timedelta(days=RECALL_LOOKBACK_DAYS)
    last: Dict[str, datetime] = {}
    future: Dict[str, str] = {}
    for v in visits:
        pid, start = str(v.get("patient_id") or ""), v.get("start")
        if not pid or not isinstance(start, datetime) or v.get("cancelled"):
            continue
        start = aware(start)
        if start >= now:
            future.setdefault(pid, str(v.get("id") or ""))
        elif v.get("attended", True) and start >= earliest:
            last[pid] = max(last.get(pid, start), start)
    out: Dict[str, Dict[str, Any]] = {}
    for pid, seen in last.items():
        if seen >= aware(since_date):
            continue
        if exclude_with_future_booking and pid in future:
            continue
        out[pid] = {"last_visit_at": seen, "has_future_booking": pid in future, "next_booking_id": future.get(pid)}
    return out


# ── Tenant settings ──────────────────────────────────────────────────────────

def pms_settings(tenant: TenantConfig) -> Dict[str, Any]:
    return dict(tenant.integrations.pms or {})


def provider_name(tenant: TenantConfig) -> str:
    s = pms_settings(tenant)
    name = s.get("provider") or "builtin"
    return name if name in PROVIDERS and s.get("connected") and s.get("api_key_enc") else "builtin"


def connected(tenant: TenantConfig) -> bool:
    return provider_name(tenant) != "builtin"


def provider_for(tenant: TenantConfig) -> Optional[SchedulingProvider]:
    """The tenant's connected diary, or None for built-in scheduling."""
    name = provider_name(tenant)
    if name == "builtin":
        return None
    s = pms_settings(tenant)
    try:
        secret = crypto.decrypt(s["api_key_enc"])
    except crypto.DecryptionError:
        logger.error("PMS credentials for %s can't be decrypted; falling back to callbacks", tenant.id)
        return _Broken(name)
    return build_provider(name, secret, s, tenant)


def build_provider(name: str, secret: str, settings_: Dict[str, Any], tenant: TenantConfig) -> SchedulingProvider:
    if name == "cliniko":
        from core.integrations.cliniko import ClinikoProvider

        return ClinikoProvider(secret, business_id=str(settings_.get("business_id") or ""), timezone=tenant.timezone)
    if name == "dentally":
        from core.integrations.dentally import DentallyProvider

        return DentallyProvider(secret, site_id=str(settings_.get("site_id") or ""), timezone=tenant.timezone)
    raise ValueError(f"unknown PMS provider {name!r}")


class _Broken:
    """A provider whose credentials are unreadable: every call fails, so tools take a callback."""

    requires_type_mapping = False

    def __init__(self, name: str) -> None:
        self.name = name

    async def _fail(self, *a: Any, **k: Any) -> Any:
        raise PMSError("stored credentials can't be decrypted", transient=False)

    test_connection = list_practitioners = list_appointment_types = _fail
    get_available_slots = create_appointment = cancel_appointment = list_lapsed_patients = _fail


# ── HTTP ─────────────────────────────────────────────────────────────────────

async def request_json(method: str, url: str, *, headers: Dict[str, str], auth: Any = None,
                       params: Any = None, json_body: Any = None, write: bool = False) -> Any:
    """One diary request. GET: one retry on a 5xx / timeout, honouring a short 429 Retry-After.
    Writes: a single attempt (the provider decides whether a retry is safe). Raises PMSError."""
    timeout = httpx.Timeout(WRITE_TIMEOUT_S if write else READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    attempts = 1 if write else 2
    last: Optional[PMSError] = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) as client:
                resp = await client.request(method, url, headers=headers, auth=auth, params=params, json=json_body)
        except httpx.TimeoutException:
            last = PMSError("the diary took too long to answer")
            continue
        except httpx.HTTPError as e:
            last = PMSError(f"couldn't reach the diary ({type(e).__name__})")
            continue
        if resp.status_code == 429:
            wait = _retry_after(resp)
            last = PMSError("the diary is rate-limiting us", status=429)
            if attempt + 1 < attempts and wait is not None and wait <= MAX_RETRY_AFTER_S:
                await asyncio.sleep(wait)
                continue
            raise last
        if resp.status_code >= 500:
            last = PMSError(f"the diary had an error (HTTP {resp.status_code})", status=resp.status_code)
            continue
        if resp.status_code in (401, 403):
            raise PMSError("the diary refused our credentials", transient=False, status=resp.status_code)
        if resp.status_code == 404:
            raise PMSError("not found in the diary", transient=False, status=404)
        if resp.status_code >= 400:
            raise PMSError(f"the diary rejected the request (HTTP {resp.status_code})", transient=False,
                           status=resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise PMSError("the diary sent an unreadable answer", transient=False)
    raise last or PMSError("the diary request failed")


def _retry_after(resp: httpx.Response) -> Optional[float]:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


def split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "Unknown", "Caller"
    if len(parts) == 1:
        return parts[0], "-"
    return " ".join(parts[:-1]), parts[-1]


def digits(phone: str) -> str:
    return "".join(c for c in phone or "" if c.isdigit())


def same_phone(a: str, b: str) -> bool:
    """+61 412 345 678 == 0412 345 678: compare the last 9 digits (national number without trunk 0)."""
    da, db = digits(a), digits(b)
    return bool(da and db) and da[-9:] == db[-9:]
