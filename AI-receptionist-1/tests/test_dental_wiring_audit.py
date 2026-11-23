"""Dental production wiring: the paths a real AU / UK practice call, booking, invoice and purge go through.

1. Voice path: the LiveKit agent gets en-AU / en-GB and dental keyterms; the call end schedules the
   missed-call text; the hotel-only Twilio Media Streams path is flagged by the preflight.
2. Greeting: chosen from the call's UTC start time converted to the practice's timezone.
3. Split shifts: no slot is offered across lunch, from the built-in diary or from Cliniko / Dentally;
   slots line up with the practice's own clock (half-hour timezones too).
4. Stripe: the setup fee rides on the first invoice without ending the trial early.
5. Retention: a dental call's summary is purged with its transcript.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import Settings, settings
from core.billing import lifecycle, plans, stripe_api
from core.repositories import Repos
from core.services import availability as av
from core.services import pms_sync
from core.services import scheduling_provider as sp
from core.services.scheduling import SchedulingError, SchedulingService
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tenancy.models import HoursEntry, TenantConfig
from scripts.check_env import dental_preflight
from tests.conftest import livekit_agents_importable
from tests.test_dental_pms import KEY, FakePMS
from tests.test_regional_deploy import PROD, _practice

UTC = timezone.utc
SYD = "Australia/Sydney"
SPLIT = [{"dow": d, "open": o, "close": c} for d in range(5) for o, c in (("09:00", "13:00"), ("14:00", "17:30"))]
CALLER = "+61412345678"


@pytest.fixture(autouse=True)
def _fresh():
    invalidate()
    yield
    invalidate()


# ── 1. Voice path ────────────────────────────────────────

@pytest.mark.skipif(not livekit_agents_importable(), reason="livekit-agents can't be imported on this machine")
@pytest.mark.parametrize("country,tz,locale", [("AU", SYD, "en-AU"), ("GB", "Europe/London", "en-GB")])
async def test_livekit_stt_gets_the_regional_model_and_dental_keyterms(db, monkeypatch, country, tz, locale):
    from agent import session_factory
    from agent.profile import resolve_profile

    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=tz, country=country,
                            tenant_id=f"ten_stt_{country}", status="active", seed_template=False)
    seen = {}
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setattr(settings, "VOICE_STT_PROVIDER", "deepgram")
    monkeypatch.setattr(settings, "VOICE_STT_MODEL", "nova-3")
    monkeypatch.setattr(session_factory.deepgram, "STT", lambda **kw: seen.update(kw) or object())
    profile = await resolve_profile(tenant_id=t.id)
    session_factory.build_stt(profile)                           # no VAD: Deepgram alone
    assert seen["language"] == locale
    assert {"Harbour Smiles", "Invisalign", "HICAPS", "Denplan"} <= set(seen["keyterm"])


@pytest.mark.parametrize("country,tz,locale", [("AU", SYD, "en-AU"), ("GB", "Europe/London", "en-GB")])
async def test_the_profile_build_stt_reads_carries_the_regional_language_and_keyterms(db, country, tz, locale):
    # Same check as above without livekit (it can't load on some Windows builds): these two properties
    # are exactly what session_factory.build_stt passes to Deepgram as `language` and `keyterm`.
    from agent.profile import resolve_profile

    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=tz, country=country,
                            tenant_id=f"ten_prof_{country}", status="active", seed_template=False)
    profile = await resolve_profile(tenant_id=t.id)
    assert profile.stt_language == locale and profile.tts_language == "en"
    assert {"Harbour Smiles", "Invisalign", "HICAPS", "Denplan"} <= set(profile.keyterms)


async def test_the_livekit_call_end_schedules_the_missed_call_text(db, monkeypatch):
    from agent.persistence import CallRecorder
    from agent.profile import VoiceProfile
    from agent.state import CallState
    from core.services import recovery

    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU",
                            tenant_id="ten_rec", status="active", seed_template=False,
                            extra={"missed_call_recovery": {"enabled": True}})
    scheduled = []

    async def fake_schedule(state):
        scheduled.append(state.call_id)
        return True
    monkeypatch.setattr(recovery, "schedule", fake_schedule)
    state = CallState(call_id="call_rec", profile=VoiceProfile(tenant=t), caller_phone=CALLER)
    await CallRecorder(state).finish("participant_disconnected")
    assert scheduled == ["call_rec"]


def test_preflight_fails_when_the_hotel_only_twilio_voice_path_is_on():
    t = _practice()
    on = Settings.model_construct(**PROD, LEGACY_TWILIO_VOICE=True, TOKEN_ENCRYPTION_KEY="")
    assert ("FAIL", "Voice path") in {(lv, ck) for lv, ck, _ in dental_preflight(on, [t])}
    off = Settings.model_construct(**PROD, LEGACY_TWILIO_VOICE=False, TOKEN_ENCRYPTION_KEY="")
    assert ("OK", "Voice path") in {(lv, ck) for lv, ck, _ in dental_preflight(off, [t])}


# ── 2. Greeting at call start ────────────────────────────

def test_greeting_uses_the_call_start_in_the_practices_own_timezone():
    from agent.profile import VoiceProfile

    au = VoiceProfile(tenant=TenantConfig(_id="t1", name="Harbour Smiles", slug="hs", vertical="dental",
                                          timezone=SYD, hours=SPLIT))
    # Monday 23 Nov 2026, 02:30 UTC = 13:30 in Sydney (AEDT, +11): reception is at lunch.
    lunch = au.greeting_for(datetime(2026, 11, 23, 2, 30, tzinfo=UTC))
    assert lunch.startswith("Just so you know, this call is transcribed for the practice. Thanks for calling Harbour Smiles. "
                            "Our reception team is currently on a short lunch break")
    # 13:30 UTC is 00:30 Tuesday in Sydney: closed, not lunch (a server-clock bug would say lunch).
    assert "We're currently closed" in au.greeting_for(datetime(2026, 11, 23, 13, 30, tzinfo=UTC))
    uk = VoiceProfile(tenant=TenantConfig(_id="t2", name="Kensington Dental", slug="kd", vertical="dental",
                                          timezone="Europe/London", hours=SPLIT, answering_mode="overflow"))
    # 13:15 UTC in November = 13:15 GMT; in July it is 14:15 BST (open, so the overflow greeting).
    assert "lunch break" in uk.greeting_for(datetime(2026, 11, 23, 13, 15, tzinfo=UTC))
    assert "helping other patients" in uk.greeting_for(datetime(2026, 7, 13, 13, 15, tzinfo=UTC))


# ── 3. Split-shift availability ──────────────────────────

def _local_times(slots, tz=SYD):
    return [s.start.astimezone(ZoneInfo(tz)).strftime("%H:%M") for s in slots]


def test_builtin_slots_stop_at_lunch_and_restart_after_it():
    hours = [HoursEntry(**h) for h in SPLIT]
    slots = av.free_slots(business_hours=hours, tz=SYD, resources=[av.ResourceWindow("r1")],
                          duration=timedelta(minutes=45), day_from=date(2026, 11, 24), day_to=date(2026, 11, 24),
                          now=datetime(2026, 11, 20, tzinfo=UTC), step=timedelta(minutes=15))
    times = _local_times(slots)
    assert "12:15" in times and "12:30" not in times             # 12:30 + 45 min would run into lunch
    assert not [x for x in times if "12:30" <= x < "14:00"] and times[times.index("12:15") + 1] == "14:00"


@pytest.mark.parametrize("tz", ["Australia/Adelaide", "Australia/Darwin", "Europe/London"])
def test_slots_line_up_with_the_practices_clock(tz):
    hours = [HoursEntry(dow=1, open="09:00", close="12:00")]
    slots = av.free_slots(business_hours=hours, tz=tz, resources=[av.ResourceWindow("r1")],
                          duration=timedelta(minutes=60), day_from=date(2026, 11, 24), day_to=date(2026, 11, 24),
                          now=datetime(2026, 11, 20, tzinfo=UTC), step=timedelta(minutes=60))
    assert _local_times(slots, tz) == ["09:00", "10:00", "11:00"]


class LunchPMS(FakePMS):
    """A Cliniko diary whose dentist has no lunch block: it offers 10:00, 12:30, 13:00 and 14:00."""

    TIMES = ["2026-11-23T23:00:00Z", "2026-11-24T01:30:00Z", "2026-11-24T02:00:00Z", "2026-11-24T03:00:00Z"]

    def route(self, request, path):
        if path.endswith("/available_times"):
            pid = path.split("/practitioners/")[1].split("/")[0]
            times = self.TIMES if pid == "p1" else []
            return httpx.Response(200, json={"available_times": [{"appointment_start": x} for x in times],
                                             "links": {}})
        return super().route(request, path)


async def test_pms_slots_respect_the_practices_lunch_break_and_closures(db, monkeypatch):
    fake = LunchPMS()
    monkeypatch.setattr(sp, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "CLINIKO_USER_AGENT", "AI Receptionist (ops@example.com)")
    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", currency="AUD",
                            tenant_id="ten_lunch", status="active", seed_template=False,
                            extra={"hours": SPLIT, "scheduling": {"min_notice_minutes": 0}})
    await pms_sync.connect(t, "cliniko", KEY, user_id="usr_1")
    t = await resolve_by_id(t.id)
    repos = Repos(db, t.id)
    svc = SchedulingService(repos, t)
    service = svc.match_service(await svc.services(), "Implant consultation")       # 45 minutes
    now = datetime(2026, 11, 20, tzinfo=UTC)
    slots = await svc.find_slots(service=service, day_from=date(2026, 11, 24), now=now, limit=10)
    # 12:30 (runs past 13:00) and 13:00 (lunch) are dropped; the diary's other times stay.
    assert _local_times(slots) == ["10:00", "14:00"]
    with pytest.raises(SchedulingError):
        lunch = datetime(2026, 11, 24, 2, 0, tzinfo=UTC)
        await svc.book(resource_id=slots[0].resource_id, start=lunch, service=service, name="Sam Smith",
                       phone=CALLER, now=now)
    assert fake.created == []                                    # nothing reached the diary
    # A practice-wide closure (public holiday) hides the diary's times too; a dentist's own doesn't.
    day = datetime(2026, 11, 23, 13, 0, tzinfo=UTC)               # Tue 24 Nov 00:00 Sydney
    await repos.exceptions.insert_one({"_id": "exc_ph", "start": day, "end": day + timedelta(days=1)})
    assert await svc.find_slots(service=service, day_from=date(2026, 11, 24), now=now, limit=10) == []


# ── 4. Stripe: setup fee and AUD / GBP ───────────────────

async def test_checkout_sends_the_retainer_overage_and_setup_fee_in_local_currency(monkeypatch):
    sent = {}

    async def fake_request(method, path, params=None, **kw):
        sent.update(path=path, pairs=dict(stripe_api.encode(params or {})))
        return {"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"}
    monkeypatch.setattr(stripe_api, "request", fake_request)
    await stripe_api.create_checkout_session(customer_id="cus_1", tenant_id="ten_1", price="price_dent_pro_m",
                                             overage_price="price_overage", setup_fee_price="price_dent_setup_pro",
                                             currency="GBP", trial_days=14, success_url="s", cancel_url="c")
    pairs = sent["pairs"]
    assert sent["path"] == "/checkout/sessions" and pairs["mode"] == "subscription" and pairs["currency"] == "gbp"
    assert pairs["line_items[0][price]"] == "price_dent_pro_m" and pairs["line_items[0][quantity]"] == "1"
    assert pairs["line_items[1][price]"] == "price_overage" and "line_items[1][quantity]" not in pairs
    assert pairs["line_items[2][price]"] == "price_dent_setup_pro" and pairs["line_items[2][quantity]"] == "1"
    assert pairs["subscription_data[trial_period_days]"] == "14"


async def _dental_trial(db, tid="ten_inv"):
    return await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", currency="AUD",
                               tenant_id=tid, status="trial", seed_template=False,
                               extra={"billing": {"plan": "dental_pro", "stripe_customer_id": "cus_d",
                                                  "stripe_subscription_id": "sub_d", "self_serve": True}})


async def test_the_setup_fee_invoice_does_not_end_the_trial(db):
    t = await _dental_trial(db)
    fee = plans.DENTAL_SETUP_FEES["dental_pro"]["AUD"] * 100
    first = {"customer": "cus_d", "subscription": "sub_d", "amount_paid": fee, "billing_reason": "subscription_create"}
    assert await lifecycle.on_invoice_paid(db, first) == "trial"
    assert TenantConfig.from_doc(await db.tenants.find_one({"_id": t.id})).status == "trial"
    # The first retainer invoice when the trial ends does make it active.
    cycle = {"customer": "cus_d", "subscription": "sub_d", "amount_paid": 99000, "billing_reason": "subscription_cycle"}
    assert await lifecycle.on_invoice_paid(db, cycle) == "active"


async def test_platform_list_mrr_converts_aud_gbp_and_prices_annual_plans_by_the_month(db, client):
    from core.auth import service as auth_service

    for tid, country, cur, interval in (("ten_au", "AU", "AUD", "year"), ("ten_gb", "GB", "GBP", "month")):
        await create_tenant(name=f"Practice {country}", vertical="dental", country=country, currency=cur,
                            timezone=SYD if country == "AU" else "Europe/London", tenant_id=tid, status="active",
                            seed_template=False, extra={"billing": {"plan": "dental_pro", "interval": interval}})
    await auth_service.create_user(email="root@platform.io", password="correct horse battery", platform_admin=True)
    tok = client.post("/auth/login", json={"email": "root@platform.io", "password": "correct horse battery"}).json()
    r = client.get("/platform/costs", headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert r.status_code == 200, r.text
    pro = plans.PLANS["dental_pro"]
    expected = round(pro.monthly_revenue_usd("AUD", "year") + pro.monthly_revenue_usd("GBP", "month"), 2)
    assert r.json()["list_mrr_usd"] == expected
    assert expected == round(990 * 10 / 12 * plans.USD_PER_UNIT["AUD"] + 499 * plans.USD_PER_UNIT["GBP"], 2)


# ── 5. Retention ─────────────────────────────────────────

async def test_dental_transcript_purge_takes_the_summary_too(db):
    from services import tenant_jobs

    now = datetime(2026, 11, 20, tzinfo=UTC)
    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU",
                            tenant_id="ten_ret", status="active", seed_template=False,
                            extra={"agent": {"transcript_retention_days": 30}})
    calls = Repos(db, t.id).calls
    old = now - timedelta(days=31)
    await calls.insert_one({"call_id": "old", "status": "completed", "started_at": old, "ended_at": old,
                            "transcript_enc": "x", "summary": "Caller has swelling and a cracked molar", "outcome": "booked"})
    # Transcript already purged before this release, summary left behind: swept too.
    await calls.insert_one({"call_id": "older", "status": "completed", "started_at": old, "ended_at": old,
                            "summary": "Bleeding gums since Tuesday", "outcome": "message"})
    await calls.insert_one({"call_id": "new", "status": "completed", "started_at": now, "ended_at": now,
                            "transcript_enc": "x", "summary": "Booked a check-up", "outcome": "booked"})
    assert await tenant_jobs.purge_transcripts(t, now) == 2
    for cid in ("old", "older"):
        doc = await calls.find_one({"call_id": cid})
        assert "summary" not in doc and "transcript_enc" not in doc and doc["outcome"] and doc["transcript_purged_at"]
    assert (await calls.find_one({"call_id": "new"}))["summary"] == "Booked a check-up"
    assert await tenant_jobs.purge_transcripts(t, now) == 0                     # idempotent
