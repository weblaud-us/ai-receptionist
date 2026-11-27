# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""SALES_DIALER_PLAN.md Phase 5: calling Australia. Every rule here is marked ⚠ VERIFY with counsel in
core/outbound/rules.py; these tests pin what the code does today."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.outbound import gate, phones, registry, rules
from core.outbound import models as m
from core.outbound.gate import GateFacts
from tests.test_phase4_outbound import admin, campaign_doc  # noqa: F401  (fixtures)

SYD = ZoneInfo("Australia/Sydney")
SYDNEY_LANDLINE = "+61290000000"
FRESH_AU = {"au_dncr": "fresh"}


def au_lead(phone=SYDNEY_LANDLINE, **kw):
    info = phones.parse(phone)
    doc = {"_id": "lead_au", "owner": "platform", "phone": info.e164, "business_name": "Harbour Dental", "country": "AU",
           "region_code": info.region_code, "timezones": list(info.timezones),
           "timezone": info.timezones[0] if len(info.timezones) == 1 else "", "status": "queued", "attempts": 0,
           "next_eligible_at": None, "campaign_id": "cmp_1"}
    doc.update(kw)
    return doc


def at(y, mo, d, h, mi=0, zone=SYD):
    return datetime(y, mo, d, h, mi, tzinfo=zone).astimezone(timezone.utc)


def human(**kw):
    return campaign_doc(mode="human_assisted", calling_window={"start": "08:00", "end": "21:00", "days": [1, 2, 3, 4, 5, 6, 7]}, **kw)


@pytest.fixture
def au_on(monkeypatch):
    monkeypatch.setattr(settings, "SALES_HUMAN_ASSISTED_COUNTRIES", "US,CA,GB,AU")
    monkeypatch.setattr(settings, "SALES_AI_CONSENTED_COUNTRIES", "US,AU")


def test_au_numbers_are_located_correctly():
    assert phones.parse("+61290000000").timezones == ("Australia/Sydney",)
    assert phones.parse("(02) 9000 0000", "AU").region_code == "NSW"
    # Queensland has no daylight saving: never Sydney's zone (the phonenumbers data says Sydney)
    assert phones.parse("+61730000000").timezones == ("Australia/Brisbane",)
    assert phones.parse("+61882000000").timezones == ("Australia/Adelaide",)             # geolocated: Adelaide
    assert "Australia/Darwin" in phones.parse("+61893000000").timezones                    # 08, not located: includes Darwin
    assert phones.parse("+61889000000").timezones == ("Australia/Darwin",)               # …and a Darwin number is Darwin
    mobile = phones.parse("0412 345 678", "AU")
    assert mobile.timezones == phones.AU_MAINLAND and len(mobile.timezones) <= phones.MAX_TIMEZONES
    assert phones.timezones_for(mobile, state="WA") == ("Australia/Perth",)              # the address narrows it
    assert phones.normalize_region("AU", "queensland") == "QLD" and phones.normalize_region("AU", "TX") == ""
    assert phones.normalize_region("US", "Texas") == "TX"
    assert phones.is_fixed_line("+61290000000") and not phones.is_fixed_line("+61412345678")


def test_au_calling_hours_weekdays_9_to_8_saturday_9_to_5_never_sunday(au_on):
    facts = GateFacts(registry_status=FRESH_AU)
    tuesday = date(2026, 12, 1)
    assert gate.evaluate(au_lead(), human(), facts, at(2026, 12, 1, 19, 30)).allowed
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2026, 12, 1, 20, 0)).failures
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2026, 12, 1, 8, 59)).failures
    assert gate.evaluate(au_lead(), human(), facts, at(2026, 12, 5, 16, 30)).allowed               # Saturday
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2026, 12, 5, 17, 0)).failures
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2026, 12, 6, 11, 0)).failures   # Sunday
    assert tuesday.weekday() == 1


def test_brisbane_is_judged_in_brisbane_time_in_summer(au_on):
    facts = GateFacts(registry_status=FRESH_AU)
    brisbane = au_lead("+61730000000")
    # 9:30 in Sydney (daylight saving) is 8:30 in Brisbane: too early there
    assert "outside_hours" in gate.evaluate(brisbane, human(), facts, at(2026, 12, 1, 9, 30)).failures
    assert gate.evaluate(brisbane, human(), facts, at(2026, 12, 1, 10, 30)).allowed


@pytest.mark.parametrize("day, name", [
    (date(2027, 3, 26), "Good Friday"), (date(2027, 3, 29), "Easter Monday"),     # Easter 2027 is 28 March
    (date(2027, 1, 26), "Australia Day"), (date(2026, 12, 25), "Christmas Day"),
    (date(2027, 12, 27), "Christmas Day (substitute)"),                           # 25 Dec 2027 is a Saturday
    (date(2027, 12, 28), "Boxing Day (substitute)"),
    (date(2027, 4, 26), "Anzac Day (substitute)"),                                 # 25 Apr 2027 is a Sunday
])
def test_national_public_holidays_and_weekend_substitutes_are_closed(day, name):
    assert rules.closed_day("AU", day) == name
    assert rules.closed_day("GB", day) is None


def test_no_calls_on_a_public_holiday_or_a_blocked_date(au_on, monkeypatch):
    facts = GateFacts(registry_status=FRESH_AU)
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2027, 3, 26, 11, 0)).failures   # Good Friday
    monkeypatch.setattr(settings, "SALES_AU_EXTRA_BLOCKED_DATES", "2026-12-01")
    assert "outside_hours" in gate.evaluate(au_lead(), human(), facts, at(2026, 12, 1, 11, 0)).failures
    decision = gate.evaluate(au_lead(), human(), facts, at(2026, 12, 1, 11, 0))
    assert gate.window_open(au_lead(), human(), decision.retry_at)                  # retries on an open day


def test_ai_cold_calls_to_australia_stay_off_until_counsel_switches_them_on(monkeypatch):
    cold = campaign_doc(mode="ai_cold_b2b_us", calling_window={"start": "09:00", "end": "17:00", "days": [1, 2, 3, 4, 5]})
    facts = GateFacts(registry_status=FRESH_AU)
    assert "country_not_allowed" in gate.evaluate(au_lead(), cold, facts, at(2026, 12, 1, 11, 0)).failures
    monkeypatch.setattr(settings, "SALES_AI_COLD_COUNTRIES", "US,AU")
    on = gate.evaluate(au_lead(), cold, facts, at(2026, 12, 1, 11, 0))
    assert on.allowed, on.checks                                       # no US state rules, no Twilio lookup
    mobile = gate.evaluate(au_lead("+61412345678"), cold, facts, at(2026, 12, 1, 13, 0))
    assert mobile.failures == ["line_type_not_allowed"]
    from config import Settings
    with pytest.raises(ValueError):
        Settings(SALES_AI_COLD_COUNTRIES="US,FR")


def test_human_assisted_au_needs_a_fresh_dncr_wash_even_with_consent(au_on):
    assert rules.required_registries("human_assisted", "AU") == ("au_dncr",)
    assert rules.required_registries("ai_consented", "AU") == ("au_dncr",)
    assert rules.required_registries("ai_consented", "US") == ()
    stale = gate.evaluate(au_lead(), human(), GateFacts(registry_status={"au_dncr": "missing"}), at(2026, 12, 1, 11, 0))
    assert stale.failures == ["registry_not_ready"] and not stale.permanent


async def test_dncr_washes_are_tracked_number_by_number(db):
    now = at(2026, 12, 1, 11, 0)
    other = "+61390000000"
    assert await registry.registry_status(db, ["au_dncr"], SYDNEY_LANDLINE, now) == {"au_dncr": "missing"}
    assert await registry.needs_wash(db, [SYDNEY_LANDLINE, other], "au_dncr", now) == [SYDNEY_LANDLINE, other]
    await registry.record_wash(db, "au_dncr", washed=[SYDNEY_LANDLINE], registered=[other], now=now)
    assert await registry.registry_status(db, ["au_dncr"], SYDNEY_LANDLINE, now) == {"au_dncr": "fresh"}
    assert await registry.registry_hits(db, other, ["au_dncr"]) == ["au_dncr"]
    assert await registry.registry_hits(db, SYDNEY_LANDLINE, ["au_dncr"]) == []
    later = now + timedelta(days=settings.SALES_AU_DNCR_WASH_MAX_AGE_DAYS + 1)
    assert await registry.registry_status(db, ["au_dncr"], SYDNEY_LANDLINE, later) == {"au_dncr": "stale"}
    # a number that leaves the register is un-listed by the next wash
    await registry.record_wash(db, "au_dncr", washed=[other], registered=[], now=later)
    assert await registry.registry_hits(db, other, ["au_dncr"]) == []


async def test_registry_names_are_checked(db):
    with pytest.raises(ValueError):
        await registry.replace_registry(db, "us_nationl", ["+15125550111"], area_codes=["512"])
    with pytest.raises(ValueError):
        await registry.replace_registry(db, "au_dncr", ["+61290000000"], area_codes=["all"])
    assert rules.known_list("us_state:TX") and not rules.known_list("us_state:Texas")


def test_us_state_checks_only_apply_to_us_numbers(monkeypatch):
    monkeypatch.setattr(settings, "SALES_AI_COLD_COUNTRIES", "US,AU")
    cold = campaign_doc(mode="ai_cold_b2b_us", calling_window={"start": "09:00", "end": "17:00", "days": [1, 2, 3, 4, 5]})
    d = gate.evaluate(au_lead(region_code=""), cold, GateFacts(registry_status=FRESH_AU), at(2026, 12, 1, 11, 0))
    assert "state_unknown" not in d.failures


async def test_au_lead_state_edit_and_wash_export(admin, db, au_on):
    await db[m.LEADS].insert_one(au_lead("+61412345678", _id="lead_m", region_code="", timezone=""))
    assert admin.patch("/platform/sales/leads/lead_m", json={"region_code": "Texas"}).status_code == 422
    r = admin.patch("/platform/sales/leads/lead_m", json={"region_code": "qld"})
    assert r.status_code == 200 and r.json()["lead"]["region_code"] == "QLD"
    assert r.json()["lead"]["timezones"] == ["Australia/Brisbane"]
    csv_text = admin.get("/platform/sales/dncr-wash.csv").text
    assert csv_text.splitlines()[1:] == ["0412345678"]
    assert admin.get("/platform/sales/status").json()["au_dncr_needs_wash"] == 1
