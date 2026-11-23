"""
Short-lived cache of practice-diary availability, plus a request budget per diary (plan A1.3,
audit P0-15). A live Cliniko / Dentally lookup costs the caller 0.6–1.8 s of silence; a cached one
costs a few milliseconds.

    raw = await slots(tenant_id, provider, service, day_from, day_to, practitioner_ids)
    raw = await slots(..., fresh=True)       # booking: always ask the diary itself
    await invalidate(tenant_id)              # after a booking, a reschedule or a cancellation
    await spend(provider_name, credential)   # before every diary request; False = over budget

Entries are per (tenant, appointment type and length, practitioner, day), so a prefetch of the next
week also answers "anything on Tuesday?". A cached slot is only ever an offer: SchedulingService.book
re-checks the diary with fresh=True, so a stale entry can make the agent say "that time just went",
never book twice. Bookings made at the front desk show up within TTL_S.

State lives in Redis, shared by the voice worker processes and the API; without Redis (or while it
is down) a small in-process cache is used instead, so nothing ever depends on Redis being up.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import ratelimit
from core import redis as redis_store
from core.services import scheduling_provider as sp

logger = logging.getLogger(__name__)

TTL_S = 120                 # long enough to cover a call's first availability question after a prefetch
REQUESTS_PER_MINUTE = 100   # per diary credential; Cliniko allows ~150–200/min per user
_PREFIX = "pmsc:"
_MAX_MEMORY = 20_000

_memory: Dict[str, Tuple[float, str]] = {}      # key → (expires_at, json)
_generation: Dict[str, int] = {}                # tenant → bumped on every write (memory fallback)


def _svc_key(service: Dict[str, Any]) -> str:
    return f"{service.get('pms_id') or ''}:{int(service.get('duration_min') or 30)}"


def _days(day_from: date, day_to: date) -> List[date]:
    return [day_from + timedelta(days=i) for i in range((day_to - day_from).days + 1)]


async def _gen(tenant_id: str) -> int:
    r = redis_store.get_redis()
    if r is not None:
        try:
            return int(await r.get(f"{_PREFIX}gen:{tenant_id}") or 0)
        except Exception:
            redis_store.warn_unavailable("diary cache")
    return _generation.get(tenant_id, 0)


async def invalidate(tenant_id: str) -> None:
    """Forget every cached slot for this business (the old generation's keys simply expire)."""
    _generation[tenant_id] = _generation.get(tenant_id, 0) + 1
    r = redis_store.get_redis()
    if r is not None:
        try:
            k = f"{_PREFIX}gen:{tenant_id}"
            await r.incr(k)
            await r.expire(k, 30 * 86400)
        except Exception:
            redis_store.warn_unavailable("diary cache")


async def _get_many(keys: List[str]) -> Dict[str, Optional[str]]:
    r = redis_store.get_redis()
    if r is not None:
        try:
            values = await r.mget(keys)
            return dict(zip(keys, values))
        except Exception:
            redis_store.warn_unavailable("diary cache")
    now = time.monotonic()
    out: Dict[str, Optional[str]] = {}
    for k in keys:
        hit = _memory.get(k)
        out[k] = hit[1] if hit and hit[0] > now else None
    return out


async def _set_many(values: Dict[str, str]) -> None:
    if not values:
        return
    r = redis_store.get_redis()
    if r is not None:
        try:
            async with r.pipeline(transaction=False) as pipe:
                for k, v in values.items():
                    pipe.set(k, v, ex=TTL_S)
                await pipe.execute()
            return
        except Exception:
            redis_store.warn_unavailable("diary cache")
    expires = time.monotonic() + TTL_S
    if len(_memory) > _MAX_MEMORY:
        now = time.monotonic()
        for k in [k for k, (t, _) in _memory.items() if t <= now]:
            _memory.pop(k, None)
        if len(_memory) > _MAX_MEMORY:
            _memory.clear()
    for k, v in values.items():
        _memory[k] = (expires, v)


def _encode(rows: Iterable[sp.ProviderSlot]) -> str:
    return json.dumps([[s.start.isoformat(), s.end.isoformat()] for s in rows])


def _decode(raw: str, practitioner_id: str) -> List[sp.ProviderSlot]:
    return [sp.ProviderSlot(start=datetime.fromisoformat(a), end=datetime.fromisoformat(b),
                            practitioner_id=practitioner_id) for a, b in json.loads(raw)]


async def slots(tenant_id: str, provider: Any, service: Dict[str, Any], day_from: date, day_to: date,
                practitioner_ids: List[str], *, zone: Any, fresh: bool = False) -> List[sp.ProviderSlot]:
    """Free times for these practitioners, from the cache where possible. `zone` decides which
    business-local day a slot belongs to. Raises PMSError like the provider does."""
    if fresh or not practitioner_ids:
        out = await provider.get_available_slots(service, day_from, day_to, practitioner_ids)
        return list(out)
    gen = await _gen(tenant_id)
    days = _days(day_from, day_to)
    svc = _svc_key(service)

    def key(pid: str, d: date) -> str:
        return f"{_PREFIX}{tenant_id}:{gen}:{provider.name}:{svc}:{pid}:{d.isoformat()}"

    wanted = {(pid, d): key(pid, d) for pid in practitioner_ids for d in days}
    cached = await _get_many(list(wanted.values()))
    found: List[sp.ProviderSlot] = []
    missing_pids: List[str] = []
    missing_days: List[date] = []
    for (pid, d), k in wanted.items():
        raw = cached.get(k)
        if raw is None:
            if pid not in missing_pids:
                missing_pids.append(pid)
            missing_days.append(d)
        else:
            found.extend(_decode(raw, pid))
    if missing_pids:
        lo, hi = min(missing_days), max(missing_days)
        fetched = await provider.get_available_slots(service, lo, hi, missing_pids)
        by_cell: Dict[Tuple[str, date], List[sp.ProviderSlot]] = {
            (pid, d): [] for pid in missing_pids for d in _days(lo, hi)}
        for s in fetched:
            cell = (str(s.practitioner_id), s.start.astimezone(zone).date())
            if cell in by_cell:
                by_cell[cell].append(s)
        await _set_many({key(pid, d): _encode(rows) for (pid, d), rows in by_cell.items()})
        found.extend(s for (pid, d), rows in by_cell.items() if (pid, d) in wanted for s in rows)
    found.sort(key=lambda x: (x.start, x.practitioner_id))
    return found


async def spend(provider_name: str, credential: str) -> bool:
    """One request against this diary's budget. False = over REQUESTS_PER_MINUTE: don't send it."""
    who = hashlib.sha256(credential.encode()).hexdigest()[:16]
    return await ratelimit.allow(f"pms:{provider_name}:{who}", REQUESTS_PER_MINUTE, 60)


def reset_for_tests() -> None:
    _memory.clear()
    _generation.clear()
