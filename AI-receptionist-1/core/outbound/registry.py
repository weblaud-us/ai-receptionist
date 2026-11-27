"""
Who must not be called, and who agreed to be (Roadmap Tasks 4.2 and 4.3).

    dnc_list           our own do-not-call list. Permanent. Nothing in the product removes a row.
    dnc_registry       national / state lists we download (US National DNC by area code, CA DNCL,
                       UK TPS / CTPS), loaded by scripts/import_dnc_registry.py
    dnc_registry_meta  when each list was loaded and which area codes it covers — a list that is
                       missing or older than SALES_DNC_REGISTRY_MAX_AGE_DAYS blocks dialling
    consent_records    append-only proof that someone asked for a call; revoked by an opt-out
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from config import settings
from core.outbound import models as m
from core.outbound import rules
from core.outbound.phones import area_code
from core.phone import mask_phone
from core.tenancy.ids import new_id

logger = logging.getLogger(__name__)

ALL_AREA_CODES = "all"


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)          # Mongo returns naive UTC
    return dt


# ── Internal do-not-call list ────────────────────────────────────────────────

async def on_internal_dnc(db: Any, phone: str) -> bool:
    return await db[m.DNC].find_one({"phone": phone}, {"_id": 1}) is not None


async def add_to_dnc(db: Any, phone: str, *, reason: str, call_id: Optional[str] = None,
                     source: str = "agent", note: str = "", now: Optional[datetime] = None) -> bool:
    """
    Put a number on the internal list. Immediate and permanent: every lead with that number is
    stopped and any consent it gave is revoked. Returns True when the number was not listed before.
    """
    now = now or datetime.now(timezone.utc)
    res = await db[m.DNC].update_one(
        {"phone": phone},
        {"$setOnInsert": {"phone": phone, "reason": reason[:80], "call_id": call_id, "source": source[:40],
                          "note": note[:300], "added_at": now}},
        upsert=True)
    await db[m.LEADS].update_many(
        {"phone": phone},
        {"$set": {"status": "dnc", "dnc.internal": True, "dnc.checked_at": now, "next_eligible_at": None,
                  "updated_at": now}})
    await db[m.CONSENT].update_many({"phone": phone, "revoked_at": None}, {"$set": {"revoked_at": now}})
    added = getattr(res, "upserted_id", None) is not None
    if added:
        logger.info("Added %s to the do-not-call list (%s)", mask_phone(phone), reason)
    return added


# ── Downloaded registries ────────────────────────────────────────────────────

async def registry_hits(db: Any, phone: str, lists: Iterable[str]) -> List[str]:
    names = [n for n in lists if n]
    if not names:
        return []
    rows = await db[m.REGISTRY].find({"phone": phone, "list": {"$in": names}}, {"list": 1}).to_list(len(names))
    return sorted({r["list"] for r in rows})


async def registry_status(db: Any, lists: Iterable[str], phone: str, now: datetime) -> Dict[str, str]:
    """Per list: "fresh", "stale" (loaded too long ago), "missing", or "uncovered" (loaded, but not
    for this number's area code). Number-by-number lists (AU DNCR) are fresh when this number was
    washed within SALES_AU_DNCR_WASH_MAX_AGE_DAYS."""
    names = [n for n in lists if n]
    out: Dict[str, str] = {n: "missing" for n in names}
    if not names:
        return out
    per_number = [n for n in names if n in rules.PER_NUMBER_LISTS]
    if per_number:
        wash_cutoff = now - timedelta(days=settings.SALES_AU_DNCR_WASH_MAX_AGE_DAYS)
        async for row in db[m.REGISTRY_COVERAGE].find({"phone": phone, "list": {"$in": per_number}}):
            washed = _aware(row.get("washed_at"))
            out[row["list"]] = "fresh" if washed and washed >= wash_cutoff else "stale"
        names = [n for n in names if n not in rules.PER_NUMBER_LISTS]
        if not names:
            return out
    cutoff = now - timedelta(days=settings.SALES_DNC_REGISTRY_MAX_AGE_DAYS)
    code = area_code(phone)
    async for meta in db[m.REGISTRY_META].find({"_id": {"$in": names}}):
        covered = meta.get("area_codes") or {}
        loaded_at = _aware(covered.get(ALL_AREA_CODES) or (covered.get(code) if code else None))
        if loaded_at is None:
            out[meta["_id"]] = "uncovered"
        else:
            out[meta["_id"]] = "fresh" if loaded_at >= cutoff else "stale"
    return out


async def replace_registry(db: Any, list_name: str, phones: Iterable[str], *, area_codes: Iterable[str],
                           now: Optional[datetime] = None, batch: int = 5000) -> int:
    """Load a fresh copy of a list for `area_codes` ("all" = the whole list), replacing the old rows."""
    if not rules.known_list(list_name):
        raise ValueError(f"unknown do-not-call list {list_name!r}; use one of {', '.join(sorted(rules.KNOWN_LISTS))} or us_state:XX")
    if list_name in rules.PER_NUMBER_LISTS:
        raise ValueError(f"{list_name} is checked number by number: load a wash result with record_wash")
    now = now or datetime.now(timezone.utc)
    codes = sorted(set(area_codes)) or [ALL_AREA_CODES]
    # Write the new copy first, then drop what it no longer lists: a number on both copies is never
    # missing, so nothing on the list can be dialled while a reload runs (it used to delete first).
    load_id = new_id("load")

    async def write(numbers: List[str]) -> None:
        await db[m.REGISTRY].update_many({"list": list_name, "phone": {"$in": numbers}}, {"$set": {"load": load_id}})
        have = {r["phone"] async for r in db[m.REGISTRY].find({"list": list_name, "phone": {"$in": numbers}}, {"phone": 1})}
        fresh = [{"list": list_name, "phone": p, "load": load_id} for p in numbers if p not in have]
        if fresh:
            await db[m.REGISTRY].insert_many(fresh, ordered=False)

    count = 0
    chunk: List[str] = []
    seen: set[str] = set()
    for phone in phones:
        if phone in seen:
            continue
        seen.add(phone)
        chunk.append(phone)
        if len(chunk) >= batch:
            await write(chunk)
            count += len(chunk)
            chunk = []
    if chunk:
        await write(chunk)
        count += len(chunk)
    old_rows: Dict[str, Any] = {"list": list_name, "load": {"$ne": load_id}}
    if ALL_AREA_CODES in codes:
        await db[m.REGISTRY].delete_many(old_rows)
    else:
        for code in codes:
            await db[m.REGISTRY].delete_many({**old_rows, "phone": {"$regex": rf"^\+1{code}"}})
    await db[m.REGISTRY_META].update_one(
        {"_id": list_name},
        {"$set": {**{f"area_codes.{c}": now for c in codes}, "refreshed_at": now, "last_rows": count}},
        upsert=True)
    logger.info("Loaded %d numbers into do-not-call registry %s (%s)", count, list_name, ", ".join(codes))
    return count


async def record_wash(db: Any, list_name: str, washed: Iterable[str], registered: Iterable[str], *,
                      now: Optional[datetime] = None) -> Dict[str, int]:
    """Store the result of washing numbers against a number-by-number list (AU Do Not Call Register):
    every washed number is covered as of `now`; the registered ones are listed, the rest un-listed
    (a number can leave the register)."""
    if list_name not in rules.PER_NUMBER_LISTS:
        raise ValueError(f"{list_name} is not a number-by-number list")
    now = now or datetime.now(timezone.utc)
    washed_set, registered_set = set(washed), set(registered)
    washed_set |= registered_set
    for phone in washed_set:
        await db[m.REGISTRY_COVERAGE].update_one({"list": list_name, "phone": phone},
                                                 {"$set": {"washed_at": now}}, upsert=True)
        if phone in registered_set:
            await db[m.REGISTRY].update_one({"list": list_name, "phone": phone},
                                            {"$setOnInsert": {"list": list_name, "phone": phone, "loaded_at": now}}, upsert=True)
        else:
            await db[m.REGISTRY].delete_many({"list": list_name, "phone": phone})
    rows = await db[m.REGISTRY].count_documents({"list": list_name})
    await db[m.REGISTRY_META].update_one({"_id": list_name}, {"$set": {"refreshed_at": now, "rows": rows,
                                                                      f"area_codes.{ALL_AREA_CODES}": now}}, upsert=True)
    return {"washed": len(washed_set), "registered": len(registered_set)}


async def needs_wash(db: Any, phones_: Iterable[str], list_name: str, now: datetime) -> List[str]:
    """Which of these numbers have no fresh wash result."""
    cutoff = now - timedelta(days=settings.SALES_AU_DNCR_WASH_MAX_AGE_DAYS)
    numbers = list(dict.fromkeys(phones_))
    fresh = {r["phone"] async for r in db[m.REGISTRY_COVERAGE].find(
        {"list": list_name, "phone": {"$in": numbers}, "washed_at": {"$gte": cutoff}}, {"phone": 1})}
    return [p for p in numbers if p not in fresh]


async def registry_overview(db: Any, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.SALES_DNC_REGISTRY_MAX_AGE_DAYS)
    out = []
    async for meta in db[m.REGISTRY_META].find({}):
        covered = {c: _aware(at) for c, at in (meta.get("area_codes") or {}).items()}
        out.append({"list": meta["_id"], "refreshed_at": _aware(meta.get("refreshed_at")),
                    "area_codes": sorted(covered),
                    "stale_area_codes": sorted(c for c, at in covered.items() if at is None or at < cutoff),
                    "rows": meta.get("last_rows", 0)})
    return sorted(out, key=lambda r: r["list"])


# ── Consent ──────────────────────────────────────────────────────────────────

async def active_consent(db: Any, phone: str) -> Optional[Dict[str, Any]]:
    rows = await db[m.CONSENT].find({"phone": phone, "revoked_at": None}).sort("at", -1).limit(1).to_list(1)
    return rows[0] if rows else None


async def record_consent(db: Any, *, phone: str, type: str, language_shown: str, source: str,
                         ip: str = "", user_agent: str = "", now: Optional[datetime] = None,
                         scope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Append a consent record. Records are never edited; an opt-out sets `revoked_at`.
    `scope` is who they agreed to hear from: {"company", "offer_id"} (gate.consent_covers)."""
    doc = {"_id": new_id("cns"), "phone": phone, "type": type, "language_shown": language_shown[:2000],
           "source": source[:200], "ip": ip[:64], "user_agent": user_agent[:300],
           "at": now or datetime.now(timezone.utc), "revoked_at": None,
           "scope": {k: str(v)[:80] for k, v in (scope or {}).items() if k in ("company", "offer_id")}}
    await db[m.CONSENT].insert_one(doc)
    return doc
