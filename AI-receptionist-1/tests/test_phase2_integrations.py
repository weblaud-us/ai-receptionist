"""Two-factor auth, Google Calendar sync, reminder/digest jobs, recording links (Tasks 2.8–2.10)."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core import crypto
from core.auth import service as auth_service
from core.auth import totp
from core.integrations import google_calendar as gcal
from core.integrations.s3_presign import presign_get
from core.notifications import drain
from core.repositories import Repos
from core.services import calendar_sync
from core.services.scheduling import SchedulingService
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id

PASSWORD = "correct horse battery"
TZ = "America/Chicago"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    crypto.reset_cache()
    invalidate()
    yield
    invalidate()


# ── crypto ───────────────────────────────────────────────

def test_encrypt_roundtrip_and_key_rotation(monkeypatch):
    from config import settings
    from cryptography.fernet import Fernet

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", old)
    crypto.reset_cache()
    token = crypto.encrypt("refresh-123")
    assert token != "refresh-123" and crypto.decrypt(token) == "refresh-123"
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", f"{new},{old}")   # rotated: old tokens still decrypt
    crypto.reset_cache()
    assert crypto.decrypt(token) == "refresh-123"
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", new)
    crypto.reset_cache()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token)


# ── TOTP ─────────────────────────────────────────────────

def test_totp_matches_rfc6238_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"            # base32 of b"12345678901234567890"
    assert totp.code_at(secret, 59 // 30) == "287082"       # RFC 6238 T=59 → 94287082 (8 digits)
    assert totp.code_at(secret, 1111111109 // 30) == "081804"
    assert totp.verify(secret, "287082", now=59)
    assert totp.verify(secret, "287082", now=59 + 30)        # one step of drift allowed
    assert not totp.verify(secret, "287082", now=59 + 90)
    assert not totp.verify(secret, "12345", now=59) and not totp.verify(secret, "abcdef", now=59)
    assert totp.otpauth_uri(secret, "a@b.co").startswith("otpauth://totp/LaudLine%3Aa%40b.co?secret=")


async def test_mfa_setup_login_and_enforcement(db, client, monkeypatch):
    from config import settings

    await auth_service.create_user(email="root@p.io", password=PASSWORD, platform_admin=True)
    first = client.post("/auth/login", json={"email": "root@p.io", "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {first['access_token']}"}
    monkeypatch.setattr(settings, "REQUIRE_ADMIN_MFA", True)
    blocked = client.get("/platform/tenants", headers=h)
    assert blocked.status_code == 403 and "mfa_required" in blocked.json()["detail"]

    setup = client.post("/auth/mfa/setup", headers=h).json()
    secret = setup["secret"]
    assert client.post("/auth/mfa/enable", headers=h, json={"code": "000000"}).status_code == 400
    enabled = client.post("/auth/mfa/enable", headers=h, json={"code": totp.code_at(secret, int(datetime.now().timestamp()) // 30)})
    assert enabled.status_code == 200 and enabled.json()["user"]["mfa_enabled"]
    assert client.get("/platform/tenants", headers={"Authorization": f"Bearer {enabled.json()['access_token']}"}).status_code == 200
    doc = await db.users.find_one({"email": "root@p.io"})
    assert doc["mfa"]["enabled"] and secret not in str(doc["mfa"])          # stored encrypted

    client.cookies.clear()
    step1 = client.post("/auth/login", json={"email": "root@p.io", "password": PASSWORD}).json()
    assert step1["mfa_required"] and step1["access_token"] is None and "rcpt_refresh" not in client.cookies
    assert client.post("/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": "000000"}).status_code == 401
    # The code used to switch two-factor on can't be used again (SEC-02): sign in with the next one,
    # which is accepted early because one step of clock drift is allowed.
    used = totp.code_at(secret, int(datetime.now().timestamp()) // 30)
    nxt = totp.code_at(secret, int(datetime.now().timestamp()) // 30 + 1)
    if nxt != used:
        assert client.post("/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": used}).status_code == 401
    ok = client.post("/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": nxt})
    assert ok.status_code == 200 and "rcpt_refresh" in client.cookies
    again = client.post("/auth/login", json={"email": "root@p.io", "password": PASSWORD}).json()
    replay = client.post("/auth/mfa/verify", json={"mfa_token": again["mfa_token"], "code": nxt})
    assert replay.status_code == 401                                     # a code works once
    refreshed = client.post("/auth/refresh").json()                      # the two-factor flag survives refresh
    assert client.get("/platform/tenants", headers={"Authorization": f"Bearer {refreshed['access_token']}"}).status_code == 200


async def test_legacy_admin_key_is_exempt_from_mfa(db, client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "REQUIRE_ADMIN_MFA", True)
    monkeypatch.setattr(settings, "ADMIN_SECRET_KEY", "k" * 40)
    assert client.get("/platform/tenants", headers={"X-Admin-Key": "k" * 40}).status_code == 200


def test_production_guard_requires_mfa_and_encryption_key():
    from pydantic import ValidationError

    from config import Settings

    safe = dict(APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
                REDIS_URL="redis://:test-redis-pw@redis:6379/0",
                VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://api.example.com", META_APP_SECRET="s",
                LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True)
    assert Settings(_env_file=None, **safe).is_production
    for bad in ({"REQUIRE_ADMIN_MFA": False}, {"COOKIE_SECURE": False}, {"GOOGLE_OAUTH_CLIENT_ID": "id", "TOKEN_ENCRYPTION_KEY": ""}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{**safe, **bad})


# ── S3 presign ───────────────────────────────────────────

def test_presign_matches_aws_documentation_example():
    url = presign_get("test.txt", bucket="examplebucket", expires=86400, region="us-east-1", endpoint="",
                      access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                      now=datetime(2013, 5, 24, tzinfo=timezone.utc), host="examplebucket.s3.amazonaws.com")
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


def test_presign_path_style_for_s3_compatible_endpoints():
    url = presign_get("recordings/2026/a b.ogg", bucket="rec", endpoint="https://acc.r2.cloudflarestorage.com",
                      region="auto", access_key="AK", secret_key="SK")
    assert url.startswith("https://acc.r2.cloudflarestorage.com/rec/recordings/2026/a%20b.ogg?X-Amz-Algorithm=")


# ── Google Calendar ──────────────────────────────────────

def test_oauth_state_is_signed_and_expires():
    state = gcal.make_state("ten_A", "usr_1", now=1000)
    assert gcal.read_state(state, now=1000 + 60) == {"tenant_id": "ten_A", "user_id": "usr_1"}
    assert gcal.read_state(state, now=1000 + gcal.STATE_TTL_S + 1) is None
    payload, _ = state.split(".")
    assert gcal.read_state(payload + ".forged", now=1000) is None


@pytest.fixture
async def salon(db):
    t = await create_tenant(name="Bella Salon", vertical="salon", timezone=TZ, tenant_id="ten_cal")
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "res_anna", "name": "Anna", "kind": "staff", "active": True})
    await repos.resources.insert_one({"_id": "res_mo", "name": "Mo", "kind": "staff", "active": True,
                                      "calendar": {"calendar_id": "mo@example.com"}})
    await calendar_sync.save_connection(t.id, refresh_token="rt-secret", user_id="usr_1")
    return await resolve_by_id(t.id)


def _event(start, end, **kw):
    return {"id": kw.pop("id", f"ev_{start:%H%M}"), "status": "confirmed",
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}, **kw}


def _fake_events(*events):
    async def list_events(refresh_token, calendar_id, start, end, **kw):
        assert calendar_id == "primary"
        return list(events)
    return list_events


async def test_sync_writes_business_and_staff_busy_times(db, salon, monkeypatch):
    now = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
    seen = {}

    async def fake_freebusy(refresh_token, calendar_ids, start, end):
        seen.update(rt=refresh_token, cals=calendar_ids)
        return {"mo@example.com": [(now + timedelta(hours=4), now + timedelta(hours=5))]}

    monkeypatch.setattr(gcal, "freebusy", fake_freebusy)
    monkeypatch.setattr(gcal, "list_events", _fake_events(_event(now + timedelta(hours=2), now + timedelta(hours=3))))
    counts = await calendar_sync.sync_busy(salon, now=now)
    # The business calendar is read as events; only staff calendars go through free/busy.
    assert seen == {"rt": "rt-secret", "cals": ["mo@example.com"]} and counts == {"primary": 1, "mo@example.com": 1}
    repos = Repos(db, salon.id)
    assert await repos.exceptions.count({"source": "google", "resource_id": None}) == 1
    assert len((await repos.resources.find_one({"_id": "res_mo"}))["busy_cache"]) == 1
    assert (await repos.resources.find_one({"_id": "res_anna"}))["busy_cache"] == []
    await calendar_sync.sync_busy(salon, now=now)                         # re-sync replaces, never duplicates
    assert await repos.exceptions.count({"source": "google"}) == 1
    raw = await db.tenants.find_one({"_id": salon.id})
    assert raw["integrations"]["google_calendar"]["refresh_token_enc"] != "rt-secret"   # encrypted at rest


async def test_google_busy_time_blocks_slots(db, salon, monkeypatch):
    salon.scheduling.min_notice_minutes = 0
    day_local = datetime(2026, 11, 24, 0, 0, tzinfo=ZoneInfo(TZ))
    busy_start = datetime(2026, 11, 24, 10, 0, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)

    async def fake_freebusy(refresh_token, calendar_ids, start, end):
        return {}

    monkeypatch.setattr(gcal, "freebusy", fake_freebusy)
    monkeypatch.setattr(gcal, "list_events", _fake_events(_event(busy_start, busy_start + timedelta(hours=1))))
    await calendar_sync.sync_busy(salon, now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    svc = SchedulingService(Repos(db, salon.id), salon)
    service = (await svc.services())[0]
    slots = await svc.find_slots(service=service, day_from=day_local.date(), now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc), limit=200)
    starts = {s.start for s in slots}
    assert busy_start not in starts and busy_start + timedelta(hours=1) in starts


async def test_the_apps_own_bookings_on_the_business_calendar_never_block_other_staff(db, salon, monkeypatch):
    """B4: Anna has no calendar of her own, so her AI bookings are written to the business calendar.
    The next sync must not turn them into a business-wide block that closes Mo's diary too."""
    salon.scheduling.min_notice_minutes = 0
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    inserted = {}

    async def fake_insert(refresh_token, calendar_id, **kw):
        inserted.update(calendar_id=calendar_id, private=kw.get("private"))
        return "evt_anna"

    monkeypatch.setattr(gcal, "insert_event", fake_insert)
    repos = Repos(db, salon.id)
    svc = SchedulingService(repos, salon)
    service = (await svc.services())[0]
    anna = await repos.resources.find_one({"_id": "res_anna"})
    slot = (await svc.find_slots(service=service, day_from=datetime(2026, 11, 24).date(), now=now, resource=anna))[0]
    appt = await svc.book(resource_id="res_anna", start=slot.start, service=service, name="Dana", phone="+15125550100", now=now)
    await drain()
    assert inserted["calendar_id"] == "primary"
    assert inserted["private"] == {calendar_sync.PRIVATE_KEY: appt["appointment_id"]}

    # Google now returns that booking (twice: with the marker, and an older copy known only by its id),
    # the owner's own dentist appointment, a "free" event and an all-day closure the day after.
    owner_start = slot.start + timedelta(hours=3)
    day_after = (datetime(2026, 11, 25)).date().isoformat()
    events = [
        _event(slot.start, slot.end, id="evt_marked",
               extendedProperties={"private": {calendar_sync.PRIVATE_KEY: appt["appointment_id"]}}),
        _event(slot.start, slot.end, id="evt_anna"),
        _event(owner_start, owner_start + timedelta(hours=1), id="evt_owner"),
        _event(owner_start + timedelta(hours=2), owner_start + timedelta(hours=3), id="evt_free", transparency="transparent"),
        {"id": "evt_holiday", "status": "confirmed", "start": {"date": day_after},
         "end": {"date": (datetime(2026, 11, 26)).date().isoformat()}},
        _event(owner_start, owner_start + timedelta(hours=1), id="evt_gone", status="cancelled"),
    ]

    async def no_staff_busy(*a, **k):
        return {"mo@example.com": []}

    monkeypatch.setattr(gcal, "list_events", _fake_events(*events))
    monkeypatch.setattr(gcal, "freebusy", no_staff_busy)
    counts = await calendar_sync.sync_busy(salon, now=now)
    assert counts["primary"] == 2                                    # the owner's appointment + the holiday
    blocks = await repos.exceptions.find({"source": "google"})
    assert sorted(b["start"].replace(tzinfo=timezone.utc) if b["start"].tzinfo is None else b["start"] for b in blocks)[0] \
        == owner_start

    mo = await repos.resources.find_one({"_id": "res_mo"})
    mo_slots = {s.start for s in await svc.find_slots(service=service, day_from=datetime(2026, 11, 24).date(), now=now,
                                                     resource=mo, limit=200)}
    assert slot.start in mo_slots                                     # Mo is still free when Anna is booked
    assert owner_start not in mo_slots                                # the owner's own event still blocks
    assert not any(s.date().isoformat() == day_after for s in mo_slots)   # the all-day closure blocks the day


async def test_bookings_and_cancellations_mirror_to_google(db, salon, monkeypatch):
    events = {}

    async def fake_insert(refresh_token, calendar_id, **kw):
        events["insert"] = (calendar_id, kw["summary"])
        return "evt_1"

    async def fake_delete(refresh_token, calendar_id, event_id):
        events["delete"] = (calendar_id, event_id)

    monkeypatch.setattr(gcal, "insert_event", fake_insert)
    monkeypatch.setattr(gcal, "delete_event", fake_delete)
    salon.scheduling.min_notice_minutes = 0
    svc = SchedulingService(Repos(db, salon.id), salon)
    service = (await svc.services())[0]
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    slot = (await svc.find_slots(service=service, day_from=datetime(2026, 11, 24).date(), now=now,
                                 resource=await Repos(db, salon.id).resources.find_one({"_id": "res_mo"})))[0]
    appt = await svc.book(resource_id="res_mo", start=slot.start, service=service, name="Dana", phone="+15125550100", now=now)
    await drain()
    assert events["insert"][0] == "mo@example.com" and "Dana" in events["insert"][1]
    stored = await Repos(db, salon.id).appointments.find_one({"appointment_id": appt["appointment_id"]})
    assert stored["external"]["google_event_id"] == "evt_1"
    await svc.cancel(appt["appointment_id"])
    await drain()
    assert events["delete"] == ("mo@example.com", "evt_1")


async def test_google_failure_never_breaks_booking(db, salon, monkeypatch):
    async def boom(*a, **k):
        raise gcal.GoogleError("down", 503)

    monkeypatch.setattr(gcal, "insert_event", boom)
    salon.scheduling.min_notice_minutes = 0
    svc = SchedulingService(Repos(db, salon.id), salon)
    service = (await svc.services())[0]
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    slot = (await svc.find_slots(service=service, day_from=datetime(2026, 11, 24).date(), now=now))[0]
    appt = await svc.book(resource_id=slot.resource_id, start=slot.start, service=service, name="X", phone="+1555", now=now)
    await drain()
    assert appt["status"] == "confirmed"


async def test_integration_endpoints_and_callback(db, client, monkeypatch):
    from config import settings

    await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_A")
    await auth_service.create_user(email="o@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    token = client.post("/auth/login", json={"email": "o@a.com", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/t/integrations/google/connect", headers=h).status_code == 503       # not configured
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(settings, "DASHBOARD_URL", "https://app.example.com")
    url = client.post("/t/integrations/google/connect", headers=h).json()["url"]
    assert "access_type=offline" in url and "calendar.freebusy" in url
    state = url.split("state=")[1]
    from urllib.parse import unquote

    async def fake_exchange(code):
        assert code == "abc"
        return {"refresh_token": "rt-from-google", "access_token": "at"}

    async def fake_freebusy(*a, **k):
        return {}

    async def fake_events(*a, **k):
        return []

    monkeypatch.setattr(gcal, "exchange_code", fake_exchange)
    monkeypatch.setattr(gcal, "freebusy", fake_freebusy)
    monkeypatch.setattr(gcal, "list_events", fake_events)
    r = client.get("/integrations/google/callback", params={"code": "abc", "state": unquote(state)}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://app.example.com/settings?tab=integrations&google=connected"
    status = client.get("/t/integrations", headers=h).json()["google_calendar"]
    assert status["connected"] and status["last_sync_at"]
    ctx = client.get("/t/context", headers=h).json()
    assert "refresh_token_enc" not in str(ctx)                                          # never sent to the browser
    bad = client.get("/integrations/google/callback", params={"code": "abc", "state": "x.y"}, follow_redirects=False)
    assert bad.headers["location"].endswith("google=invalid_link")
    assert client.post("/t/integrations/google/disconnect", headers=h).status_code == 200
    assert client.get("/t/integrations", headers=h).json()["google_calendar"]["connected"] is False


# ── jobs: reminders and digest ───────────────────────────

async def test_reminders_send_once_and_respect_do_not_contact(db, salon, monkeypatch):
    from services import tenant_jobs

    sent = []

    async def fake_notify(t, phone, text, **kw):
        sent.append((phone, text))
        return True

    monkeypatch.setattr(tenant_jobs, "notify_contact", fake_notify)
    repos = Repos(db, salon.id)
    now = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    await repos.contacts.insert_one({"_id": "con_ok", "phone": "+15125550100"})
    await repos.contacts.insert_one({"_id": "con_dnc", "phone": "+15125550199", "do_not_contact": True})
    base = {"status": "confirmed", "service_name": "Haircut", "reminders": {"day_before": {"sent_at": None}, "hour_before": {"sent_at": None}}}
    await repos.appointments.insert_one({**base, "appointment_id": "a1", "contact_id": "con_ok", "phone": "+15125550100",
                                         "start": now + timedelta(hours=24), "end": now + timedelta(hours=25)})
    await repos.appointments.insert_one({**base, "appointment_id": "a2", "contact_id": "con_dnc", "phone": "+15125550199",
                                         "start": now + timedelta(hours=24), "end": now + timedelta(hours=25)})
    await repos.appointments.insert_one({**base, "appointment_id": "a3", "contact_id": "con_ok", "phone": "+15125550100",
                                         "start": now + timedelta(minutes=60), "end": now + timedelta(minutes=90)})
    # a prospect's meeting booked by the sales dialer is not a customer appointment: no reminder
    await repos.appointments.insert_one({**base, "appointment_id": "a4", "phone": "+15125550177", "source": "outbound_sales",
                                         "start": now + timedelta(hours=24), "end": now + timedelta(hours=25)})
    assert await tenant_jobs.send_reminders(salon, now) == 2
    assert {p for p, _ in sent} == {"+15125550100"} and "Bella Salon" in sent[0][1]
    assert (await repos.appointments.find_one({"appointment_id": "a4"}))["reminders"]["day_before"]["sent_at"] is None
    assert await tenant_jobs.send_reminders(salon, now) == 0                              # claimed: never twice
    assert (await repos.appointments.find_one({"appointment_id": "a2"}))["reminders"]["day_before"]["skipped"] == "do_not_contact"


async def test_daily_digest_once_per_day_after_the_hour(db, salon, monkeypatch):
    from services import tenant_jobs

    mails = []

    async def fake_email(to, subject, text, html=None):
        mails.append((to, subject, text))
        return True

    monkeypatch.setattr(tenant_jobs, "send_email", fake_email)
    await db.tenants.update_one({"_id": salon.id}, {"$set": {"notifications.owner_email": "o@bella.com"}})
    invalidate()
    t = await resolve_by_id(salon.id)
    repos = Repos(db, salon.id)
    evening = datetime(2026, 11, 23, 19, 30, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)
    await repos.calls.insert_one({"call_id": "c1", "outcome": "booked", "duration_s": 120, "started_at": evening - timedelta(hours=3)})
    await repos.unanswered.insert_one({"question": "Do you sell gift cards?", "status": "open", "created_at": evening - timedelta(hours=1)})
    morning = datetime(2026, 11, 23, 9, 0, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)
    raw = await db.tenants.find_one({"_id": salon.id})
    assert await tenant_jobs.maybe_send_digest(t, morning, raw) is False                  # before DAILY_DIGEST_HOUR
    assert await tenant_jobs.maybe_send_digest(t, evening, raw) is True
    assert mails[0][0] == "o@bella.com" and "1 call answered" in mails[0][2] and "gift cards" in mails[0][2]
    raw = await db.tenants.find_one({"_id": salon.id})
    assert await tenant_jobs.maybe_send_digest(t, evening + timedelta(minutes=5), raw) is False
    assert len(mails) == 1


async def test_jobs_run_once_survives_a_failing_tenant(db, salon, monkeypatch):
    from services import tenant_jobs

    async def boom(t, now):
        raise RuntimeError("bad tenant")

    monkeypatch.setattr(tenant_jobs, "send_reminders", boom)

    async def no_sync(t, now):
        return False

    monkeypatch.setattr(tenant_jobs, "maybe_sync_calendar", no_sync)
    stats = await tenant_jobs.run_once(datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert stats["tenants"] == 1 and stats["reminders"] == 0


# ── no call audio, ever ──────────────────────────────────

async def test_there_is_no_way_to_play_call_audio(db, client):
    """Calls are transcribed, never recorded: no audio link exists even for an old call that has one."""
    await create_tenant(name="A", vertical="salon", tenant_id="ten_A")
    await auth_service.create_user(email="o@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    await Repos(db, "ten_A").calls.insert_one({"call_id": "cA", "recording": {"path": "recordings/cA.ogg", "bucket": "rec"}})
    h = {"Authorization": f"Bearer {client.post('/auth/login', json={'email': 'o@a.com', 'password': PASSWORD}).json()['access_token']}"}
    assert client.get("/t/calls/cA/recording", headers=h).status_code in (404, 405)
    import agent

    assert not (Path(agent.__file__).parent / "recording.py").exists()


async def test_booking_confirmation_records_sms_consent(db, monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa
    from core.tools.registry import ToolContext, run_tool
    import json

    async def ok(*a, **k):
        return True

    monkeypatch.setattr(wa, "send_text", ok)
    monkeypatch.setattr(sms, "send_sms", ok)
    t = await create_tenant(name="Bella", vertical="salon", timezone=TZ, tenant_id="ten_c")
    t.scheduling.min_notice_minutes = 0
    await Repos(db, t.id).resources.insert_one({"_id": "r1", "name": "Anna", "kind": "staff", "active": True})
    now = datetime(2026, 11, 23, 14, tzinfo=timezone.utc)
    ctx = ToolContext(channel="voice", caller_phone="+15125550100", call_id="call-1", tenant=t, now=now)
    avail = json.loads(await run_tool(ctx, "check_availability", {"service": "haircut", "date": "2026-11-24"}))
    booked = json.loads(await run_tool(ctx, "book_appointment", {"slot_id": avail["slots"][0]["slot_id"], "service": "haircut", "name": "Dana"}))
    assert booked["success"]
    await drain()
    contact = await Repos(db, t.id).contacts.find_one({"phone": "+15125550100"})
    assert contact["consent"]["sms"]["granted"] and contact["consent"]["sms"]["call_id"] == "call-1"
