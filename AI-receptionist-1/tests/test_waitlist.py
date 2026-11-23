"""Plan 4.2: a cancelled time goes to waitlisted patients; the first to tap the link gets it."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.notifications import drain
from core.repositories import Repos
from core.services import waitlist
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool

SYD = "Australia/Sydney"


@pytest.fixture(autouse=True)
async def texts(monkeypatch):
    import core.integrations.whatsapp as wa

    out: list = []

    async def fake_text(to, text, pnid=None):
        out.append((to, text))
        return True

    monkeypatch.setattr(wa, "send_text", fake_text)
    monkeypatch.setattr(wa, "configured", lambda pnid=None: True)
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://api.example.com")
    invalidate()
    yield out
    await drain_background_tasks()
    await drain()
    invalidate()


async def _practice(db):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone=SYD, tenant_id="ten_wl",
                            status="active", seed_template=False,
                            extra={"hours": [{"dow": d, "open": "08:00", "close": "18:00"} for d in range(7)],
                                   "scheduling": {"min_notice_minutes": 0}})
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "res_lee", "name": "Dr Lee", "kind": "staff", "active": True})
    await repos.services.insert_one({"_id": "svc", "name": "Check-up", "duration_min": 30, "active": True, "sort": 1})
    return await resolve_by_id(t.id)


def _ctx(t, phone, now):
    return ToolContext(channel="voice", caller_phone=phone, call_id="c-wl", tenant=t, now=now)


async def _setup(db):
    t = await _practice(db)
    now = datetime.now(timezone.utc)
    day = (now.astimezone(ZoneInfo(SYD)) + timedelta(days=3)).date()
    for name, phone in (("Ann Lim", "+61400000001"), ("Ben Ho", "+61400000002")):
        out = json.loads(await run_tool(_ctx(t, phone, now), "join_waitlist", {
            "name": name, "service": "Check-up", "date_from": day.isoformat(), "date_to": day.isoformat()}))
        assert out["success"], out
    # Someone on the list for a different week isn't offered this time.
    await run_tool(_ctx(t, "+61400000003", now), "join_waitlist", {
        "name": "Cal Wu", "service": "Check-up", "date_from": (day + timedelta(days=10)).isoformat()})
    start = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo(SYD)).replace(hour=10).astimezone(timezone.utc)
    slot = json.loads(await run_tool(_ctx(t, "+61400000009", now), "check_availability",
                                     {"service": "Check-up", "date": day.isoformat(), "time": "10:00"}))["slots"][0]
    booked = json.loads(await run_tool(_ctx(t, "+61400000009", now), "book_appointment", {
        "slot_id": slot["slot_id"], "service": "Check-up", "name": "Dee Fox", "date_of_birth": "1990-01-01"}))
    assert booked["success"], booked
    return t, now, start, booked["appointment_id"]


async def test_a_cancelled_time_is_offered_to_the_matching_waitlist_only(db, texts):
    t, now, _, appt_id = await _setup(db)
    texts.clear()
    out = json.loads(await run_tool(_ctx(t, "+61400000009", now), "cancel_appointment",
                                    {"appointment_id": appt_id, "name": "Dee Fox", "date_of_birth": "1990-01-01"}))
    assert out["success"]
    await drain()
    assert sorted(to for to, _ in texts) == ["+61400000001", "+61400000002"]       # not Cal, wrong week
    assert all("https://api.example.com/public/waitlist/ten_wl/" in text for _, text in texts)
    assert all("Dee" not in text for _, text in texts)                            # nothing about who cancelled


async def test_the_first_to_tap_gets_it_and_the_other_hears_its_gone(db, client, texts):
    t, now, _, appt_id = await _setup(db)
    await run_tool(_ctx(t, "+61400000009", now), "cancel_appointment",
                   {"appointment_id": appt_id, "name": "Dee Fox", "date_of_birth": "1990-01-01"})
    await drain()
    offers = await Repos(db, t.id).waitlist_offers.find({}, sort=[("created_at", 1)])
    assert len(offers) == 2
    first, second = (waitlist.offer_url(t.id, o["_id"]).replace("https://api.example.com", "") for o in offers)
    assert "Book this time" in client.get(first).text                              # GET only shows it
    assert await Repos(db, t.id).appointments.count({"source": "waitlist"}) == 0
    assert "re booked</h1>" in client.post(first).text
    assert "gone" in client.post(second).text
    assert client.post(first.split("?")[0] + "?sig=forged").status_code == 404
    booked = await Repos(db, t.id).appointments.find_one({"source": "waitlist"})
    assert booked and booked["phone"] in ("+61400000001", "+61400000002")


async def test_a_moved_booking_keeps_its_date_of_birth_check(db, texts):
    t, now, _, appt_id = await _setup(db)
    day = (now.astimezone(ZoneInfo(SYD)) + timedelta(days=4)).date().isoformat()
    slot = json.loads(await run_tool(_ctx(t, "+61400000009", now), "check_availability",
                                     {"service": "Check-up", "date": day}))["slots"][0]
    moved = json.loads(await run_tool(_ctx(t, "+61400000009", now), "reschedule_appointment", {
        "appointment_id": appt_id, "new_slot_id": slot["slot_id"], "name": "Dee Fox", "date_of_birth": "1990-01-01"}))
    assert moved["success"], moved
    other_phone = ToolContext(channel="voice", caller_phone="+61411111111", call_id="c2", tenant=t, now=now)
    found = json.loads(await run_tool(other_phone, "find_my_appointments", {"name": "Dee Fox", "date_of_birth": "1990-01-01"}))
    assert found["found"] and found["appointments"][0]["appointment_id"] == moved["appointment_id"]


async def test_staff_see_and_tidy_the_waitlist(db, client, monkeypatch):
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    t, *_ = await _setup(db)
    await auth_service.create_user(email="desk@hs.au", password="correct horse battery", tenant_id=t.id, role="staff")
    tok = client.post("/auth/login", json={"email": "desk@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    rows = client.get("/t/waitlist", headers=h).json()["waitlist"]
    assert [r["name"] for r in rows] == ["Ann Lim", "Ben Ho", "Cal Wu"]
    assert client.delete(f"/t/waitlist/{rows[2]['id']}", headers=h).status_code == 200
    assert [r["name"] for r in client.get("/t/waitlist", headers=h).json()["waitlist"]] == ["Ann Lim", "Ben Ho"]
