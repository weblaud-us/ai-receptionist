"""Dental Phase 2: AU/UK locale defaults, answer windows and greetings, missed-call recovery, legal entity."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core import dedupe
from core.notifications import drain
from core.prompts.builder import build_voice_prompt
from core.repositories import Repos
from core.services import recovery
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.models import HoursEntry, LegalEntity, TenantConfig, abn_valid


@pytest.fixture(autouse=True)
def _sms_on(monkeypatch):
    """These tests cover the SMS paths, which the platform has switched off (config.SMS_ENABLED)."""
    from config import settings as _settings

    monkeypatch.setattr(_settings, "SMS_ENABLED", True)


SYD = ZoneInfo("Australia/Sydney")
SPLIT = [HoursEntry(dow=d, open=o, close=c) for d in range(5) for o, c in (("09:00", "13:00"), ("14:00", "17:30"))]
CALLER = "+61412345678"


def _dental(**kw) -> TenantConfig:
    base = dict(_id="ten_dent", name="Harbour Smiles", slug="harbour", vertical="dental", country="AU",
                timezone="Australia/Sydney", hours=SPLIT)
    base.update(kw)
    return TenantConfig(**base)


def _mon(h, m=0):
    return datetime(2026, 11, 23, h, m, tzinfo=SYD)            # a Monday


@pytest.fixture(autouse=True)
def _fresh():
    dedupe.reset()
    invalidate()
    yield
    dedupe.reset()
    invalidate()


NOTICE = "Just so you know, this call is transcribed for the practice. "


# ── Answer window and greeting ───────────────────────────

def test_answer_window_open_lunch_and_after_hours():
    t = _dental()
    assert t.answer_window(_mon(10)) == "open"
    assert t.answer_window(_mon(13, 30)) == "lunch_break" and t.reopens_today(_mon(13, 30)) == "14:00"
    assert t.answer_window(_mon(8)) == "after_hours"            # before opening is not a break
    assert t.answer_window(_mon(20)) == "after_hours"
    assert t.answer_window(datetime(2026, 11, 28, 11, tzinfo=SYD)) == "after_hours"   # Saturday
    long_gap = _dental(hours=[HoursEntry(dow=0, open="08:00", close="10:00"), HoursEntry(dow=0, open="16:00", close="20:00")])
    assert long_gap.answer_window(_mon(12)) == "after_hours"   # a 6-hour gap is not lunch
    night = _dental(hours=[HoursEntry(dow=0, open="20:00", close="02:00")])
    assert night.answer_window(datetime(2026, 11, 24, 1, tzinfo=SYD)) == "open"       # overnight, from Monday


def test_dental_greetings_follow_the_moment_of_the_call():
    t = _dental()
    lunch = t.greeting_text(_mon(13, 10))
    assert lunch.startswith(f"{NOTICE}Thanks for calling Harbour Smiles. Our reception team is currently on a "
                            "short lunch break")
    assert "AI assistant" in lunch and lunch.endswith("How can I help?")
    closed = t.greeting_text(_mon(19))
    assert "We're currently closed" in closed and "AI assistant" in closed
    assert t.greeting_text(_mon(10)).startswith(f"{NOTICE}Thanks for calling Harbour Smiles, this is Aria")
    overflow = _dental(answering_mode="overflow")
    assert "Our receptionists are helping other patients" in overflow.greeting_text(_mon(10))
    assert "lunch break" in overflow.greeting_text(_mon(13, 10))
    assert t.greeting_text() == t.greeting_text(_mon(10))      # no time given: the standard greeting


def test_dental_transcription_notice_comes_first_and_custom_greetings_are_kept():
    # Calls are never recorded, but every call is transcribed, and dental callers hear that first.
    t = _dental()
    assert t.greeting_text(_mon(13, 10)).startswith(f"{NOTICE}Thanks for calling")
    own = _dental(agent={"call_notice": "  Calls are transcribed  so the team can follow up. "})
    assert own.greeting_text(_mon(19)).startswith("Calls are transcribed so the team can follow up. Thanks for calling")
    custom = _dental(agent={"greeting": "Hello from Harbour Smiles, I'm Aria, the AI assistant."})
    assert custom.greeting_text(_mon(13, 10)) == (f"{NOTICE}Hello from Harbour Smiles, I'm Aria, the AI assistant. "
                                                  "How can I help?")
    told = _dental(agent={"greeting": "Hi, I'm Aria, the AI assistant; this call is transcribed. How can I help?"})
    assert not told.greeting_text(_mon(10)).startswith("Just so you know")      # already said: not twice
    # A recording notice left over from before is ignored: no audio is recorded any more.
    old = _dental(agent={"recording": {"enabled": True, "announce_text": "Calls are recorded for training."}})
    assert "recorded" not in old.greeting_text(_mon(10))


def test_other_verticals_keep_their_greeting_at_any_time():
    salon = _dental(vertical="salon", agent={"recording": {"enabled": True}})
    expected = ("Thanks for calling Harbour Smiles, this is Aria, Harbour Smiles's virtual assistant. "
                "This call is transcribed for the team. How can I help?")              # an AU business
    for moment in (_mon(10), _mon(13, 10), _mon(20), None):
        assert salon.greeting_text(moment) == expected


def test_right_now_block_mentions_the_lunch_break_for_dental_only():
    now = _mon(13, 20).astimezone(timezone.utc)
    assert "Reception is on a lunch break until 2pm" in build_voice_prompt(_dental(), now=now)
    assert "lunch break" not in build_voice_prompt(_dental(vertical="restaurant"), now=now)
    busy = build_voice_prompt(_dental(answering_mode="overflow"), now=_mon(10).astimezone(timezone.utc))
    assert "Reception is busy with other patients" in busy


def test_call_record_stores_the_answer_window():
    from agent.persistence import call_document
    from agent.profile import VoiceProfile
    from agent.state import CallState

    state = CallState(call_id="c1", profile=VoiceProfile(tenant=_dental(answering_mode="overflow")),
                      caller_phone=CALLER)
    state.started_at = _mon(13, 15).astimezone(timezone.utc)
    doc = call_document(state, final=True)
    assert doc["answer_window"] == "lunch_break" and doc["overflow"] is False and doc["after_hours"] is True
    state.started_at = _mon(10).astimezone(timezone.utc)
    assert call_document(state, final=True)["overflow"] is True


def test_keyterms_are_capped_for_deepgram():
    from agent.profile import MAX_KEYTERMS, VoiceProfile

    t = _dental(agent={"stt_keyterms": [f"word{i}" for i in range(50)]})
    terms = VoiceProfile(tenant=t).keyterms
    assert "Invisalign" in terms and "word0" in terms and len(terms) <= MAX_KEYTERMS


# ── Locale defaults at creation ──────────────────────────

async def test_au_and_uk_businesses_start_with_regional_english(db):
    au = await create_tenant(name="Harbour Smiles", vertical="dental", timezone="Australia/Sydney", country="AU",
                             currency="AUD", tenant_id="ten_au", seed_template=False)
    assert au.locale == "en-AU" and au.agent.language == "en-AU" and au.agent.stt_language == "en-AU"
    gb = await create_tenant(name="Kensington Dental", vertical="dental", timezone="Europe/London", country="GB",
                             currency="GBP", tenant_id="ten_gb", seed_template=False)
    assert gb.locale == "en-GB" and gb.agent.stt_language == "en-GB"
    chosen = await create_tenant(name="Multi Dental", vertical="dental", country="AU", tenant_id="ten_multi",
                                 seed_template=False, extra={"agent": {"stt_language": "multi"}})
    assert chosen.agent.stt_language == "multi" and chosen.agent.language == "en-AU"
    us = await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_us", seed_template=False)
    assert us.locale == "en-US" and us.agent.stt_language == "en-US"


# ── Legal entity ─────────────────────────────────────────

def test_abn_and_companies_house_numbers_are_validated():
    assert abn_valid("51 824 753 556") and not abn_valid("51 824 753 557") and not abn_valid("1234")
    e = LegalEntity(registered_name="  Harbour  Smiles Pty Ltd ", abn="51 824 753 556", company_number="")
    assert e.abn == "51824753556" and e.registered_name == "Harbour Smiles Pty Ltd"
    assert LegalEntity(company_number="sc 123456").company_number == "SC123456"
    assert LegalEntity(company_number="01234567").company_number == "01234567"
    with pytest.raises(ValueError):
        LegalEntity(abn="12345678901")
    with pytest.raises(ValueError):
        LegalEntity(company_number="ABC")


# ── Missed-call recovery ─────────────────────────────────

def _wants(t, **kw):
    args = dict(channel="phone", direction="inbound", caller_phone=CALLER, outcome="completed", ended_reason=None)
    args.update(kw)
    return recovery.wants_recovery(t, **args)


def test_who_gets_a_recovery_text():
    on = _dental(missed_call_recovery={"enabled": True})
    assert _wants(on) and _wants(on, outcome=None) and _wants(on, outcome="busy", ended_reason="concurrency_limit")
    assert not _wants(_dental())                                          # off by default
    for outcome in ("booked", "message", "transferred", "rescheduled", "cancelled", "voicemail"):
        assert not _wants(on, outcome=outcome)
    assert not _wants(on, ended_reason="agent_hangup") and not _wants(on, ended_reason="billing_hard_cap")
    assert not _wants(on, channel="browser") and not _wants(on, direction="outbound") and not _wants(on, caller_phone=None)


def test_recovery_text_names_the_business_and_says_how_to_opt_out():
    t = _dental(missed_call_recovery={"enabled": True}, links={"booking": "https://book.example/hs"})
    text = recovery.message_text(t)
    assert "Harbour Smiles" in text and "https://book.example/hs" in text and text.endswith("Reply STOP to opt out.")
    phone_only = _dental(phone="+61290000000")
    assert "+61290000000" in recovery.message_text(phone_only)
    assert recovery.message_text(_dental()) is None                       # nothing useful to send
    own = _dental(phone="+61290000000", missed_call_recovery={"template": "Missed you! Ring {phone}. Text STOP to quit"})
    assert recovery.message_text(own) == "Harbour Smiles: Missed you! Ring +61290000000. Text STOP to quit"
    broken = _dental(phone="+61290000000", missed_call_recovery={"template": "Hi {name}"})
    assert "Call us back on +61290000000" in recovery.message_text(broken)


@pytest.fixture
def texts(monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa

    sent = []

    async def fake_sms(to, body, *a, **k):
        sent.append((to, body))
        return True

    async def fake_wa(*a, **k):
        return False

    monkeypatch.setattr(sms, "send_sms", fake_sms)
    monkeypatch.setattr(wa, "send_text", fake_wa)
    return sent


async def _business_with_call(db, tenant_id="ten_rec", **call):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id=tenant_id,
                            seed_template=False, status="active",
                            extra={"missed_call_recovery": {"enabled": True},
                                   "links": {"booking": "https://book.example/hs"}})
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    await Repos(db, t.id).calls.insert_one({"call_id": f"call_{tenant_id}", "channel": "phone", "direction": "inbound",
                                            "caller_phone": CALLER, "outcome": "completed", "ended_reason": None,
                                            "started_at": started, **call})
    return t


async def test_recovery_text_is_sent_once_and_stamped(db, texts):
    t = await _business_with_call(db)
    r = await recovery.send(t.id, "call_ten_rec")
    assert r == {"sent": True, "reason": "sent"} and texts[0][0] == CALLER and "Reply STOP" in texts[0][1]
    call = await Repos(db, t.id).calls.find_one({"call_id": "call_ten_rec"})
    assert call["recovery_sent_at"] and call["recovery_channel"] == "sms"
    assert (await recovery.send(t.id, "call_ten_rec"))["reason"] == "already_sent"
    await Repos(db, t.id).calls.insert_one({"call_id": "call_r2", "channel": "phone", "direction": "inbound",
                                            "caller_phone": CALLER, "outcome": "completed",
                                            "started_at": datetime.now(timezone.utc)})
    assert (await recovery.send(t.id, "call_r2"))["reason"] == "recently_texted"     # one per caller per day
    assert len(texts) == 1


async def test_recovery_is_skipped_after_booking_opt_out_or_a_handled_call(db, texts):
    t = await _business_with_call(db, outcome="booked")
    assert (await recovery.send(t.id, "call_ten_rec"))["reason"] == "not_needed"
    t2 = await _business_with_call(db, tenant_id="ten_rec2")
    await Repos(db, t2.id).appointments.insert_one({"_id": "apt_1", "phone": CALLER, "status": "confirmed"})
    assert (await recovery.send(t2.id, "call_ten_rec2"))["reason"] == "booked_since"
    t3 = await _business_with_call(db, tenant_id="ten_rec3")
    await Repos(db, t3.id).contacts.insert_one({"_id": "con_1", "phone": CALLER, "do_not_contact": True})
    r = await recovery.send(t3.id, "call_ten_rec3")
    assert r["sent"] is False and not texts
    assert (await recovery.send(t3.id, "no_such_call"))["reason"] == "unknown_call"


async def test_schedule_sends_directly_without_a_jobs_queue(db, texts, monkeypatch):
    from agent.profile import VoiceProfile
    from agent.state import CallState
    import jobs.queue as queue

    async def no_queue(*a, **k):
        return False

    monkeypatch.setattr(queue, "enqueue", no_queue)
    t = await _business_with_call(db, tenant_id="ten_sched")
    state = CallState(call_id="call_ten_sched", profile=VoiceProfile(tenant=t), caller_phone=CALLER)
    assert await recovery.schedule(state) is True
    await drain()
    assert len(texts) == 1
    state.outcome = "booked"
    assert await recovery.schedule(state) is False
