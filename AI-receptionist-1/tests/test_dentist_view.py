"""Plan E: a Dentist role that sees only their own day, staff reassigning appointments, a regular dentist."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import settings
from core.auth import service as auth_service
from core.repositories import Repos
from core.services import my_day
from core.services import scheduling_provider as sp
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from tests.test_dental_pms import KEY, FakePMS

PASSWORD = "correct horse battery"
SYD = "Australia/Sydney"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    my_day.reset_for_tests()
    invalidate()
    yield
    invalidate()


def _login(client, email):
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _practice(db, tenant_id="ten_dv"):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone=SYD, tenant_id=tenant_id,
                            status="active", seed_template=False,
                            extra={"hours": [{"dow": d, "open": "00:00", "close": "24:00"} for d in range(7)],
                                   "scheduling": {"min_notice_minutes": 0}})
    r = Repos(db, t.id)
    await r.resources.insert_one({"_id": "res_ava", "name": "Dr Ava", "active": True})
    await r.resources.insert_one({"_id": "res_ben", "name": "Dr Ben", "active": True})
    await r.services.insert_one({"_id": "svc_chk", "name": "Check-up", "duration_min": 30, "active": True})
    await auth_service.create_user(email="owner@hs.au", password=PASSWORD, tenant_id=t.id, role="owner")
    await auth_service.create_user(email="ava@hs.au", password=PASSWORD, tenant_id=t.id, role="dentist",
                                   resource_id="res_ava")
    return t


def _today_at(hour: int) -> datetime:
    local = datetime.now(ZoneInfo(SYD)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


async def _appt(db, t, appt_id, resource_id, start, **kw):
    doc = {"_id": appt_id, "appointment_id": appt_id, "resource_id": resource_id, "resource_name": "x",
           "start": start, "end": start + timedelta(minutes=30), "status": "confirmed", "name": kw.pop("name", "Sam Smith"),
           "phone": kw.pop("phone", "+61412345678"), "service_name": "Check-up", "source": "voice", **kw}
    await Repos(db, t.id).appointments.insert_one(doc)


# ── E1: the role ───────────────────────────────────────────────────────────

async def test_a_dentist_reaches_only_their_day(db, client):
    t = await _practice(db)
    h = _login(client, "ava@hs.au")
    assert client.get("/t/context", headers=h).status_code == 200          # the app shell loads
    for path in ("/t/calls", "/t/contacts", "/t/settings", "/t/appointments", "/t/leads", "/t/team", "/t/overview"):
        assert client.get(path, headers=h).status_code == 403, path
    assert client.get("/t/my-day", headers=h).status_code == 200
    assert client.get("/t/my-day", headers={**h, "X-Tenant-Id": t.id}).status_code == 200


async def test_a_dentist_only_sees_their_own_patients(db, client):
    t = await _practice(db)
    await _appt(db, t, "apt_ava", "res_ava", _today_at(10), name="Mia Wong")
    await _appt(db, t, "apt_ben", "res_ben", _today_at(11), name="Tom Ford", phone="+61400000002")
    await _appt(db, t, "apt_cx", "res_ava", _today_at(12), status="cancelled", name="Gone Patient")
    await _appt(db, t, "apt_tmrw", "res_ava", _today_at(9) + timedelta(days=1), name="Next Day")
    h = _login(client, "ava@hs.au")
    day = client.get("/t/my-day", headers=h).json()
    assert [a["patient_name"] for a in day["appointments"]] == ["Mia Wong"]
    assert day["resource"]["name"] == "Dr Ava" and day["appointments"][0]["source"] == "ai"
    # Asking for another dentist's day is ignored: always their own.
    assert [a["patient_name"] for a in client.get("/t/my-day", params={"resource_id": "res_ben"},
                                                   headers=h).json()["appointments"]] == ["Mia Wong"]
    assert [a["patient_name"] for a in client.get("/t/my-day", params={"day": "tomorrow"},
                                                   headers=h).json()["appointments"]] == ["Next Day"]
    # Staff can look at any dentist's day.
    owner = _login(client, "owner@hs.au")
    ben = client.get("/t/my-day", params={"resource_id": "res_ben"}, headers=owner).json()
    assert [a["patient_name"] for a in ben["appointments"]] == ["Tom Ford"]
    assert client.get("/t/my-day", headers=owner).status_code == 422       # staff must say whose
    audit = await Repos(db, t.id).audit.find({"action": "my_day.view"})
    assert len(audit) >= 3


async def test_the_day_shows_what_the_practice_knows(db, client):
    t = await _practice(db)
    r = Repos(db, t.id)
    await r.contacts.insert_one({"_id": "con_mia", "phone": "+61412345678", "name": "Mia Wong",
                                 "stats": {"appointments": 1, "no_shows": 2}})
    await _appt(db, t, "apt_ava", "res_ava", _today_at(10), contact_id="con_mia", notes="Prefers mornings")
    await r.calls.insert_one({"call_id": "c1", "caller_phone": "+61412345678", "started_at": _today_at(8),
                              "summary": "Chipped front tooth, booked a same-day visit."})
    from core.sensitive import LEAD_TEXT_FIELDS, seal

    await r.leads.insert_one(seal({"_id": "lead_1", "kind": "dental_emergency", "contact_id": "con_mia",
                                   "severity": "urgent", "problem": "chipped tooth, sore", "red_flag": False,
                                   "created_at": _today_at(8)}, LEAD_TEXT_FIELDS))
    row = client.get("/t/my-day", headers=_login(client, "ava@hs.au")).json()["appointments"][0]
    assert row["first_visit"] is True and row["no_shows"] == 2 and row["notes"] == "Prefers mornings"
    assert "Chipped front tooth" in row["last_call_summary"]
    assert row["triage"] == {"severity": "urgent", "problem": "chipped tooth, sore", "red_flag": False}


async def test_a_dentist_without_a_practitioner_link_is_told_why(db, client):
    t = await _practice(db)
    await auth_service.create_user(email="new@hs.au", password=PASSWORD, tenant_id=t.id, role="dentist")
    r = client.get("/t/my-day", headers=_login(client, "new@hs.au"))
    assert r.status_code == 403 and "linked" in r.json()["detail"]


async def test_marking_a_patient_seen_or_not_arrived(db, client):
    t = await _practice(db)
    r = Repos(db, t.id)
    await r.contacts.insert_one({"_id": "con_mia", "phone": "+61412345678", "name": "Mia", "stats": {"no_shows": 0}})
    await _appt(db, t, "apt_ava", "res_ava", _today_at(10), contact_id="con_mia")
    await _appt(db, t, "apt_ben", "res_ben", _today_at(11))
    h = _login(client, "ava@hs.au")
    assert client.patch("/t/my-day/apt_ava", json={"status": "no_show"}, headers=h).json()["status"] == "no_show"
    assert (await r.contacts.find_one({"_id": "con_mia"}))["stats"]["no_shows"] == 1
    assert client.patch("/t/my-day/apt_ben", json={"status": "completed"}, headers=h).status_code == 404   # not hers
    assert (await r.appointments.find_one({"appointment_id": "apt_ben"}))["status"] == "confirmed"


# ── Team: inviting a dentist ────────────────────────────────────────────────

async def test_inviting_a_dentist_needs_a_practitioner(db, client):
    await _practice(db)
    owner = _login(client, "owner@hs.au")
    r = client.post("/t/team/invite", json={"email": "ben@hs.au", "role": "dentist"}, headers=owner)
    assert r.status_code == 422
    r = client.post("/t/team/invite", json={"email": "ben@hs.au", "role": "dentist", "resource_id": "res_nope"},
                    headers=owner)
    assert r.status_code == 422
    r = client.post("/t/team/invite", json={"email": "ben@hs.au", "role": "dentist", "resource_id": "res_ben"},
                    headers=owner)
    assert r.status_code == 200
    token = r.json()["link"].split("token=")[1]
    doc = await auth_service.accept_invite(token, password=PASSWORD, name="Ben")
    assert doc["memberships"][0] == {"tenant_id": "ten_dv", "role": "dentist", "resource_id": "res_ben"}
    team = client.get("/t/team", headers=owner).json()["members"]
    assert any(m["email"] == "ben@hs.au" and m["role"] == "dentist" and m["resource_id"] == "res_ben" for m in team)


# ── E3: staff move an appointment to another dentist ──────────────────────

async def test_reassigning_keeps_the_time_and_needs_the_new_dentist_free(db, client):
    t = await _practice(db)
    owner = _login(client, "owner@hs.au")
    start = _today_at(10) + timedelta(days=2)
    r = client.post("/t/appointments", json={"service_id": "svc_chk", "resource_id": "res_ava", "start": start.isoformat(),
                                             "name": "Sam Smith", "phone": "+61412345678"}, headers=owner)
    assert r.status_code == 200, r.text
    old_id = r.json()["appointment_id"]
    moved = client.post(f"/t/appointments/{old_id}/reassign", json={"resource_id": "res_ben"}, headers=owner)
    assert moved.status_code == 200, moved.text
    new = moved.json()
    assert new["resource_id"] == "res_ben" and new["start"].startswith(start.isoformat()[:16])
    repos = Repos(db, t.id)
    assert (await repos.appointments.find_one({"appointment_id": old_id}))["status"] == "cancelled"
    # Ben is now busy then: moving another of Ava's patients onto him at the same time is refused.
    r2 = client.post("/t/appointments", json={"service_id": "svc_chk", "resource_id": "res_ava", "start": start.isoformat(),
                                              "name": "Lee Kim", "phone": "+61400000003"}, headers=owner).json()
    clash = client.post(f"/t/appointments/{r2['appointment_id']}/reassign", json={"resource_id": "res_ben"},
                        headers=owner)
    assert clash.status_code == 409 and "Dr Ben" in clash.json()["detail"]
    dentist = _login(client, "ava@hs.au")
    assert client.post(f"/t/appointments/{r2['appointment_id']}/reassign", json={"resource_id": "res_ben"},
                       headers=dentist).status_code == 403


async def test_change_dentist_lists_only_dentists_free_at_that_time(db, client):
    await _practice(db)
    owner = _login(client, "owner@hs.au")
    start = _today_at(11) + timedelta(days=2)
    a = client.post("/t/appointments", json={"service_id": "svc_chk", "resource_id": "res_ava", "start": start.isoformat(),
                                             "name": "Sam Smith", "phone": "+61412345678"}, headers=owner).json()
    free = client.get(f"/t/appointments/{a['appointment_id']}/free-practitioners", headers=owner).json()
    assert [p["_id"] for p in free["practitioners"]] == ["res_ben"]          # never the current dentist
    client.post("/t/appointments", json={"service_id": "svc_chk", "resource_id": "res_ben", "start": start.isoformat(),
                                         "name": "Lee Kim", "phone": "+61400000003"}, headers=owner)
    busy = client.get(f"/t/appointments/{a['appointment_id']}/free-practitioners", headers=owner).json()
    assert busy["practitioners"] == []
    dentist = _login(client, "ava@hs.au")
    assert client.get(f"/t/appointments/{a['appointment_id']}/free-practitioners",
                      headers=dentist).status_code == 403


async def test_a_regular_dentist_is_validated(db, client):
    t = await _practice(db)
    await Repos(db, t.id).contacts.insert_one({"_id": "con_1", "phone": "+61412345678", "name": "Mia"})
    owner = _login(client, "owner@hs.au")
    assert client.patch("/t/contacts/con_1", json={"preferred_resource_id": "res_nope"}, headers=owner).status_code == 422
    r = client.patch("/t/contacts/con_1", json={"preferred_resource_id": "res_ava"}, headers=owner)
    assert r.status_code == 200 and r.json()["preferred_resource_id"] == "res_ava"
    assert client.patch("/t/contacts/con_1", json={"preferred_resource_id": ""}, headers=owner).json()[
        "preferred_resource_id"] is None


# ── E2: the practice diary, for bookings made at the front desk ───────────

async def test_the_day_includes_the_pms_diary_without_duplicates(db, client, monkeypatch):
    from core.services import pms_sync

    fake = FakePMS()
    monkeypatch.setattr(sp, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "CLINIKO_USER_AGENT", "AI Receptionist (ops@example.com)")
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone=SYD, tenant_id="ten_dvp",
                            status="active", seed_template=False)
    await pms_sync.connect(t, "cliniko", KEY, user_id="usr_1")
    invalidate()
    repos = Repos(db, t.id)
    ava = await repos.resources.find_one({"name": "Ava Nguyen"})
    await auth_service.create_user(email="ava@pms.au", password=PASSWORD, tenant_id=t.id, role="dentist",
                                   resource_id=ava["_id"])
    ten = _today_at(10)
    eleven = _today_at(11)
    fake.existing_appointments = [
        {"id": "ia1", "starts_at": ten.isoformat().replace("+00:00", "Z"), "ends_at": (ten + timedelta(minutes=30)).isoformat(),
         "patient_name": "Mia Wong"},
        {"id": "ia2", "starts_at": eleven.isoformat().replace("+00:00", "Z"), "patient_name": "Front Desk Booking"},
        {"id": "ia3", "starts_at": eleven.isoformat().replace("+00:00", "Z"), "patient_name": "Cancelled One",
         "cancelled_at": "2026-11-20T00:00:00Z"},
    ]
    # Mia was booked by the AI: the local row and the diary row are the same appointment.
    await _appt(db, t, "apt_mia", ava["_id"], ten, name="Mia Wong", external={"provider": "cliniko", "id": "ia1"})
    h = _login(client, "ava@pms.au")
    day = client.get("/t/my-day", headers=h).json()
    names = [(a["patient_name"], a["source"]) for a in day["appointments"]]
    assert names == [("Mia Wong", "ai"), ("Front Desk Booking", "pms")] and day["warning"] is None
    asked = len([r for r in fake.requests if r.url.path == "/v1/individual_appointments"])
    client.get("/t/my-day", headers=h)
    assert len([r for r in fake.requests if r.url.path == "/v1/individual_appointments"]) == asked   # cached
    # The diary down: what was booked here still shows, with a warning.
    my_day.reset_for_tests()
    fake.fail[("GET", "/v1/individual_appointments")] = lambda r: httpx.Response(503)
    day = client.get("/t/my-day", headers=h).json()
    assert [a["patient_name"] for a in day["appointments"]] == ["Mia Wong"] and "isn't answering" in day["warning"]


async def test_a_dentist_away_notice_is_saved_and_reaches_the_call_snapshot(db, client):
    from core.services.snapshot import load_snapshot

    t = await _practice(db)
    owner = _login(client, "owner@hs.au")
    day = (datetime.now(ZoneInfo(SYD)) + timedelta(days=2)).date().isoformat()
    r = client.post("/t/exceptions", json={"resource_id": "res_ava", "date_from": day, "date_to": day, "reason": "Leave",
                                           "announce": True, "notice": "Dr Ben is seeing her patients"}, headers=owner)
    assert r.status_code == 200 and r.json()["announce"] is True
    snap = await load_snapshot(Repos(db, t.id), await resolve_by_id(t.id))
    assert [n["notice"] for n in snap.notices] == ["Dr Ben is seeing her patients"]
