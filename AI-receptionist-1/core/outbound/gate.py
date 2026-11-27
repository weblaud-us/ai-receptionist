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
from core.outbound import lookup, phones, registry, rules
from core.outbound import models as m
from core.outbound import offers as of

logger = logging.getLogger(__name__)

GATE_LOG_MAX = 25
RETRY_LOOKUP = timedelta(hours=1)
RETRY_REGISTRY = timedelta(hours=6)
WINDOW_SEARCH_STEP = timedelta(minutes=5)
WINDOW_SEARCH_DAYS = 8

PERMANENT = frozenset({
    "internal_dnc", "country_not_allowed", "state_blocked", "state_unknown", "line_type_not_allowed",
    "registry_listed", "no_consent", "consent_not_written", "max_attempts", "timezone_unknown",
    "wrong_campaign_type", "offer_declined", "cooldown", "consent_scope",
    # patient reactivation
    "opted_out", "not_mobile", "no_consent_basis", "consent_expired", "not_lapsed", "has_future_booking",
    "recall_disabled", "voice_disabled", "pms_marketing_opt_out",
})


@dataclass(frozen=True)
class GateFacts:
    """What was looked up about the number, outside the lead document."""

    internal_dnc: bool = False
    registry_hits: tuple[str, ...] = ()
    registry_status: Mapping[str, str] = field(default_factory=dict)   # required list → fresh/stale/missing/uncovered
    consent: Optional[Mapping[str, Any]] = None
    offer_company: str = ""            # the company the campaign's offer calls for ("" = the built-in offer)


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
        permanent = [f for f in self.failures if f in PERMANENT]
        if permanent and all(f in ("offer_declined", "cooldown") for f in permanent):
            return "not_interested"          # said no on an earlier call: not blocked, just resting
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
    country = str(lead.get("country") or "")
    legal = rules.legal_windows(country, str(lead.get("region_code") or ""))
    for zone in zones:
        local = now.astimezone(ZoneInfo(zone))
        t = local.time()
        if local.isoweekday() not in days or not (start <= t < end):
            return False
        if rules.closed_day(country, local.date()):       # e.g. an Australian public holiday
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

    # 0. This gate is for the sales dialer only; patient recall has its own (evaluate_recall).
    if str(campaign.get("campaign_type") or "sales_outbound") != "sales_outbound":
        fail("campaign_type", "wrong_campaign_type", "only sales_outbound campaigns use the sales gate")

    # 1. Our own do-not-call list
    if facts.internal_dnc or lead.get("status") == "dnc":
        fail("internal_dnc", "internal_dnc", "on the internal do-not-call list")
    else:
        checks["internal_dnc"] = "pass"

    # 2. Is this kind of call allowed in the lead's country (and state)?
    if not country or country not in rules.allowed_countries(mode):
        fail("market", "country_not_allowed", f"{mode} campaigns may not call {country or 'an unknown country'}")
    elif mode == "ai_cold_b2b_us" and country == "US" and not region:
        fail("market", "state_unknown", "the US state is unknown, so state rules cannot be checked")
    elif mode == "ai_cold_b2b_us" and country == "US" and region in settings.sales_blocked_us_states:
        fail("market", "state_blocked", f"AI cold calls to {region} are switched off (SALES_BLOCKED_US_STATES)")
    else:
        checks["market"] = "pass"

    # 3. Line type — an AI voice may only cold-call business landlines
    if mode == "ai_cold_b2b_us" and country and country != "US":
        # No paid lookup outside the US: the numbering plan says whether it is a geographic landline.
        if phones.is_fixed_line(str(lead.get("phone") or "")):
            checks["line_type"] = "pass: fixed line (numbering plan)"
        else:
            fail("line_type", "line_type_not_allowed", "not a geographic landline")
    elif mode == "ai_cold_b2b_us":
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
        elif not consent_covers(consent, facts.offer_company):
            fail("consent", "consent_scope", "they agreed to calls from "
                 f"{consent_company(consent)}, not {facts.offer_company or settings.SALES_COMPANY_NAME}")
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

    # 8. Offer: a "no" to this offer is final for it; a "no" to any offer rests the lead for a while
    offer_id = of.offer_id_of(campaign)
    cooldown = _aware(lead.get("cooldown_until"))
    if offer_id in (lead.get("declined_offers") or []):
        fail("offer", "offer_declined", "they said they weren't interested in this offer")
    elif cooldown is not None and cooldown > now:
        fail("offer", "cooldown", f"they said no to an offer recently; resting until {cooldown:%Y-%m-%d}")
    else:
        checks["offer"] = "pass"

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


def consent_company(consent: Mapping[str, Any]) -> str:
    """Who the person agreed to be called by. Records from before offers were for the built-in
    LaudLine offer."""
    scope = consent.get("scope") or {}
    return str(scope.get("company") or settings.SALES_COMPANY_NAME)


def consent_covers(consent: Mapping[str, Any], offer_company: str) -> bool:
    """A consent record covers calls from the company it named, and nobody else."""
    wanted = (offer_company or settings.SALES_COMPANY_NAME).strip().lower()
    return consent_company(consent).strip().lower() == wanted


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
    offer_company = ""
    if mode == "ai_consented":
        offer_company = str((await of.offer_for_campaign(db, campaign)).get("company_name") or "")
    return GateFacts(
        offer_company=offer_company,
        internal_dnc=await registry.on_internal_dnc(db, phone),
        registry_hits=tuple(await registry.registry_hits(db, phone, (*required, *optional))),
        registry_status=await registry.registry_status(db, required, phone, now),
        consent=await registry.active_consent(db, phone) if mode == "ai_consented" else None,
    )


async def run_gate(db: Any, lead: Dict[str, Any], campaign: Mapping[str, Any], now: Optional[datetime] = None,
                   *, lookup_line_type: bool = True, record: bool = True) -> GateDecision:
    """Evaluate the gate for one lead against live data and write the decision on the lead."""
    now = now or datetime.now(timezone.utc)
    if lookup_line_type and campaign.get("mode") == "ai_cold_b2b_us" and lead.get("country") == "US":
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


# ── Patient reactivation (dental recall) ─────────────────────────────────────
#
# Pure, like `evaluate`: the recall row, the practice's settings, what was looked up, the clock.
# Fails closed: an unknown country, no consent basis, an unknown last visit, outside the sending
# hours, a text in the last 30 days all block the send.

@dataclass(frozen=True)
class RecallFacts:
    opted_out: bool = False                    # core/compliance/optout.is_opted_out (tenant + platform)
    explicit_opt_in_at: Optional[datetime] = None   # the patient said yes to recall texts
    pms_marketing_consent: Optional[bool] = None    # the PMS's own SMS-marketing flag; False = refused
    last_recall_sent_at: Optional[datetime] = None  # any recall text to this number, any cycle
    explicit_voice_consent_at: Optional[datetime] = None
    registry_hits: tuple[str, ...] = ()
    registry_status: Mapping[str, str] = field(default_factory=dict)


def _months_ago(now: datetime, months: int) -> datetime:
    return now - timedelta(days=round(months * 30.44))


def recall_window_open(window: Mapping[str, Any], tz: str, now: datetime) -> bool:
    """Inside the practice's sending hours (local time), which can never pass 08:00–20:00 or Sunday."""
    try:
        local = now.astimezone(ZoneInfo(tz))
    except Exception:
        return False
    if local.isoweekday() == 7:
        return False
    span = window.get("saturday") if local.isoweekday() == 6 else window.get("weekday")
    if not span:
        return False
    start = max(_hhmm(span[0]), _hhmm(rules.RECALL_HARD_START))
    end = min(_hhmm(span[1]), _hhmm(rules.RECALL_HARD_END))
    return start <= local.time() < end


def next_recall_window(window: Mapping[str, Any], tz: str, now: datetime) -> Optional[datetime]:
    probe = now.replace(second=0, microsecond=0)
    probe += timedelta(minutes=5 - probe.minute % 5)
    for _ in range(int(timedelta(days=WINDOW_SEARCH_DAYS) / WINDOW_SEARCH_STEP)):
        if recall_window_open(window, tz, probe):
            return probe
        probe += WINDOW_SEARCH_STEP
    return None


def consent_basis(country: str, facts: RecallFacts, last_visit: Optional[datetime], now: datetime) -> Optional[str]:
    """The lawful basis for one recall text, or None.
    AU: express opt-in, or inferred consent from an existing patient relationship (Spam Act).
    GB: express opt-in, or PECR soft opt-in (existing patient, similar routine care, opt-out offered)."""
    if facts.pms_marketing_consent is False:
        return None
    if facts.explicit_opt_in_at is not None or facts.pms_marketing_consent is True:
        return "explicit_opt_in"
    if last_visit is not None and last_visit >= _months_ago(now, rules.INFERRED_MAX_MONTHS):
        return rules.INFERRED_CONSENT_BASIS.get(country)
    return None


def evaluate_recall(recall: Mapping[str, Any], settings_: Mapping[str, Any], *, country: str, tz: str,
                    facts: RecallFacts, now: datetime, channel: str = "sms") -> GateDecision:
    checks: Dict[str, str] = {}
    failures: List[str] = []
    retry: List[datetime] = []
    phone = str(recall.get("phone") or "")
    last_visit = _aware(recall.get("last_visit_at"))
    interval = int(settings_.get("recall_interval_months") or 6)
    attempts = len(recall.get("attempts") or [])

    def fail(check: str, code: str, detail: str, *, retry_at: Optional[datetime] = None) -> None:
        checks[check] = f"fail: {detail}"
        failures.append(code)
        if retry_at is not None:
            retry.append(retry_at)

    if not settings_.get("enabled"):
        fail("enabled", "recall_disabled", "patient recall is switched off")
    if country not in rules.RECALL_COUNTRIES:
        fail("market", "country_not_allowed", f"patient recall isn't set up for {country or 'an unknown country'}")
    else:
        checks["market"] = "pass"
    if facts.opted_out:
        fail("opt_out", "opted_out", "the patient opted out")
    elif facts.pms_marketing_consent is False:
        fail("opt_out", "pms_marketing_opt_out", "the practice system says no marketing texts")
    else:
        checks["opt_out"] = "pass"
    if channel == "sms" and not rules.is_mobile(phone, country):
        fail("mobile", "not_mobile", "no valid mobile number")
    else:
        checks["mobile"] = "pass" if channel == "sms" else "n/a"

    # Lapsed: the last completed visit is old enough, and nothing is booked.
    if last_visit is None:
        fail("lapsed", "not_lapsed", "last visit unknown")
    elif last_visit > _months_ago(now, interval):
        fail("lapsed", "not_lapsed", f"seen within the last {interval} months")
    elif recall.get("has_future_booking"):
        fail("lapsed", "has_future_booking", "already has an appointment booked")
    else:
        checks["lapsed"] = "pass"

    basis = consent_basis(country, facts, last_visit, now)
    if basis is None or basis not in rules.RECALL_CONSENT_BASES.get(country, ()):
        expired = last_visit is not None and last_visit < _months_ago(now, rules.INFERRED_MAX_MONTHS)
        fail("consent", "consent_expired" if expired else "no_consent_basis",
             f"no consent basis (last visit over {rules.INFERRED_MAX_MONTHS} months ago)" if expired
             else "no consent basis")
    else:
        checks["consent"] = f"pass: {basis}"

    # Voice (off by default): the practice must allow it, and the number must clear the register
    # unless the patient gave express consent to calls.
    if channel == "voice":
        if not settings_.get("voice_enabled"):
            fail("voice", "voice_disabled", "voice recall is switched off")
        elif facts.explicit_voice_consent_at is None:
            if facts.registry_hits:
                fail("registry", "registry_listed", "listed on " + ", ".join(facts.registry_hits))
            else:
                required = rules.VOICE_RECALL_REGISTRIES.get(country, ())
                not_ready = [n for n in required if facts.registry_status.get(n) != "fresh"]
                if not_ready:
                    fail("registry", "registry_not_ready", ", ".join(not_ready) + " not loaded or stale",
                         retry_at=now + RETRY_REGISTRY)
                else:
                    checks["registry"] = "pass"
        else:
            checks["registry"] = "pass: express voice consent"

    # Frequency: one text per 30 days per patient, two per cycle.
    last_sent = _aware(facts.last_recall_sent_at)
    if attempts >= rules.RECALL_MAX_ATTEMPTS:
        fail("frequency", "max_attempts", f"already sent {attempts} recall texts this cycle")
    elif last_sent is not None and last_sent > now - timedelta(days=rules.RECALL_MIN_GAP_DAYS):
        fail("frequency", "too_soon", "a recall text went out in the last 30 days",
             retry_at=last_sent + timedelta(days=rules.RECALL_MIN_GAP_DAYS))
    else:
        checks["frequency"] = "pass"

    window = settings_.get("send_window") or {}
    if not recall_window_open(window, tz, now):
        fail("sending_hours", "outside_hours", "outside the practice's sending hours",
             retry_at=next_recall_window(window, tz, now) or now + timedelta(days=1))
    else:
        checks["sending_hours"] = "pass"

    decision = GateDecision(allowed=not failures, failures=failures, checks=checks, at=now,
                            mode=f"patient_reactivation_{channel}", retry_at=max(retry) if retry and failures else None)
    decision.checks["consent_basis"] = basis or ""
    return decision
