"""Phase 6.2: dental patient recall — PMS-only sourcing, AU/UK consent gates, sending hours, frequency
caps, PHI-free copy, STOP handling, attribution and tenant isolation."""

import json
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
import pytest
from twilio.request_validator import RequestValidator

from config import settings
from core import crypto, dedupe
from core.outbound import gate, rules
from core.outbound.models import CampaignSettings
from core.repositories import Repos
from core.services import reactivation
from core.services import scheduling_provider as sp
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tenancy.models import DEFAULT_RECALL_TEMPLATE, ReactivationSettings, recall_template_problems

AU_MOBILE = "+61412345678"
UK_MOBILE = "+447911123456"
BASE = "https://receptionist.example.com"
# Tuesday 24 Nov 2026, 10:00 in Sydney and London.
SYD_TUE_10 = datetime(2026, 11, 23, 23, 0, tzinfo=timezone.utc)
LDN_TUE_10 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
VISIT_8_MONTHS_AGO = SYD_TUE_10 - timedelta(days=240)


class FakePMS:
    name = "cliniko"
    requires_type_mapping = True

    def __init__(self, patients: List[sp.LapsedPatient]) -> None:
        self.patients = patients
        self.calls: list = []

    async def list_lapsed_patients(self, since_date, exclude_with_future_booking=True, *, now=None):
        self.calls.append((since_date, exclude_with_future_booking))
        return [p for p in self.patients if not (exclude_with_future_booking and p.has_future_booking)]


def _patient(pid="p1", mobile="0412 345 678", visit=VISIT_8_MONTHS_AGO, **kw) -> sp.LapsedPatient:
    return sp.LapsedPatient(pms_patient_id=pid, first_name="Sam", mobile=mobile, last_visit_at=visit, **kw)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    dedupe.reset()
    invalidate()
    yield
    dedupe.reset()
    invalidate()


@pytest.fixture
def texts(monkeypatch):
    import core.integrations.sms as sms

    sent: list = []

    async def fake(to, body, *a, **k):
        sent.append((to, body))
        return f"SM{len(sent):04d}"

    monkeypatch.setattr(sms, "send_sms_sid", fake)
    return sent


@pytest.fixture
def pms(monkeypatch):
    fake = FakePMS([_patient()])
    monkeypatch.setattr(sp, "provider_for", lambda tenant: fake)
    return fake


async def _practice(tenant_id="ten_rc", *, country="AU", enabled=True, **extra):
    tz = "Australia/Sydney" if country == "AU" else "Europe/London"
    await create_tenant(
        name="Harbour Smiles", vertical="dental", country=country, timezone=tz, currency="AUD" if country == "AU" else "GBP",
        tenant_id=tenant_id, status="active", seed_template=False,
        extra={"links": {"booking": "https://book.example/hs"},
               "reactivation": {"enabled": enabled, **extra.pop("reactivation", {})},
               "integrations": {"pms": {"provider": "cliniko", "connected": True, "api_key_enc": crypto.encrypt("k-au1")}},
               **extra})
    return await resolve_by_id(tenant_id)


# ── Campaign separation ──────────────────────────────────

def test_the_sales_dialer_never_runs_patient_recall():
    with pytest.raises(ValueError, match="per practice"):
        CampaignSettings(name="Recall", campaign_type="patient_reactivation")
    assert CampaignSettings(name="Sales").campaign_type == "sales_outbound"
    decision = gate.evaluate({"country": "US", "timezones": ["America/Chicago"], "phone": "+15125550100"},
                             {"mode": "human_assisted", "campaign_type": "patient_reactivation"},
                             gate.GateFacts(), SYD_TUE_10)
    assert not decision.allowed and "wrong_campaign_type" in decision.failures and decision.permanent


def test_uk_voice_calls_always_need_tps_and_ctps():
    assert rules.required_registries("ai_consented", "GB") == ("uk_tps", "uk_ctps")
    assert rules.required_registries("human_assisted", "GB") == ("uk_tps", "uk_ctps")
    assert rules.required_registries("ai_consented", "US") == ()


# ── Copy ─────────────────────────────────────────────────

def test_recall_copy_is_neutral_names_the_practice_and_offers_stop():
    assert recall_template_problems(DEFAULT_RECALL_TEMPLATE) == []
    for word in ("root canal", "gum disease", "periodontal treatment", "your implant", "extraction"):
        problems = recall_template_problems(f"Hi {{first_name}}, time for your {word} at {{clinic_name}}. Reply STOP")
        assert any("health details" in p for p in problems), word
    assert any("clinic_name" in p for p in recall_template_problems("Hi {first_name}, book now. Reply STOP"))
    assert any("opt-out" in p for p in recall_template_problems("Hi {first_name} from {clinic_name}"))
    assert any("Unknown placeholder" in p for p in recall_template_problems("{clinic_name} {dob} STOP"))
    with pytest.raises(ValueError):
        ReactivationSettings(template="{clinic_name}: your crown is ready. Reply STOP")
    ok = ReactivationSettings(template="Hi {first_name}, {clinic_name} here — due a check-up? {booking_link} Reply STOP")
    assert ok.template.startswith("Hi {first_name}")


def test_sending_hours_are_clamped_to_8am_8pm_and_never_sunday():
    s = ReactivationSettings(send_window={"weekday": ["06:00", "23:00"], "saturday": ["10:00", "16:00"]})
    assert s.send_window.weekday == ["08:00", "20:00"]
    window = s.send_window.model_dump()
    sunday_noon = datetime(2026, 11, 29, 1, 0, tzinfo=timezone.utc)                 # Sunday 12:00 Sydney
    assert not gate.recall_window_open(window, "Australia/Sydney", sunday_noon)
    assert gate.recall_window_open(window, "Australia/Sydney", SYD_TUE_10)
    late = datetime(2026, 11, 24, 10, 30, tzinfo=timezone.utc)                       # Tuesday 21:30 Sydney
    assert not gate.recall_window_open(window, "Australia/Sydney", late)


# ── The gate ─────────────────────────────────────────────

def _decide(country="AU", now=SYD_TUE_10, recall=None, facts=None, **settings_):
    row = {"phone": AU_MOBILE if country == "AU" else UK_MOBILE, "last_visit_at": VISIT_8_MONTHS_AGO,
           "attempts": [], **(recall or {})}
    cfg = {**ReactivationSettings(enabled=True).model_dump(), **settings_}
    tz = "Australia/Sydney" if country == "AU" else "Europe/London"
    return gate.evaluate_recall(row, cfg, country=country, tz=tz, facts=facts or gate.RecallFacts(), now=now)


def test_au_inferred_consent_from_an_existing_patient():
    d = _decide()
    assert d.allowed and d.checks["consent_basis"] == "au_inferred_existing_patient"
    old = _decide(recall={"last_visit_at": SYD_TUE_10 - timedelta(days=800)})          # over 24 months
    assert not old.allowed and "consent_expired" in old.failures
    assert _decide(facts=gate.RecallFacts(pms_marketing_consent=True)).checks["consent_basis"] == "explicit_opt_in"
    refused = _decide(facts=gate.RecallFacts(pms_marketing_consent=False))
    assert not refused.allowed and "pms_marketing_opt_out" in refused.failures
    assert "opted_out" in _decide(facts=gate.RecallFacts(opted_out=True)).failures


def test_uk_pecr_soft_opt_in():
    d = _decide(country="GB", now=LDN_TUE_10)
    assert d.allowed and d.checks["consent_basis"] == "uk_pecr_soft_opt_in"
    assert "not_mobile" in _decide(country="GB", now=LDN_TUE_10, recall={"phone": "+442079460958"}).failures
    assert "country_not_allowed" in _decide(country="US").failures


def test_not_lapsed_booked_or_disabled_patients_are_not_texted():
    assert "not_lapsed" in _decide(recall={"last_visit_at": SYD_TUE_10 - timedelta(days=30)}).failures
    assert "has_future_booking" in _decide(recall={"has_future_booking": True}).failures
    assert "recall_disabled" in _decide(enabled=False).failures


def test_quiet_hours_defer_instead_of_blocking():
    sunday = datetime(2026, 11, 29, 1, 0, tzinfo=timezone.utc)
    d = _decide(now=sunday)
    assert not d.allowed and d.failures == ["outside_hours"] and not d.permanent
    assert d.retry_at is not None and gate.recall_window_open(ReactivationSettings().send_window.model_dump(),
                                                              "Australia/Sydney", d.retry_at)


def test_frequency_caps():
    recent = _decide(facts=gate.RecallFacts(last_recall_sent_at=SYD_TUE_10 - timedelta(days=10)))
    assert recent.failures == ["too_soon"] and recent.retry_at == SYD_TUE_10 + timedelta(days=20)
    two = _decide(recall={"attempts": [{}, {}]})
    assert "max_attempts" in two.failures and two.permanent


def test_voice_recall_is_off_and_needs_the_register_unless_consented():
    assert "voice_disabled" in gate.evaluate_recall(
        {"phone": AU_MOBILE, "last_visit_at": VISIT_8_MONTHS_AGO, "attempts": []},
        {**ReactivationSettings(enabled=True).model_dump()}, country="AU", tz="Australia/Sydney",
        facts=gate.RecallFacts(), now=SYD_TUE_10, channel="voice").failures
    on = {**ReactivationSettings(enabled=True, voice_enabled=True).model_dump()}
    row = {"phone": AU_MOBILE, "last_visit_at": VISIT_8_MONTHS_AGO, "attempts": []}
    unchecked = gate.evaluate_recall(row, on, country="AU", tz="Australia/Sydney", facts=gate.RecallFacts(),
                                     now=SYD_TUE_10, channel="voice")
    assert "registry_not_ready" in unchecked.failures
    listed = gate.evaluate_recall(row, on, country="AU", tz="Australia/Sydney",
                                  facts=gate.RecallFacts(registry_hits=("au_dncr",)), now=SYD_TUE_10, channel="voice")
    assert "registry_listed" in listed.failures
    clean = gate.evaluate_recall(row, on, country="AU", tz="Australia/Sydney",
                                 facts=gate.RecallFacts(registry_status={"au_dncr": "fresh"}), now=SYD_TUE_10,
                                 channel="voice")
    consented = gate.evaluate_recall(row, on, country="AU", tz="Australia/Sydney",
                                     facts=gate.RecallFacts(explicit_voice_consent_at=SYD_TUE_10), now=SYD_TUE_10,
                                     channel="voice")
    assert clean.allowed and consented.allowed


# ── End to end ───────────────────────────────────────────

async def test_pms_only_sourcing_and_a_full_au_send_with_audit_trail(db, pms, texts):
    t = await _practice()
    stats = await reactivation.refresh(t, now=SYD_TUE_10)
    assert stats["new"] == 1 and pms.calls and pms.calls[0][1] is False
    since = pms.calls[0][0]
    assert SYD_TUE_10 - since == timedelta(days=round(6 * 30.44))                       # 6-month default
    sent = await reactivation.send_due(t, now=SYD_TUE_10)
    assert sent["sent"] == 1
    to, body = texts[0]
    assert to == AU_MOBILE and body.startswith("Hi Sam, it's been a while since your last visit to Harbour Smiles")
    assert body.endswith("Reply STOP to opt out.") and "https://book.example/hs?ref=rcl_" in body and "&sig=" in body
    row = (await Repos(db, t.id).recalls.find({}))[0]
    assert row["campaign_type"] == "patient_reactivation" and row["pms_patient_id"] == "p1"
    assert row["consent_basis"] == "au_inferred_existing_patient" and row["twilio_sid"] == "SM0001"
    assert row["status"] == "sent" and row["attempts"][0]["consent_verified_at"]
    # A refresh is idempotent: the same patient and visit is the same cycle.
    assert (await reactivation.refresh(t, now=SYD_TUE_10))["new"] == 0


async def test_no_pms_means_no_recall(db, texts, monkeypatch):
    t = await _practice(integrations={})
    assert not reactivation.available(t)
    assert await reactivation.refresh(t, now=SYD_TUE_10) == {"patients": 0, "new": 0, "booked": 0, "skipped_no_mobile": 0}
    assert (await reactivation.send_due(t, now=SYD_TUE_10))["sent"] == 0 and not texts


async def test_30_day_cap_and_two_attempts_per_cycle(db, pms, texts):
    t = await _practice()
    await reactivation.refresh(t, now=SYD_TUE_10)
    await reactivation.send_due(t, now=SYD_TUE_10)
    assert (await reactivation.send_due(t, now=SYD_TUE_10 + timedelta(days=5)))["sent"] == 0   # not due yet
    # A second cycle for the same number (another PMS record) still can't text within 30 days.
    pms.patients.append(_patient(pid="p2", visit=VISIT_8_MONTHS_AGO - timedelta(days=3)))
    await reactivation.refresh(t, now=SYD_TUE_10 + timedelta(days=5))
    assert (await reactivation.send_due(t, now=SYD_TUE_10 + timedelta(days=5)))["sent"] == 0
    dedupe.reset()
    later = SYD_TUE_10 + timedelta(days=35)
    assert (await reactivation.send_due(t, now=later))["sent"] == 1                         # follow-up for p1
    dedupe.reset()
    much_later = SYD_TUE_10 + timedelta(days=80)
    await reactivation.send_due(t, now=much_later)
    p1 = await Repos(db, t.id).recalls.find_one({"pms_patient_id": "p1"})
    assert len(p1["attempts"]) == 2 and p1["status"] == "completed"
    assert len([x for x in texts if x[0] == AU_MOBILE]) == 3                               # p1 ×2, p2 ×1, 30 days apart


async def test_quiet_hours_defer_the_send(db, pms, texts):
    t = await _practice()
    sunday = datetime(2026, 11, 29, 1, 0, tzinfo=timezone.utc)
    await reactivation.refresh(t, now=sunday)
    assert (await reactivation.send_due(t, now=sunday))["deferred"] == 1 and not texts
    row = (await Repos(db, t.id).recalls.find({}))[0]
    assert row["status"] == "scheduled" and row["next_send_at"].replace(tzinfo=timezone.utc) > sunday


async def test_uk_soft_opt_in_send(db, monkeypatch, texts):
    fake = FakePMS([_patient(mobile="07911 123456", visit=LDN_TUE_10 - timedelta(days=240))])
    monkeypatch.setattr(sp, "provider_for", lambda tenant: fake)
    t = await _practice("ten_uk", country="GB")
    await reactivation.refresh(t, now=LDN_TUE_10)
    assert (await reactivation.send_due(t, now=LDN_TUE_10))["sent"] == 1
    row = (await Repos(db, t.id).recalls.find({}))[0]
    assert row["consent_basis"] == "uk_pecr_soft_opt_in" and texts[0][0] == UK_MOBILE


async def test_stop_reply_opts_out_cancels_queued_recalls_and_confirms(db, client, pms, texts):
    t = await _practice()
    await db.phone_numbers.insert_one({"e164": "+61290000000", "tenant_id": t.id, "status": "active"})
    await reactivation.refresh(t, now=SYD_TUE_10)
    form = {"From": AU_MOBILE, "To": "+61290000000", "Body": "opt out"}
    sig = RequestValidator("test-twilio-auth-token").compute_signature(BASE + "/telephony/sms/inbound", form)
    r = client.post("/telephony/sms/inbound", data=form, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "You've been unsubscribed from Harbour Smiles reminders." in r.text
    row = (await Repos(db, t.id).recalls.find({}))[0]
    assert row["status"] == "opted_out" and row["next_send_at"] is None
    # Even a fresh recall row for that number is blocked by the opt-out record.
    await Repos(db, t.id).recalls.update_one({"_id": row["_id"]}, {"$set": {"status": "scheduled", "next_send_at": SYD_TUE_10}})
    assert (await reactivation.send_due(t, now=SYD_TUE_10))["closed"] == 1 and not texts


async def test_a_booking_after_a_recall_is_attributed_and_ends_the_cycle(db, pms, texts):
    t = await _practice()
    await reactivation.refresh(t, now=SYD_TUE_10)
    await reactivation.send_due(t, now=SYD_TUE_10)
    assert await reactivation.on_booking(t, "0412 345 678", "apt_42", now=SYD_TUE_10 + timedelta(days=3)) == 1
    row = (await Repos(db, t.id).recalls.find({}))[0]
    assert row["status"] == "booked" and row["attributed_booking_id"] == "apt_42"
    s = await reactivation.stats(t, SYD_TUE_10 - timedelta(days=1), SYD_TUE_10 + timedelta(days=10))
    assert s == {"recall_texts_sent": 1, "reactivation_bookings_attributed": 1}
    # A booking seen in the PMS before any text: the recall is cancelled, not credited.
    pms.patients = [_patient(pid="p9", mobile="0412 999 888", has_future_booking=True, next_booking_id="ia7")]
    await Repos(db, t.id).recalls.insert_one({"_id": "rcl_x", "pms_patient_id": "p9", "cycle": reactivation._cycle(VISIT_8_MONTHS_AGO),
                                              "status": "scheduled", "phone": "+61412999888", "attempts": []})
    await reactivation.refresh(t, now=SYD_TUE_10)
    assert (await Repos(db, t.id).recalls.find_one({"_id": "rcl_x"}))["status"] == "cancelled"


async def test_preview_counts_and_tenant_isolation(db, pms, texts):
    a = await _practice("ten_ra")
    b = await _practice("ten_rb")
    pms.patients.append(_patient(pid="p_old", mobile="0412 000 111", visit=SYD_TUE_10 - timedelta(days=900)))
    pms.patients.append(_patient(pid="p_land", mobile="02 9000 0000"))
    p = await reactivation.preview(a, now=SYD_TUE_10)
    assert p["eligible"] == 1 and p["blocked"].get("not_mobile") == 1
    await reactivation.refresh(a, now=SYD_TUE_10)
    assert await Repos(db, b.id).recalls.count({}) == 0
    await reactivation.cancel_for_phone(db, AU_MOBILE, tenant_id=b.id)
    assert (await Repos(db, a.id).recalls.find_one({"pms_patient_id": "p1"}))["status"] == "scheduled"


async def test_insights_and_erasure_include_recalls(db, pms, texts):
    from core.compliance import subjects
    from core.services import insights

    t = await _practice()
    await reactivation.refresh(t, now=SYD_TUE_10)
    await reactivation.send_due(t, now=SYD_TUE_10)
    await reactivation.on_booking(t, AU_MOBILE, "apt_1", now=SYD_TUE_10 + timedelta(days=1))
    r = await insights.value_report(db, t, SYD_TUE_10 - timedelta(days=1), SYD_TUE_10 + timedelta(days=5))
    assert r["dental"]["reactivation_bookings_attributed"] == 1 and r["dental"]["recall_texts_sent"] == 1
    await subjects.erase(db, t, AU_MOBILE, actor="usr_1")
    assert await Repos(db, t.id).recalls.count({}) == 0


async def test_reactivation_api(db, client, pms, texts):
    from core.auth import service as auth_service

    auth_service.reset_rate_limits()
    await _practice("ten_api_rc")
    await auth_service.create_user(email="dr@hs.au", password="correct horse battery", tenant_id="ten_api_rc", role="owner")
    tok = client.post("/auth/login", json={"email": "dr@hs.au", "password": "correct horse battery"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    status = client.get("/t/reactivation", headers=h).json()
    assert status["available"] is True and status["settings"]["recall_interval_months"] == 6
    assert client.post("/t/reactivation/preview", headers=h).json()["eligible"] == 1
    bad = client.patch("/t/settings", headers=h, json={"reactivation": {"enabled": True, "template": "Your root canal is due"}})
    assert bad.status_code == 422
    ok = client.patch("/t/settings", headers=h, json={"reactivation": {"enabled": True, "recall_interval_months": 12}})
    assert ok.status_code == 200, ok.text


# ── Cliniko: lapsed patients from the diary ──────────────

async def test_cliniko_lists_lapsed_patients_from_appointments(monkeypatch):
    from core.integrations.cliniko import ClinikoProvider

    now = SYD_TUE_10

    def iso(days):
        return (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    appts = [
        {"id": 1, "patient_id": "p1", "starts_at": iso(-240)},                         # lapsed
        {"id": 2, "patient_id": "p2", "starts_at": iso(-300)},
        {"id": 3, "patient_id": "p2", "starts_at": iso(+10)},                          # p2 is booked
        {"id": 4, "patient_id": "p3", "starts_at": iso(-20)},                          # seen recently
        {"id": 5, "patient_id": "p4", "starts_at": iso(-200), "did_not_arrive": True},  # never attended
        {"id": 6, "patient_id": "p5", "starts_at": iso(-210), "cancelled_at": iso(-215)},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/individual_appointments":
            return httpx.Response(200, json={"individual_appointments": appts, "links": {}})
        pid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"id": pid, "first_name": "Sam", "accepted_sms_marketing": True,
                                         "patient_phone_numbers": [{"phone_type": "Home", "number": "02 9000 0000"},
                                                                   {"phone_type": "Mobile", "number": "0412 345 678"}]})

    monkeypatch.setattr(sp, "TRANSPORT", httpx.MockTransport(handler))
    provider = ClinikoProvider("key-au1", business_id="b1", timezone="Australia/Sydney")
    since = now - timedelta(days=180)
    lapsed = await provider.list_lapsed_patients(since, now=now)
    assert [p.pms_patient_id for p in lapsed] == ["p1"]
    assert lapsed[0].mobile == "0412 345 678" and lapsed[0].sms_marketing_consent is True
    with_booked = await provider.list_lapsed_patients(since, exclude_with_future_booking=False, now=now)
    assert {p.pms_patient_id: p.has_future_booking for p in with_booked} == {"p1": False, "p2": True}
    assert json.dumps([p.pms_patient_id for p in with_booked])
