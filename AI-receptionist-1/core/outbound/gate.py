"""
The compliance gate (Roadmap Task 4.3). Runs when a lead is imported or queued, and again
immediately before every dial. There is no way round it: the dialer and the sales agent both call
`run_gate`, and neither the API nor the dashboard has an override.

`evaluate` is a pure function of the lead, the campaign, the facts looked up for the number and
the clock, so every rule is tested without a database. Every decision is written to the lead
(`gate`, and the last GATE_LOG_MAX in `gate_log`): if a call is ever challenged, the record shows
why that number was dialled at that moment.

A failure is either
  permanent — waiting will not fix it (on a do-not-call list, a mobile, no consent, wrong country):
              the lead is taken out of the queue; or
  temporary — outside calling hours, a list that needs refreshing, a lookup that failed: the lead
              stays queued and is looked at again at `retry_at`.
Anything unknown fails: no timezone, no line type, no state, an unloaded list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

from config import settings
from core.outbound import lookup, registry, rules
from core.outbound import models as m

logger = logging.getLogger(__name__)

GATE_LOG_MAX = 25
RETRY_LOOKUP = timedelta(hours=1)
RETRY_REGISTRY = timedelta(hours=6)
WINDOW_SEARCH_STEP = timedelta(minutes=5)
WINDOW_SEARCH_DAYS = 8

PERMANENT = frozenset({
    "internal_dnc", "country_not_allowed", "state_blocked", "state_unknown", "line_type_not_allowed",
    "registry_listed", "no_consent", "consent_not_written", "max_attempts", "timezone_unknown",
})


@dataclass(frozen=True)
class GateFacts:
    """What was looked up about the number, outside the lead document."""

    internal_dnc: bool = False
    registry_hits: tuple[str, ...] = ()
    registry_status: Mapping[str, str] = field(default_factory=dict)   # required list → fresh/stale/missing/uncovered
    consent: Optional[Mapping[str, Any]] = None


@dataclass
class GateDecision:
    allowed: bool
    failures: List[str]
    checks: Dict[str, str]
    at: datetime
    mode: str
    campaign_id: Optional[str] = None
    retry_at: Optional[datetime] = None

    @property
    def permanent(self) -> bool:
        return any(f in PERMANENT for f in self.failures)

    def lead_status(self) -> str:
        """Where a permanently failed lead ends up."""
        if "internal_dnc" in self.failures:
            return "dnc"
        if "max_attempts" in self.failures:
            return "exhausted"
        return "blocked"

    def to_doc(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "failures": self.failures, "permanent": self.permanent,
                "checks": self.checks, "at": self.at, "mode": self.mode, "campaign_id": self.campaign_id,
                "retry_at": self.retry_at}


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hhmm(value: str) -> time:
    h, mi = value.split(":")
    return time(int(h), int(mi))


def lead_timezones(lead: Mapping[str, Any]) -> tuple[str, ...]:
    zones = lead.get("timezones") or ([lead["timezone"]] if lead.get("timezone") else [])
    return tuple(z for z in zones if z)


def window_open(lead: Mapping[str, Any], campaign: Mapping[str, Any], now: datetime) -> bool:
    """True when `now` is inside the campaign window AND every legal window, in every timezone the
    lead might be in."""
    zones = lead_timezones(lead)
    if not zones:
        return False
    window = campaign.get("calling_window") or {}
    start, end = _hhmm(window.get("start", "09:30")), _hhmm(window.get("end", "17:00"))
    days = set(window.get("days") or [1, 2, 3, 4, 5])
    legal = rules.legal_windows(str(lead.get("country") or ""), str(lead.get("region_code") or ""))
    for zone in zones:
        local = now.astimezone(ZoneInfo(zone))
        t = local.time()
        if local.isoweekday() not in days or not (start <= t < end):
            return False
        for w in legal:
            span = w.for_weekday(local.weekday())
            if span is None or not (span[0] <= t < span[1]):
                return False
    return True


def next_window(lead: Mapping[str, Any], campaign: Mapping[str, Any], now: datetime) -> Optional[datetime]:
    """The next moment the window opens (5-minute resolution), or None within the next week."""
    probe = now.replace(second=0, microsecond=0)
    probe += timedelta(minutes=5 - probe.minute % 5)
    for _ in range(int(timedelta(days=WINDOW_SEARCH_DAYS) / WINDOW_SEARCH_STEP)):
        if window_open(lead, campaign, probe):
            return probe
        probe += WINDOW_SEARCH_STEP
    return None


def evaluate(lead: Mapping[str, Any], campaign: Mapping[str, Any], facts: GateFacts, now: datetime) -> GateDecision:
    mode = str(campaign.get("mode") or "")
    country = str(lead.get("country") or "")
    region = str(lead.get("region_code") or "")
    checks: Dict[str, str] = {}
    failures: List[str] = []
    retry: List[datetime] = []

    def fail(check: str, code: str, detail: str, *, retry_at: Optional[datetime] = None) -> None:
        checks[check] = f"fail: {detail}"
        failures.append(code)
        if retry_at is not None:
            retry.append(retry_at)

    # 1. Our own do-not-call list
    if facts.internal_dnc or lead.get("status") == "dnc":
        fail("internal_dnc", "internal_dnc", "on the internal do-not-call list")
    else:
        checks["internal_dnc"] = "pass"

    # 2. Is this kind of call allowed in the lead's country (and state)?
    if not country or country not in rules.allowed_countries(mode):
        fail("market", "country_not_allowed", f"{mode} campaigns may not call {country or 'an unknown country'}")
    elif mode == "ai_cold_b2b_us" and not region:
        fail("market", "state_unknown", "the US state is unknown, so state rules cannot be checked")
    elif mode == "ai_cold_b2b_us" and region in settings.sales_blocked_us_states:
        fail("market", "state_blocked", f"AI cold calls to {region} are switched off (SALES_BLOCKED_US_STATES)")
    else:
        checks["market"] = "pass"

    # 3. Line type — an AI voice may only cold-call business landlines
    if mode == "ai_cold_b2b_us":
        line_type = str(lead.get("line_type") or "")
        checked = _aware(lead.get("line_type_checked_at"))
        if not line_type or checked is None or checked < now - timedelta(days=settings.SALES_LINE_TYPE_MAX_AGE_DAYS):
            fail("line_type", "line_type_unchecked", "line type not looked up recently", retry_at=now + RETRY_LOOKUP)
        elif line_type not in rules.AI_COLD_LINE_TYPES:
            fail("line_type", "line_type_not_allowed", f"{line_type} line")
        else:
            checks["line_type"] = f"pass: {line_type}"
    else:
        checks["line_type"] = "n/a"

    # 4. National / state do-not-call registries
    if facts.registry_hits:
        fail("registry", "registry_listed", "listed on " + ", ".join(facts.registry_hits))
    else:
        not_ready = sorted(name for name, status in facts.registry_status.items() if status != "fresh")
        if not_ready:
            fail("registry", "registry_not_ready",
                 "; ".join(f"{n} is {facts.registry_status[n]}" for n in not_ready), retry_at=now + RETRY_REGISTRY)
        else:
            checks["registry"] = "pass" if facts.registry_status else "n/a"

    # 5. Consent
    if mode == "ai_consented":
        consent = facts.consent
        if not consent or consent.get("revoked_at"):
            fail("consent", "no_consent", "no consent record for this number")
        elif country == "US" and consent.get("type") != "written":
            fail("consent", "consent_not_written", "US AI calls need written consent")
        else:
            checks["consent"] = f"pass: {consent.get('type')} via {consent.get('source')}"
    else:
        checks["consent"] = "n/a"

    # 6. Frequency
    attempts = int(lead.get("attempts") or 0)
    eligible = _aware(lead.get("next_eligible_at"))
    if attempts >= int(campaign.get("max_attempts") or 3):
        fail("frequency", "max_attempts", f"already tried {attempts} times")
    elif eligible is not None and eligible > now:
        fail("frequency", "too_soon", "not due yet", retry_at=eligible)
    else:
        checks["frequency"] = "pass"

    # 7. Calling hours, in the lead's local time
    if not lead_timezones(lead):
        fail("calling_window", "timezone_unknown", "the lead's timezone is unknown")
    elif not window_open(lead, campaign, now):
        fail("calling_window", "outside_hours", "outside the campaign or legal calling hours",
             retry_at=next_window(lead, campaign, now) or now + timedelta(days=1))
    else:
        checks["calling_window"] = "pass"

    return GateDecision(allowed=not failures, failures=failures, checks=checks, at=now, mode=mode,
                        campaign_id=campaign.get("_id"), retry_at=max(retry) if retry and failures else None)


async def refresh_line_type(db: Any, lead: Dict[str, Any], now: datetime) -> None:
    """Look the line type up when it is missing or old, and store it on the lead (and in `lead`)."""
    checked = _aware(lead.get("line_type_checked_at"))
    if lead.get("line_type") and checked and checked >= now - timedelta(days=settings.SALES_LINE_TYPE_MAX_AGE_DAYS):
        return
    found = await lookup.line_type(lead["phone"])
    if found is None:
        return
    lead["line_type"], lead["line_type_checked_at"] = found, now
    await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"line_type": found, "line_type_checked_at": now}})


async def gather_facts(db: Any, lead: Mapping[str, Any], campaign: Mapping[str, Any], now: datetime) -> GateFacts:
    phone = lead["phone"]
    mode, country = str(campaign.get("mode") or ""), str(lead.get("country") or "")
    required = rules.required_registries(mode, country)
    optional = (rules.state_registry(str(lead.get("region_code") or "")),) if required and country == "US" else ()
    return GateFacts(
        internal_dnc=await registry.on_internal_dnc(db, phone),
        registry_hits=tuple(await registry.registry_hits(db, phone, (*required, *optional))),
        registry_status=await registry.registry_status(db, required, phone, now),
        consent=await registry.active_consent(db, phone) if mode == "ai_consented" else None,
    )


async def run_gate(db: Any, lead: Dict[str, Any], campaign: Mapping[str, Any], now: Optional[datetime] = None,
                   *, lookup_line_type: bool = True, record: bool = True) -> GateDecision:
    """Evaluate the gate for one lead against live data and write the decision on the lead."""
    now = now or datetime.now(timezone.utc)
    if lookup_line_type and campaign.get("mode") == "ai_cold_b2b_us":
        await refresh_line_type(db, lead, now)
    decision = evaluate(lead, campaign, await gather_facts(db, lead, campaign, now), now)
    if record:
        doc = decision.to_doc()
        await db[m.LEADS].update_one(
            {"_id": lead["_id"]},
            {"$set": {"gate": doc, "dnc.checked_at": now,
                      "dnc.national": "registry_listed" in decision.failures,
                      "dnc.internal": "internal_dnc" in decision.failures},
             "$push": {"gate_log": {"$each": [doc], "$slice": -GATE_LOG_MAX}}})
    return decision
