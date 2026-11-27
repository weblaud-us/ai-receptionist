"""
Campaign quick-setup (SALES_SUITE_PLAN.md Phase 9): "create a campaign from this offer" with the
settings filled in from the rules, never from a model.

For the leads picked (an import source, a tag, or ids) it counts those that could be queued, by
country, and maps each country to the call type the rules allow:

    an AI type when `rules.allowed_countries(mode)` includes the country
    otherwise human_assisted when that is allowed there
    otherwise nothing, with the reason ("AU AI cold calls are off until counsel adds AU …")

and returns one campaign draft per call type: a name, the offer's first two published scripts for
an A/B split, business hours inside every country's legal window, caller IDs already used for that
country (and not resting), safe starting limits, and the most common vertical. Nothing is created.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Mapping, Optional

from core.outbound import models as m
from core.outbound import number_health, phones, rules

AI_MODES = ("ai_cold_b2b_us",)                   # consented calls need consent records, so never suggested
BUSINESS_HOURS = (time(9, 30), time(16, 30))
START_LIMITS = {"max_concurrent": 2, "daily_cap": 50, "max_attempts": 3, "retry_after_hours": [48, 96]}
COUNTRY_NAMES = {"US": "US", "GB": "UK", "AU": "Australia", "CA": "Canada"}
QUEUEABLE = ("new", "exhausted", "not_interested")


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def window_for(countries: List[str]) -> Dict[str, Any]:
    """Business hours, Monday to Friday, clamped inside every country's legal weekday window."""
    start, end = BUSINESS_HOURS
    days = [1, 2, 3, 4, 5]
    for c in countries:
        for w in rules.legal_windows(c):
            for wd in range(5):
                span = w.for_weekday(wd)
                if span is None:
                    if wd + 1 in days:
                        days.remove(wd + 1)
                    continue
                start, end = max(start, span[0]), min(end, span[1])
    if end <= start:
        return {"start": "10:00", "end": "11:00", "days": days or [2]}
    return {"start": _hhmm(start), "end": _hhmm(end), "days": days or [2]}


def mode_for(country: str) -> Optional[str]:
    for mode in AI_MODES:
        if country in rules.allowed_countries(mode):
            return mode
    return "human_assisted" if country in rules.allowed_countries("human_assisted") else None


def _why_not_ai(country: str) -> str:
    name = COUNTRY_NAMES.get(country, country)
    return (f"{name}: AI cold calls are off there (SALES_AI_COLD_COUNTRIES; counsel decides), so these leads are set "
            "up as human-assisted calls: you talk, the AI takes notes.")


async def suggest(db: Any, offer: Mapping[str, Any], published: Mapping[str, Any], query: Dict[str, Any],
                  *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    leads = await db[m.LEADS].find({**query, "owner": m.OWNER_PLATFORM, "status": {"$in": list(QUEUEABLE)}},
                                   {"country": 1, "vertical": 1, "phone": 1, "declined_offers": 1, "cooldown_until": 1}
                                   ).to_list(20_000)
    offer_id = str(offer.get("id") or offer.get("_id") or "")
    usable: List[Dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for lead in leads:
        cool = lead.get("cooldown_until")
        if isinstance(cool, datetime) and (cool if cool.tzinfo else cool.replace(tzinfo=timezone.utc)) > now:
            skipped["cooling down"] += 1
        elif offer_id in (lead.get("declined_offers") or []):
            skipped["said no to this offer"] += 1
        else:
            usable.append(lead)
    by_country = Counter(str(lead.get("country") or "US") for lead in usable)
    groups: Dict[str, List[str]] = {}
    warnings: List[str] = [f"{n} lead{'s' if n != 1 else ''} skipped: {why}" for why, n in skipped.items()]
    reasons_by_mode: Dict[str, List[str]] = {}
    for country, n in sorted(by_country.items()):
        mode = mode_for(country)
        if mode is None:
            warnings.append(f"{n} {COUNTRY_NAMES.get(country, country)} lead{'s' if n != 1 else ''} can't be called by any "
                            "campaign type yet.")
            continue
        groups.setdefault(mode, []).append(country)
        if mode == "human_assisted":
            reasons_by_mode.setdefault(mode, []).append(_why_not_ai(country))

    used = await db[m.CAMPAIGNS].find({"from_numbers": {"$ne": []}}, {"from_numbers": 1}).to_list(500)
    known = sorted({num for c in used for num in c.get("from_numbers") or []})
    resting = await number_health.rested(db, known, now) if known else set()
    variants = [v["id"] for v in (published.get("settings") or {}).get("variants") or []][:2]
    month = now.strftime("%b %Y")
    drafts = []
    for mode, countries in groups.items():
        group_leads = [lead for lead in usable if str(lead.get("country") or "US") in countries]
        numbers = [n for n in known if (phones.parse(n) or phones.PhoneInfo("", "", "", ())).country in countries and n not in resting]
        vertical = Counter(str(lead.get("vertical") or "") for lead in group_leads if lead.get("vertical")).most_common(1)
        window = window_for(countries)
        reasons = [
            f"Calls go out {window['start']}–{window['end']} on weekdays in each lead's own time: inside the legal hours of "
            + ", ".join(COUNTRY_NAMES.get(c, c) for c in countries) + ".",
            "Starts small: 2 calls at once and 50 a day, 3 attempts two and four days apart. Raise them once the first "
            "results look right.",
            *(["Splits leads 50/50 between your first two scripts to compare meeting rates."] if len(variants) == 2 else []),
            *reasons_by_mode.get(mode, []),
        ]
        draft_warnings = []
        if not numbers:
            draft_warnings.append("No caller ID for " + ", ".join(COUNTRY_NAMES.get(c, c) for c in countries)
                                  + " yet: add a local number before starting.")
        if "AU" in countries:
            draft_warnings.append("Australian numbers must be washed against the Do Not Call Register first (Compliance tab).")
        drafts.append({
            "name": f"{offer.get('name') or 'Offer'} · {', '.join(COUNTRY_NAMES.get(c, c) for c in countries)} · {month}"[:120],
            "mode": mode, "offer_id": offer_id, "offer_version": published.get("version"), "variant_ids": variants,
            "calling_window": window, "from_numbers": numbers[:20], **START_LIMITS,
            "voicemail_policy": "hang_up", "vertical": (vertical[0][0] if vertical else "")[:40], "notes": "",
            "lead_count": len(group_leads), "countries": countries, "reasons": reasons, "warnings": draft_warnings,
        })
    return {"drafts": drafts, "warnings": warnings, "leads_considered": len(leads)}
