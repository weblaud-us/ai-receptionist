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

    monkeypatch.setattr(settings, "CLINIKO_USER_AGENT", "AI Receptionist (ops@example.com)")
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
    assert req.url.host == "api.au2.cliniko.com" and req.headers["User-Agent"].startswith("AI Receptionist")
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
    assert hits["n"] == 2                                  # one retry for a read, then give up

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
