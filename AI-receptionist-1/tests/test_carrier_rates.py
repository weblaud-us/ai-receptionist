"""Call costs per number (core/billing/carrier_rates.py): Twilio trunking prices, cache, fallback, and where
the estimate shows before a call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from config import settings
from core.billing import carrier_rates as cr
from core.billing import costs
from core.outbound import preflight

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)

AU = {
    "country": "Australia", "iso_country": "AU", "price_unit": "USD",
    "terminating_prefix_prices": [
        {"origination_prefixes": ["ALL"], "destination_prefixes": ["61"], "base_price": None,
         "current_price": "0.0212", "friendly_name": "Trunking Outbound Minute - Australia"},
        {"origination_prefixes": ["ALL"], "destination_prefixes": ["614", "6145"], "base_price": None,
         "current_price": "0.0710", "friendly_name": "Trunking Outbound Minute - Australia - Mobile"},
        # A cheaper mobile rate only from an Australian caller ID: wins over ALL at the same prefix length.
        {"origination_prefixes": ["61"], "destination_prefixes": ["614"], "base_price": None,
         "current_price": "0.0500", "friendly_name": "Trunking Outbound Minute - Australia - Mobile (domestic)"},
        {"origination_prefixes": ["ALL"], "destination_prefixes": ["6119"], "base_price": None,
         "current_price": "0.9000", "friendly_name": "Trunking Outbound Minute - Australia - Premium"},
    ],
    "originating_call_prices": [
        {"number_type": "local", "base_price": None, "current_price": "0.006"},
        {"number_type": "mobile", "base_price": None, "current_price": "0.006"},
        {"number_type": "toll free", "base_price": None, "current_price": "0.046"},
    ],
}


@pytest.fixture
def twilio(monkeypatch):
    """Twilio configured, answering from `pages` (ISO → JSON body or HTTP status). Records the countries asked."""
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    state = {"pages": {"AU": AU}, "asked": []}

    def handler(request: httpx.Request) -> httpx.Response:
        iso = request.url.path.rsplit("/", 1)[-1]
        state["asked"].append(iso)
        page = state["pages"].get(iso, 404)
        return httpx.Response(page) if isinstance(page, int) else httpx.Response(200, json=page)

    monkeypatch.setattr(cr, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return state


def test_parse_keeps_prices_and_refuses_other_currencies():
    doc = cr.parse_country(AU, "AU")
    assert doc["originating"] == {"local": 0.006, "mobile": 0.006, "toll_free": 0.046}
    assert len(doc["terminating"]) == 4
    assert cr.parse_country({**AU, "price_unit": "EUR"}, "AU") is None
    assert cr.parse_country({"price_unit": "usd"}, "AU") is None              # nothing usable


def test_longest_prefix_and_origin_specific_rows_win():
    doc = cr.parse_country(AU, "AU")
    assert cr.match_termination(doc, "61298765432")["usd"] == 0.0212           # landline: "61"
    assert cr.match_termination(doc, "61412345678")["usd"] == 0.071            # mobile from abroad: "614", ALL
    assert cr.match_termination(doc, "61412345678", "61290000000")["usd"] == 0.05   # AU caller ID: origin row
    assert cr.match_termination(doc, "61451234567", "61290000000")["usd"] == 0.071  # "6145" is longer still
    assert cr.match_termination(doc, "4420", "") is None


async def test_estimate_uses_twilio_and_caches_per_country(db, twilio):
    mobile = await cr.estimate(db, "+61412345678", direction="outbound", now=NOW)
    landline = await cr.estimate(db, "+61298765432", direction="outbound", now=NOW)
    assert (mobile["source"], mobile["carrier_usd_per_min"], mobile["line_kind"]) == ("twilio", 0.071, "mobile")
    assert landline["carrier_usd_per_min"] == 0.0212
    assert mobile["per_minute"]["mid"] == costs.per_minute(outbound=True, carrier=0.071)["mid"]
    assert mobile["typical_call_usd"] == round(mobile["per_minute"]["mid"] * settings.SALES_TYPICAL_CALL_MINUTES, 2)
    assert twilio["asked"] == ["AU"]                                            # one fetch for both numbers
    inbound = await cr.estimate(db, "+61298765432", direction="inbound", now=NOW)
    assert (inbound["carrier_usd_per_min"], inbound["source"]) == (0.006, "twilio")
    assert twilio["asked"] == ["AU"]


async def test_stale_cache_is_refreshed_and_kept_when_twilio_fails(db, twilio):
    await cr.estimate(db, "+61412345678", direction="outbound", now=NOW)
    later = NOW + timedelta(days=settings.CARRIER_RATES_MAX_AGE_DAYS + 1)
    twilio["pages"]["AU"] = 503
    est = await cr.estimate(db, "+61412345678", direction="outbound", now=later)
    assert (est["source"], est["carrier_usd_per_min"]) == ("twilio", 0.071)     # the stale copy still answers
    assert twilio["asked"] == ["AU", "AU"]
    await cr.estimate(db, "+61412345678", direction="outbound", now=later + timedelta(minutes=5))
    assert twilio["asked"] == ["AU", "AU"]                                      # no retry within the hour


async def test_fallback_without_twilio(db):
    au = await cr.estimate(db, "+61412345678", direction="outbound", now=NOW)
    assert (au["source"], au["carrier_usd_per_min"]) == ("estimate", costs.FALLBACK_CARRIER["AU"]["out"]["mobile"])
    bd = await cr.estimate(db, "+8801712345678", direction="outbound", now=NOW)
    assert (bd["source"], bd["carrier_usd_per_min"]) == ("estimate", costs.FALLBACK_DEFAULT["out"]["mobile"])
    assert bd["warn"] is True                                                    # an unknown country looks expensive
    gb = await cr.estimate(db, "+447911123456", direction="outbound", now=NOW)   # Guernsey range: the UK's list
    assert gb["carrier_usd_per_min"] == costs.FALLBACK_CARRIER["GB"]["out"]["mobile"]


async def test_browser_calls_pay_no_carrier(db, twilio):
    est = await cr.estimate(db, "+61412345678", direction="inbound", channel="browser", now=NOW)
    assert est["carrier_usd_per_min"] == 0 and est["source"] == "none"
    assert est["per_minute"] == costs.per_minute(channel="browser")
    assert twilio["asked"] == []


async def test_for_call_prices_inbound_on_our_number_and_outbound_on_theirs(db, twilio):
    inbound = await cr.for_call(db, direction="inbound", channel="phone", caller_phone="+447911123456",
                                dialed_number="+61298765432")
    outbound = await cr.for_call(db, direction="outbound", channel="phone", caller_phone="+61412345678",
                                 dialed_number="+61290000000")
    assert inbound["carrier_usd_per_min"] == 0.006
    assert outbound["carrier_usd_per_min"] == 0.05                              # the AU caller ID's rate


def test_cost_model_outbound_replaces_the_carrier_line():
    inbound, outbound = costs.per_minute(), costs.per_minute(outbound=True)
    carrier = next(i for i in costs.COST_ITEMS if i.key == "carrier_inbound")
    us_out = costs.FALLBACK_CARRIER["US"]["out"]["landline"]
    assert outbound["mid"] == pytest.approx(inbound["mid"] - carrier.mid + us_out, abs=1e-4)
    assert not any(i.key == "carrier_outbound" for i in costs.COST_ITEMS)       # never both
    assert costs.call_cost_usd(60, outbound=True, carrier=0.5) == costs.per_minute(carrier=0.5)["mid"]


async def test_summary_groups_and_warns(db, twilio):
    phones_list = ["+61298765432", "+61398765432", "+61412345678", "+61191234567"]
    s = await cr.summary(db, phones_list, from_numbers=["+61290000000"], now=NOW)
    kinds = {(g["country"], g["line_kind"]): g for g in s["groups"]}
    assert kinds[("AU", "landline")]["count"] == 2
    assert kinds[("AU", "mobile")]["carrier_usd_per_min"] == 0.05
    assert s["low"] < s["high"] and s["numbers"] == 4


async def test_preflight_cost_check(db, twilio):
    check = await preflight.cost_check(db, ["+61298765432", "+61412345678"], ["+61290000000"], now=NOW)
    assert check["id"] == "call_cost" and check["status"] == "pass"
    assert "AU landline 1" in check["detail"] and "AU mobile 1" in check["detail"]
    twilio["pages"]["AU"] = {**AU, "terminating_prefix_prices": [
        {**AU["terminating_prefix_prices"][0], "current_price": "0.40"}]}
    await db[cr.COLLECTION].delete_many({})
    pricey = await preflight.cost_check(db, ["+61298765432"], [], now=NOW)
    assert pricey["status"] == "warn" and "⚠" in pricey["detail"]


async def test_rate_endpoint(monkeypatch, db, twilio):
    from routers import billing

    out = await billing.carrier_rate(number="+61412345678", admin=None)
    assert out["outbound"]["carrier_usd_per_min"] == 0.071 and out["inbound"]["carrier_usd_per_min"] == 0.006
    with pytest.raises(Exception):
        await billing.carrier_rate(number="12", admin=None)
