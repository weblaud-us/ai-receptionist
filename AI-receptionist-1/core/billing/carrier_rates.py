"""
What the phone network charges for one call-minute to or from a number, in any country.

    est = await estimate(db, "+61412345678", direction="outbound", from_number="+61290000000")
    est["per_minute"]["mid"], est["carrier_usd_per_min"], est["source"]      # 0.1098, 0.071, "twilio"

Rates come from Twilio's Trunking Pricing API v2 (GET https://pricing.twilio.com/v2/Trunking/Countries/{ISO}):
account-specific `current_price` per destination prefix (termination: our outbound calls) and per number
type (origination: calls coming in on our numbers). One document per country is cached in
`carrier_rates` for CARRIER_RATES_MAX_AGE_DAYS and prefixes are matched here, so a review or a whole
campaign costs one API call per country per week, not one per number. The API itself is free.

Without Twilio credentials, or when Twilio can't be reached, the static table in core/billing/costs.py
answers instead (`source: "estimate"`). Nothing here raises: a cost estimate never stops a call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import httpx
import phonenumbers

from config import settings
from core.billing import costs
from core.billing.usage import aware
from core.outbound import phones

logger = logging.getLogger(__name__)

COLLECTION = "carrier_rates"
PRICING_URL = "https://pricing.twilio.com/v2/Trunking/Countries/{iso}"
TIMEOUT_S = 8.0
RETRY_AFTER_ERROR = timedelta(hours=1)     # don't ask a failing API again on every call
SUMMARY_SAMPLE = 5000                      # numbers looked at for a campaign's cost summary

# Twilio's originating `number_type` → our column.
_NUMBER_TYPES = {"local": "local", "mobile": "mobile", "national": "national", "toll free": "toll_free",
                 "toll_free": "toll_free", "tollfree": "toll_free"}
_LINE_TEXT = {"landline": "landline", "mobile": "mobile", "landline_or_mobile": "landline or mobile",
              "toll_free": "toll-free", "business_line": "business line", "voip": "internet phone",
              "premium_rate": "premium rate", "other": "number"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)


def _client() -> httpx.AsyncClient:          # tests swap this for a MockTransport client
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0))


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _price(row: Mapping[str, Any]) -> Optional[float]:
    for key in ("current_price", "base_price"):
        try:
            if row.get(key) is not None:
                return max(0.0, float(row[key]))
        except (TypeError, ValueError):
            continue
    return None


def parse_country(body: Mapping[str, Any], iso: str) -> Optional[Dict[str, Any]]:
    """Twilio's country response → the cached document. None when it isn't usable (not USD, no prices)."""
    unit = str(body.get("price_unit") or "usd").strip().lower()
    if unit != "usd":
        logger.warning("Twilio trunking prices for %s are in %s, not USD: using the fallback table", iso, unit)
        return None
    terminating: List[Dict[str, Any]] = []
    for row in body.get("terminating_prefix_prices") or []:
        usd = _price(row)
        dest = [d for d in (_digits(p) for p in row.get("destination_prefixes") or []) if d]
        if usd is None or not dest:
            continue
        orig = ["ALL" if str(p).strip().upper() == "ALL" else _digits(p) for p in row.get("origination_prefixes") or []]
        terminating.append({"dest": dest, "orig": [o for o in orig if o] or ["ALL"], "usd": usd,
                            "name": str(row.get("friendly_name") or "")[:120]})
    originating: Dict[str, float] = {}
    for row in body.get("originating_call_prices") or []:
        kind = _NUMBER_TYPES.get(str(row.get("number_type") or "").strip().lower())
        usd = _price(row)
        if kind and usd is not None:
            originating[kind] = usd
    if not terminating and not originating:
        return None
    return {"_id": iso, "country": str(body.get("country") or ""), "terminating": terminating,
            "originating": originating, "source": "twilio"}


async def refresh(db: Any, iso: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Fetch one country's rates from Twilio and cache them. None (and the error noted) on any failure."""
    iso = (iso or "").upper()
    if not iso or not configured():
        return None
    now = now or utcnow()
    try:
        async with _client() as client:
            resp = await client.get(PRICING_URL.format(iso=iso),
                                    auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        doc = parse_country(resp.json() or {}, iso) if resp.status_code < 400 else None
        if resp.status_code >= 400:
            logger.warning("Twilio trunking prices for %s: HTTP %s", iso, resp.status_code)
    except Exception:
        logger.warning("Twilio trunking prices for %s errored", iso, exc_info=True)
        doc = None
    try:
        if doc is None:
            await db[COLLECTION].update_one({"_id": iso}, {"$set": {"error_at": now}}, upsert=True)  # cross-tenant: carrier prices, no tenant data
            return None
        doc["fetched_at"] = now
        await db[COLLECTION].replace_one({"_id": iso}, doc, upsert=True)  # cross-tenant: carrier prices, no tenant data
    except Exception:
        logger.warning("Caching trunking prices for %s failed", iso, exc_info=True)
    return doc


async def country_rates(db: Any, iso: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The cached rates for a country: fresh from the cache, else from Twilio, else the stale copy, else None."""
    iso = (iso or "").upper()
    if not iso:
        return None
    now = now or utcnow()
    try:
        doc = await db[COLLECTION].find_one({"_id": iso})  # cross-tenant: carrier prices, no tenant data
    except Exception:
        logger.warning("Reading trunking prices for %s failed", iso, exc_info=True)
        doc = None
    have = bool(doc and doc.get("fetched_at") and (doc.get("terminating") or doc.get("originating")))
    fetched, failed = aware((doc or {}).get("fetched_at")), aware((doc or {}).get("error_at"))
    if have and fetched and fetched > now - timedelta(days=settings.CARRIER_RATES_MAX_AGE_DAYS):
        return doc
    if configured() and not (failed and failed > now - RETRY_AFTER_ERROR):
        fresh = await refresh(db, iso, now=now)
        if fresh:
            return fresh
    return doc if have else None


def match_termination(doc: Mapping[str, Any], to_digits: str, from_digits: str = "") -> Optional[Dict[str, Any]]:
    """The termination row for a destination: the longest destination prefix that matches, among rows for
    every origin ("ALL") or for the caller ID's prefix; at the same length, the more specific origin wins."""
    best: Optional[Dict[str, Any]] = None
    best_key = (-1, -1)
    for row in doc.get("terminating") or []:
        orig = row.get("orig") or ["ALL"]
        if "ALL" in orig:
            origin_len = 0
        else:
            lens = [len(p) for p in orig if from_digits and from_digits.startswith(p)]
            if not lens:
                continue
            origin_len = max(lens)
        for prefix in row.get("dest") or []:
            key = (len(prefix), origin_len)
            if to_digits.startswith(prefix) and key > best_key:
                best, best_key = row, key
    return best


def classify(number: str) -> Tuple[str, str, str]:
    """(e164, country, line kind) from the numbering plan, without the timezone work phones.parse does."""
    text = (number or "").strip()
    if not text:
        return "", "", "other"
    try:
        parsed = phonenumbers.parse(text if text.startswith("+") else "+" + _digits(text), None)
    except phonenumbers.NumberParseException:
        return "", "", "other"
    if not phonenumbers.is_possible_number(parsed):
        return "", "", "other"
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return e164, phonenumbers.region_code_for_number(parsed) or "", phones.line_kind(e164)


def _main_country(e164: str) -> str:
    """The main country of the number's calling code (+44 → GB, +1 → US), whose price list covers it."""
    try:
        return phonenumbers.region_code_for_country_code(phonenumbers.parse(e164, None).country_code) or ""
    except Exception:
        return ""


def _when(value: Any) -> Optional[str]:
    at = aware(value)
    return at.isoformat() if at else None


async def estimate(db: Any, number: str, *, direction: str, from_number: str = "", channel: str = "phone",
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """The cost of one minute of this call, everything included, and where the carrier rate came from.

    `number`: the far end for an outbound call (who we ring), our own number for an inbound one.
    `from_number`: the caller ID of an outbound call (some routes are priced by origin)."""
    outbound = direction == "outbound"
    e164, country, kind = classify(number)
    carrier, source, basis, fetched = 0.0, "none", "Not on the phone network", None
    if channel in costs.PHONE_CHANNELS:
        doc = None
        try:
            doc = await country_rates(db, country, now=now) if country else None
            main = _main_country(e164)
            if not doc and main and main != country:     # Guernsey, Jersey…: priced under the UK's +44 list
                doc = await country_rates(db, main, now=now)
        except Exception:
            logger.warning("Carrier rate for %s failed", country, exc_info=True)
        rate: Optional[float] = None
        if doc and outbound:
            row = match_termination(doc, _digits(e164), _digits(from_number))
            if row:
                rate, basis = float(row["usd"]), row.get("name") or "Twilio trunking rate"
        elif doc:
            orig = doc.get("originating") or {}
            col = {"mobile": "mobile", "toll_free": "toll_free", "business_line": "national"}.get(kind, "local")
            rate = orig.get(col, orig.get("local"))
            if rate is not None:
                basis = f"Twilio trunking, inbound to a {col.replace('_', '-')} number"
        if rate is not None:
            carrier, source, fetched = float(rate), "twilio", _when(doc.get("fetched_at") if doc else None)
        else:
            fallback_country = country if country in costs.FALLBACK_CARRIER else (_main_country(e164) or country or "US")
            carrier, basis, _known = costs.fallback_carrier(fallback_country, outbound=outbound, line_kind=kind)
            source = "estimate"
    per_min = costs.per_minute(outbound=outbound, carrier=carrier, channel=channel)
    return {
        "direction": "outbound" if outbound else "inbound",
        "country": country, "country_name": phones.country_name(country) if country else "",
        "line_kind": kind, "line_text": _LINE_TEXT.get(kind, "number"),
        "carrier_usd_per_min": round(carrier, 4), "per_minute": per_min,
        "typical_minutes": settings.SALES_TYPICAL_CALL_MINUTES,
        "typical_call_usd": round(per_min["mid"] * settings.SALES_TYPICAL_CALL_MINUTES, 2),
        "source": source, "basis": basis, "fetched_at": fetched,
        "warn": per_min["mid"] > settings.SALES_CARRIER_WARN_USD_PER_MIN,
    }


def stamp(est: Mapping[str, Any]) -> Dict[str, Any]:
    """The part of an estimate kept on a usage event, call or attempt: enough to audit the cost later."""
    return {"usd_per_min": est.get("per_minute", {}).get("mid"), "carrier_usd_per_min": est.get("carrier_usd_per_min"),
            "country": est.get("country"), "line_kind": est.get("line_kind"), "source": est.get("source"),
            "basis": est.get("basis")}


async def for_call(db: Any, *, direction: str, channel: str, caller_phone: Optional[str],
                   dialed_number: Optional[str]) -> Dict[str, Any]:
    """The estimate for a call that happened: inbound is priced on our number, outbound on the one we rang
    (for outbound calls `caller_phone` holds the far end and `dialed_number` our caller ID)."""
    if direction == "outbound":
        return await estimate(db, caller_phone or "", direction="outbound", from_number=dialed_number or "",
                              channel=channel)
    return await estimate(db, dialed_number or "", direction="inbound", channel=channel)


async def summary(db: Any, numbers: Iterable[str], *, from_numbers: Iterable[str] = (),
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Outbound cost across a list of numbers, grouped by country and line kind (a campaign's leads).
    Large lists are sampled evenly and the counts scaled back up."""
    numbers = [n for n in numbers if n]
    total = len(numbers)
    step = max(1, -(-total // SUMMARY_SAMPLE))
    sample = numbers[::step]
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for n in sample:
        _e164, country, kind = classify(n)
        g = groups.setdefault((country, kind), {"count": 0, "number": n})
        g["count"] += 1
    callers = list(from_numbers)
    rows: List[Dict[str, Any]] = []
    for (country, kind), g in groups.items():
        origin = next((f for f in callers if classify(f)[1] == country), callers[0] if callers else "")
        est = await estimate(db, g["number"], direction="outbound", from_number=origin, now=now)
        rows.append({"country": country, "country_name": est["country_name"], "line_kind": kind,
                     "line_text": est["line_text"], "count": round(g["count"] * total / max(1, len(sample))),
                     "usd_per_min": est["per_minute"]["mid"], "carrier_usd_per_min": est["carrier_usd_per_min"],
                     "source": est["source"], "warn": est["warn"]})
    rows.sort(key=lambda r: (-r["count"], r["country"], r["line_kind"]))
    mids = [r["usd_per_min"] for r in rows]
    minutes = settings.SALES_TYPICAL_CALL_MINUTES
    return {"groups": rows, "numbers": total, "low": min(mids) if mids else None, "high": max(mids) if mids else None,
            "typical_minutes": minutes,
            "typical_total_usd": round(sum(r["count"] * r["usd_per_min"] * minutes for r in rows), 2),
            "warn": any(r["warn"] for r in rows), "sampled": step > 1}


async def table(db: Any, countries: Iterable[str] = costs.LAUNCH_COUNTRIES) -> List[Dict[str, Any]]:
    """The Economics page's rate table: per country, inbound on a local number and outbound to a landline
    and a mobile, using each country's example numbers from the numbering plan."""
    out: List[Dict[str, Any]] = []
    for iso in countries:
        landline = _example(iso, phonenumbers.PhoneNumberType.FIXED_LINE)
        mobile = _example(iso, phonenumbers.PhoneNumberType.MOBILE)
        inbound = await estimate(db, landline, direction="inbound")
        to_landline = await estimate(db, landline, direction="outbound")
        to_mobile = await estimate(db, mobile, direction="outbound")
        out.append({"country": iso, "country_name": phones.country_name(iso),
                    "inbound": inbound["carrier_usd_per_min"], "outbound_landline": to_landline["carrier_usd_per_min"],
                    "outbound_mobile": to_mobile["carrier_usd_per_min"],
                    "per_minute_inbound": inbound["per_minute"]["mid"],
                    "per_minute_outbound_mobile": to_mobile["per_minute"]["mid"],
                    "source": to_mobile["source"], "fetched_at": to_mobile["fetched_at"]})
    return out


def _example(iso: str, kind: Any) -> str:
    try:
        ex = phonenumbers.example_number_for_type(iso, kind)
        return phonenumbers.format_number(ex, phonenumbers.PhoneNumberFormat.E164) if ex else ""
    except Exception:
        return ""
