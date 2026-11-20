"""
Legal calling rules, as data (Roadmap Tasks 4.0 and 4.3).

⚖ THIS FILE IS NOT LEGAL ADVICE. It encodes the conservative reading in ROADMAP.md §9 and §11 so
the dialer fails closed. A telemarketing lawyer in each market must confirm every row before the
first campaign, and again when the law changes. The rule the code enforces: a row may only make
calling NARROWER than the country floor, never wider.

- A campaign *mode* says which kind of call is being made; each mode is only available in the
  countries listed here (two of the lists are settings, so counsel's answer is a config change).
- A calling window is the callee's local time. US states can be narrower than the federal
  8 am – 9 pm; the gate intersects the federal window, the state window and the campaign's own.
- Do-not-call lists that must be loaded, and fresh, before a number in that country is dialled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Mapping, Optional

from config import settings

Day = Optional[tuple[time, time]]          # None = no calls that day


@dataclass(frozen=True)
class LegalWindow:
    """Allowed local hours per weekday (0 = Monday … 6 = Sunday)."""

    days: tuple[Day, ...]
    source: str

    def for_weekday(self, weekday: int) -> Day:
        return self.days[weekday]


def _t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _every_day(start: str, end: str, source: str, *, sunday: bool = True, saturday: bool = True) -> LegalWindow:
    span = (_t(start), _t(end))
    return LegalWindow(days=(span,) * 5 + (span if saturday else None, span if sunday else None), source=source)


def _weekly(weekday: tuple[str, str], saturday: Optional[tuple[str, str]], sunday: Optional[tuple[str, str]],
            source: str) -> LegalWindow:
    def span(v: Optional[tuple[str, str]]) -> Day:
        return (_t(v[0]), _t(v[1])) if v else None
    return LegalWindow(days=(span(weekday),) * 5 + (span(saturday), span(sunday)), source=source)


# Country floors.
US_FEDERAL = _every_day("08:00", "21:00", "US federal: 47 CFR 64.1200(c)(1), 8 am – 9 pm callee local time")
COUNTRY_WINDOWS: Mapping[str, LegalWindow] = {
    "US": US_FEDERAL,
    # ⚠ VERIFY: CRTC Unsolicited Telecommunications Rules — weekdays 9:00–21:30, weekends 10:00–18:00.
    "CA": _weekly(("09:00", "21:30"), ("10:00", "18:00"), ("10:00", "18:00"), "CA: CRTC UTR calling hours"),
    # The UK sets no statutory hours; business hours are used so a B2B call lands at a business.
    "GB": _every_day("09:00", "18:00", "GB: business hours (no statutory window)", saturday=False, sunday=False),
    # UAE Cabinet Resolution 56 of 2024: 9 am – 6 pm.
    "AE": _every_day("09:00", "18:00", "AE: Cabinet Resolution 56/2024, 9 am – 6 pm"),
}
# Anywhere else a consented call is placed: weekday business hours until researched.
DEFAULT_WINDOW = _every_day("09:00", "18:00", "default: weekday business hours", saturday=False, sunday=False)

# US states with a narrower window than federal. ⚠ VERIFY every row with counsel; a wrong row here
# can only stop calls that federal law would allow, never permit one it forbids.
US_STATE_WINDOWS: Mapping[str, LegalWindow] = {
    "AL": _every_day("08:00", "20:00", "AL: 8 am – 8 pm, no Sunday calls", sunday=False),
    "CT": _every_day("09:00", "20:00", "CT: 9 am – 8 pm"),
    "FL": _every_day("08:00", "20:00", "FL: Florida Telephone Solicitation Act, 8 am – 8 pm"),
    "KY": _every_day("10:00", "21:00", "KY: 10 am – 9 pm"),
    "LA": _every_day("08:00", "20:00", "LA: 8 am – 8 pm, no Sunday calls", sunday=False),
    "MA": _every_day("08:00", "20:00", "MA: 8 am – 8 pm"),
    "MD": _every_day("08:00", "20:00", "MD: Stop the Spam Calls Act, 8 am – 8 pm"),
    "MS": _every_day("08:00", "20:00", "MS: 8 am – 8 pm, no Sunday calls", sunday=False),
    "NM": _every_day("09:00", "21:00", "NM: 9 am – 9 pm"),
    "NV": _every_day("09:00", "20:00", "NV: 9 am – 8 pm"),
    "OK": _every_day("08:00", "20:00", "OK: Oklahoma Telephone Solicitation Act, 8 am – 8 pm"),
    "RI": _weekly(("09:00", "18:00"), ("10:00", "17:00"), None, "RI: weekdays 9–6, Saturday 10–5, no Sunday calls"),
    "SD": _every_day("09:00", "21:00", "SD: 9 am – 9 pm, no Sunday calls", sunday=False),
    "TX": _weekly(("09:00", "21:00"), ("09:00", "21:00"), ("12:00", "21:00"), "TX: 9 am – 9 pm, Sunday from noon"),
    "UT": _every_day("08:00", "21:00", "UT: no Sunday calls", sunday=False),
    "WA": _every_day("08:00", "20:00", "WA: 8 am – 8 pm"),
    "WY": _every_day("08:00", "20:00", "WY: 8 am – 8 pm"),
}

# Do-not-call lists that must be loaded (scripts/import_dnc_registry.py) before dialling there.
# US numbers are scrubbed even for B2B: many "business" numbers are personal phones.
REQUIRED_REGISTRIES: Mapping[str, tuple[str, ...]] = {
    "US": ("us_national",),
    "CA": ("ca_dncl",),
    "GB": ("uk_tps", "uk_ctps"),
}

# Line types an AI voice may cold-call in the US. A mobile needs prior express written consent; a
# non-fixed VoIP number (Google Voice and the like) usually rings a mobile, so it is treated as one.
AI_COLD_LINE_TYPES = frozenset({"landline", "fixed_voip", "toll_free"})


# ── Patient reactivation (dental recall, SMS-first) ──────────────────────────
# ⚖ Counsel must confirm. AU: Spam Act 2003 (consent, sender identification, functional unsubscribe)
# and the DNCR Act 2006 for voice. UK: PECR reg. 22 soft opt-in (existing patient, similar service,
# opt-out offered every time) and TPS/CTPS for voice.
RECALL_COUNTRIES = frozenset({"AU", "GB"})
RECALL_CONSENT_BASES: Mapping[str, tuple[str, ...]] = {
    "AU": ("explicit_opt_in", "au_inferred_existing_patient"),
    "GB": ("explicit_opt_in", "uk_pecr_soft_opt_in"),
}
INFERRED_CONSENT_BASIS: Mapping[str, str] = {"AU": "au_inferred_existing_patient", "GB": "uk_pecr_soft_opt_in"}
# Inferred / soft-opt-in consent only lasts while the patient relationship is recent.
INFERRED_MAX_MONTHS = 24
RECALL_MIN_GAP_DAYS = 30               # at most one recall text per patient per 30 days
RECALL_MAX_ATTEMPTS = 2                # per recall cycle (the first text and one follow-up)
RECALL_FOLLOW_UP_DAYS = 14
RECALL_ATTRIBUTION_DAYS = 14           # a booking this soon after a recall text is credited to it
# Sending hours in the practice's local time: the default, and the hard limits no setting can pass.
RECALL_HARD_START, RECALL_HARD_END = "08:00", "20:00"
RECALL_DEFAULT_WINDOW = _weekly(("09:00", "18:00"), ("10:00", "16:00"), None,
                                "recall default: weekdays 9–6, Saturday 10–4, never Sunday")
# Voice recall (off by default) must clear the national do-not-call register unless the patient gave
# express consent to calls.
VOICE_RECALL_REGISTRIES: Mapping[str, tuple[str, ...]] = {"AU": ("au_dncr",), "GB": ("uk_tps", "uk_ctps")}
# Mobile numbers only (an SMS to a landline goes nowhere and may be read aloud to the household).
MOBILE_PREFIXES: Mapping[str, tuple[str, ...]] = {"AU": ("+614",), "GB": ("+447",)}


def is_mobile(phone: str, country: str) -> bool:
    prefixes = MOBILE_PREFIXES.get(country, ())
    return any((phone or "").startswith(p) for p in prefixes) and 11 <= len(phone or "") <= 13


def allowed_countries(mode: str) -> frozenset[str]:
    """Where a campaign mode may dial. Empty for an unknown mode: fail closed."""
    if mode == "ai_cold_b2b_us":
        return frozenset({"US"})                 # UK / EU / CA / UAE: no AI cold calls (Task 4.0)
    if mode == "ai_consented":
        return settings.sales_ai_consented_countries
    if mode == "human_assisted":
        return settings.sales_human_assisted_countries
    return frozenset()


def legal_windows(country: str, region_code: str = "") -> list[LegalWindow]:
    """Every window that applies; the gate requires the local time to be inside all of them."""
    windows = [COUNTRY_WINDOWS.get(country, DEFAULT_WINDOW)]
    if country == "US" and region_code in US_STATE_WINDOWS:
        windows.append(US_STATE_WINDOWS[region_code])
    return windows


def required_registries(mode: str, country: str) -> tuple[str, ...]:
    """Lists to scrub against. A consent record is an exemption from the registry (not from the
    internal list: asking not to be called revokes consent)."""
    if mode == "ai_consented" and country != "GB":
        return ()
    # UK: every voice call is screened against TPS/CTPS, even with a consent record (fail closed).
    lists = REQUIRED_REGISTRIES.get(country, ())
    return lists


def state_registry(region_code: str) -> str:
    """Name of a state's own do-not-call list, checked when it has been loaded."""
    return f"us_state:{region_code}" if region_code else ""
