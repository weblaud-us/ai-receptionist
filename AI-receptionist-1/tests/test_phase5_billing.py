"""
Phase 5 — billing and self-serve onboarding. No network: Stripe and Twilio calls are replaced.

Acceptance items covered here (ROADMAP §10):
- failed payment → past_due → suspended → calls forwarded, never silence (the call gate);
- overage is reported to Stripe exactly once (the "next invoice" itself is checked with a Stripe
  test clock — docs/PHASE_5_TESTING.md);
- cancel → export works → data deleted after retention;
- signup → checkout → number, unassisted (with the development number provider).
"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from config import settings
from core.auth import service as auth_service
from core.billing import costs, jobs as billing_jobs, lifecycle, metering, plans, policy, stripe_api, usage
from core.repositories import Repos
from core.services import numbers, offboarding
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig

PASSWORD = "correct horse battery"
SECRET = "whsec_test_secret"
NOW = datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "STRIPE_PRICE_STARTER_MONTHLY", "price_starter_m")
    monkeypatch.setattr(settings, "STRIPE_PRICE_GROWTH_MONTHLY", "price_growth_m")
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO_MONTHLY", "price_pro_m")
    monkeypatch.setattr(settings, "STRIPE_PRICE_OVERAGE", "price_overage")
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


@pytest.fixture
def stripe_on(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_123")


def _h(token, tenant=None):
    h = {"Authorization": f"Bearer {token}"}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


async def _login(client, email):
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _tenant(db, tid="ten_S", status="active", **billing):
    extra = {"billing": {"plan": "growth", "stripe_customer_id": "cus_1", "stripe_subscription_id": "sub_1",
                         **billing}, "limits": plans.limits_for(plans.PLANS["growth"]),
             "notifications": {"owner_email": "owner@salon.com"}}
    return await create_tenant(name="Bella Salon", vertical="salon", timezone="America/Chicago", tenant_id=tid,
                               status=status, extra=extra)


async def _reload(db, tid="ten_S") -> TenantConfig:
    return TenantConfig.from_doc(await db.tenants.find_one({"_id": tid}))


def _event(kind, obj, eid="evt_1"):
    return {"id": eid, "type": kind, "livemode": False, "data": {"object": obj}}


def _post_event(client, event, secret=SECRET):
    body = json.dumps(event).encode()
    return client.post("/billing/stripe/webhook", content=body,
                       headers={"Stripe-Signature": stripe_api.sign_for_tests(body, secret),
                                "Content-Type": "application/json"})


def _sub(status="active", price="price_growth_m", sid="sub_1", **kw):
    start = int(NOW.timestamp()) - 86400
    return {"id": sid, "object": "subscription", "status": status, "customer": "cus_1",
            "metadata": {"tenant_id": "ten_S"}, "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": price}, "current_period_start": start,
                                "current_period_end": start + 30 * 86400},
                               {"price": {"id": "price_overage"}}]}, **kw}


async def _usage(db, tid, seconds, at=None, billable=True):
    await Repos(db, tid).usage.insert_one({"call_id": f"c{seconds}{at}", "kind": "voice_minute", "seconds": seconds,
                                           "quantity": -(-seconds // 60), "billable": billable,
                                           "at": at or datetime.now(timezone.utc)})


# ── Plans and costs (Tasks 5.1, 5.2) ─────────────────────────────────────────

def test_cost_model_and_margins_match_the_roadmap():
    cpm = costs.per_minute()
    assert 0.040 <= cpm["mid"] <= 0.050
    assert costs.per_minute(outbound=True)["mid"] > cpm["mid"]
    assert not any(i.key == "recording" for i in costs.COST_ITEMS)      # calls are never recorded
    rows = costs.model()["plans"]
    margins = {p["key"]: p["margin_pct"] for p in rows if not plans.PLANS[p["key"]].verticals}
    assert all(82 <= m <= 88 for m in margins.values()), margins
    # Vertical plans (dental retainers) are priced as premium plans, costed on their market (AU).
    dental = {p["key"]: p for p in rows if plans.PLANS[p["key"]].verticals}
    assert set(dental) == {"dental_essentials", "dental_pro"}
    assert all(p["cost_country"] == "AU" and p["margin_pct"] >= 85 for p in dental.values()), dental
    assert costs.per_minute(country="AU")["mid"] > cpm["mid"]
    assert plans.PLANS["growth"].annual_usd == 149 * 10
    assert costs.call_cost_usd(120) == pytest.approx(2 * cpm["mid"], abs=1e-4)


def test_price_mapping_both_ways():
    assert plans.plan_for_price("price_pro_m") == ("pro", "month")
    assert plans.plan_for_price("price_overage") is None
    assert plans.limits_for(plans.PLANS["starter"])["included_minutes"] == 150


# ── Stripe client ───────────────────────────────────────────────────────────

def test_stripe_form_encoding_of_nested_params():
    pairs = stripe_api.encode({"line_items": [{"price": "p", "quantity": 1}], "a": {"b": True}, "skip": None})
    assert ("line_items[0][price]", "p") in pairs and ("line_items[0][quantity]", "1") in pairs
    assert ("a[b]", "true") in pairs and not any(k == "skip" for k, _ in pairs)


def test_webhook_signature_good_bad_and_replayed():
    body = json.dumps(_event("invoice.paid", {})).encode()
    assert stripe_api.verify_webhook(body, stripe_api.sign_for_tests(body, SECRET), SECRET)["id"] == "evt_1"
    with pytest.raises(stripe_api.SignatureError):
        stripe_api.verify_webhook(body, stripe_api.sign_for_tests(body, "whsec_other"), SECRET)
    with pytest.raises(stripe_api.SignatureError):
        stripe_api.verify_webhook(body + b" ", stripe_api.sign_for_tests(body, SECRET), SECRET)
    old = stripe_api.sign_for_tests(body, SECRET, timestamp=1_000_000)
    with pytest.raises(stripe_api.SignatureError):
        stripe_api.verify_webhook(body, old, SECRET)


async def test_webhook_endpoint_rejects_unsigned(db, client):
    r = client.post("/billing/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=nope"})
    assert r.status_code == 400


def test_subscription_period_read_from_either_api_shape():
    old = {"current_period_start": 1_700_000_000, "current_period_end": 1_702_592_000}
    new = {"items": {"data": [{"current_period_start": 1_700_000_000, "current_period_end": 1_702_592_000}]}}
    assert stripe_api.subscription_period(old) == stripe_api.subscription_period(new)


# ── Lifecycle (webhook → tenant) ────────────────────────────────────────────

async def test_checkout_completed_activates_trial_and_syncs_plan(db, client, stripe_on, monkeypatch):
    await create_tenant(name="New Biz", tenant_id="ten_S", status="incomplete",
                        extra={"billing": {"plan": "starter"}, "notifications": {"owner_email": "o@x.com"}})

    async def retrieve(sid):
        return _sub(status="trialing", price="price_pro_m", trial_end=int(NOW.timestamp()) + 14 * 86400)
    monkeypatch.setattr(stripe_api, "retrieve_subscription", retrieve)
    r = _post_event(client, _event("checkout.session.completed", {
        "mode": "subscription", "client_reference_id": "ten_S", "customer": "cus_1", "subscription": "sub_1"}))
    assert r.status_code == 200, r.text
    t = await _reload(db)
    assert t.status == "trial" and t.billing.plan == "pro" and t.billing.trial_used
    assert t.billing.stripe_customer_id == "cus_1" and t.limits.included_minutes == 1000
    assert t.onboarding.get("checkout_completed_at")


async def test_events_are_idempotent_by_id(db, client):
    await _tenant(db)
    sub = _sub(status="past_due")
    assert _post_event(client, _event("customer.subscription.updated", sub, "evt_A")).json()["status"] == "ok"
    first = (await _reload(db)).billing.past_due_since
    r = _post_event(client, _event("customer.subscription.updated", sub, "evt_A"))
    assert r.json()["status"] == "duplicate"
    assert (await _reload(db)).billing.past_due_since == first
    assert await db.stripe_events.count_documents({}) == 1


async def test_failed_handler_releases_its_claim_so_stripe_can_retry(db, client, monkeypatch):
    await _tenant(db)

    async def boom(db_, obj):
        raise RuntimeError("database hiccup")
    monkeypatch.setitem(lifecycle.HANDLERS, "invoice.paid", boom)
    with pytest.raises(RuntimeError):
        await lifecycle.handle_event(db, _event("invoice.paid", {"customer": "cus_1"}, "evt_R"))
    assert await db.stripe_events.find_one({"_id": "evt_R"}) is None


async def test_failed_payment_past_due_suspended_then_paid(db, client):
    """Acceptance: failed payment → past_due → suspended → calls forwarded, never silence."""
    await _tenant(db)
    assert _post_event(client, _event("invoice.payment_failed", {"customer": "cus_1", "amount_due": 14900},
                                      "evt_F")).status_code == 200
    t = await _reload(db)
    assert t.status == "past_due" and t.billing.past_due_since
    assert usage.gate(t) == "ok"                           # still answering while Stripe retries

    later = usage.aware(t.billing.past_due_since) + timedelta(days=settings.PAST_DUE_SUSPEND_DAYS, hours=1)
    stats = await billing_jobs.run_once(db, now=later)
    assert stats["suspended"] == 1
    t = await _reload(db)
    assert t.status == "suspended" and usage.gate(t) == "suspended"

    # Stripe still says past_due → our suspension holds; a payment lifts it.
    _post_event(client, _event("customer.subscription.updated", _sub(status="past_due"), "evt_U"))
    assert (await _reload(db)).status == "suspended"
    _post_event(client, _event("invoice.paid", {"customer": "cus_1", "amount_paid": 14900,
                                                "subscription": "sub_1"}, "evt_P"))
    t = await _reload(db)
    assert t.status == "active" and t.billing.past_due_since is None and usage.gate(t) == "ok"


async def test_zero_dollar_trial_invoice_keeps_the_trial(db, client):
    await _tenant(db, status="trial")
    _post_event(client, _event("invoice.paid", {"customer": "cus_1", "amount_paid": 0}))
    assert (await _reload(db)).status == "trial"


async def test_subscription_from_another_id_is_ignored(db, client):
    await _tenant(db)
    r = _post_event(client, _event("customer.subscription.updated", _sub(status="canceled", sid="sub_old")))
    assert r.json()["status"] == "ignored" and (await _reload(db)).status == "active"


# ── Call gate ───────────────────────────────────────────────────────────────

async def test_trial_is_hard_capped_and_hard_cap_is_optional(db):
    t = await _tenant(db, status="trial", trial_ends_at=datetime.now(timezone.utc) + timedelta(days=10))
    assert usage.included_minutes(t) == settings.TRIAL_MINUTES
    assert usage.gate(t, seconds=settings.TRIAL_MINUTES * 60 - 1) == "ok"
    assert usage.gate(t, seconds=settings.TRIAL_MINUTES * 60) == "cap"

    active = await _tenant(db, tid="ten_T")
    over = active.limits.included_minutes * 60 + 600
    assert usage.gate(active, seconds=over) == "ok"          # default: keep answering, bill overage
    capped = active.model_copy(update={"billing": active.billing.model_copy(update={"hard_cap": True})})
    assert usage.gate(capped, seconds=over) == "cap"


async def test_manual_and_legacy_tenants_are_never_capped(db):
    t = await create_tenant(name="Hand Made", tenant_id="ten_M", status="trial")
    assert t.billing.plan == "manual" and usage.gate(t, seconds=10**6) == "ok"


async def test_profile_for_suspended_tenant_reaches_the_worker(db):
    from agent.profile import resolve_profile
    from core.repositories.repos import PhoneNumbersStore

    await _tenant(db, status="suspended")
    await db.tenants.update_one({"_id": "ten_S"}, {"$set": {"status": "suspended"}})
    await PhoneNumbersStore(db).assign({"e164": "+15125550100", "tenant_id": "ten_S", "purpose": "inbound"})
    invalidate()
    profile = await resolve_profile(dialed_number="+15125550100")
    assert profile is not None and usage.gate(profile.tenant) == "suspended"
    await db.tenants.update_one({"_id": "ten_S"}, {"$set": {"status": "incomplete"}})
    invalidate()
    assert await resolve_profile(dialed_number="+15125550100") is None


# ── Usage, alerts, metering ─────────────────────────────────────────────────

async def test_usage_alerts_go_out_once_per_threshold(db, monkeypatch):
    sent = []

    async def fake_send(to, subject, text, html=None):
        sent.append(subject)
        return True
    monkeypatch.setattr(lifecycle, "send_email", fake_send)
    t = await _tenant(db)
    included = t.limits.included_minutes
    await _usage(db, t.id, int(included * 60 * 0.85))
    await billing_jobs.run_once(db)
    await billing_jobs.run_once(db)
    assert sent == ["You've used 80% of your included minutes"]
    await _usage(db, t.id, int(included * 60 * 0.2))
    await billing_jobs.run_once(db)
    assert sent[-1] == "You've used all your included minutes" and len(sent) == 2


async def test_overage_reported_exactly_once_even_when_a_send_fails(db, stripe_on, monkeypatch):
    calls = []
    fail = {"once": True}

    async def meter(customer, value, *, identifier, timestamp=None):
        calls.append((identifier, value))
        if fail.pop("once", False):
            raise stripe_api.StripeError("timeout", status=502)
        return {}
    monkeypatch.setattr(stripe_api, "send_meter_event", meter)
    t = await _tenant(db)
    start, _ = usage.period_window(t)
    await _usage(db, t.id, t.limits.included_minutes * 60 + 61, at=start + timedelta(hours=1))   # 2 min over
    await _usage(db, t.id, 600, at=start + timedelta(hours=2), billable=False)                    # not counted

    with pytest.raises(stripe_api.StripeError):
        await metering.report_overage(db, await _reload(db), now=start + timedelta(days=1))
    assert (await _reload(db)).billing.overage_pending["delta"] == 2
    # Retry re-sends the *same* report (same identifier), then marks it reported.
    assert await metering.report_overage(db, await _reload(db), now=start + timedelta(days=1)) == 2
    assert calls[0] == calls[1] and calls[0][1] == 2
    assert await metering.report_overage(db, await _reload(db), now=start + timedelta(days=2)) == 0
    # More usage → only the difference, under a new identifier.
    await _usage(db, t.id, 180, at=start + timedelta(days=2))
    assert await metering.report_overage(db, await _reload(db), now=start + timedelta(days=3)) == 3
    assert calls[-1][0].endswith(":5") and len({c[0] for c in calls}) == 2


async def test_trial_minutes_are_never_billed(db, stripe_on, monkeypatch):
    async def meter(*a, **k):
        raise AssertionError("must not report trial usage")
    monkeypatch.setattr(stripe_api, "send_meter_event", meter)
    t = await _tenant(db, status="trial")
    await _usage(db, t.id, 10_000)
    assert await metering.report_overage(db, await _reload(db)) == 0


# ── Plan rules (abuse prevention) ───────────────────────────────────────────

async def test_transfer_rules(db, client):
    t = await _tenant(db)
    with pytest.raises(policy.PolicyError, match="premium"):
        policy.check_transfer_number(t, "+19005551234")
    with pytest.raises(policy.PolicyError, match="another country"):
        policy.check_transfer_number(t, "+442071234567")
    policy.check_transfer_number(t, "+14165551234")          # Canada from a US business: same zone
    trial = await _tenant(db, tid="ten_T", status="trial")
    with pytest.raises(policy.PolicyError, match="own number"):
        policy.check_transfer_number(trial, "+15125550123")
    ok = trial.model_copy(update={"billing": trial.billing.model_copy(update={"verified_phone": "+15125550123"})})
    policy.check_transfer_number(ok, "+15125550123")


@pytest.mark.parametrize("number", ["+449098790000", "+448712345678", "+44118500", "+447012345678",   # UK
                                    "+611900123456", "+35315201234", "+35650001234",                   # AU, IE, MT
                                    "+4990012345678", "+33891234567", "+881234567890", "+979123456789"])
async def test_premium_rate_and_satellite_transfers_are_refused_in_launch_markets(db, number, monkeypatch):
    from config import Settings
    # The shipped default, whatever a developer's local .env says.
    monkeypatch.setattr(settings, "TRANSFER_BLOCKED_PREFIXES", Settings.model_fields["TRANSFER_BLOCKED_PREFIXES"].default)
    t = await _tenant(db)
    with pytest.raises(policy.PolicyError, match="premium"):
        policy.check_transfer_number(t, number)


def test_plans_are_priced_in_the_business_currency():
    growth = plans.PLANS["growth"]
    assert plans.billing_currency("gbp") == "GBP" and plans.billing_currency("CAD") == "USD"   # not sold in CAD yet
    assert growth.public("GBP")["monthly"] == plans.PRICE_BOOK["GBP"]["growth"]
    assert growth.public("AUD")["annual"] == plans.PRICE_BOOK["AUD"]["growth"] * 10
    assert growth.public("XYZ")["currency"] == "USD" and growth.public("XYZ")["monthly"] == growth.monthly_usd
    cat = plans.catalogue("EUR")
    assert cat["currency"] == "EUR" and cat["overage_per_min"] == plans.OVERAGE_PER_MIN["EUR"]
    assert all(p["currency"] == "EUR" for p in cat["plans"])
    for cur in plans.SUPPORTED_CURRENCIES:            # every currency has every self-serve plan
        assert set(plans.PRICE_BOOK[cur]) == set(plans.SELF_SERVE_PLANS)
        assert cur in plans.OVERAGE_PER_MIN and cur in plans.SETUP_FEE and cur in plans.USD_PER_UNIT
    # Margin reports stay in USD
    assert growth.monthly_revenue_usd("GBP") == round(plans.PRICE_BOOK["GBP"]["growth"] * plans.USD_PER_UNIT["GBP"], 2)


def test_no_plan_sells_warm_transfer_until_it_is_proven():
    """Warm transfer is built (agent/transfer.py) but not yet tested on a live trunk: no plan may promise it."""
    for p in plans.PLANS.values():
        assert not p.warm_transfer
        assert not any("warm" in f.lower() for f in p.features) and "warm" not in p.tagline.lower()


async def test_public_plans_follow_the_visitors_country(client):
    assert client.get("/public/plans", params={"country": "AU"}).json()["currency"] == "AUD"
    assert client.get("/public/plans", params={"country": "IE"}).json()["currency"] == "EUR"
    assert client.get("/public/plans", params={"currency": "GBP"}).json()["plans"][0]["currency"] == "GBP"
    assert client.get("/public/plans").json()["currency"] == "USD"


async def test_settings_patch_enforces_plan_and_phone_verification_flow(db, client):
    await _tenant(db, status="trial", plan="starter")
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    r = client.patch("/t/settings", json={"transfer": {"number": "+15125550123"}}, headers=_h(tok))
    assert r.status_code == 422 and "own number" in r.json()["detail"]

    r = client.post("/t/billing/verify-phone", json={"phone": "+15125550123"}, headers=_h(tok))
    code = r.json()["dev_code"]                             # development: no SMS provider
    assert client.post("/t/billing/verify-phone/confirm", json={"code": "000000" if code != "000000" else "111111"},
                       headers=_h(tok)).status_code == 400
    assert client.post("/t/billing/verify-phone/confirm", json={"code": code}, headers=_h(tok)).status_code == 200
    invalidate()
    r = client.patch("/t/settings", json={"transfer": {"number": "+15125550123"}}, headers=_h(tok))
    assert r.status_code == 200, r.text


async def test_invites_respect_the_plan_user_limit(db, client):
    await _tenant(db, plan="starter")
    await db.tenants.update_one({"_id": "ten_S"}, {"$set": {"limits.max_users": 1}})
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    r = client.post("/t/team/invite", json={"email": "two@salon.com", "role": "staff"}, headers=_h(tok))
    assert r.status_code == 402


# ── Signup, checkout ────────────────────────────────────────────────────────

async def test_signup_without_stripe_starts_a_local_trial(db, client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ENABLED", True)
    body = {"email": "New@Biz.com", "password": PASSWORD, "business_name": "New Biz", "vertical": "salon",
            "country": "GB", "timezone": "Europe/London", "plan": "growth", "accept_terms": True}
    r = client.post("/signup", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["next"] == "onboarding" and out["access_token"] and out["user"]["email_verified"] is False
    t = await _reload(db, out["tenant_id"])
    assert t.status == "trial" and t.billing.plan == "growth" and t.region == "eu" and t.currency == "GBP"
    assert t.limits.included_minutes == 400
    assert client.post("/signup", json=body).status_code == 409
    assert client.post("/signup", json={**body, "email": "x@y.com", "accept_terms": False}).status_code == 422

    token = await auth_service.start_email_verification(out["user"]["id"])
    assert client.post("/signup/verify-email", json={"token": token}).status_code == 200
    me = client.get("/auth/me", headers=_h(out["access_token"])).json()
    assert me["user"]["email_verified"] is True


async def test_signup_closed_by_default(db, client):
    assert client.post("/signup", json={}).status_code in (404, 422)
    assert client.get("/public/plans").json()["signup_enabled"] is False


async def test_checkout_creates_customer_once_and_offers_the_trial(db, client, stripe_on, monkeypatch):
    await create_tenant(name="New Biz", tenant_id="ten_S", status="incomplete", extra={"billing": {"plan": "growth"}})
    await auth_service.create_user(email="o@biz.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "o@biz.com")
    seen = {}

    async def customer(**kw):
        seen["customer"] = kw
        return {"id": "cus_9"}

    async def session(**kw):
        seen["session"] = kw
        return {"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}
    monkeypatch.setattr(stripe_api, "create_customer", customer)
    monkeypatch.setattr(stripe_api, "create_checkout_session", session)
    r = client.post("/t/billing/checkout", json={"plan": "growth"}, headers=_h(tok))
    assert r.status_code == 200, r.text and r.json()["url"].startswith("https://checkout.stripe.com")
    assert seen["session"]["trial_days"] == settings.TRIAL_DAYS and seen["session"]["price"] == "price_growth_m"
    assert seen["session"]["overage_price"] == "price_overage"
    assert seen["session"]["currency"] == "USD"
    assert (await _reload(db)).billing.stripe_customer_id == "cus_9"


async def test_checkout_charges_a_uk_business_in_pounds(db, client, stripe_on, monkeypatch):
    await create_tenant(name="London Biz", tenant_id="ten_S", status="incomplete", country="GB", currency="GBP",
                        timezone="Europe/London", extra={"billing": {"plan": "growth"}})
    await auth_service.create_user(email="o@biz.co.uk", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "o@biz.co.uk")
    seen = {}

    async def customer(**kw):
        return {"id": "cus_uk"}

    async def session(**kw):
        seen.update(kw)
        return {"id": "cs_uk", "url": "https://checkout.stripe.com/c/cs_uk"}
    monkeypatch.setattr(stripe_api, "create_customer", customer)
    monkeypatch.setattr(stripe_api, "create_checkout_session", session)
    assert client.post("/t/billing/checkout", json={"plan": "growth"}, headers=_h(tok)).status_code == 200
    assert seen["currency"] == "GBP"
    summary = client.get("/t/billing", headers=_h(tok)).json()
    assert summary["currency"] == "GBP" and summary["catalogue"]["plans"][1]["monthly"] == plans.PRICE_BOOK["GBP"]["growth"]


async def test_checkout_session_sends_the_currency_to_stripe_only_when_not_usd(monkeypatch):
    sent = {}

    async def fake_request(method, path, params=None, **kw):
        sent.update(params or {})
        return {"id": "cs"}
    monkeypatch.setattr(stripe_api, "request", fake_request)
    await stripe_api.create_checkout_session(customer_id="c", tenant_id="t", price="p", success_url="s", cancel_url="c")
    assert "currency" not in sent
    await stripe_api.create_checkout_session(customer_id="c", tenant_id="t", price="p", success_url="s", cancel_url="c",
                                             currency="AUD")
    assert sent["currency"] == "aud"


async def test_second_subscription_is_refused(db, client, stripe_on):
    await _tenant(db)
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    assert client.post("/t/billing/checkout", json={"plan": "pro"}, headers=_h(tok)).status_code == 409


# ── Numbers ─────────────────────────────────────────────────────────────────

async def test_number_search_and_order_in_development(db, client, monkeypatch):
    """The wizard's 'get a number' step, end to end with the development provider."""
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "softphone")
    await _tenant(db)
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    await db.users.update_one({"email": "owner@salon.com"}, {"$set": {"email_verified_at": datetime.now(timezone.utc)}})
    tok = await _login(client, "owner@salon.com")
    found = client.get("/t/numbers/search?country=US&area_code=512", headers=_h(tok)).json()
    pick = found["numbers"][0]["e164"]
    order = (await numbers.start_order(db, await _reload(db), pick, requested_by="u"))
    done = await numbers.run_order(db, order["_id"])
    assert done["status"] == "active" and all(done["steps"].get(s) for s in numbers.STEPS)
    row = await db.phone_numbers.find_one({"e164": pick})
    assert row["tenant_id"] == "ten_S" and row["status"] == "active"
    assert (await numbers.run_order(db, order["_id"]))["status"] == "active"        # idempotent
    # Plan allows one number.
    with pytest.raises(numbers.ProvisioningError, match="includes 1"):
        await numbers.start_order(db, await _reload(db), "+15555550000", requested_by="u")


async def test_number_order_needs_a_verified_email(db, client, monkeypatch):
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "softphone")
    await _tenant(db)
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    assert client.post("/t/numbers", json={"e164": "+15555551234"}, headers=_h(tok)).status_code == 403


async def test_twilio_order_resumes_from_the_failed_step(db, monkeypatch):
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "NUMBER_VERIFY_CALL", False)
    calls = {"buy": 0, "trunk": 0, "lk": 0}

    async def buy(number, tid, **regulatory):
        calls["buy"] += 1
        return "PN1"

    async def trunk(sid):
        calls["trunk"] += 1
        if calls["trunk"] == 1:
            raise numbers.ProvisioningError("trunk down", status=502)

    async def lk(**kw):
        calls["lk"] += 1
    monkeypatch.setattr(numbers, "_purchase", buy)
    monkeypatch.setattr(numbers, "_attach_trunk", trunk)
    monkeypatch.setattr(numbers, "_livekit_numbers", lk)
    t = await _tenant(db)
    order = await numbers.start_order(db, t, "+15125550177", requested_by="u")
    with pytest.raises(numbers.ProvisioningError):
        await numbers.run_order(db, order["_id"])
    assert (await db.number_orders.find_one({"_id": order["_id"]}))["status"] == "failed"
    done = await numbers.run_order(db, order["_id"])
    assert done["status"] == "active" and calls == {"buy": 1, "trunk": 2, "lk": 1}


async def test_non_instant_country_waits_for_documents(db, monkeypatch):
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "NUMBER_VERIFY_CALL", False)
    monkeypatch.setattr(settings, "SUPPORT_EMAIL", "ops@example.com")
    alerts, bought = [], {}

    async def fake_email(to, subject, body, **kw):
        alerts.append((to, subject))
        return True

    async def buy(number, tid, **regulatory):
        bought.update(number=number, **regulatory)
        return "PN_GB"

    async def noop(*a, **k):
        return None
    import core.integrations.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_email)
    monkeypatch.setattr(numbers, "_purchase", buy)
    monkeypatch.setattr(numbers, "_attach_trunk", noop)
    monkeypatch.setattr(numbers, "_livekit_numbers", noop)
    t = await _tenant(db)
    order = await numbers.start_order(db, t, "+442071234567", requested_by="u")
    assert order["status"] == "needs_documents" and order["country"] == "GB"
    assert alerts == [("ops@example.com", "[numbers] Bella Salon needs a GB number")]       # the operator is told
    assert (await numbers.run_order(db, order["_id"]))["status"] == "needs_documents"     # nothing bought yet

    # Approving needs the approved bundle: Twilio refuses AU/UK/EU purchases without it.
    with pytest.raises(numbers.ProvisioningError, match="bundle SID"):
        await numbers.complete_regulatory(db, order["_id"], admin="root")
    with pytest.raises(numbers.ProvisioningError, match="AD"):
        await numbers.complete_regulatory(db, order["_id"], admin="root", bundle_sid="BU" + "a" * 32, address_sid="x")
    await numbers.complete_regulatory(db, order["_id"], admin="root", bundle_sid="BU" + "a" * 32,
                                      address_sid="AD" + "b" * 32)
    done = await numbers.run_order(db, order["_id"])
    assert done["status"] == "active"
    assert bought == {"number": "+442071234567", "bundle_sid": "BU" + "a" * 32, "address_sid": "AD" + "b" * 32}


# ── Export and offboarding (Task 5.5) ───────────────────────────────────────

async def _seed_data(db, tid):
    repos = Repos(db, tid)
    await repos.calls.insert_one({"call_id": "call_1", "started_at": NOW, "caller_phone": "+15125550111",
                                  "outcome": "booked", "summary": "=HYPERLINK(\"evil\")",
                                  "transcript": [{"role": "user", "text": "hi", "at": NOW}]})
    await repos.contacts.insert_one({"phone": "+15125550111", "name": "Pat"})
    await repos.appointments.insert_one({"appointment_id": "apt_1", "start": NOW, "end": NOW + timedelta(hours=1),
                                         "service_name": "Cut", "name": "Pat", "status": "confirmed"})
    await repos.messages.insert_one({"message_id": "m1", "message": "call me", "status": "new"})


async def test_export_zip_has_every_file_and_defuses_formulas(db, client):
    await _tenant(db)
    await _seed_data(db, "ten_S")
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    r = client.get("/t/export", headers=_h(tok))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(offboarding.EXPORT_KINDS) <= set(z.namelist())
    assert "'=HYPERLINK" in z.read("calls.csv").decode()
    assert json.loads(z.read("transcripts.json"))["calls"][0]["transcript"][0]["text"] == "hi"
    assert "BEGIN:VEVENT" in z.read("appointments.ics").decode()
    assert client.get("/t/export/contacts.csv", headers=_h(tok)).text.count("Pat") == 1
    assert client.get("/t/export/secrets.txt", headers=_h(tok)).status_code == 404


async def test_cancel_export_then_deleted_after_retention(db, client, monkeypatch):
    """Acceptance: cancel → export works → data deleted after retention."""
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "softphone")
    from core.repositories.repos import PhoneNumbersStore

    await _tenant(db)
    await _seed_data(db, "ten_S")
    await PhoneNumbersStore(db).assign({"e164": "+15555550001", "tenant_id": "ten_S", "purpose": "inbound",
                                        "provider": "fake"})
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")

    _post_event(client, _event("customer.subscription.deleted", _sub(status="canceled")))
    t = await _reload(db)
    assert t.status == "cancelled" and usage.gate(t) == "suspended"
    assert client.get("/t/calls", headers=_h(tok)).status_code == 403            # locked…
    assert client.get("/t/export", headers=_h(tok)).status_code == 200           # …but they can take their data

    delete_after = usage.aware(t.billing.delete_after)
    assert (await billing_jobs.run_once(db, now=delete_after - timedelta(days=10)))["deleted"] == 0
    assert (await billing_jobs.run_once(db, now=delete_after - timedelta(days=2)))["warned"] == 1
    stats = await billing_jobs.run_once(db, now=delete_after + timedelta(minutes=5))
    assert stats["deleted"] == 1
    for name in ("calls", "contacts", "appointments", "messages", "usage_events"):
        assert await db[name].count_documents({"tenant_id": "ten_S"}) == 0, name
    assert (await db.phone_numbers.find_one({"e164": "+15555550001"}))["status"] == "released"
    assert await db.users.find_one({"email": "owner@salon.com"}) is None
    tomb = await db.tenants.find_one({"_id": "ten_S"})
    assert tomb["name"] == "Deleted business" and tomb["billing"]["deleted_at"]
    assert await db.deletion_log.count_documents({"tenant_id": "ten_S"}) == 1
    # A late Stripe event can't bring it back.
    r = _post_event(client, _event("invoice.paid", {"customer": "cus_1", "amount_paid": 100,
                                                    "subscription": "sub_2"}, "evt_late"))
    assert r.json()["status"] == "ignored"


async def test_reactivation_inside_retention_clears_the_deletion_clock(db, client):
    await _tenant(db)
    _post_event(client, _event("customer.subscription.deleted", _sub(status="canceled")))
    assert (await _reload(db)).billing.delete_after
    _post_event(client, _event("customer.subscription.created", _sub(status="active", sid="sub_2"), "evt_new"))
    t = await _reload(db)
    assert t.status == "active" and t.billing.delete_after is None and t.billing.stripe_subscription_id == "sub_2"


# ── Usage recording at call end ─────────────────────────────────────────────

async def test_call_end_records_seconds_cost_and_refreshes_the_cache(db):
    from agent.persistence import CallRecorder
    from agent.profile import VoiceProfile
    from agent.state import CallState

    t = await _tenant(db)
    state = CallState(call_id="call_x", profile=VoiceProfile(tenant=t), channel="phone")
    state.started_at = datetime.now(timezone.utc) - timedelta(seconds=61.2)
    await CallRecorder(state)._record_usage()
    ev = await db.usage_events.find_one({"call_id": "call_x"})
    assert ev["seconds"] == 62 and ev["quantity"] == 2 and ev["billable"] is True and ev["cost_usd"] > 0
    assert (await _reload(db)).billing.period_seconds == 62


async def test_billing_view_for_owner(db, client):
    await _tenant(db)
    await _usage(db, "ten_S", 1200)
    await auth_service.create_user(email="owner@salon.com", password=PASSWORD, tenant_id="ten_S", role="owner")
    tok = await _login(client, "owner@salon.com")
    body = client.get("/t/billing", headers=_h(tok)).json()
    assert body["plan"] == "growth" and body["usage"]["used_minutes"] == 20.0 and body["usage"]["included_minutes"] == 400
    ctx = client.get("/t/context", headers=_h(tok)).json()
    assert ctx["billing"]["gate"] == "ok" and "stripe_customer_id" not in json.dumps(ctx)
