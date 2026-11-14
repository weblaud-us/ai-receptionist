"""Double booking is impossible (Task 0.10) and callers only reach their own bookings (Task 0.9)."""

import asyncio
import json
from datetime import timedelta

import pytest

from core.tools.hotel import HotelToolExecutor, drain_background_tasks
from core.timeutils import business_today
from database.models import BedType, Room, RoomPricing, RoomType

CALLER_A = "+8801711111111"
CALLER_B = "+8801722222222"


async def _add_room(db, number: str = "101") -> str:
    room = Room(room_number=number, room_type=RoomType.STANDARD, bed_type=BedType.DOUBLE, floor=1,
                capacity=2, pricing=RoomPricing(base_price_per_night=4500, tax_pct=15))
    doc = room.model_dump(by_alias=True)
    await db.rooms.insert_one(doc)
    return doc["_id"]


def _dates(offset: int = 10, nights: int = 2) -> tuple[str, str]:
    start = business_today("Asia/Dhaka") + timedelta(days=offset)
    return start.isoformat(), (start + timedelta(days=nights)).isoformat()


async def _book(ex: HotelToolExecutor, name: str, phone: str, dates: tuple[str, str]) -> dict:
    return await ex.create_booking(guest_name=name, guest_phone=phone, room_type="standard",
                                   check_in_date=dates[0], check_out_date=dates[1])


# ── Double booking ──────────────────────────────────────

def _race(ex: HotelToolExecutor, parties: int) -> None:
    """
    Force the worst-case interleaving: every caller finishes its availability check (and sees
    the room free) before ANY of them claims it. Only the unique index can stop the race then.
    """
    barrier = asyncio.Barrier(parties)
    original = ex.check_room_availability

    async def check_then_wait(*args, **kwargs):
        result = await original(*args, **kwargs)
        await barrier.wait()
        return result

    ex.check_room_availability = check_then_wait


async def test_concurrent_bookings_for_last_room_only_one_wins(db):
    await _add_room(db)
    ex = HotelToolExecutor()
    _race(ex, 5)
    results = await asyncio.gather(*[_book(ex, f"Guest {i}", f"+88017000000{i:02d}", _dates()) for i in range(5)])
    winners = [r for r in results if r.get("success")]
    assert len(winners) == 1, results
    assert await db.bookings.count_documents({}) == 1
    assert await db.room_nights.count_documents({}) == 2  # two nights, one owner


async def test_concurrent_bookings_spread_across_free_rooms(db):
    await _add_room(db, "101")
    await _add_room(db, "102")
    ex = HotelToolExecutor()
    _race(ex, 2)
    results = await asyncio.gather(_book(ex, "A", CALLER_A, _dates()), _book(ex, "B", CALLER_B, _dates()))
    assert all(r.get("success") for r in results), results
    assert {r["room_number"] for r in results} == {"101", "102"}


async def test_overlapping_stay_is_refused_but_back_to_back_is_fine(db):
    await _add_room(db)
    ex = HotelToolExecutor()
    first = await _book(ex, "A", CALLER_A, _dates(10, 3))           # nights 10, 11, 12
    overlap = await _book(ex, "B", CALLER_B, _dates(12, 2))         # night 12 clashes
    after = await _book(ex, "C", CALLER_B, _dates(13, 1))           # checks in on A's checkout day
    assert first["success"] and not overlap.get("success") and after["success"]


async def test_cancelling_frees_the_room(db):
    await _add_room(db)
    ex = HotelToolExecutor()
    first = await _book(ex, "A", CALLER_A, _dates())
    assert (await _book(ex, "B", CALLER_B, _dates())).get("success") is not True
    await ex.cancel_booking(first["booking_id"], "test")
    assert (await _book(ex, "B", CALLER_B, _dates()))["success"]


async def test_unique_index_rejects_a_direct_second_claim(db):
    from pymongo.errors import DuplicateKeyError

    await db.room_nights.insert_one({"room_id": "r1", "night": "2030-01-01", "booking_id": "HTL1"})
    with pytest.raises(DuplicateKeyError):
        await db.room_nights.insert_one({"room_id": "r1", "night": "2030-01-01", "booking_id": "HTL2"})


# ── Caller ownership ────────────────────────────────────

async def _call(ex, tool, caller, **args) -> dict:
    return json.loads(await ex.execute(tool, args, caller_phone=caller))


@pytest.fixture
async def booked(db):
    await _add_room(db)
    ex = HotelToolExecutor()
    b = await _book(ex, "Rahim Uddin", CALLER_A, _dates())
    return ex, b["booking_id"]


async def test_owner_can_read_own_booking(booked):
    ex, bid = booked
    r = await _call(ex, "get_booking_details", CALLER_A, booking_id=bid)
    assert r["found"] and r["bookings"][0]["booking_id"] == bid


async def test_owner_matches_national_format(booked):
    ex, bid = booked
    r = await _call(ex, "get_booking_details", "01711111111", booking_id=bid)
    assert r.get("found")


async def test_other_caller_cannot_read_booking_by_id(booked):
    ex, bid = booked
    r = await _call(ex, "get_booking_details", CALLER_B, booking_id=bid)
    assert r["error"] == "not_found"
    assert "Rahim" not in json.dumps(r)


async def test_not_found_is_identical_for_missing_and_foreign_bookings(booked):
    ex, bid = booked
    foreign = await _call(ex, "get_booking_details", CALLER_B, booking_id=bid)
    missing = await _call(ex, "get_booking_details", CALLER_B, booking_id="HTLDOESNOT")
    assert foreign == missing


async def test_other_caller_cannot_look_up_by_someone_elses_phone(booked):
    ex, _ = booked
    r = await _call(ex, "get_booking_details", CALLER_B, guest_phone=CALLER_A)
    assert r["error"] == "not_found"


async def test_other_caller_cannot_cancel(booked, db):
    ex, bid = booked
    r = await _call(ex, "cancel_booking", CALLER_B, booking_id=bid)
    assert r["error"] == "not_found"
    assert (await db.bookings.find_one({"booking_id": bid}))["status"] == "confirmed"


async def test_wrong_name_is_not_enough(booked):
    ex, bid = booked
    r = await _call(ex, "cancel_booking", CALLER_B, booking_id=bid, guest_name="Someone Else")
    assert r["error"] == "not_found"


async def test_booking_id_plus_full_name_from_another_number_works(booked, db):
    ex, bid = booked
    r = await _call(ex, "cancel_booking", CALLER_B, booking_id=bid, guest_name="uddin rahim")
    assert r["success"]
    assert (await db.bookings.find_one({"booking_id": bid}))["status"] == "cancelled"


async def test_unknown_caller_needs_id_and_name(booked):
    ex, bid = booked
    assert (await _call(ex, "get_booking_details", None, booking_id=bid))["error"] == "not_found"
    assert (await _call(ex, "get_booking_details", None, booking_id=bid, guest_name="Rahim Uddin"))["found"]


async def test_confirmation_from_another_number_goes_to_phone_on_file(booked, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "META_WA_ACCESS_TOKEN", "test-token")
    ex, bid = booked
    sent = {}

    async def fake_send(booking_id, whatsapp_number):
        sent["to"] = whatsapp_number
        return {"success": True}

    monkeypatch.setattr(ex, "send_booking_confirmation_whatsapp", fake_send)
    r = await _call(ex, "send_booking_confirmation_whatsapp", CALLER_B, booking_id=bid,
                    whatsapp_number="+15550009999", guest_name="Rahim Uddin")
    assert r["queued"]                      # never awaited inside the turn (Task 1.9)
    await drain_background_tasks()
    assert sent["to"] == CALLER_A
