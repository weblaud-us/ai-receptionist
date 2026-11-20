"""Dental Phase 6.1: AUD/GBP dental plans, a mandatory onboarding fee, plans offered by vertical."""

import pytest

from config import settings
from core.auth import service as auth_service
from core.billing import plans, stripe_api
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    for name, value in (("STRIPE_PRICE_GROWTH_MONTHLY", "price_growth_m"),
                        ("STRIPE_PRICE_DENTAL_ESSENTIALS_MONTHLY", "price_dent_ess_m"),
                        ("STRIPE_PRICE_DENTAL_PRO_MONTHLY", "price_dent_pro_m"),
                        ("STRIPE_PRICE_DENTAL_SETUP_ESSENTIALS", "price_dent_setup_ess"),
                        ("STRIPE_PRICE_DENTAL_SETUP_PRO", "price_dent_setup_pro"),
                        ("STRIPE_PRICE_SETUP_FEE", "price_setup_generic"),
                        ("STRIPE_PRICE_OVERAGE", "price_overage")):
        monkeypatch.setattr(settings, name, value)
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


def test_dental_practices_see_only_dental_plans_and_everyone_else_none():
    assert [p.key for p in plans.plans_for("dental")] == ["dental_essentials", "dental_pro", "agency"]
    for vertical in ("salon", "clinic", "home_services", None):
        keys = [p.key for p in plans.plans_for(vertical)]
        assert "dental_essentials" not in keys and "growth" in keys
    dental = plans.PLANS["dental_essentials"]
    assert not plans.plan_allowed(dental, "salon") and plans.plan_allowed(dental, "dental")
    assert not plans.plan_allowed(plans.PLANS["growth"], "dental")


def test_dental_prices_and_onboarding_fees_are_in_aud_and_gbp():
    ess, pro = plans.PLANS["dental_essentials"], plans.PLANS["dental_pro"]
    au, uk = ess.public("AUD"), pro.public("GBP")
    assert au["currency"] == "AUD" and au["monthly"] == plans.PRICE_BOOK["AUD"]["dental_essentials"]
    assert au["setup_fee"] == plans.DENTAL_SETUP_FEES["dental_essentials"]["AUD"] and au["setup_fee_required"]
    assert uk["monthly"] == plans.PRICE_BOOK["GBP"]["dental_pro"] and uk["setup_fee"] == plans.DENTAL_SETUP_FEES["dental_pro"]["GBP"]
    assert au["verticals"] == ["dental"] and ess.included_minutes > plans.PLANS["growth"].included_minutes
    assert pro.recording and not pro.warm_transfer
    cat = plans.catalogue("AUD", "dental")
    assert [p["key"] for p in cat["plans"]] == ["dental_essentials", "dental_pro", "agency"]
    assert plans.setup_fee_price("dental_pro") == "price_dent_setup_pro"
    assert plans.setup_fee_price("growth") == "price_setup_generic"


def test_a_dental_plan_is_not_self_serve_until_its_setup_price_exists(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_123")
    assert plans.PLANS["dental_essentials"].public("AUD")["self_serve"] is True
    monkeypatch.setattr(settings, "STRIPE_PRICE_DENTAL_SETUP_ESSENTIALS", "")
    monkeypatch.setattr(settings, "STRIPE_PRICE_SETUP_FEE", "")
    assert plans.PLANS["dental_essentials"].public("AUD")["self_serve"] is False


async def test_public_plans_follow_the_business_type(client):
    dental = client.get("/public/plans", params={"country": "AU", "vertical": "dental"}).json()
    assert dental["currency"] == "AUD" and dental["plans"][0]["key"] == "dental_essentials"
    salon = client.get("/public/plans", params={"country": "GB", "vertical": "salon"}).json()
    assert all(not p["verticals"] for p in salon["plans"])
    assert client.get("/public/plans", params={"vertical": "nonsense"}).json()["plans"][0]["key"] == "starter"


async def test_signup_checks_the_plan_fits_the_business(db, client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ENABLED", True)
    body = {"email": "dr@smiles.au", "password": PASSWORD, "business_name": "Harbour Smiles", "vertical": "dental",
            "country": "AU", "timezone": "Australia/Sydney", "plan": "growth", "accept_terms": True}
    r = client.post("/signup", json=body)
    assert r.status_code == 422 and "dental_essentials" in r.text
    r = client.post("/signup", json={**body, "plan": "dental_essentials"})
    assert r.status_code == 200, r.text
    salon = {**body, "email": "x@salon.co.uk", "business_name": "Salon", "vertical": "salon", "country": "GB",
             "plan": "dental_pro"}
    assert client.post("/signup", json=salon).status_code == 422


async def test_checkout_always_adds_the_dental_onboarding_fee(db, client, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_123")
    await create_tenant(name="Harbour Smiles", vertical="dental", tenant_id="ten_bill", status="incomplete",
                        country="AU", currency="AUD", seed_template=False,
                        extra={"billing": {"plan": "dental_pro"}})
    await auth_service.create_user(email="dr@smiles.au", password=PASSWORD, tenant_id="ten_bill", role="owner")
    tok = client.post("/auth/login", json={"email": "dr@smiles.au", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    seen = {}

    async def customer(**kw):
        return {"id": "cus_d"}

    async def session(**kw):
        seen.update(kw)
        return {"id": "cs_d", "url": "https://checkout.stripe.com/c/cs_d"}

    monkeypatch.setattr(stripe_api, "create_customer", customer)
    monkeypatch.setattr(stripe_api, "create_checkout_session", session)
    r = client.post("/t/billing/checkout", json={"plan": "dental_pro"}, headers=h)    # no setup_fee flag sent
    assert r.status_code == 200, r.text
    assert seen["price"] == "price_dent_pro_m" and seen["setup_fee_price"] == "price_dent_setup_pro"
    assert seen["currency"] == "AUD" and seen["overage_price"] == "price_overage"
    assert client.post("/t/billing/checkout", json={"plan": "growth"}, headers=h).status_code == 422
    summary = client.get("/t/billing", headers=h).json()
    assert [p["key"] for p in summary["catalogue"]["plans"]][:2] == ["dental_essentials", "dental_pro"]
