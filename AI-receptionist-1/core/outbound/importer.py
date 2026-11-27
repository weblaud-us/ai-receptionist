"""
Lead intake (Roadmap Task 4.3): CSV → `sales_leads`.

Every row is normalised to E.164 (invalid numbers are dropped and counted), de-duplicated against
the file, existing leads and existing customers — a customer is never cold-called — and stamped
with where it came from (`source`), which a compliance audit will ask for. Rows are then
pre-screened by the gate so the Leads page shows straight away which numbers can never be dialled.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.outbound import gate, phones, registry, rules
from core.outbound import models as m
from core.tenancy.ids import new_id

logger = logging.getLogger(__name__)

MAX_ROWS = 5000
MAX_BYTES = 3_000_000

FIELDS: tuple[str, ...] = ("business_name", "contact_name", "phone", "email", "website", "vertical", "address",
                           "city", "state", "postal", "country", "timezone", "notes", "agent_notes", "tags")
_ALIASES: Mapping[str, tuple[str, ...]] = {
    "business_name": ("business name", "business", "company", "company name", "name", "organization"),
    "contact_name": ("contact name", "contact", "owner", "owner name", "first name", "full name"),
    "phone": ("phone", "phone number", "telephone", "tel", "number", "business phone", "main phone"),
    "email": ("email", "e-mail", "email address"),
    "website": ("website", "url", "site", "web"),
    "vertical": ("vertical", "category", "industry", "type"),
    "address": ("address", "street", "street address", "address 1", "address line 1"),
    "city": ("city", "town"),
    "state": ("state", "region", "province", "state code"),
    "postal": ("zip", "zip code", "postal", "postal code", "postcode"),
    "country": ("country", "country code"),
    "timezone": ("timezone", "time zone", "tz"),
    "notes": ("notes", "note", "comments"),
    "agent_notes": ("agent notes", "notes for the agent", "about", "about them", "brief", "call notes", "background"),
    "tags": ("tags", "labels"),
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _key(header: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (header or "").strip().lower())


def read_csv(text: str) -> tuple[List[str], List[Dict[str, str]]]:
    """Headers and rows. Raises ValueError for an empty or oversized file."""
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValueError("The file is too large (3 MB at most). Split it and import in parts.")
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h]
    if not headers:
        raise ValueError("The file has no header row.")
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"The file has {len(rows)} rows; import at most {MAX_ROWS} at a time.")
    return headers, rows


def guess_mapping(headers: Iterable[str]) -> Dict[str, str]:
    """field → CSV header, by common names."""
    by_key = {_key(h): h for h in headers}
    out: Dict[str, str] = {}
    for field_name, aliases in _ALIASES.items():
        for alias in (field_name.replace("_", " "), *aliases):
            if alias in by_key and by_key[alias] not in out.values():
                out[field_name] = by_key[alias]
                break
    return out


async def customer_numbers(db: Any) -> set[str]:
    """Every number that belongs to a business already on the platform."""
    raw: set[str] = set()
    async for t in db.tenants.find({}, {"phone": 1, "notifications": 1, "transfer": 1, "country": 1}):
        country = t.get("country") or "US"
        n = t.get("notifications") or {}
        for value in (t.get("phone"), n.get("owner_phone"), n.get("owner_whatsapp"), (t.get("transfer") or {}).get("number")):
            info = phones.parse(value, country)
            if info:
                raw.add(info.e164)
    async for row in db.phone_numbers.find({"status": {"$ne": "released"}}, {"e164": 1}):
        if row.get("e164"):
            raw.add(row["e164"])
    return raw


def build_lead(row: Mapping[str, str], mapping: Mapping[str, str], *, source: str, default_country: str,
               vertical: str, tags: List[str], now: datetime) -> Optional[Dict[str, Any]]:
    def get(field_name: str) -> str:
        return (row.get(mapping.get(field_name, ""), "") or "").strip()

    country_hint = get("country").upper()[:2] or default_country
    info = phones.parse(get("phone"), country_hint)
    if info is None:
        return None
    state = phones.normalize_region(info.country, get("state")) or info.region_code
    zones = phones.timezones_for(info, state=state, timezone=get("timezone"))
    email = get("email").lower()
    return {
        "_id": new_id("lead"), "owner": m.OWNER_PLATFORM,
        "business_name": get("business_name")[:200], "vertical": (get("vertical") or vertical)[:60],
        "contact_name": get("contact_name")[:120], "phone": info.e164,
        "email": email if _EMAIL.match(email) else "", "website": get("website")[:300],
        "address": {"line1": get("address")[:200], "city": get("city")[:80], "region": state or get("state")[:40],
                    "postal": get("postal")[:20], "country": info.country},
        "timezone": zones[0] if len(zones) == 1 else "", "timezones": list(zones),
        "country": info.country, "region_code": state,
        "source": source, "line_type": "toll_free" if info.toll_free else "", "line_type_checked_at": None,
        "dnc": {"national": False, "state": False, "internal": False, "checked_at": None},
        "consent": None, "status": "new", "campaign_id": None,
        "attempts": 0, "last_attempt_at": None, "next_eligible_at": None,
        "notes": get("notes")[:1000], "agent_notes": get("agent_notes")[:600],
        "tags": list(dict.fromkeys([*tags, *[t.strip()[:40] for t in get("tags").split(",") if t.strip()]]))[:20],
        "gate": None, "gate_log": [], "created_at": now, "updated_at": now,
    }


async def prescreen(db: Any, leads: List[Dict[str, Any]], campaign: Mapping[str, Any], now: datetime) -> Dict[str, int]:
    """
    Gate many leads at once without the paid line-type lookup (that happens just before a dial).
    Leads the gate will never pass are marked now; everything else is decided at dial time.
    Mutates `leads` in place and returns counts per outcome.
    """
    if not leads:
        return {"blocked": 0, "ok": 0}
    numbers = [lead["phone"] for lead in leads]
    mode = str(campaign.get("mode") or "")
    internal = {r["phone"] async for r in db[m.DNC].find({"phone": {"$in": numbers}}, {"phone": 1})}
    listed: Dict[str, List[str]] = {}
    async for r in db[m.REGISTRY].find({"phone": {"$in": numbers}}, {"phone": 1, "list": 1}):
        listed.setdefault(r["phone"], []).append(r["list"])
    consents: Dict[str, Dict[str, Any]] = {}
    if mode == "ai_consented":
        async for r in db[m.CONSENT].find({"phone": {"$in": numbers}, "revoked_at": None}).sort("at", 1):
            consents[r["phone"]] = r
    # Consent covers only the company it named (gate.consent_covers): check it against this offer's.
    offer_company = ""
    if mode == "ai_consented":
        from core.outbound import offers

        offer_company = str((await offers.offer_for_campaign(db, campaign)).get("company_name") or "")
    counts = {"blocked": 0, "ok": 0}
    for lead in leads:
        required = rules.required_registries(mode, lead["country"])
        wanted = {*required, rules.state_registry(lead["region_code"])} if required else set()
        facts = gate.GateFacts(
            internal_dnc=lead["phone"] in internal,
            registry_hits=tuple(sorted(n for n in listed.get(lead["phone"], []) if n in wanted)),
            registry_status=await registry.registry_status(db, required, lead["phone"], now),
            consent=consents.get(lead["phone"]), offer_company=offer_company)
        decision = gate.evaluate(lead, campaign, facts, now)
        doc = decision.to_doc()
        lead["gate"], lead["gate_log"] = doc, [doc]
        lead["dnc"] = {"national": "registry_listed" in decision.failures, "state": False,
                       "internal": facts.internal_dnc, "checked_at": now}
        if decision.permanent:
            lead["status"], lead["next_eligible_at"] = decision.lead_status(), None
            counts["blocked"] += 1
        else:
            counts["ok"] += 1
    return counts


async def import_leads(db: Any, *, text: str, mapping: Optional[Mapping[str, str]] = None, source: str,
                       default_country: str = "US", vertical: str = "", tags: Optional[List[str]] = None,
                       campaign: Optional[Mapping[str, Any]] = None, mode: str = "ai_cold_b2b_us",
                       dry_run: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    headers, rows = read_csv(text)
    mapping = {k: v for k, v in (mapping or guess_mapping(headers)).items() if k in FIELDS and v in headers}
    result: Dict[str, Any] = {"headers": headers, "mapping": mapping, "rows": len(rows), "imported": 0,
                              "invalid": 0, "duplicates_in_file": 0, "already_leads": 0, "customers": 0,
                              "blocked": 0, "invalid_rows": [], "dry_run": dry_run}
    if "phone" not in mapping:
        result["error"] = "Choose which column holds the phone number."
        return result

    customers = await customer_numbers(db)
    leads: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows, start=2):                       # row 1 is the header
        lead = build_lead(row, mapping, source=source, default_country=default_country.upper(),
                          vertical=vertical, tags=tags or [], now=now)
        if lead is None:
            result["invalid"] += 1
            if len(result["invalid_rows"]) < 20:
                result["invalid_rows"].append(index)
        elif lead["phone"] in leads:
            result["duplicates_in_file"] += 1
        elif lead["phone"] in customers:
            result["customers"] += 1
        else:
            leads[lead["phone"]] = lead
    if leads:
        existing = {r["phone"] async for r in db[m.LEADS].find(
            {"owner": m.OWNER_PLATFORM, "phone": {"$in": list(leads)}}, {"phone": 1})}
        result["already_leads"] = len(existing)
        for phone in existing:
            del leads[phone]

    new = list(leads.values())
    screen_as = campaign or {"mode": mode, "max_attempts": 3, "calling_window": {}}
    result["blocked"] = (await prescreen(db, new, screen_as, now))["blocked"]
    if campaign is not None:
        for lead in new:
            lead["campaign_id"] = campaign["_id"]
            if lead["status"] == "new":
                lead["status"], lead["next_eligible_at"] = "queued", now
    result["imported"] = len(new)
    if dry_run or not new:
        return result
    try:
        await db[m.LEADS].insert_many(new, ordered=False)
    except Exception as exc:                      # a concurrent import inserted some of the same numbers
        written = getattr(exc, "details", {}).get("nInserted")
        if written is None:
            raise
        result["already_leads"] += len(new) - int(written)
        result["imported"] = int(written)
    logger.info("Imported %d leads from %s (%d invalid, %d duplicates, %d customers, %d blocked)",
                result["imported"], source, result["invalid"], result["duplicates_in_file"] + result["already_leads"],
                result["customers"], result["blocked"])
    return result
