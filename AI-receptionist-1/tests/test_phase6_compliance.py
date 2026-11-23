"""
Phase 6 — legal and compliance in the product (ROADMAP §11). No network.

Covered:
- consent revocation from any channel: SMS STOP (signed Twilio webhook), WhatsApp, START undoes SMS only,
  platform-level opt-outs reach the sales dnc_list, opted-out contacts get no texts;
- data subject requests: find / export / erase one caller, upcoming appointments guard, call rows kept
  without personal data, request log with due date, owners only;
- legal: signup records which document versions were accepted, DPA acceptance, public sub-processor list;
- retention: per-business recording purge;
- prompt: emergency number per country, "agent / operator / press 0" reaches a person;
- production guard: public sign-up needs the published legal documents.
"""

from datetime import datetime, timedelta, timezone

import pytest
from twilio.request_validator import RequestValidator

from config import Settings, settings
from core.auth import service as auth_service
from core.compliance import legal, optout, retention, subjects
from core.notifications import notify_contact
from core.prompts import builder
from core.repositories import Repos
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig


@pytest.fixture(autouse=True)
def _sms_on(monkeypatch):
    """These tests cover the SMS paths, which the platform has switched off (config.SMS_ENABLED)."""
    from config import settings as _settings

    monkeypatch.setattr(_settings, "SMS_ENABLED", True)


PASSWORD = "correct horse battery"
CALLER = "+15125550111"
BASE = "https://receptionist.example.com"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


def _h(token, tenant=None):
    h = {"Authorization": f"Bearer {token}"}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


async def _login(client, email):
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _tenant(tid="ten_A", name="Bella Salon", **extra) -> TenantConfig:
    return await create_tenant(name=name, vertical="salon", timezone="America/Chicago", tenant_id=tid,
                               status="active", extra=extra, seed_template=False)


async def _seed_caller(db, tid="ten_A", phone=CALLER):
    now = datetime.now(timezone.utc)
    repos = Repos(db, tid)
    await repos.contacts.upsert_by_phone(phone, {"name": "Maria Lopez", "email": "maria@example.com"})
    await repos.calls.insert_one({"call_id": f"call_{tid}_1", "caller_phone": phone, "status": "completed",
                                  "started_at": now - timedelta(days=2), "transcript": [{"role": "user", "text": "Hi, it's Maria"}],
                                  "summary": "Maria asked about a haircut.", "duration_s": 60, "outcome": "booked",
                                  "recording": {"path": "recordings/x.ogg", "bucket": "b"}})
    await repos.appointments.insert_one({"appointment_id": f"apt_{tid}_past", "phone": phone, "name": "Maria Lopez",
                                         "email": "maria@example.com", "status": "completed",
                                         "start": now - timedelta(days=2), "end": now - timedelta(days=2, hours=-1)})
    await repos.messages.insert_one({"message_id": f"msg_{tid}", "caller_phone": phone, "callback_number": phone,
                                     "caller_name": "Maria", "message": "Call me back", "status": "new"})


def _twilio_post(client, path, form):
    sig = RequestValidator("test-twilio-auth-token").compute_signature(BASE + path, form)
    return client.post(path, data=form, headers={"X-Twilio-Signature": sig})


# ── Consent revocation ──────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("STOP", "stop"), ("stop ", "stop"), ("Unsubscribe", "stop"), ("STOPALL", "stop"), ("revoke", "stop"),
    ("please stop texting me", "stop"), ("Don't text me again", "stop"), ("START", "start"), ("unstop", "start"),
    ("HELP", "help"), ("Can I move my appointment?", None), ("Thanks!", None),
])
def test_classify_sms(text, kind):
    assert optout.classify(text) == kind


def test_chat_ignores_words_that_mean_something_else():
    assert optout.classify("cancel") == "stop"                 # SMS: a carrier keyword
    assert optout.classify("cancel", chat=True) is None        # WhatsApp: "cancel my booking"
    assert optout.classify("stop", chat=True) == "stop"


async def test_sms_stop_to_a_business_number_opts_out_of_that_business_only(db, client):
    a = await _tenant("ten_A")
    await _tenant("ten_B", name="Other Salon")
    await db.phone_numbers.insert_one({"e164": "+15125550000", "tenant_id": a.id, "status": "active"})
    for tid in ("ten_A", "ten_B"):
        await Repos(db, tid).contacts.upsert_by_phone(CALLER, {"name": "Maria"})
    r = _twilio_post(client, "/telephony/sms/inbound", {"From": CALLER, "To": "+15125550000", "Body": "STOP"})
    assert r.status_code == 200 and "unsubscribed" in r.text and "Bella Salon" in r.text
    assert (await db.contacts.find_one({"tenant_id": "ten_A", "phone": CALLER}))["do_not_contact"] is True
    assert (await db.contacts.find_one({"tenant_id": "ten_B", "phone": CALLER}))["do_not_contact"] is False
    assert await db.dnc_list.count_documents({}) == 0          # a business's own number: not the sales list
    ev = await db.consent_events.find_one({})
    assert ev["kind"] == "opt_out" and ev["scope"] == "tenant" and ev["channel"] == "sms"


async def test_sms_stop_to_the_shared_sender_reaches_every_business_and_the_dnc_list(db, client, monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_SMS_FROM", "+15125559999")
    await _tenant("ten_A")
    await _tenant("ten_B", name="Other Salon")
    for tid in ("ten_A", "ten_B"):
        await Repos(db, tid).contacts.upsert_by_phone(CALLER, {"name": "Maria"})
    r = _twilio_post(client, "/telephony/sms/inbound", {"From": CALLER, "To": "+15125559999", "Body": "stop"})
    assert r.status_code == 200
    assert await db.contacts.count_documents({"phone": CALLER, "do_not_contact": True}) == 2
    assert await db.dnc_list.find_one({"phone": CALLER}) is not None

    # START re-subscribes texts, but the do-not-call list is permanent.
    r = _twilio_post(client, "/telephony/sms/inbound", {"From": CALLER, "To": "+15125559999", "Body": "START"})
    assert "resubscribed" in r.text
    assert await db.contacts.count_documents({"phone": CALLER, "do_not_contact": True}) == 0
    assert await db.dnc_list.find_one({"phone": CALLER}) is not None


async def test_start_does_not_undo_a_do_not_contact_set_by_staff(db):
    await _tenant("ten_A")
    await Repos(db, "ten_A").contacts.upsert_by_phone(CALLER, {"name": "Maria"})
    await db.contacts.update_one({"phone": CALLER}, {"$set": {"do_not_contact": True}})   # dashboard, no source
    await optout.record_opt_in(db, CALLER, channel="sms", tenant_id="ten_A")
    assert (await db.contacts.find_one({"phone": CALLER}))["do_not_contact"] is True


async def test_sms_webhook_rejects_unsigned_requests(db, client):
    r = client.post("/telephony/sms/inbound", data={"From": CALLER, "Body": "STOP"})
    assert r.status_code == 403
    assert await db.consent_events.count_documents({}) == 0


async def test_ordinary_texts_are_ignored(db, client):
    r = _twilio_post(client, "/telephony/sms/inbound", {"From": CALLER, "To": "+15125550000", "Body": "Running late"})
    assert r.status_code == 200 and "<Message>" not in r.text
    assert await db.consent_events.count_documents({}) == 0


async def test_opted_out_contacts_get_no_texts(db, monkeypatch):
    t = await _tenant("ten_A")
    sent = []

    async def fake_sms(to, body, sender=None):
        sent.append(to)
        return True

    monkeypatch.setattr("core.integrations.sms.send_sms", fake_sms)
    await Repos(db, t.id).contacts.upsert_by_phone(CALLER, {"name": "Maria"})
    assert await notify_contact(t, CALLER, "Reminder") is True
    await optout.record_opt_out(db, CALLER, channel="sms", tenant_id=t.id)
    assert await notify_contact(t, CALLER, "Reminder") is False
    assert await notify_contact(t, CALLER, "Here's the link you asked for", requested=True) is True
    assert sent == [CALLER, CALLER]


async def test_platform_opt_out_of_the_sales_tenant_reaches_dnc(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_TENANT_ID", "ten_sales")
    out = await optout.record_opt_out(db, CALLER, channel="whatsapp", tenant_id="ten_sales")
    assert out["dnc_added"] is True


# ── Data subject requests ───────────────────────────────────

async def test_find_export_and_erase_one_caller(db, monkeypatch):
    t = await _tenant("ten_A")
    await _tenant("ten_B", name="Other Salon")
    await _seed_caller(db, "ten_A")
    await _seed_caller(db, "ten_B")
    deleted = []

    async def fake_delete(rec):
        deleted.append(rec["path"])
        return True

    monkeypatch.setattr(retention, "delete_recording_object", fake_delete)

    found = await subjects.find(db, t, "+1 (512) 555-0111")
    assert found["counts"]["contacts"] == 1 and found["counts"]["calls"] == 1 and found["counts"]["messages"] == 1
    assert found["recordings"] == 1 and found["future_appointments"] == 0 and found["phone"].endswith("0111")

    data = await subjects.export(db, t, CALLER)
    assert data["data"]["calls"][0]["transcript"][0]["text"] == "Hi, it's Maria"
    assert "tenant_id" not in data["data"]["contacts"][0]

    result = await subjects.erase(db, t, CALLER, actor="usr_1")
    assert result["counts"]["messages"] == 1 and result["counts"]["calls"] == 1 and result["counts"]["recordings"] == 1
    assert deleted == ["recordings/x.ogg"]
    call = await db.calls.find_one({"tenant_id": "ten_A"})
    assert "transcript" not in call and "summary" not in call and "recording" not in call
    assert call["caller_phone"].startswith("erased:") and call["duration_s"] == 60   # billing data kept
    appt = await db.appointments.find_one({"tenant_id": "ten_A"})
    assert appt["name"] == "" and appt["email"] == "" and appt["phone"].startswith("erased:")
    assert await db.messages.count_documents({"tenant_id": "ten_A"}) == 0
    # Only the suppression row is left, so the business never contacts the number again.
    contact = await db.contacts.find_one({"tenant_id": "ten_A", "phone": CALLER})
    assert contact["do_not_contact"] is True and contact.get("suppression_only") is True and "name" not in contact
    # The other business is untouched.
    assert await db.messages.count_documents({"tenant_id": "ten_B"}) == 1
    assert (await db.calls.find_one({"tenant_id": "ten_B"}))["transcript"]


async def test_erase_refuses_upcoming_appointments_unless_confirmed(db):
    t = await _tenant("ten_A")
    await Repos(db, "ten_A").appointments.insert_one({
        "appointment_id": "apt_future", "phone": CALLER, "name": "Maria", "status": "confirmed",
        "start": datetime.now(timezone.utc) + timedelta(days=3)})
    with pytest.raises(subjects.SubjectError, match="upcoming"):
        await subjects.erase(db, t, CALLER)
    out = await subjects.erase(db, t, CALLER, include_future=True, suppress=False)
    assert out["counts"]["appointments"] == 1 and out["suppressed"] is False


def test_bad_numbers_are_refused():
    with pytest.raises(subjects.SubjectError):
        subjects.normalise("12345")


async def test_privacy_api_is_owner_only_logged_and_masked(db, client, monkeypatch):
    await _tenant("ten_A")
    await _seed_caller(db, "ten_A")
    monkeypatch.setattr(retention, "delete_recording_object", lambda rec: _true())
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    await auth_service.create_user(email="staff@a.com", password=PASSWORD, tenant_id="ten_A", role="staff")
    owner, staff = await _login(client, "owner@a.com"), await _login(client, "staff@a.com")

    assert client.get("/t/privacy/subject", params={"phone": CALLER}, headers=_h(staff)).status_code == 403
    r = client.get("/t/privacy/subject", params={"phone": CALLER}, headers=_h(owner))
    assert r.status_code == 200 and r.json()["counts"]["calls"] == 1

    r = client.get("/t/privacy/subject/export", params={"phone": CALLER}, headers=_h(owner))
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.json()["data"]["messages"][0]["message"] == "Call me back"

    bad = client.post("/t/privacy/subject/erase", json={"phone": CALLER, "confirm": "+15125550999"}, headers=_h(owner))
    assert bad.status_code == 422
    r = client.post("/t/privacy/subject/erase", json={"phone": CALLER, "confirm": CALLER}, headers=_h(owner))
    assert r.status_code == 200, r.text

    log = client.get("/t/privacy/requests", headers=_h(owner)).json()["requests"]
    assert [x["kind"] for x in log] == ["erasure", "access"]
    assert CALLER not in str(log) and log[0]["phone_masked"].endswith("0111")
    due = datetime.fromisoformat(log[0]["due_at"].replace("Z", "+00:00"))
    received = datetime.fromisoformat(log[0]["received_at"].replace("Z", "+00:00"))
    assert (due - received).days == settings.DSR_DEADLINE_DAYS
    audit = await db.audit_log.find({"tenant_id": "ten_A", "action": {"$regex": "^privacy"}}).to_list(10)
    assert {a["action"] for a in audit} == {"privacy.export", "privacy.erase"}


async def _true():
    return True


# ── Legal documents ─────────────────────────────────────────

async def test_signup_records_the_accepted_versions(db, client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ENABLED", True)
    body = {"email": "new@biz.com", "password": PASSWORD, "business_name": "New Biz", "vertical": "salon",
            "country": "US", "timezone": "America/Chicago", "plan": "growth", "accept_terms": True}
    r = client.post("/signup", json=body, headers={"user-agent": "pytest"})
    assert r.status_code == 200, r.text
    user = await db.users.find_one({"email": "new@biz.com"})
    rows = {a["doc"]: a for a in user["legal_acceptances"]}
    assert set(rows) == set(legal.SIGNUP_DOCUMENTS)
    assert rows["terms"]["version"] == legal.version_of("terms") and rows["terms"]["user_agent"] == "pytest"
    assert legal.outstanding(user) == []


def test_a_new_terms_version_is_outstanding(monkeypatch):
    user = {"legal_acceptances": legal.acceptance_rows(legal.SIGNUP_DOCUMENTS)}
    monkeypatch.setitem(legal.DOCUMENTS, "terms", {**legal.DOCUMENTS["terms"], "version": "2099-01-01"})
    assert legal.outstanding(user) == ["terms"]


async def test_dpa_acceptance_and_legal_status(db, client):
    await _tenant("ten_A")
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    owner = await _login(client, "owner@a.com")
    status = client.get("/t/legal", headers=_h(owner)).json()
    assert status["dpa"]["current"] is False and status["outstanding"] == list(legal.SIGNUP_DOCUMENTS)
    assert any(s["name"].startswith("Twilio") for s in status["subprocessors"])
    assert status["retention"]["transcript_days"] == 90

    stale = client.post("/t/legal/dpa/accept", json={"version": "2000-01-01", "signer_name": "Ann Owner"}, headers=_h(owner))
    assert stale.status_code == 409
    ok = client.post("/t/legal/dpa/accept", json={"version": legal.version_of("dpa"), "signer_name": "Ann Owner"},
                     headers=_h(owner))
    assert ok.status_code == 200
    assert client.get("/t/legal", headers=_h(owner)).json()["dpa"]["current"] is True
    assert client.post("/t/legal/accept", json={"documents": ["terms", "privacy", "aup"]}, headers=_h(owner)).status_code == 200
    assert client.get("/t/legal", headers=_h(owner)).json()["outstanding"] == []


async def test_public_legal_and_site(db, client, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_PHONE_NUMBER", "+15125550100")
    monkeypatch.setattr(settings, "TERMS_URL", "https://example.com/terms")
    docs = client.get("/public/legal").json()
    assert {d["key"] for d in docs["documents"]} >= {"terms", "privacy", "dpa", "subprocessors"}
    assert next(d for d in docs["documents"] if d["key"] == "terms")["url"] == "https://example.com/terms"
    assert client.get("/public/site").json()["demo_phone_number"] == "+15125550100"


def test_public_signup_needs_published_legal_documents():
    base = dict(APP_ENV="production", SECRET_KEY="x" * 40, CORS_ORIGINS="https://app.example.com",
                WEBHOOK_BASE_URL="https://api.example.com", META_APP_SECRET="s", REQUIRE_ADMIN_MFA=True,
                REDIS_URL="redis://r:6379/0", TELEPHONY_PROVIDER="livekit", SIGNUP_ENABLED=True,
                STRIPE_SECRET_KEY="sk_live_x", STRIPE_WEBHOOK_SECRET="whsec_x", STRIPE_PRICE_GROWTH_MONTHLY="price_x",
                RESEND_API_KEY="re_x", EMAIL_FROM="hi@example.com", DASHBOARD_URL="https://app.example.com",
                LOG_LEVEL="INFO", DEBUG=False)
    with pytest.raises(ValueError, match="DPA_URL"):
        Settings(_env_file=None, **base)
    Settings(_env_file=None, **base, TERMS_URL="https://x/t", PRIVACY_URL="https://x/p", DPA_URL="https://x/d")


# ── Retention ───────────────────────────────────────────────

async def test_audio_from_before_recording_stopped_is_still_deleted(db, monkeypatch):
    t = await _tenant("ten_A")
    monkeypatch.setattr(settings, "RECORDING_RETENTION_DAYS", 7)
    now = datetime.now(timezone.utc)
    repos = Repos(db, t.id)
    await repos.calls.insert_one({"call_id": "old", "started_at": now - timedelta(days=8), "recording": {"path": "a.ogg"}})
    await repos.calls.insert_one({"call_id": "new", "started_at": now - timedelta(days=2), "recording": {"path": "b.ogg"}})
    monkeypatch.setattr(settings, "RECORDING_S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "RECORDING_S3_SECRET", "secret")
    gone = []

    async def fake_delete(rec):
        gone.append(rec["path"])
        return True

    monkeypatch.setattr(retention, "delete_recording_object", fake_delete)
    assert await retention.purge_recordings(db, t, now) == 1
    assert gone == ["a.ogg"]
    old = await db.calls.find_one({"call_id": "old"})
    assert "recording" not in old and old["recording_deleted_at"]
    assert (await db.calls.find_one({"call_id": "new"}))["recording"]["path"] == "b.ogg"


# ── Prompt and call-level protections ───────────────────────

@pytest.mark.parametrize("country,number", [("US", "911"), ("CA", "911"), ("GB", "999"), ("AE", "999"),
                                            ("DE", "112"), ("FR", "112")])
async def test_prompt_names_the_local_emergency_number(country, number):
    t = TenantConfig(_id="ten_X", name="Shop", slug="shop", country=country)
    assert f"Emergency number here: {number}" in builder.render_business_facts(t, builder.BusinessSnapshot())


def test_prompt_lets_callers_reach_a_person():
    rules = builder.BASE_VOICE_RULES
    assert "operator" in rules and "pressing 0" in rules and "transfer_to_human" in rules


def test_pressing_zero_asks_for_a_person():
    from tests.conftest import livekit_agents_importable

    if not livekit_agents_importable():
        pytest.skip("livekit-agents can't be imported on this machine")
    from agent.receptionist import DtmfWatcher, wants_person

    assert wants_person("0") and not wants_person("5")

    class FakeRoom:
        def on(self, _event):
            return lambda fn: fn

    class FakeSession:
        def __init__(self):
            self.replies = []

        def interrupt(self):
            pass

        def generate_reply(self, instructions):
            self.replies.append(instructions)

    class State:
        call_id, transfer = "c1", None

    session = FakeSession()
    watcher = DtmfWatcher(FakeRoom(), session, State())
    assert watcher.on_digit("5") is False
    assert watcher.on_digit("0") is True and "transfer_to_human" in session.replies[0]
    assert watcher.on_digit("0") is False and len(session.replies) == 1     # one press is enough


# ── Opt-outs from people who aren't contacts yet, and platform-wide opt-outs ──

async def test_stop_from_someone_who_is_not_a_contact_yet_still_applies_later(db):
    """A STOP from a number this business has never stored must survive until they become a contact."""
    number = "+447700900123"
    assert not await optout.is_opted_out(db, "ten_X", number)
    await optout.record_opt_out(db, number, channel="sms", source="test", tenant_id="ten_X")
    assert await optout.is_opted_out(db, "ten_X", number)
    contact = await Repos(db, "ten_X").contacts.upsert_by_phone(number, {"name": "Later caller"})
    assert contact["do_not_contact"] is True and contact["name"] == "Later caller"
    assert not await optout.is_opted_out(db, "ten_Y", number)              # another business is unaffected
    await optout.record_opt_in(db, number, channel="sms", tenant_id="ten_X")
    assert not await optout.is_opted_out(db, "ten_X", number)


async def test_stop_to_the_shared_sender_applies_to_every_business_until_start(db):
    number = "+61412345678"
    await optout.record_opt_out(db, number, channel="sms", source="shared sender")          # platform scope
    assert await optout.is_opted_out(db, "ten_any", number) and await optout.is_opted_out(db, "ten_new", number)
    await optout.record_opt_in(db, number, channel="sms")
    assert not await optout.is_opted_out(db, "ten_any", number)
