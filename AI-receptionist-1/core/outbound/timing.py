"""
When businesses pick up, and retries timed for it (SALES_LEARNING_PLAN.md Phase 6).

- Every attempt records the lead's local weekday and hour at dial time (`local_slot`).
- `reach_table` counts, per country and local weekday × hour, how often a FIRST attempt reached a
  person. First attempts only: our own retry timing would otherwise teach itself.
- `best_retry_at` moves a retry (never a callback, which keeps the time that was agreed) to the hour
  most likely to reach someone, within SALES_SMART_RETRY_MAX_DELAY_H after it was due. Every hour
  considered must pass `gate.window_open` — the campaign window, the legal windows, public holidays
  and every time zone the lead may be in — so legality is never worked out any other way. Each hour's
  rate is smoothed toward the country's, one random draw per hour picks the winner (so it keeps
  exploring and retries don't all land at once), and hours that already failed for this lead count less.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

STATS = "sales_learning_stats"
SMOOTHING = 20                         # an hour with few dials counts like this many dials at the country's rate
DEFAULT_RATE = 0.2
TRIED_PENALTY = 0.5
PREFERRED_BOOST = 1.3
WINDOW_DAYS = 90
_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
CACHE_SECONDS = 600


def lead_zone(lead: Mapping[str, Any]) -> str:
    zones = [z for z in (lead.get("timezones") or []) if z]
    return str(lead.get("timezone") or (zones[0] if zones else "") or "")


def local_slot(lead: Mapping[str, Any], at: datetime) -> Optional[Dict[str, Any]]:
    """{"tz", "dow" (1 = Monday … 7 = Sunday), "hour"} in the lead's time zone, or None when it isn't known."""
    zone = lead_zone(lead)
    if not zone:
        return None
    try:
        local = at.astimezone(ZoneInfo(zone))
    except Exception:
        return None
    return {"tz": zone, "dow": local.isoweekday(), "hour": local.hour}


def _reached(a: Mapping[str, Any]) -> bool:
    from core.outbound import models as m

    return a.get("amd") == "human" or (a.get("outcome") in m.HUMAN_OUTCOMES and a.get("outcome") != "wrong_number")


def reach_table(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """country → {"cells": {"dow-hour": [reached, dials]}, "reached", "dials"}, from first attempts."""
    from core.outbound import models as m

    out: Dict[str, Dict[str, Any]] = {}
    for a in rows:
        local = a.get("local") or {}
        if a.get("status") != "done" or a.get("outcome") in m.UNCOUNTED_OUTCOMES or int(a.get("attempt_no") or 0) != 1:
            continue
        if not isinstance(local.get("dow"), int) or not isinstance(local.get("hour"), int):
            continue
        t = out.setdefault(str(a.get("country") or "?"), {"cells": {}, "reached": 0, "dials": 0})
        cell = t["cells"].setdefault(f"{local['dow']}-{local['hour']}", [0, 0])
        hit = int(_reached(a))
        cell[0] += hit
        cell[1] += 1
        t["reached"] += hit
        t["dials"] += 1
    return out


def base_rate(table: Optional[Mapping[str, Any]]) -> float:
    if not table or not table.get("dials"):
        return DEFAULT_RATE
    return max(0.01, table["reached"] / table["dials"])


def smoothed(table: Optional[Mapping[str, Any]], dow: int, hour: int) -> Tuple[float, float]:
    """(reached, missed) for one hour, pulled toward the country's rate."""
    p = base_rate(table)
    r, d = ((table or {}).get("cells") or {}).get(f"{dow}-{hour}", [0, 0])
    return r + SMOOTHING * p, (d - r) + SMOOTHING * (1 - p)


def preferred_hours(text: str) -> set[int]:
    """Hours they asked for ("after 2pm", "mornings", "between 10 and 12"); empty = no preference."""
    t = (text or "").lower()
    if not t:
        return set()
    m = re.search(r"after\s+(\d{1,2})\s*(am|pm)?", t)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" or (not m.group(2) and int(m.group(1)) < 7) else 0)
        return set(range(h, 21))
    m = re.search(r"before\s+(\d{1,2})\s*(am|pm)?", t)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" or (not m.group(2) and int(m.group(1)) < 7) else 0)
        return set(range(7, h))
    if "morning" in t:
        return set(range(7, 12))
    if "lunch" in t:
        return {12, 13}
    if "afternoon" in t:
        return set(range(12, 18))
    return set()


def best_retry_at(lead: Mapping[str, Any], campaign: Mapping[str, Any], *, earliest: datetime,
                  table: Optional[Mapping[str, Any]], tried: Sequence[Tuple[int, int]] = (),
                  rng: Optional[random.Random] = None, horizon_h: Optional[int] = None,
                  prefer: Iterable[int] = ()) -> Optional[datetime]:
    """The best hour to try again, from `earliest` to `earliest + horizon_h`. None = no hour in that
    span is open (the caller keeps the plain retry time)."""
    from core.outbound import gate

    rng = rng or random.Random()
    horizon = settings.SALES_SMART_RETRY_MAX_DELAY_H if horizon_h is None else horizon_h
    zone = lead_zone(lead)
    try:
        tz = ZoneInfo(zone) if zone else timezone.utc
    except Exception:
        tz = timezone.utc
    tried_set, liked = set(tried), set(prefer)
    start = earliest.replace(minute=0, second=0, microsecond=0)
    best: Optional[datetime] = None
    best_draw = -1.0
    for i in range(horizon + 1):
        t = start + timedelta(hours=i)
        if t < earliest:
            t = earliest
        if not gate.window_open(lead, campaign, t):
            continue
        local = t.astimezone(tz)
        reached, missed = smoothed(table, local.isoweekday(), local.hour)
        draw = rng.betavariate(max(reached, 0.01) + 1, max(missed, 0.01) + 1)
        if (local.isoweekday(), local.hour) in tried_set:
            draw *= TRIED_PENALTY
        if local.hour in liked:
            draw *= PREFERRED_BOOST
        if draw > best_draw:
            best, best_draw = t, draw
    if best is None:
        return None
    jittered = best + timedelta(minutes=rng.randint(0, 10))
    return jittered if gate.window_open(lead, campaign, jittered) else best


async def refresh(db: Any, *, now: Optional[datetime] = None) -> int:
    """Recount pick-up rates from the last WINDOW_DAYS of first attempts. Returns the countries stored."""
    from core.outbound import models as m

    now = now or datetime.now(timezone.utc)
    rows = await db[m.ATTEMPTS].find({"attempt_no": 1, "status": "done", "created_at": {"$gte": now - timedelta(days=WINDOW_DAYS)}},
                                     {"local": 1, "country": 1, "outcome": 1, "amd": 1, "status": 1, "attempt_no": 1}
                                     ).limit(200_000).to_list(200_000)
    tables = reach_table(rows)
    for country, t in tables.items():
        await db[STATS].update_one({"_id": f"timing:{country}"}, {"$set": {**t, "country": country, "updated_at": now,
                                                                          "days": WINDOW_DAYS}}, upsert=True)
    _CACHE.clear()
    return len(tables)


async def table_for(db: Any, country: str) -> Optional[Dict[str, Any]]:
    key = str(country or "?")
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    doc = await db[STATS].find_one({"_id": f"timing:{key}"})
    _CACHE[key] = (time.monotonic(), doc)
    return doc


async def smart_retry(db: Any, lead: Mapping[str, Any], campaign: Mapping[str, Any], *, earliest: datetime,
                      now: datetime) -> Optional[datetime]:
    """The retry time for this lead, or None to keep `earliest`. Never raises."""
    from core.outbound import models as m

    try:
        tried_rows = await db[m.ATTEMPTS].find({"lead_id": lead["_id"], "local": {"$ne": None}}, {"local": 1}) \
            .sort("created_at", -1).limit(10).to_list(10)
        tried = [(int(r["local"]["dow"]), int(r["local"]["hour"])) for r in tried_rows
                 if isinstance((r.get("local") or {}).get("dow"), int)]
        prefer = preferred_hours(str((lead.get("qualification") or {}).get("best_time_to_reach") or ""))
        table = await table_for(db, str(lead.get("country") or "?"))
        return best_retry_at(lead, campaign, earliest=earliest, table=table, tried=tried, prefer=prefer)
    except Exception:
        logger.warning("Smart retry timing failed for lead %s; keeping the plain delay", lead.get("_id"), exc_info=True)
        return None


def heatmap(table: Optional[Mapping[str, Any]], *, hours: Sequence[int] = tuple(range(7, 21))) -> List[Dict[str, Any]]:
    """Cells for the "When businesses pick up" chart: raw counts and the smoothed rate."""
    out = []
    for dow in range(1, 8):
        for hour in hours:
            r, d = ((table or {}).get("cells") or {}).get(f"{dow}-{hour}", [0, 0])
            reached, missed = smoothed(table, dow, hour)
            out.append({"dow": dow, "hour": hour, "reached": r, "dials": d,
                        "rate": round(reached / (reached + missed), 4) if d else None})
    return out
