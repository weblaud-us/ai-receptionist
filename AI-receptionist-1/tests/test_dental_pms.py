"""Dental Phase 3: Cliniko / Dentally adapters behind the scheduling tools (httpx.MockTransport, no network)."""

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List
from zoneinfo import ZoneInfo

import httpx
import pytest

from core.notifications import drain
from core.repositories import Repos
from core.services import pms_sync
from core.services import scheduling_provider as sp
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool
from core.integrations import cliniko

SYD = "Australia/Sydney"
KEY = "MS0xMjM0NTY3ODkwYWJjZGVm-au2"
CALLER = "+61412345678"
# Tuesday 24 Nov 2026, 10:00 Sydney = 23:00 UTC Monday.
SLOT_1 = "2026-11-23T23:00:00Z"
SLOT_2 = "2026-11-24T00:00:00Z"
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)   # Monday 09:00


class FakePMS:
    """A tiny Cliniko/Dentally: records every request; `fail` maps (method, path-prefix) → behaviour."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.fail: Dict[tuple, Callable[[httpx.Request], Any]] = {}
        self.created: List[Dict[str, Any]] = []
        self.cancelled: List[str] = []
        self.patients: List[Dict[str, Any]] = []
        self.existing_appointments: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for (method, prefix), behaviour in list(self.fail.items()):
            if request.method == method and path.startswith(prefix):
                out = behaviour(request)
                if out is not None:
                    return out
        return self.route(request, path)

    def route(self, request: httpx.Request, path: str) -> httpx.Response:
        m = request.method
        if path == "/v1/businesses":
            return httpx.Response(200, json={"businesses": [{"id": "b1", "business_name": "Harbour Smiles"}], "links": {}})
        if path == "/v1/practitioners":
            return httpx.Response(200, json={"practitioners": [
                {"id": "p1", "first_name": "Ava", "last_name": "Nguyen", "active": True},
                {"id": "p2", "first_name": "Ben", "last_name": "Cole", "active": True}], "links": {}})
        if path == "/v1/appointment_types":
            return httpx.Response(200, json={"appointment_types": [
                {"id": "at1", "name": "Implant consultation", "duration_in_minutes": 45, "archived_at": None},
                {"id": "at9", "name": "Old type", "duration_in_minutes": 30, "archived_at": "2025-01-01T00:00:00Z"}],
                "links": {}})
        if path.endswith("/available_times"):
            pid = path.split("/practitioners/")[1].split("/")[0]
            times = [SLOT_1, SLOT_2] if pid == "p1" else [SLOT_2]
            return httpx.Response(200, json={"available_times": [{"appointment_start": t} for t in times], "links": {}})
        if path == "/v1/patients" and m == "GET":
            return httpx.Response(200, json={"patients": self.patients, "links": {}})
        if path == "/v1/patients" and m == "POST":
            body = json.loads(request.content)
            self.patients.append({"id": "pat1", **body})
            return httpx.Response(201, json={"id": "pat1"})
        if path == "/v1/individual_appointments" and m == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            return httpx.Response(201, json={"id": f"ia{len(self.created)}"})
        if path == "/v1/individual_appointments" and m == "GET":
            return httpx.Response(200, json={"individual_appointments": self.existing_appointments, "links": {}})
        if path.endswith("/cancel") and m == "PATCH":
            self.cancelled.append(path.split("/")[3])
            return httpx.Response(204)
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def pms(monkeypatch):
    fake = FakePMS()
    monkeypatch.setattr(sp, "TRANSPORT", httpx.MockTransport(fake))
    from config import settings

    monkeypatch.setattr(settings, "CLINIKO_USER_AGENT", "LaudLine (ops@example.com)")
    return fake


@pytest.fixture(autouse=True)
async def _quiet(monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa

    async def fake(*a, **k):
        return True

    monkeypatch.setattr(sms, "send_sms", fake)
    monkeypatch.setattr(wa, "send_text", fake)
    invalidate()
    yield
    await drain_background_tasks()
    await drain()
    invalidate()


async def _connected(db, pms_fake, tenant_id="ten_pms"):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", currency="AUD",
                            tenant_id=tenant_id, status="active", seed_template=False,
                            extra={"hours": [{"dow": d, "open": "08:00", "close": "18:00"} for d in range(7)],
                                   "scheduling": {"min_notice_minutes": 0}})
    await pms_sync.connect(t, "cliniko", KEY, user_id="usr_1")
    return await resolve_by_id(t.id)


def _ctx(t, call_id="call-pms"):
    return ToolContext(channel="voice", caller_phone=CALLER, call_id=call_id, tenant=t, now=NOW)


async def _call(ctx, name, args):
    return json.loads(await run_tool(ctx, name, args))


# ── Client basics ────────────────────────────────────────

def test_shard_comes_from_the_api_key():
    assert cliniko.shard_of("abc-au2") == "au2" and cliniko.shard_of("abc-uk1") == "uk1"
    assert cliniko.shard_of("abc") == "au1"
    assert cliniko.base_url("abc-uk1") == "https://api.uk1.cliniko.com/v1"


async def test_connect_stores_the_key_encrypted_and_mirrors_dentists_and_types(db, pms):
    t = await _connected(db, pms)
    settings_ = sp.pms_settings(t)
    assert settings_["connected"] and settings_["provider"] == "cliniko" and settings_["business_id"] == "b1"
    assert settings_["api_key_enc"] != KEY and KEY not in json.dumps(settings_, default=str)
    req = pms.requests[0]
    assert req.url.host == "api.au2.cliniko.com" and req.headers["User-Agent"].startswith("LaudLine")
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(f"{KEY}:".encode()).decode()
    repos = Repos(db, t.id)
    dentists = await repos.resources.find({"source": "cliniko"})
    assert {(r["name"], r["pms_id"]) for r in dentists} == {("Ava Nguyen", "p1"), ("Ben Cole", "p2")}
    types = {s["pms_id"]: s for s in await repos.services.find({"source": "cliniko"})}
    assert types["at1"]["duration_min"] == 45 and types["at1"]["active"] and types["at9"]["active"] is False
    # Owner edits survive a re-sync; names follow Cliniko.
    await repos.services.update_one({"pms_id": "at1"}, {"$set": {"estimated_value_minor": 450000, "category": "implant",
                                                                 "name": "Local name"}})
    await pms_sync.sync(t)
    again = await repos.services.find_one({"pms_id": "at1"})
    assert again["estimated_value_minor"] == 450000 and again["category"] == "implant"
    assert again["name"] == "Implant consultation"
    # Another business sees none of it.
    assert await Repos(db, "ten_other").resources.count({}) == 0


async def test_bad_credentials_are_refused_and_nothing_is_stored(db, pms):
    pms.fail[("GET", "/v1/businesses")] = lambda r: httpx.Response(401)
    t = await create_tenant(name="X Dental", vertical="dental", country="AU", tenant_id="ten_bad", seed_template=False)
    with pytest.raises(sp.PMSError) as e:
        await pms_sync.connect(t, "cliniko", KEY, user_id="usr_1")
    assert not e.value.transient
    assert not sp.connected(await resolve_by_id(t.id))


# ── Availability and booking through the tools ───────────

async def test_check_availability_reads_the_cliniko_diary(db, pms):
    t = await _connected(db, pms)
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24"})
    assert r["available"] is True, r
    says = [s["say"] for s in r["slots"]]
    assert len(says) == 2 and any("10" in s for s in says)
    asked = [q for q in pms.requests if q.url.path.endswith("/available_times")]
    assert {q.url.path.split("/practitioners/")[1].split("/")[0] for q in asked} == {"p1", "p2"}
    assert asked[0].url.params["from"] == "2026-11-24"


async def test_booking_creates_the_patient_and_appointment_in_cliniko_and_mirrors_it(db, pms):
    t = await _connected(db, pms)
    slots = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                         "resource": "Ava Nguyen"}))["slots"]
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slots[0]["slot_id"], "name": "Sam Smith",
                                                  "service": "Implant consultation", "notes": "missing molar"})
    assert r["success"], r
    body = pms.created[0]
    assert body["patient_id"] == "pat1" and body["practitioner_id"] == "p1" and body["appointment_type_id"] == "at1"
    assert body["business_id"] == "b1" and body["starts_at"] == SLOT_1
    assert body["ends_at"] == "2026-11-23T23:45:00Z"
    assert pms.patients[0]["last_name"] == "Smith" and pms.patients[0]["patient_phone_numbers"][0]["number"] == CALLER
    appt = await Repos(db, t.id).appointments.find_one({"appointment_id": r["appointment_id"]})
    assert appt["external"] == {"provider": "cliniko", "id": "ia1", "patient_id": "pat1"}
    # The same patient again: found by surname and phone, not created twice.
    slots = (await _call(_ctx(t, "call-2"), "check_availability", {"service": "Implant consultation",
                                                                   "date": "2026-11-24", "resource": "Ben Cole"}))["slots"]
    pms.patients[0]["patient_phone_numbers"] = [{"number": "0412 345 678"}]
    r2 = await _call(_ctx(t, "call-2"), "book_appointment", {"slot_id": slots[0]["slot_id"], "name": "Sam Smith",
                                                             "service": "Implant consultation"})
    assert r2["success"] and len(pms.patients) == 1 and pms.created[1]["patient_id"] == "pat1"


async def test_a_slow_or_failing_diary_means_take_a_callback(db, pms):
    t = await _connected(db, pms)
    hits = {"n": 0}

    def flaky(request):
        hits["n"] += 1
        return httpx.Response(503)

    pms.fail[("GET", "/v1/businesses/b1/practitioners")] = flaky
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24"})
    assert r["error"] == "pms_unavailable" and "take_message" in r["message"]
    assert hits["n"] == 4                                  # both dentists asked at once, one retry each

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    pms.fail[("GET", "/v1/businesses/b1/practitioners")] = timeout
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24"})
    assert r["error"] == "pms_unavailable"


async def test_rate_limit_with_a_short_retry_after_is_retried(db, pms):
    t = await _connected(db, pms)
    state = {"limited": True}

    def once(request):
        if state["limited"]:
            state["limited"] = False
            return httpx.Response(429, headers={"Retry-After": "0"})
        return None

    pms.fail[("GET", "/v1/businesses/b1/practitioners")] = once
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24"})
    assert r["available"] is True


async def test_a_create_that_times_out_but_went_through_is_not_booked_twice(db, pms):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    pms.fail[("POST", "/v1/individual_appointments")] = timeout
    pms.existing_appointments = [{"id": "ia77"}]
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith", "service": "Implant consultation"})
    assert r["success"], r
    appt = await Repos(db, t.id).appointments.find_one({"appointment_id": r["appointment_id"]})
    assert appt["external"]["id"] == "ia77"
    posts = [q for q in pms.requests if q.method == "POST" and q.url.path == "/v1/individual_appointments"]
    assert len(posts) == 1                                  # writes are never retried blindly
    # And when it really didn't go through: callback, slot released.
    pms.existing_appointments = []
    slot2 = (await _call(_ctx(t, "c3"), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                               "resource": "Ben Cole"}))["slots"][0]["slot_id"]
    r = await _call(_ctx(t, "c3"), "book_appointment", {"slot_id": slot2, "name": "Jo Lee", "service": "Implant consultation"})
    assert r["error"] == "pms_unavailable"
    assert await Repos(db, t.id).slot_locks.count({"resource_id": slot2.split("@")[0]}) == 0


async def test_unmapped_services_are_not_booked_against_cliniko(db, pms):
    t = await _connected(db, pms)
    await Repos(db, t.id).services.insert_one({"_id": "svc_local", "name": "Whitening", "duration_min": 60,
                                               "active": True, "ai_bookable": True})
    r = await _call(_ctx(t), "check_availability", {"service": "Whitening", "date": "2026-11-24"})
    assert r["error"] == "pms_unmapped"


async def test_cancel_goes_to_cliniko_first(db, pms):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    booked = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith",
                                                       "service": "Implant consultation"})
    pms.fail[("PATCH", "/v1/individual_appointments")] = lambda r: httpx.Response(503)
    r = await _call(_ctx(t), "cancel_appointment", {"appointment_id": booked["appointment_id"], "name": "Sam Smith"})
    assert r["error"] == "pms_unavailable" and "NOT cancelled" in r["message"]
    appt = await Repos(db, t.id).appointments.find_one({"appointment_id": booked["appointment_id"]})
    assert appt["status"] == "confirmed"
    del pms.fail[("PATCH", "/v1/individual_appointments")]
    r = await _call(_ctx(t), "cancel_appointment", {"appointment_id": booked["appointment_id"], "name": "Sam Smith"})
    assert r["success"] and pms.cancelled == ["ia1"]


async def test_disconnect_returns_to_built_in_scheduling(db, pms):
    t = await _connected(db, pms)
    await pms_sync.disconnect(t)
    fresh = await resolve_by_id(t.id)
    assert not sp.connected(fresh) and sp.provider_for(fresh) is None
    assert "api_key_enc" not in sp.pms_settings(fresh)
    assert await Repos(db, t.id).resources.count({"source": "cliniko", "active": True}) == 0


async def test_sync_is_due_every_few_hours():
    from core.tenancy.models import TenantConfig

    t = TenantConfig(_id="t", name="x", slug="x", vertical="dental",
                     integrations={"pms": {"provider": "cliniko", "connected": True, "api_key_enc": "x",
                                           "last_sync_at": NOW}})
    assert not pms_sync.due(t, NOW + timedelta(hours=1)) and pms_sync.due(t, NOW + timedelta(hours=7))
    assert not pms_sync.due(TenantConfig(_id="u", name="y", slug="y"), NOW)


# ── Dentally ─────────────────────────────────────────────

class FakeDentally:
    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.created: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, m = request.url.path, request.method
        if path == "/v1/sites":
            return httpx.Response(200, json={"sites": [{"id": 11, "name": "Kensington"}]})
        if path == "/v1/practitioners":
            return httpx.Response(200, json={"practitioners": [{"id": 5, "active": True,
                                                                "user": {"first_name": "Priya", "last_name": "Shah"}}]})
        if path == "/v1/appointments/availability":
            return httpx.Response(200, json={"availability": [
                {"start_time": "2026-11-24T10:00:00Z", "finish_time": "2026-11-24T11:30:00Z", "practitioner_id": 5}]})
        if path == "/v1/patients" and m == "GET":
            return httpx.Response(200, json={"patients": []})
        if path == "/v1/patients" and m == "POST":
            return httpx.Response(201, json={"patient": {"id": 900}})
        if path == "/v1/appointments" and m == "POST":
            self.created.append(json.loads(request.content))
            return httpx.Response(201, json={"appointment": {"id": 4242}})
        return httpx.Response(404)


async def test_dentally_books_local_services_by_duration(db, monkeypatch):
    from config import settings

    fake = FakeDentally()
    monkeypatch.setattr(sp, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "PMS_DENTALLY_ENABLED", True)
    t = await create_tenant(name="Kensington Dental", vertical="dental", timezone="Europe/London", country="GB",
                            currency="GBP", tenant_id="ten_dentally", status="active", seed_template=False,
                            extra={"scheduling": {"min_notice_minutes": 0}})
    await Repos(db, t.id).services.insert_one({"_id": "svc_exam", "name": "New patient examination", "duration_min": 45,
                                               "active": True, "ai_bookable": True})
    await pms_sync.connect(t, "dentally", "dentally-token-123456", user_id="usr_1")
    t = await resolve_by_id(t.id)
    assert sp.pms_settings(t)["site_id"] == "11"
    assert fake.requests[0].headers["Authorization"] == "Bearer dentally-token-123456"
    ctx = ToolContext(channel="voice", caller_phone="+447700900123", call_id="call-uk", tenant=t,
                      now=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc))
    r = await _call(ctx, "check_availability", {"service": "New patient examination", "date": "2026-11-24"})
    assert r["available"] and len(r["slots"]) == 1, r
    avail = next(q for q in fake.requests if q.url.path == "/v1/appointments/availability")
    assert avail.url.params["duration"] == "45" and avail.url.params.get_list("practitioner_ids[]") == ["5"]
    b = await _call(ctx, "book_appointment", {"slot_id": r["slots"][0]["slot_id"], "name": "Olivia Brown",
                                              "service": "New patient examination"})
    assert b["success"], b
    sent = fake.created[0]["appointment"]
    assert sent["patient_id"] == "900" and sent["practitioner_id"] == "5" and sent["reason"] == "New patient examination"
    assert sent["start_time"] == "2026-11-24T10:00:00Z" and sent["finish_time"] == "2026-11-24T10:45:00Z"


async def test_dentally_is_not_offered_until_enabled(db):
    t = await create_tenant(name="Y Dental", vertical="dental", country="GB", tenant_id="ten_nodent", seed_template=False)
    with pytest.raises(ValueError, match="isn't available"):
        await pms_sync.connect(t, "dentally", "dentally-token-123456", user_id="usr_1")


# ── API: the key never leaves the server ─────────────────

async def test_api_connects_and_never_returns_the_key(db, client, pms, monkeypatch):
    from config import settings
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", tenant_id="ten_api",
                        seed_template=False)
    await auth_service.create_user(email="owner@hs.au", password="correct horse battery", tenant_id="ten_api",
                                   role="owner")
    token = client.post("/auth/login", json={"email": "owner@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    r = client.put("/t/integrations/pms", headers=h, json={"provider": "cliniko", "api_key": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["sync"]["practitioners"] == 2
    status = client.get("/t/integrations", headers=h).json()["pms"]
    assert status["connected"] and status["provider"] == "cliniko" and status["available"]["cliniko"] is True
    for body in (client.get("/t/integrations", headers=h).text, client.get("/t/context", headers=h).text):
        assert "api_key_enc" not in body and KEY not in body
    assert client.post("/t/integrations/pms/sync", headers=h).status_code == 200
    assert client.delete("/t/integrations/pms", headers=h).status_code == 200
    assert client.get("/t/integrations", headers=h).json()["pms"]["connected"] is False


async def test_dashboard_slots_turn_a_pms_refusal_into_a_409_not_a_500(db, client, pms, monkeypatch):
    from config import settings
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", tenant_id="ten_api",
                        seed_template=False)
    await auth_service.create_user(email="owner@hs.au", password="correct horse battery", tenant_id="ten_api",
                                   role="owner")
    token = client.post("/auth/login", json={"email": "owner@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.put("/t/integrations/pms", headers=h, json={"provider": "cliniko", "api_key": KEY}).status_code == 200
    await Repos(db, "ten_api").services.insert_one({"_id": "svc_local", "name": "Whitening", "duration_min": 60,
                                                    "active": True, "ai_bookable": True})
    r = client.get("/t/slots", headers=h, params={"service_id": "svc_local", "day": "2026-11-24"})
    assert r.status_code == 409, r.text
    assert "can't be booked automatically" in r.json()["detail"]


# ── Audit P0-09 / P0-16: no split brain, no locks left behind ──────────────

async def test_a_failed_local_save_cancels_the_pms_booking(db, pms, monkeypatch):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    from core.repositories.repos import AppointmentsRepo

    async def boom(self, doc):
        raise RuntimeError("primary stepped down")

    monkeypatch.setattr(AppointmentsRepo, "insert_one", boom)
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith",
                                                  "service": "Implant consultation"})
    assert not r.get("success")
    assert pms.created and pms.cancelled == ["ia1"]           # created in Cliniko, then undone there
    assert await Repos(db, t.id).slot_locks.count({}) == 0     # and nothing held here


async def test_a_failed_rollback_alerts_the_practice(db, pms, monkeypatch):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    from core import notifications
    from core.repositories.repos import AppointmentsRepo

    alerts: list = []

    async def boom(self, doc):
        raise RuntimeError("disk full")

    async def owner(t, event, text, **kw):
        alerts.append((text, kw))
        return {}

    monkeypatch.setattr(AppointmentsRepo, "insert_one", boom)
    monkeypatch.setattr(notifications, "notify_owner", owner)
    pms.fail[("PATCH", "/v1/individual_appointments")] = lambda r: httpx.Response(503)
    await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith", "service": "Implant consultation"})
    await drain()
    assert alerts and "Sam Smith" in alerts[0][0] and alerts[0][1].get("urgent") is True


async def _failed_rollback(db, pms, monkeypatch):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    from core import notifications
    from core.repositories.repos import AppointmentsRepo

    alerts: list = []

    async def boom(self, doc):
        raise RuntimeError("disk full")

    async def owner(t, event, text, **kw):
        alerts.append(text)
        return {}

    monkeypatch.setattr(AppointmentsRepo, "insert_one", boom)
    monkeypatch.setattr(notifications, "notify_owner", owner)
    pms.fail[("PATCH", "/v1/individual_appointments")] = lambda r: httpx.Response(503)
    await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith", "service": "Implant consultation"})
    await drain()
    return t, alerts


async def test_a_failed_rollback_keeps_being_retried_until_the_pms_cancels_it(db, pms, monkeypatch):
    from core.services import pms_compensation

    t, _ = await _failed_rollback(db, pms, monkeypatch)
    rows = await Repos(db, t.id).pms_compensations.find({})
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["external_id"] == "ia1"
    assert "Sam" not in json.dumps(rows[0], default=str)              # no patient details kept
    assert await pms_compensation.run_due(t, NOW) == 0                 # not due yet
    aware = pms_compensation._aware
    later = aware(rows[0]["next_at"]) + timedelta(seconds=1)
    assert await pms_compensation.run_due(t, later) == 0               # PMS still down: try again later
    row = await Repos(db, t.id).pms_compensations.find_one({})
    assert row["status"] == "pending" and row["attempts"] == 1 and aware(row["next_at"]) > later
    pms.fail.clear()                                                   # the diary is back
    assert await pms_compensation.run_due(t, aware(row["next_at"]) + timedelta(seconds=1)) == 1
    assert pms.cancelled == ["ia1"]
    assert (await Repos(db, t.id).pms_compensations.find_one({}))["status"] == "done"


async def test_a_rollback_that_never_succeeds_gives_up_after_a_day_and_alerts_again(db, pms, monkeypatch):
    from core.services import pms_compensation

    t, alerts = await _failed_rollback(db, pms, monkeypatch)
    row = await Repos(db, t.id).pms_compensations.find_one({})
    await pms_compensation.run_due(t, pms_compensation._aware(row["created_at"]) + timedelta(hours=25))
    assert (await Repos(db, t.id).pms_compensations.find_one({}))["status"] == "gave_up"
    assert len(alerts) == 2 and "ia1" in alerts[1] and "Action needed" in alerts[1]


async def test_orphaned_locks_from_a_crash_are_freed_but_live_ones_are_not(db, pms):
    from core.services import availability as av
    from core.services.scheduling import SchedulingService

    t = await _connected(db, pms)
    repos = Repos(db, t.id)
    ava = await repos.resources.find_one({"name": "Ava Nguyen"})
    start = datetime.fromisoformat(SLOT_1.replace("Z", "+00:00"))
    blocks = av.lock_blocks(start, start + timedelta(minutes=45))
    svc = SchedulingService(repos, t)

    # A booking that's still in flight (fresh locks, no appointment yet) keeps its slot.
    await repos.slot_locks.insert_many([{"resource_id": ava["_id"], "block": b, "appointment_id": "apt_inflight"}
                                        for b in blocks])
    assert await svc._clear_orphan_locks(ava["_id"], blocks) is False

    # The same locks 20 minutes later with still no appointment: a crash left them. Freed.
    await repos.slot_locks.update_many({"appointment_id": "apt_inflight"},
                                       {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(minutes=20)}})
    assert await svc._clear_orphan_locks(ava["_id"], blocks) is True
    assert await repos.slot_locks.count({"appointment_id": "apt_inflight"}) == 0


async def test_old_locks_of_a_live_appointment_are_kept(db, pms):
    from core.services.scheduling import SchedulingService

    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    booked = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith",
                                                       "service": "Implant consultation"})
    repos = Repos(db, t.id)
    await repos.slot_locks.update_many({}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(days=1)}})
    locks = await repos.slot_locks.find({"appointment_id": booked["appointment_id"]})
    svc = SchedulingService(repos, t)
    assert await svc._clear_orphan_locks(locks[0]["resource_id"], [lk["block"] for lk in locks]) is False
    assert await repos.slot_locks.count({"appointment_id": booked["appointment_id"]}) == len(locks)


async def test_a_pms_booking_never_locks_any(db, pms):
    from core.services.scheduling import SchedulingError, SchedulingService

    t = await _connected(db, pms)
    svc = SchedulingService(Repos(db, t.id), t)
    with pytest.raises(SchedulingError) as e:
        await svc.book(resource_id="any", start=datetime.fromisoformat(SLOT_1.replace("Z", "+00:00")),
                       service=None, name="Sam", phone=CALLER, now=NOW)
    assert e.value.code in ("unknown_resource", "pms_unmapped")
    assert not await Repos(db, t.id).slot_locks.find({"resource_id": "any"})


# ── Plan A1.3: availability cache and diary request budget ─────────────────

def _availability_requests(pms_fake) -> int:
    return sum(1 for r in pms_fake.requests if r.url.path.endswith("/available_times"))


async def test_a_second_availability_question_is_answered_from_the_cache(db, pms):
    t = await _connected(db, pms)
    args = {"service": "Implant consultation", "date": "2026-11-24"}
    first = await _call(_ctx(t), "check_availability", args)
    asked = _availability_requests(pms)
    assert first["slots"] and asked > 0
    again = await _call(_ctx(t), "check_availability", args)
    assert again["slots"] == first["slots"] and _availability_requests(pms) == asked   # no new diary request


async def test_booking_asks_the_diary_itself_and_then_forgets_the_cache(db, pms):
    t = await _connected(db, pms)
    args = {"service": "Implant consultation", "date": "2026-11-24", "resource": "Ava Nguyen"}
    slot = (await _call(_ctx(t), "check_availability", args))["slots"][0]["slot_id"]
    before = _availability_requests(pms)
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith",
                                                  "service": "Implant consultation"})
    assert r["success"]
    assert _availability_requests(pms) > before                # the booking re-checked live
    after_booking = _availability_requests(pms)
    await _call(_ctx(t, "call-2"), "check_availability", args)
    assert _availability_requests(pms) > after_booking         # the cache was dropped after the booking


async def test_a_stale_offer_is_refused_not_double_booked(db, pms):
    t = await _connected(db, pms)
    args = {"service": "Implant consultation", "date": "2026-11-24", "resource": "Ava Nguyen"}
    slot = (await _call(_ctx(t), "check_availability", args))["slots"][0]["slot_id"]

    def gone(request):                                         # the front desk took it a moment ago
        if "/practitioners/p1/" in request.url.path:
            return httpx.Response(200, json={"available_times": [{"appointment_start": SLOT_2}], "links": {}})
        return None

    pms.fail[("GET", "/v1/businesses/b1/practitioners")] = gone
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith",
                                                  "service": "Implant consultation"})
    assert r.get("error") == "taken" and not pms.created


async def test_the_cache_is_per_day_so_a_week_prefetch_answers_one_day(db, pms):
    from core.services.scheduling import SchedulingService

    t = await _connected(db, pms)
    repos = Repos(db, t.id)
    svc = SchedulingService(repos, t)
    service = await repos.services.find_one({"name": "Implant consultation"})
    week = await svc.find_slots(service=service, day_from=NOW.date(), day_to=NOW.date() + timedelta(days=6),
                                now=NOW, limit=50)
    asked = _availability_requests(pms)
    tuesday = await svc.find_slots(service=service, day_from=datetime(2026, 11, 24).date(), now=NOW, limit=50)
    assert tuesday and _availability_requests(pms) == asked
    assert {s.start for s in tuesday} <= {s.start for s in week}


async def test_the_diary_request_budget(monkeypatch):
    from core.services import pms_cache

    monkeypatch.setattr(pms_cache, "REQUESTS_PER_MINUTE", 3)
    assert [await pms_cache.spend("cliniko", "key-a") for _ in range(4)] == [True, True, True, False]
    assert await pms_cache.spend("cliniko", "key-b") is True   # another practice has its own budget


async def test_over_budget_means_take_a_callback_not_a_hang(db, pms, monkeypatch):
    from core.services import pms_cache

    t = await _connected(db, pms)
    monkeypatch.setattr(pms_cache, "REQUESTS_PER_MINUTE", 0)
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-25"})
    assert r["error"] == "pms_unavailable"


async def test_the_cache_works_through_redis_too(db, pms, redis):
    from core.services import pms_cache

    t = await _connected(db, pms)
    args = {"service": "Implant consultation", "date": "2026-11-24"}
    first = await _call(_ctx(t), "check_availability", args)
    asked = _availability_requests(pms)
    assert any(k.startswith("pmsc:") for k in await redis.keys("*"))
    pms_cache.reset_for_tests()                               # another process: no local memory, same Redis
    again = await _call(_ctx(t), "check_availability", args)
    assert again["slots"] == first["slots"] and _availability_requests(pms) == asked
    await pms_cache.invalidate(t.id)
    await _call(_ctx(t), "check_availability", args)
    assert _availability_requests(pms) > asked


async def test_prefetch_warms_the_week_for_the_most_booked_service(db, pms):
    from core.services.scheduling import prefetch_availability

    t = await _connected(db, pms)
    cached = await prefetch_availability(Repos(db, t.id), t, now=NOW)
    assert cached > 0
    asked = _availability_requests(pms)
    r = await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24"})
    assert r["slots"] and _availability_requests(pms) == asked          # the caller's question: no diary wait


async def test_prefetch_never_raises(db, pms):
    from core.services.scheduling import prefetch_availability

    t = await _connected(db, pms)
    pms.fail[("GET", "/v1/businesses/b1/practitioners")] = lambda r: httpx.Response(503)
    assert await prefetch_availability(Repos(db, t.id), t, now=NOW) == 0
    salon = await create_tenant(name="Salon", vertical="salon", tenant_id="ten_salon", seed_template=False)
    assert await prefetch_availability(Repos(db, salon.id), salon, now=NOW) == 0   # no PMS: nothing to do


# ── Audit P0-14: the right patient on a shared family phone ────────────────

def test_phone_numbers_compare_on_the_whole_national_number():
    assert sp.same_phone("+61 412 345 678", "0412 345 678")
    assert sp.same_phone("+44 7700 900123", "07700 900123")
    assert not sp.same_phone("07700 900123", "08700 900123")            # the old last-9-digits rule said equal
    assert not sp.same_phone("", "0412 345 678") and not sp.same_phone("12", "12")


def test_a_shared_phone_needs_the_first_name_and_date_of_birth_to_agree():
    from datetime import date

    family = [{"id": "mum", "first_name": "Sarah", "date_of_birth": "1980-04-02", "phones": ["0412 345 678"]},
              {"id": "kid", "first_name": "Sam", "date_of_birth": "2015-09-10", "phones": ["0412 345 678"]}]
    p = lambda name, dob=None: sp.Patient(name=name, phone=CALLER, date_of_birth=dob)  # noqa: E731
    assert sp.pick_patient(family, p("Sarah Smith")) == "mum"
    assert sp.pick_patient(family, p("Sam Smith", date(2015, 9, 10))) == "kid"
    assert sp.pick_patient(family, p("Sam Smith", date(1980, 4, 2))) is None          # name and DOB disagree
    twins = family + [{"id": "twin", "first_name": "Samuel", "date_of_birth": None, "phones": ["0412345678"]}]
    assert sp.pick_patient(twins, p("Sam Smith")) is None                              # two left: never guess
    assert sp.pick_patient(twins, p("Sam Smith", date(2015, 9, 10))) == "kid"
    assert sp.pick_patient(family, p("Jo Smith")) is None


async def test_the_date_of_birth_reaches_cliniko_for_a_new_patient(db, pms):
    t = await _connected(db, pms)
    slot = (await _call(_ctx(t), "check_availability", {"service": "Implant consultation", "date": "2026-11-24",
                                                        "resource": "Ava Nguyen"}))["slots"][0]["slot_id"]
    r = await _call(_ctx(t), "book_appointment", {"slot_id": slot, "name": "Sam Smith", "service": "Implant consultation",
                                                  "date_of_birth": "2015-09-10"})
    assert r["success"] and pms.patients[0]["date_of_birth"] == "2015-09-10"
    from core.tools.scheduling import _parse_dob

    assert _parse_dob("3020-01-01", NOW) is None and _parse_dob("10/09/2015", NOW) is None   # misheard: dropped
    assert str(_parse_dob("2015-09-10", NOW)) == "2015-09-10"
