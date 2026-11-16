"""
What a phone number tells us: E.164 form, country, US state and the timezone(s) it may be in.

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
    zones = tuple(z for z in pn_timezone.time_zones_for_number(number) if valid_timezone(z))
    state = _state_from_description(geocoder.description_for_number(number, "en")) if country == "US" else ""
    toll_free = phonenumbers.number_type(number) == phonenumbers.PhoneNumberType.TOLL_FREE
    return PhoneInfo(
        e164=phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
        country=country, region_code=state,
        timezones=zones if len(zones) <= MAX_TIMEZONES else (), toll_free=toll_free)


def timezones_for(info: PhoneInfo, *, state: str = "", timezone: str = "") -> tuple[str, ...]:
    """Best knowledge of where the lead is: an explicit timezone, else the number, else the state."""
    if valid_timezone(timezone):
        return (timezone,)
    if info.timezones:
        return info.timezones
    return US_STATE_TIMEZONES.get(state or info.region_code, ())


def area_code(e164: str) -> str:
    """NANP area code of a +1 number, else ''."""
    return e164[2:5] if e164.startswith("+1") and len(e164) == 12 else ""
