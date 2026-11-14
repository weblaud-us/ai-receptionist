"""
Availability engine (Roadmap Task 2.7) — pure functions, no database.

    free_slots = business hours (tenant tz)
               ∩ resource hours
               − schedule_exceptions
               − existing appointments (+ buffers)
               − external calendar busy times
               − slots in the past or inside the tenant's minimum notice
               → stepped by slot_step, aligned to the step
               → ranked by closeness to the requested time → top N

All datetimes in and out are timezone-aware UTC. Business hours are interpreted in the tenant's
IANA timezone, so DST changes and overnight intervals ("20:00–02:00") come out right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from core.tenancy.models import HoursEntry

UTC = timezone.utc
LOCK_BLOCK_MINUTES = 5


@dataclass(frozen=True, order=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Slot:
    resource_id: str
    start: datetime
    end: datetime

    @property
    def slot_id(self) -> str:
        return encode_slot_id(self.resource_id, self.start)


def encode_slot_id(resource_id: str, start: datetime) -> str:
    return f"{resource_id}@{start.astimezone(UTC):%Y%m%dT%H%MZ}"


def decode_slot_id(slot_id: str) -> Optional[tuple[str, datetime]]:
    try:
        resource_id, stamp = slot_id.strip().rsplit("@", 1)
        start = datetime.strptime(stamp, "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
        return resource_id, start
    except (ValueError, AttributeError):
        return None


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _local(day: date, hhmm: Any, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, hhmm, tzinfo=tz).astimezone(UTC)


def opening_intervals(hours: Sequence[HoursEntry], day: date, tz: str) -> List[Interval]:
    """Opening intervals (UTC) for one business-local calendar day."""
    zone = _zone(tz)
    out: List[Interval] = []
    for h in hours:
        if h.dow != day.weekday():
            continue
        start = _local(day, h.open_time, zone)
        end_day = day + timedelta(days=1) if h.overnight else day
        end = _local(end_day, h.close_time, zone)
        if h.close == "24:00":
            end = _local(day + timedelta(days=1), datetime.min.time(), zone)
        if end > start:
            out.append(Interval(start, end))
    return merge(out)


def merge(intervals: Iterable[Interval]) -> List[Interval]:
    out: List[Interval] = []
    for iv in sorted(intervals):
        if out and iv.start <= out[-1].end:
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    out: List[Interval] = []
    for x in a:
        for y in b:
            s, e = max(x.start, y.start), min(x.end, y.end)
            if s < e:
                out.append(Interval(s, e))
    return merge(out)


def subtract(free: Sequence[Interval], busy: Sequence[Interval]) -> List[Interval]:
    out: List[Interval] = list(merge(free))
    for b in merge(busy):
        nxt: List[Interval] = []
        for f in out:
            if not f.overlaps(b):
                nxt.append(f)
                continue
            if f.start < b.start:
                nxt.append(Interval(f.start, b.start))
            if b.end < f.end:
                nxt.append(Interval(b.end, f.end))
        out = nxt
    return out


def _aligned(t: datetime, step: timedelta) -> datetime:
    """Round up to the next multiple of `step` since midnight UTC of the same day."""
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = t - day_start
    n = -(-offset // step)  # ceil
    return day_start + n * step


def slots_in(free: Sequence[Interval], duration: timedelta, step: timedelta, resource_id: str) -> List[Slot]:
    out: List[Slot] = []
    for iv in free:
        t = _aligned(iv.start, step)
        while t + duration <= iv.end:
            out.append(Slot(resource_id, t, t + duration))
            t += step
    return out


@dataclass
class ResourceWindow:
    """What is known about one bookable resource (a person, chair, table, bay…)."""

    resource_id: str
    hours: Optional[Sequence[HoursEntry]] = None       # None → business hours
    busy: Sequence[Interval] = ()                      # appointments (with buffers) + external busy
    exceptions: Sequence[Interval] = ()                # closures for this resource only


def free_slots(
    *,
    business_hours: Sequence[HoursEntry],
    tz: str,
    resources: Sequence[ResourceWindow],
    duration: timedelta,
    day_from: date,
    day_to: date,
    now: datetime,
    min_notice: timedelta = timedelta(0),
    step: timedelta = timedelta(minutes=15),
    business_exceptions: Sequence[Interval] = (),
) -> List[Slot]:
    """Every bookable start time for every resource between two business-local dates (inclusive)."""
    earliest = now.astimezone(UTC) + min_notice
    result: List[Slot] = []
    day = day_from
    while day <= day_to:
        business_open = subtract(opening_intervals(business_hours, day, tz), business_exceptions)
        if business_open:
            for r in resources:
                window = business_open
                if r.hours is not None:
                    window = intersect(window, opening_intervals(r.hours, day, tz))
                window = subtract(window, [*r.exceptions, *r.busy, Interval(datetime.min.replace(tzinfo=UTC), earliest)])
                result.extend(slots_in(window, duration, step, r.resource_id))
        day += timedelta(days=1)
    result.sort(key=lambda s: (s.start, s.resource_id))
    return result


def rank(slots: Sequence[Slot], requested: Optional[datetime], n: int = 3, *, one_per_time: bool = True) -> List[Slot]:
    """Closest to the requested time first (or earliest when none was asked for), ≤ n, distinct times."""
    if requested is None:
        ordered = sorted(slots, key=lambda s: (s.start, s.resource_id))
    else:
        ordered = sorted(slots, key=lambda s: (abs((s.start - requested).total_seconds()), s.start))
    out: List[Slot] = []
    seen: set = set()
    for s in ordered:
        key = s.start if one_per_time else (s.start, s.resource_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= n:
            break
    return out


def lock_blocks(start: datetime, end: datetime) -> List[datetime]:
    """The 5-minute blocks an appointment occupies — one slot_locks row each."""
    step = timedelta(minutes=LOCK_BLOCK_MINUTES)
    t = start.astimezone(UTC).replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % LOCK_BLOCK_MINUTES)
    out: List[datetime] = []
    while t < end:
        out.append(t)
        t += step
    return out


def appointment_busy(appointments: Iterable[Dict[str, Any]], buffer: timedelta) -> List[Interval]:
    """Busy intervals from appointment docs, each extended by the service buffer."""
    out: List[Interval] = []
    for a in appointments:
        s, e = a.get("start"), a.get("end")
        if not isinstance(s, datetime) or not isinstance(e, datetime):
            continue
        s = s if s.tzinfo else s.replace(tzinfo=UTC)
        e = e if e.tzinfo else e.replace(tzinfo=UTC)
        out.append(Interval(s, e + buffer))
    return out


def spoken(slot_start: datetime, tz: str, *, today: Optional[date] = None) -> str:
    """'Thursday the 20th at 3:30 PM' — what the agent says."""
    local = slot_start.astimezone(_zone(tz))
    day = local.date()
    hour = local.strftime("%I:%M %p").lstrip("0")
    if today is not None:
        if day == today:
            return f"today at {hour}"
        if day == today + timedelta(days=1):
            return f"tomorrow at {hour}"
    n = day.day
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{local:%A} the {n}{suffix} at {hour}"
