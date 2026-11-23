"""Landing-page live demo: Google sign-in → lead → LiveKit token; the platform's Demo leads page; clean-up."""

import time
from datetime import timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from livekit import api

from config import settings
from core.auth import google_id
from core.auth import service as auth_service
from core.repositories import Repos
from core.repositories.base import utcnow
from core.services import web_demo as wd
from core.services.tenant_admin import create_tenant
from routers.web_demo import _cell

PASSWORD = "correct horse battery"
CLIENT_ID = "demo-client.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


def credential(sub="g-123", email="dr.lee@smiles.com.au", name="Jo Lee", aud=CLIENT_ID, exp_in=600,
               verified=True, iss="https://accounts.google.com", key=KEY):
    now = int(time.time())
    claims = {"sub": sub, "email": email, "email_verified": verified, "name": name, "aud": aud, "iss": iss,
              "iat": now, "exp": now + exp_in, "hd": email.split("@")[1]}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def _google(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SIGNIN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(google_id, "_jwks", _FakeJwks())
    yield


@pytest.fixture
async def demo(db, monkeypatch):
    au = await create_tenant(name="Harbourline Dental", vertical="dental", country="AU", timezone="Australia/Sydney",
                             currency="AUD", status="active", tenant_id="ten_demo_au", seed_template=False)
    monkeypatch.setattr(settings, "WEB_DEMO_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_DEMO_TENANT_AU", au.id)
    monkeypatch.setattr(settings, "WEB_DEMO_TENANT_GB", "")
    return au


def _start(client, cred=None, **body):
    return client.post("/public/web-demo/start", json={"credential": cred or credential(), "region": "au", **body})


# ── Google ID token ─────────────────────────────────────────────────────────

async def test_a_valid_google_credential_gives_the_identity():
    ident = await google_id.verify(credential())
    assert (ident.sub, ident.email, ident.name, ident.hosted_domain) == ("g-123", "dr.lee@smiles.com.au", "Jo Lee",
                                                                          "smiles.com.au")


@pytest.mark.parametrize("kw, reason", [
    ({"aud": "someone-elses-client"}, "isn't valid"),
    ({"exp_in": -3600}, "expired"),
    ({"verified": False}, "verified e-mail"),
    ({"iss": "https://evil.example.com"}, "isn't valid"),
    ({"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)}, "isn't valid"),   # not Google's key
])
async def test_bad_credentials_are_refused(kw, reason):
    with pytest.raises(google_id.GoogleTokenError, match=reason):
        await google_id.verify(credential(**kw))


async def test_no_client_id_means_sign_in_is_off(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SIGNIN_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    with pytest.raises(google_id.GoogleTokenError, match="isn't set up"):
        await google_id.verify(credential())


# ── Public endpoints ────────────────────────────────────────────────────────

async def test_start_creates_the_lead_and_a_token_for_the_demo_practice(demo, client, db):
    r = _start(client, utm_source="google", utm_campaign="au-dental", page="/")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room"].startswith("demo-") and body["max_seconds"] == settings.WEB_DEMO_MAX_SECONDS
    assert body["practice_name"] == "Harbourline Dental" and body["first_name"] == "Jo"

    lead = await db[wd.DEMO_LEADS].find_one({"google_sub": "g-123"})
    assert lead["email"] == "dr.lee@smiles.com.au" and lead["status"] == "new" and lead["demo_count"] == 1
    assert lead["utm"] == {"source": "google", "medium": "", "campaign": "au-dental"}

    claims = api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET).verify(body["token"])
    assert claims.attributes == {"tenant_id": "ten_demo_au", "web_demo_lead": lead["_id"]}
    assert claims.video.room == body["room"] and claims.video.can_publish_data is False
    assert [a.agent_name for a in claims.room_config.agents] == [settings.LIVEKIT_AGENT_NAME]


async def test_a_second_demo_is_the_same_lead_with_first_touch_kept(demo, client, db):
    _start(client, utm_source="google")
    _start(client, utm_source="linkedin")
    leads = await db[wd.DEMO_LEADS].find({}).to_list(10)
    assert len(leads) == 1 and leads[0]["demo_count"] == 2 and leads[0]["utm"]["source"] == "google"


async def test_bad_credential_is_401_and_creates_nothing(demo, client, db):
    assert _start(client, cred=credential(aud="other")).status_code == 401
    assert await db[wd.DEMO_LEADS].count_documents({}) == 0


async def test_demo_off_or_region_without_a_practice_is_503(demo, client, monkeypatch):
    assert _start(client, region="uk").status_code == 503                  # no UK demo practice
    monkeypatch.setattr(settings, "WEB_DEMO_ENABLED", False)
    assert _start(client).status_code == 503
    cfg = client.get("/public/web-demo/config").json()
    assert cfg["enabled"] == {"au": False, "uk": False}


async def test_config_reports_regions(demo, client):
    assert client.get("/public/web-demo/config").json()["enabled"] == {"au": True, "uk": False}


async def test_demos_per_account_are_limited(demo, client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_DEMO_PER_ACCOUNT_DAY", 2)
    assert [_start(client).status_code for _ in range(3)] == [200, 200, 429]
    assert "tomorrow" in _start(client).json()["detail"]
    assert _start(client, cred=credential(sub="g-other", email="b@b.com")).status_code == 200   # someone else


async def test_global_daily_cap(demo, client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_DEMO_MAX_PER_DAY", 1)
    assert _start(client).status_code == 200
    assert _start(client, cred=credential(sub="g-2", email="c@c.com")).status_code == 429


async def test_practice_name_from_the_after_call_card(demo, client, db):
    cred = credential()
    assert client.patch("/public/web-demo/lead", json={"credential": cred, "practice_name": "Smiles"}).status_code == 404
    _start(client, cred=cred)
    assert client.patch("/public/web-demo/lead", json={"credential": cred, "practice_name": " Lee Smiles "}).json()["ok"]
    assert (await db[wd.DEMO_LEADS].find_one({"google_sub": "g-123"}))["practice_name"] == "Lee Smiles"


# ── The call on the lead ────────────────────────────────────────────────────

async def test_a_finished_demo_call_goes_on_the_lead_with_its_summary_sealed(demo, client, db):
    _start(client)
    lead = await db[wd.DEMO_LEADS].find_one({})
    started = utcnow() - timedelta(seconds=95)
    await wd.record_session(db, lead["_id"], call_id="demo-abc", tenant_id="ten_demo_au", started_at=started,
                            duration_s=94.6, outcome="booked", summary="Booked a check-up for Tuesday.",
                            tools_used=["check_availability", "book_appointment", "check_availability"])
    lead = await db[wd.DEMO_LEADS].find_one({})
    assert lead["talk_seconds"] == 95 and lead["call_count"] == 1
    stored = lead["sessions"][0]
    assert "summary" not in stored and stored["summary_enc"]                  # encrypted at rest
    assert stored["tools_used"] == ["check_availability", "book_appointment"]
    assert wd.lead_row(lead)["last_summary"] == "Booked a check-up for Tuesday."


async def test_the_recorder_updates_the_lead_only_for_demo_calls(demo, db, monkeypatch):
    from agent.persistence import CallRecorder
    from agent.profile import VoiceProfile
    from agent.state import CallState

    _lead = await wd.upsert_lead(db, await google_id.verify(credential()), region="au")
    state = CallState(call_id="demo-xyz", profile=VoiceProfile(tenant=demo), channel="browser",
                      web_demo_lead_id=_lead["_id"])
    rec = CallRecorder(state)
    rec.summary = "Asked about a clean."
    await rec._update_demo_lead()
    assert (await db[wd.DEMO_LEADS].find_one({}))["call_count"] == 1
    await CallRecorder(CallState(call_id="c2", profile=VoiceProfile(tenant=demo)))._update_demo_lead()
    assert (await db[wd.DEMO_LEADS].find_one({}))["call_count"] == 1


# ── Platform: Demo leads ────────────────────────────────────────────────────

async def _admin(client, monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    token = client.post("/auth/login", json={"email": "root@platform.io", "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_platform_lists_reads_updates_exports_and_deletes_leads(demo, client, db, monkeypatch):
    _start(client)
    lead = await db[wd.DEMO_LEADS].find_one({})
    started = utcnow()
    await Repos(db, "ten_demo_au").calls.insert_one({
        "call_id": "demo-1", "started_at": started, "status": "completed",
        "transcript": [{"role": "assistant", "text": "Hi, Harbourline Dental."}, {"role": "user", "text": "Hi!"}]})
    await wd.record_session(db, lead["_id"], call_id="demo-1", tenant_id="ten_demo_au", started_at=started,
                            duration_s=40, outcome="completed", summary="Said hello.")
    h = await _admin(client, monkeypatch)

    rows = client.get("/platform/demo-leads", headers=h).json()
    assert [r["email"] for r in rows["leads"]] == ["dr.lee@smiles.com.au"] and rows["counts"]["new"] == 1
    assert rows["leads"][0]["last_summary"] == "Said hello."
    assert client.get("/platform/demo-leads", params={"q": "nobody"}, headers=h).json()["leads"] == []

    detail = client.get(f"/platform/demo-leads/{lead['_id']}", headers=h).json()
    assert [t["text"] for t in detail["sessions"][0]["transcript"]] == ["Hi, Harbourline Dental.", "Hi!"]
    assert "summary_enc" not in detail["sessions"][0]

    r = client.patch(f"/platform/demo-leads/{lead['_id']}", json={"status": "qualified", "notes": "Call Monday"}, headers=h)
    assert r.json()["lead"]["status"] == "qualified"
    assert client.patch(f"/platform/demo-leads/{lead['_id']}", json={"status": "hot"}, headers=h).status_code == 422

    csv_text = client.get("/platform/demo-leads.csv", headers=h).text
    assert "dr.lee@smiles.com.au" in csv_text and csv_text.startswith("name,email")

    assert client.delete(f"/platform/demo-leads/{lead['_id']}", headers=h).json()["calls_deleted"] == 1
    assert await db[wd.DEMO_LEADS].count_documents({}) == 0
    assert await Repos(db, "ten_demo_au").calls.count({}) == 0


async def test_demo_leads_are_platform_admin_only(demo, client, monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id="ten_demo_au", role="owner")
    token = client.post("/auth/login", json={"email": "owner@a.com", "password": PASSWORD}).json()["access_token"]
    assert client.get("/platform/demo-leads", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.get("/platform/demo-leads").status_code == 401


def test_csv_cells_cannot_be_formulas():
    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _cell("+61") == "'+61" and _cell("Jo") == "Jo"
    assert _cell(None) == ""


# ── Clean-up ────────────────────────────────────────────────────────────────

async def test_demo_practice_data_is_cleared_but_other_practices_are_untouched(demo, db):
    other = await create_tenant(name="Real Dental", vertical="dental", country="AU", timezone="Australia/Sydney",
                                tenant_id="ten_real", seed_template=False)
    now = utcnow()
    old, fresh = now - timedelta(hours=30), now - timedelta(hours=2)
    for tid in (demo.id, other.id):
        repos = Repos(db, tid)
        await repos.appointments.insert_one({"_id": f"a-old-{tid}", "appointment_id": f"a-old-{tid}", "created_at": old})
        await repos.appointments.insert_one({"_id": f"a-new-{tid}", "appointment_id": f"a-new-{tid}", "created_at": fresh})
        await repos.contacts.update_one({"phone": "+61400000000"}, {"$set": {"updated_at": old}}, upsert=True)
        await repos.calls.insert_one({"call_id": f"c-old-{tid}", "started_at": now - timedelta(days=40)})
    removed = await wd.purge_demo_tenant(db, demo.id, now)
    assert removed == 3
    assert await Repos(db, demo.id).appointments.count({}) == 1
    assert await Repos(db, other.id).appointments.count({}) == 2
    assert await wd.purge_demo_tenant(db, other.id, now) == 0                 # never a real practice


async def test_leads_past_retention_are_deleted(db):
    now = utcnow()
    await db[wd.DEMO_LEADS].insert_many([
        {"_id": "dml_old", "google_sub": "a", "last_seen_at": now - timedelta(days=settings.WEB_DEMO_LEAD_RETENTION_DAYS + 1)},
        {"_id": "dml_new", "google_sub": "b", "last_seen_at": now - timedelta(days=3)}])
    assert await wd.purge_leads(db, now) == 1
    assert [d["_id"] for d in await db[wd.DEMO_LEADS].find({}).to_list(5)] == ["dml_new"]
