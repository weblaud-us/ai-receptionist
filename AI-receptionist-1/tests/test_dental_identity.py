"""SEC-06: at a dental practice, caller ID alone never reveals, moves or cancels a booking.

Families share one phone. A caller proves it's their booking with the patient's name and date of
birth (checked against a keyed fingerprint; the DOB itself is never stored), or, for a booking made
without a DOB, the name plus the number it was booked with.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.notifications import drain
from core.repositories import Repos
from core.tenancy.models import HoursEntry, TenantConfig
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool

SYD = "Australia/Sydney"
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)   # Monday 08:00 local
FAMILY_PHONE = "+61412345678"
OTHER_PHONE = "+61498765432"


def _tenant(vertical="dental", tenant_id="ten_dob") -> TenantConfig:
    t = TenantConfig(_id=tenant_id, name="Harbour Smiles", slug=tenant_id.replace("_", "-"), vertical=vertical,
                     country="AU", timezone=SYD, currency="AUD",
                     hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(7)])
    t.scheduling.min_notice_minutes = 0
    return t


def _ctx(tenant, phone=FAMILY_PHONE):
    return ToolContext(channel="voice", caller_phone=phone, call_id="call-dob", tenant=tenant, now=NOW)


async def _call(ctx, name, args=None):
    return json.loads(await run_tool(ctx, name, args or {}))


@pytest.fixture(autouse=True)
async def _quiet(monkeypatch):
    import core.integrations.whatsapp as wa

    async def fake(*a, **k):
        return True

    monkeypatch.setattr(wa, "send_text", fake)
    yield
    await drain_background_tasks()
    await drain()


async def _book(db, t, *, name, dob=None, slot_index=0):
    repos = Repos(db, t.id)
    if not await repos.resources.find_one({"_id": "res_lee"}):
        await repos.resources.insert_one({"_id": "res_lee", "name": "Dr Lee", "kind": "staff", "hours": None, "active": True})
        await repos.services.insert_one({"_id": "svc_check", "name": "Check-up", "duration_min": 30, "buffer_min": 0,
                                         "active": True, "sort": 1})
    slots = await _call(_ctx(t), "check_availability", {"service": "Check-up", "date": "2026-11-24"})
    args = {"slot_id": slots["slots"][slot_index]["slot_id"], "service": "Check-up", "name": name}
    if dob:
        args["date_of_birth"] = dob
    booked = await _call(_ctx(t), "book_appointment", args)
    assert booked.get("success"), booked
    return booked["appointment_id"]


async def test_the_dob_is_never_stored_only_a_fingerprint(db):
    t = _tenant()
    appt_id = await _book(db, t, name="Mia Chen", dob="2014-03-09")
    doc = await Repos(db, t.id).appointments.find_one({"appointment_id": appt_id})
    assert doc["dob_check"] and len(doc["dob_check"]) == 64
    assert "2014-03-09" not in json.dumps(doc, default=str)


async def test_caller_id_alone_reveals_nothing(db):
    t = _tenant()
    await _book(db, t, name="Mia Chen", dob="2014-03-09")
    out = await _call(_ctx(t), "find_my_appointments")
    assert out["error"] == "identity_required" and "appointments" not in out


async def test_a_sibling_on_the_same_phone_doesnt_hear_the_other_childs_booking(db):
    t = _tenant()
    await _book(db, t, name="Mia Chen", dob="2014-03-09")
    out = await _call(_ctx(t), "find_my_appointments", {"name": "Leo Chen", "date_of_birth": "2016-07-01"})
    assert out["found"] is False and out["error"] == "not_verified" and "Mia" not in json.dumps(out)
    wrong_dob = await _call(_ctx(t), "find_my_appointments", {"name": "Mia Chen", "date_of_birth": "2014-03-10"})
    assert wrong_dob["found"] is False


async def test_name_and_dob_find_the_booking_even_from_another_phone(db):
    t = _tenant()
    appt_id = await _book(db, t, name="Mia Chen", dob="2014-03-09")
    out = await _call(_ctx(t, phone=OTHER_PHONE), "find_my_appointments",
                      {"name": "Mia Chen", "date_of_birth": "2014-03-09"})
    assert out["found"] is True and [a["appointment_id"] for a in out["appointments"]] == [appt_id]
    # Speech-to-text heard one name slightly differently: the DOB still carries it.
    out = await _call(_ctx(t), "find_my_appointments", {"name": "Mia Chan", "date_of_birth": "2014-03-09"})
    assert out["found"] is True


async def test_cancel_and_reschedule_need_name_and_dob(db):
    t = _tenant()
    appt_id = await _book(db, t, name="Mia Chen", dob="2014-03-09")
    out = await _call(_ctx(t), "cancel_appointment", {"appointment_id": appt_id})
    assert out["error"] == "identity_required"
    out = await _call(_ctx(t), "cancel_appointment",
                      {"appointment_id": appt_id, "name": "Mia Chen", "date_of_birth": "2001-01-01"})
    assert out["error"] == "not_verified"
    slots = await _call(_ctx(t), "check_availability", {"service": "Check-up", "date": "2026-11-25"})
    out = await _call(_ctx(t), "reschedule_appointment",
                      {"appointment_id": appt_id, "new_slot_id": slots["slots"][0]["slot_id"]})
    assert out["error"] == "identity_required"
    out = await _call(_ctx(t), "cancel_appointment",
                      {"appointment_id": appt_id, "name": "Mia Chen", "date_of_birth": "2014-03-09"})
    assert out["success"] is True


async def test_a_booking_made_without_a_dob_needs_the_name_and_its_own_number(db):
    t = _tenant()
    appt_id = await _book(db, t, name="Sam Patel")          # e.g. booked by the front desk
    ok = await _call(_ctx(t), "find_my_appointments", {"name": "Sam Patel", "date_of_birth": "1980-05-05"})
    assert ok["found"] is True and ok["appointments"][0]["appointment_id"] == appt_id
    other = await _call(_ctx(t, phone=OTHER_PHONE), "cancel_appointment",
                        {"appointment_id": appt_id, "name": "Sam Patel", "date_of_birth": "1980-05-05"})
    assert other["error"] == "not_verified"


async def test_other_businesses_keep_caller_id_lookup(db):
    t = _tenant(vertical="salon", tenant_id="ten_dob_salon")
    await _book(db, t, name="Sam Patel")
    out = await _call(_ctx(t), "find_my_appointments")
    assert out["found"] is True


# ── P0-16: "anyone" is resolved to a real dentist before the slot is locked ─────

async def test_an_anyone_slot_is_booked_with_a_free_dentist_and_locks_their_chair(db):
    from core.services import availability as av

    t = _tenant(tenant_id="ten_any")
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "res_lee", "name": "Dr Lee", "kind": "staff", "hours": None, "active": True})
    await repos.services.insert_one({"_id": "svc_check", "name": "Check-up", "duration_min": 30, "buffer_min": 0,
                                     "active": True, "sort": 1})
    start = datetime(2026, 11, 24, 10, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)
    out = await _call(_ctx(t), "book_appointment", {"slot_id": av.encode_slot_id("any", start), "service": "Check-up",
                                                    "name": "Mia Chen", "date_of_birth": "2014-03-09"})
    assert out["success"] is True and out["with"] == "Dr Lee"
    locks = await repos.slot_locks.find({"appointment_id": out["appointment_id"]})
    assert locks and {lk["resource_id"] for lk in locks} == {"res_lee"}
