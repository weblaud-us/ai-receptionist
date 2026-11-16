"""
Phase 2 completion: send_info_link (Task 2.7), outgoing webhooks (Task 2.8), transcript retention
(Task 2.2), post-call summaries (Task 2.2) and the voice catalogue (Task 2.10).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent import summary as call_summary
from core.auth import service as auth_service
from core.integrations import elevenlabs, webhooks
from core.notifications import drain
from core.prompts.builder import build_voice_prompt
from core.repositories import Repos
from core.services.scheduling import SchedulingService
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tenancy.models import TenantConfig
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool, tool_names_for
from services import tenant_jobs

PASSWORD = "correct horse battery"
TZ = "America/Chicago"
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def _reset(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    elevenlabs.reset_cache()
    invalidate()
    yield
    await drain_background_tasks()
    await drain()
    invalidate()


@pytest.fixture
def deliveries(monkeypatch):
    """Capture every webhook POST instead of hitting the network."""
    sent = []

    async def fake_post(url, body, headers):
        sent.append({"url": url, "body": json.loads(body), "headers": headers})
        return 500 if url.endswith("/fail") else 200

    monkeypatch.setattr(webhooks, "_post", fake_post)
    monkeypatch.setattr(webhooks, "RETRIES", 0)
    return sent


@pytest.fixture
def texts(monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa

    sent = []

    async def fake(to, text, *a, **k):
        sent.append((to, text))
        return True

    monkeypatch.setattr(wa, "send_text", fake)
    monkeypatch.setattr(sms, "send_sms", fake)
    return sent


async def _salon(db, **extra) -> TenantConfig:
    t = await create_tenant(name="Bella Salon", vertical="salon", timezone=TZ, tenant_id="ten_s",
                            extra={"phone": "+15125550000", **extra})
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "res_anna", "name": "Anna", "kind": "staff", "hours": None, "active": True})
    return t


def _ctx(t, phone="+15125550100"):
    return ToolContext(channel="voice", caller_phone=phone, call_id="call-x", tenant=t, now=NOW)


async def _tool(ctx, name, args):
    return json.loads(await run_tool(ctx, name, args))


# ── send_info_link ───────────────────────────────────────

def test_tool_is_offered_only_when_a_link_exists():
    bare = TenantConfig(_id="ten_a", name="A", slug="a", vertical="salon")
    assert "send_info_link" not in tool_names_for(bare)
    with_site = TenantConfig(_id="ten_b", name="B", slug="b", vertical="salon", website="https://b.example")
    assert "send_info_link" in tool_names_for(with_site)
    with_address = TenantConfig(_id="ten_c", name="C", slug="c", vertical="restaurant", address={"line1": "1 Main St", "city": "Austin"})
    assert "send_info_link" in tool_names_for(with_address)          # directions from the address
    assert "You can text the caller a link to: directions" in build_voice_prompt(with_address, now=NOW)
    assert "send_info_link" not in build_voice_prompt(bare, now=NOW)


async def test_send_info_link_needs_consent_then_sends_and_logs_it(db, texts):
    t = await _salon(db, links={"booking": "bella.example/book"})
    t = await resolve_by_id(t.id)
    assert t.links.booking == "https://bella.example/book"            # scheme added on save

    r = await _tool(_ctx(t), "send_info_link", {"kind": "booking", "consent": False})
    assert r["error"] == "consent_required" and not texts
    r = await _tool(_ctx(t), "send_info_link", {"kind": "menu", "consent": True})
    assert r["error"] == "no_link" and not texts
    r = await _tool(_ctx(t), "send_info_link", {"kind": "booking", "consent": True})
    assert r["success"] and texts[0][0] == "+15125550100" and "https://bella.example/book" in texts[0][1]

    contact = await Repos(db, t.id).contacts.find_one({"phone": "+15125550100"})
    assert contact["consent"]["sms"]["granted"] is True and contact["consent"]["sms"]["call_id"] == "call-x"


async def test_send_info_link_respects_do_not_contact_and_browser_calls(db, texts):
    t = await _salon(db, website="https://bella.example")
    t = await resolve_by_id(t.id)
    await Repos(db, t.id).contacts.insert_one({"_id": "con_1", "phone": "+15125550199", "do_not_contact": True})
    r = await _tool(_ctx(t, phone="+15125550199"), "send_info_link", {"kind": "website", "consent": True})
    assert r["error"] == "do_not_contact" and not texts
    r = await _tool(_ctx(t, phone=None), "send_info_link", {"kind": "website", "consent": True})
    assert r["error"] == "phone_required"
    r = await _tool(_ctx(t, phone=None), "send_info_link", {"kind": "website", "consent": True, "phone": "+15125550177"})
    assert r["success"] and texts[0][0] == "+15125550177"


# ── webhooks ─────────────────────────────────────────────

def test_signature_roundtrip_and_replay_window():
    sig = webhooks.sign("whsec_x", "1700000000", '{"a":1}')
    assert sig.startswith("v1=") and webhooks.verify("whsec_x", "1700000000", '{"a":1}', sig, now=1700000100)
    assert not webhooks.verify("whsec_x", "1700000000", '{"a":2}', sig, now=1700000100)      # body changed
    assert not webhooks.verify("whsec_y", "1700000000", '{"a":1}', sig, now=1700000100)      # wrong key
    assert not webhooks.verify("whsec_x", "1700000000", '{"a":1}', sig, now=1700009999)      # too old


def test_endpoint_model_filters_events_and_accepts_legacy_strings():
    t = TenantConfig(_id="ten_w", name="W", slug="w", integrations={"webhooks": [
        "https://hooks.zapier.com/abc",                                   # pre-Phase-2 shape
        {"url": "https://x.example/h", "events": ["message.created", "bogus.event"], "active": False},
        {"url": "not a url"},                                            # dropped, never crashes the tenant
    ]})
    hooks = t.integrations.webhooks
    assert [h.url for h in hooks] == ["https://hooks.zapier.com/abc", "https://x.example/h"]
    assert hooks[0].events == [] and hooks[0].wants("call.completed")
    assert hooks[1].events == ["message.created"] and not hooks[1].wants("message.created")   # inactive


async def test_booking_message_and_lead_fire_matching_webhooks(db, deliveries, texts):
    hooks = [{"id": "whk_all", "url": "https://x.example/all", "secret": "s1"},
             {"id": "whk_msg", "url": "https://x.example/msg", "secret": "s2", "events": ["message.created"]},
             {"id": "whk_bad", "url": "https://x.example/fail", "secret": "s3", "events": ["appointment.created"]}]
    t = await _salon(db, integrations={"webhooks": hooks})
    t = await resolve_by_id(t.id)
    t.scheduling.min_notice_minutes = 0

    svc = SchedulingService(Repos(db, t.id), t)
    service = (await svc.services())[0]
    start = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
    appt = await svc.book(resource_id="res_anna", start=start, service=service, name="Dana", phone="+15125550100", now=NOW)
    await _tool(_ctx(t), "take_message", {"caller_name": "Dana", "callback_number": "+15125550100", "message": "Call me"})
    await drain()

    by_url = {}
    for d in deliveries:
        by_url.setdefault(d["url"], []).append(d["body"]["event"])
    assert by_url["https://x.example/all"] == ["appointment.created", "message.created"]
    assert by_url["https://x.example/msg"] == ["message.created"]
    assert by_url["https://x.example/fail"] == ["appointment.created"]

    created = next(d for d in deliveries if d["url"].endswith("/all") and d["body"]["event"] == "appointment.created")
    assert created["body"]["tenant_id"] == t.id and created["body"]["data"]["appointment_id"] == appt["appointment_id"]
    assert "tenant_id" not in created["body"]["data"] and created["body"]["data"]["name"] == "Dana"
    h = created["headers"]
    assert h["X-Receptionist-Event"] == "appointment.created" and h["X-Receptionist-Delivery"] == created["body"]["id"]
    assert webhooks.verify("s1", h["X-Receptionist-Timestamp"], json.dumps(created["body"], separators=(",", ":")),
                           h["X-Receptionist-Signature"])

    raw = await db.tenants.find_one({"_id": t.id})
    status = {w["id"]: w["last_delivery"] for w in raw["integrations"]["webhooks"]}
    assert status["whk_all"]["ok"] and status["whk_all"]["status"] == 200
    assert status["whk_bad"]["ok"] is False and status["whk_bad"]["status"] == 500

    await svc.cancel(appt["appointment_id"], reason="sick")
    await drain()
    assert deliveries[-1]["body"]["event"] == "appointment.cancelled" and deliveries[-1]["body"]["data"]["cancel_reason"] == "sick"


async def test_no_webhooks_means_no_network(db, deliveries, texts):
    t = await _salon(db)
    await _tool(_ctx(await resolve_by_id(t.id)), "take_message", {"caller_name": "D", "callback_number": "+1512", "message": "m"})
    await drain()
    assert deliveries == []


async def test_webhook_api_create_list_test_delete_and_secret_shown_once(db, client, deliveries, monkeypatch):
    import routers.tenant as tenant_router

    monkeypatch.setattr(tenant_router, "_public_http_url", lambda url: url)    # no DNS in tests
    t = await _salon(db)
    await auth_service.create_user(email="owner@s.com", password=PASSWORD, tenant_id=t.id, role="owner")
    await auth_service.create_user(email="staff@s.com", password=PASSWORD, tenant_id=t.id, role="staff")
    token = client.post("/auth/login", json={"email": "owner@s.com", "password": PASSWORD}).json()["access_token"]
    staff = client.post("/auth/login", json={"email": "staff@s.com", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    assert client.get("/t/integrations/webhooks", headers={"Authorization": f"Bearer {staff}"}).status_code == 403
    r = client.post("/t/integrations/webhooks", headers=h, json={"url": "https://x.example/zap", "events": ["call.completed", "nope"]})
    assert r.status_code == 200, r.text
    secret, hook_id = r.json()["secret"], r.json()["endpoint"]["id"]
    assert secret.startswith("whsec_") and r.json()["endpoint"]["events"] == ["call.completed"]
    assert "secret" not in r.json()["endpoint"]

    listed = client.get("/t/integrations/webhooks", headers=h).json()
    assert [e["id"] for e in listed["endpoints"]] == [hook_id] and "secret" not in listed["endpoints"][0]
    assert "secret" not in json.dumps(client.get("/t/settings", headers=h).json())    # never in the tenant view
    assert "call.completed" in listed["events"]

    ping = client.post(f"/t/integrations/webhooks/{hook_id}/test", headers=h).json()
    assert ping["ok"] and ping["status"] == 200 and deliveries[-1]["body"]["event"] == "ping"
    assert webhooks.verify(secret, deliveries[-1]["headers"]["X-Receptionist-Timestamp"],
                           json.dumps(deliveries[-1]["body"], separators=(",", ":")),
                           deliveries[-1]["headers"]["X-Receptionist-Signature"])

    assert client.delete(f"/t/integrations/webhooks/{hook_id}", headers=h).json()["success"]
    assert client.get("/t/integrations/webhooks", headers=h).json()["endpoints"] == []
    assert client.delete(f"/t/integrations/webhooks/{hook_id}", headers=h).status_code == 404
    actions = [a["action"] for a in client.get("/t/audit", headers=h).json()["entries"]]
    assert "webhook.create" in actions and "webhook.delete" in actions


# ── transcript retention ─────────────────────────────────

async def test_old_transcripts_are_purged_but_records_kept(db):
    t = await _salon(db)
    t = await resolve_by_id(t.id)
    repos = Repos(db, t.id)
    old, recent = NOW - timedelta(days=100), NOW - timedelta(days=5)
    for call_id, ended in (("old", old), ("recent", recent)):
        await repos.calls.insert_one({"call_id": call_id, "status": "completed", "outcome": "booked", "summary": "s",
                                      "started_at": ended - timedelta(minutes=3), "ended_at": ended,
                                      "transcript": [{"role": "user", "text": "hi"}], "turns": [{"x": 1}]})
    await repos.calls.insert_one({"call_id": "live", "status": "in_progress", "started_at": old,
                                  "transcript": [{"role": "user", "text": "hi"}]})
    assert await tenant_jobs.purge_transcripts(t, NOW) == 1
    purged = await repos.calls.find_one({"call_id": "old"})
    assert "transcript" not in purged and "turns" not in purged and purged["summary"] == "s" and purged["outcome"] == "booked"
    assert "transcript" in await repos.calls.find_one({"call_id": "recent"})
    assert "transcript" in await repos.calls.find_one({"call_id": "live"})
    assert await tenant_jobs.purge_transcripts(t, NOW) == 0                     # idempotent

    stats = await tenant_jobs.run_once(NOW)
    assert stats["tenants"] == 1 and "purged" in stats


# ── summaries ────────────────────────────────────────────

TRANSCRIPT = [{"role": "assistant", "text": "Thanks for calling Bella Salon, this is Aria."},
              {"role": "user", "text": "Hi, I'd like a haircut on Thursday with Anna."},
              {"role": "assistant", "text": "Thursday at 3:30 with Anna is booked."}]


async def test_summary_falls_back_to_heuristic_without_an_llm(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    text = await call_summary.summarise_call(TRANSCRIPT, outcome="booked", tools_used=["check_availability", "book_appointment"],
                                             duration_s=184)
    assert text.startswith("The caller made a booking") and "haircut on Thursday" in text and "book_appointment" in text
    assert await call_summary.summarise_call([{"role": "assistant", "text": "Hello?"}], outcome=None, tools_used=[], duration_s=4) is None


async def test_summary_uses_the_llm_when_available_and_survives_its_failure(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    async def fake_llm(transcript, model=None):
        assert "Caller: Hi, I'd like a haircut" in call_summary.transcript_text(transcript)
        return "  Dana booked a haircut with Anna on Thursday at 3:30pm.  "

    monkeypatch.setattr(call_summary, "llm_summary", fake_llm)
    assert await call_summary.summarise_call(TRANSCRIPT, outcome="booked", tools_used=[], duration_s=60) == \
        "Dana booked a haircut with Anna on Thursday at 3:30pm."

    async def broken(transcript, model=None):
        raise RuntimeError("quota")

    monkeypatch.setattr(call_summary, "llm_summary", broken)
    assert (await call_summary.summarise_call(TRANSCRIPT, outcome="message", tools_used=[], duration_s=60)).startswith(
        "The caller left a message")


async def test_call_record_gets_summary_and_call_completed_webhook(db, deliveries, monkeypatch):
    from agent.persistence import CallRecorder
    from agent.profile import VoiceProfile
    from agent.state import CallState
    from config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    t = await _salon(db, integrations={"webhooks": [{"id": "whk_1", "url": "https://x.example/calls", "secret": "k"}]})
    t = await resolve_by_id(t.id)
    profile = VoiceProfile(tenant=t)
    state = CallState(call_id="call-sum", profile=profile, caller_phone="+15125550100")
    state.transcript.extend(TRANSCRIPT)
    state.set_outcome("booked")
    recorder = CallRecorder(state)
    await recorder.finish("caller hung up")
    await drain()

    doc = await Repos(db, t.id).calls.find_one({"call_id": "call-sum"})
    assert doc["status"] == "completed" and doc["summary"].startswith("The caller made a booking")
    assert deliveries and deliveries[-1]["body"]["event"] == "call.completed"
    data = deliveries[-1]["body"]["data"]
    assert data["call_id"] == "call-sum" and data["outcome"] == "booked" and data["summary"] == doc["summary"]
    assert "transcript" not in data


# ── voices ───────────────────────────────────────────────

async def test_voice_catalogue_is_cached_and_shaped(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "xi-test")
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return [{"voice_id": "v1", "name": "Rachel", "preview_url": "https://cdn/x.mp3", "gender": "female",
                 "accent": "american", "age": None, "description": None, "category": "premade"}]

    monkeypatch.setattr(elevenlabs, "fetch_voices", fake_fetch)
    assert (await elevenlabs.list_voices(now=1000))[0]["name"] == "Rachel"
    await elevenlabs.list_voices(now=1500)
    assert calls["n"] == 1                                                    # served from cache
    await elevenlabs.list_voices(now=1000 + elevenlabs.CACHE_TTL_S + 1)
    assert calls["n"] == 2

    async def broken():
        raise RuntimeError("down")

    monkeypatch.setattr(elevenlabs, "fetch_voices", broken)
    assert (await elevenlabs.list_voices(now=99_999))[0]["voice_id"] == "v1"   # stale beats empty
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "")
    assert await elevenlabs.list_voices() == []


def test_voice_shape_drops_incomplete_rows():
    assert elevenlabs._shape({"name": "no id"}) is None
    v = elevenlabs._shape({"voice_id": "v", "name": "N", "labels": {"gender": "male", "description": "deep"}})
    assert v["gender"] == "male" and v["description"] == "deep" and v["preview_url"] is None


async def test_voices_endpoint(db, client, monkeypatch):
    from config import settings

    t = await _salon(db)
    await auth_service.create_user(email="owner@s.com", password=PASSWORD, tenant_id=t.id, role="owner")
    token = client.post("/auth/login", json={"email": "owner@s.com", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "")
    off = client.get("/t/voices", headers=h).json()
    assert off["available"] is False and off["voices"] == []

    async def fake_fetch():
        return [{"voice_id": "v1", "name": "Rachel", "preview_url": "https://cdn/x.mp3", "gender": None, "accent": None,
                 "age": None, "description": None, "category": "premade"}]

    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "xi-test")
    monkeypatch.setattr(elevenlabs, "fetch_voices", fake_fetch)
    on = client.get("/t/voices", headers=h).json()
    assert on["available"] and on["voices"][0]["voice_id"] == "v1"
    assert client.get("/t/voices?provider=openai", headers=h).json()["available"] is False


# ── settings surface ─────────────────────────────────────

async def test_links_and_retention_are_editable_from_the_dashboard(db, client):
    t = await _salon(db)
    await auth_service.create_user(email="owner@s.com", password=PASSWORD, tenant_id=t.id, role="owner")
    token = client.post("/auth/login", json={"email": "owner@s.com", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.patch("/t/settings", headers=h, json={"links": {"booking": "bella.example/book", "menu": ""},
                                                     "agent": {"transcript_retention_days": 30}})
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["links"]["booking"] == "https://bella.example/book"
    assert r.json()["tenant"]["agent"]["transcript_retention_days"] == 30
    assert client.patch("/t/settings", headers=h, json={"agent": {"transcript_retention_days": 0}}).status_code == 422
    assert "send_info_link" in tool_names_for(await resolve_by_id(t.id))
