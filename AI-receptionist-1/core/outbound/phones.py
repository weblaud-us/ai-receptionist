"""
What a phone number tells us: E.164 form, country, US state / Australian state and the timezone(s)
it may be in.

The compliance gate needs the callee's LOCAL time. A number does not always pin that down (toll-free
numbers, states that span two zones), so a lead carries every candidate timezone and the gate
requires the call to be legal in all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import phonenumbers
from phonenumbers import geocoder
from phonenumbers import timezone as pn_timezone

US_STATES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Washington D.C.": "DC",
}
_STATE_CODES = frozenset(US_STATES.values())

_E, _C, _M, _P = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"
# Every timezone found inside each state. Used when the lead's address gives a state but the
# number does not locate it (toll-free, a number ported from elsewhere).
US_STATE_TIMEZONES: dict[str, tuple[str, ...]] = {
    "AL": (_C,), "AK": ("America/Anchorage", "America/Adak"), "AZ": ("America/Phoenix", _M), "AR": (_C,),
    "CA": (_P,), "CO": (_M,), "CT": (_E,), "DE": (_E,), "DC": (_E,), "FL": (_E, _C), "GA": (_E,),
    "HI": ("Pacific/Honolulu",), "ID": ("America/Boise", _P), "IL": (_C,), "IN": (_E, _C), "IA": (_C,),
    "KS": (_C, _M), "KY": (_E, _C), "LA": (_C,), "ME": (_E,), "MD": (_E,), "MA": (_E,), "MI": (_E, _C),
    "MN": (_C,), "MS": (_C,), "MO": (_C,), "MT": (_M,), "NE": (_C, _M), "NV": (_P, _M), "NH": (_E,),
    "NJ": (_E,), "NM": (_M,), "NY": (_E,), "NC": (_E,), "ND": (_C, _M), "OH": (_E,), "OK": (_C,),
    "OR": (_P, "America/Boise"), "PA": (_E,), "RI": (_E,), "SC": (_E,), "SD": (_C, _M), "TN": (_C, _E),
    "TX": (_C, _M), "UT": (_M,), "VT": (_E,), "VA": (_E,), "WA": (_P,), "WV": (_E,), "WI": (_C,), "WY": (_M,),
}
MAX_TIMEZONES = 6       # more candidates than this = we do not know where the number is

# Australia (SALES_DIALER_PLAN.md Phase 5). The phonenumbers data puts 07 (Queensland) in Sydney's
# zone, which is an hour wrong in summer (Queensland has no daylight saving), so Australian numbers
# are located here instead.
AU_STATES: dict[str, str] = {
    "New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD", "South Australia": "SA",
    "Western Australia": "WA", "Tasmania": "TAS", "Northern Territory": "NT", "Australian Capital Territory": "ACT",
}
_AU_CODES = frozenset(AU_STATES.values())
AU_STATE_TIMEZONES: dict[str, tuple[str, ...]] = {
    "NSW": ("Australia/Sydney", "Australia/Broken_Hill"), "ACT": ("Australia/Sydney",), "VIC": ("Australia/Melbourne",),
    "QLD": ("Australia/Brisbane",), "SA": ("Australia/Adelaide",), "WA": ("Australia/Perth",),
    "TAS": ("Australia/Hobart",), "NT": ("Australia/Darwin",),
}
# Mobiles and 13/1300/1800 numbers can be anywhere: every mainland zone (the gate needs all of them open).
AU_MAINLAND: tuple[str, ...] = ("Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane",
                                "Australia/Adelaide", "Australia/Perth", "Australia/Darwin")
_AU_AREA_ZONES: dict[str, tuple[str, ...]] = {
    "2": ("Australia/Sydney",), "3": ("Australia/Melbourne", "Australia/Hobart"), "7": ("Australia/Brisbane",),
    "8": ("Australia/Adelaide", "Australia/Perth", "Australia/Darwin"),
}
_AU_CITY_STATE = {"Sydney": "NSW", "Melbourne": "VIC", "Brisbane": "QLD", "Adelaide": "SA", "Perth": "WA",
                  "Hobart": "TAS", "Darwin": "NT", "Canberra": "ACT"}
_AU_CITY_ZONE = {"Sydney": "Australia/Sydney", "Canberra": "Australia/Sydney", "Melbourne": "Australia/Melbourne",
                 "Brisbane": "Australia/Brisbane", "Adelaide": "Australia/Adelaide", "Perth": "Australia/Perth",
                 "Hobart": "Australia/Hobart", "Darwin": "Australia/Darwin"}


def au_timezones(e164: str, state: str = "") -> tuple[str, ...]:
    """Candidate zones for an Australian number: the state's, else the area code's, else the mainland's."""
    if state in AU_STATE_TIMEZONES:
        return AU_STATE_TIMEZONES[state]
    national = e164[3:] if e164.startswith("+61") else ""
    return _AU_AREA_ZONES.get(national[:1], AU_MAINLAND) if national else ()


@dataclass(frozen=True)
class PhoneInfo:
    e164: str
    country: str                      # ISO 3166-1 alpha-2, "" when unknown
    region_code: str                  # US state / CA province code when known
    timezones: tuple[str, ...]        # candidate IANA zones; empty = unknown
    toll_free: bool = False


def _state_from_description(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return ""
    tail = text.rsplit(",", 1)[-1].strip()
    if len(tail) == 2 and tail.upper() in _STATE_CODES:
        return tail.upper()
    return US_STATES.get(text) or US_STATES.get(tail, "")


def valid_timezone(tz: Optional[str]) -> bool:
    try:
        ZoneInfo(tz or "")
        return bool(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def normalize_state(value: Optional[str]) -> str:
    text = (value or "").strip()
    if len(text) == 2 and text.upper() in _STATE_CODES:
        return text.upper()
    return US_STATES.get(text.title(), "")


def normalize_region(country: str, value: Optional[str]) -> str:
    """A state code for the countries whose states the gate knows (US, AU); "" otherwise."""
    if country == "AU":
        text = (value or "").strip()
        if text.upper() in _AU_CODES:
            return text.upper()
        return AU_STATES.get(text.title(), "") or next((v for k, v in AU_STATES.items() if k.lower() == text.lower()), "")
    if country in ("US", ""):
        return normalize_state(value)
    return ""


def is_fixed_line(e164: str) -> bool:
    """A geographic landline by its numbering plan (outside the US there is no paid line-type lookup)."""
    return line_kind(e164) == "landline"


_KINDS = {
    phonenumbers.PhoneNumberType.FIXED_LINE: "landline",
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile",   # e.g. every +1 number
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
    phonenumbers.PhoneNumberType.UAN: "business_line",          # one number for a company, e.g. Pakistan's 111
    phonenumbers.PhoneNumberType.SHARED_COST: "business_line",
    phonenumbers.PhoneNumberType.VOIP: "voip",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
}


def line_kind(e164: str) -> str:
    """What the numbering plan says the number is: landline, mobile, landline_or_mobile, toll_free,
    business_line, voip, premium_rate, or other."""
    try:
        number = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return "other"
    return _KINDS.get(phonenumbers.number_type(number), "other")


def country_name(code: str) -> str:
    """The country's English name, from the numbering plan's data ("BD" → "Bangladesh"); else the code."""
    code = (code or "").upper()
    try:
        example = phonenumbers.example_number(code)
        name = geocoder.country_name_for_number(example, "en") if example else ""
    except Exception:
        name = ""
    return name or code


def parse(raw: Optional[str], default_region: str = "US") -> Optional[PhoneInfo]:
    """None when `raw` is not a dialable number. A national number is read in `default_region`."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        number = phonenumbers.parse(text, (default_region or "US").upper())
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    country = phonenumbers.region_code_for_number(number) or ""
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    zones = tuple(z for z in pn_timezone.time_zones_for_number(number) if valid_timezone(z))
    description = geocoder.description_for_number(number, "en")
    state = _state_from_description(description) if country == "US" else ""
    if country == "AU":
        city = (description or "").strip()
        state = _AU_CITY_STATE.get(city, "")
        zones = (_AU_CITY_ZONE[city],) if city in _AU_CITY_ZONE else au_timezones(e164)
    toll_free = phonenumbers.number_type(number) == phonenumbers.PhoneNumberType.TOLL_FREE
    return PhoneInfo(
        e164=e164,
        country=country, region_code=state,
        timezones=zones if len(zones) <= MAX_TIMEZONES else (), toll_free=toll_free)


def timezones_for(info: PhoneInfo, *, state: str = "", timezone: str = "") -> tuple[str, ...]:
    """Best knowledge of where the lead is: an explicit timezone, else the number, else the state."""
    if valid_timezone(timezone):
        return (timezone,)
    if info.country == "AU":                  # a state from the lead's address beats the area code
        return au_timezones(info.e164, state or info.region_code)
    if info.timezones:
        return info.timezones
    return US_STATE_TIMEZONES.get(state or info.region_code, ())


def area_code(e164: str) -> str:
    """NANP area code of a +1 number, else ''."""
    return e164[2:5] if e164.startswith("+1") and len(e164) == 12 else ""
