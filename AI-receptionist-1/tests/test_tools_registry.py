"""Shared AI tools (Roadmap Tasks 1.9 and 1.10): one implementation, fast, never hangs a call."""

import asyncio
import json
from datetime import timedelta

from core.timeutils import business_today
from core.tools import registry
from core.tools.hotel import HotelToolExecutor, drain_background_tasks
from core.tools.registry import AI_TOOL_SCHEMAS, ToolContext, run_tool
from database.models import BedType, Room, RoomPricing, RoomType

VOICE = ToolContext(channel="voice", caller_phone="+8801711111111", call_id="call-test")


async def _room(db, number: str, room_type: RoomType = RoomType.STANDARD, price: float = 4500) -> None:
    room = Room(room_number=number, room_type=room_type, bed_type=BedType.DOUBLE, floor=1, capacity=2,
                pricing=RoomPricing(base_price_per_night=price, tax_pct=15))
    await db.rooms.insert_one(room.model_dump(by_alias=True))


def _day(offset: int) -> str:
    return (business_today("Asia/Dhaka") + timedelta(days=offset)).isoformat()


# ── Availability: one rooms query + one bookings query, alternatives included ──

async def test_alternatives_come_from_one_bookings_query(db, monkeypatch):
    await _room(db, "101")
    await _room(db, "201", RoomType.DELUXE, 7000)
    ex = HotelToolExecutor()
    booked = await ex.create_booking(guest_name="A", guest_phone="+8801700000001", room_type="standard",
                                     check_in_date=_day(10), check_out_date=_day(12))
    assert booked["success"]

    queries = {"bookings": 0}
    collection_cls = type(db.bookings)
    original_find = collection_cls.find

    def counting_find(self, *args, **kwargs):
        if self.name == "bookings":
            queries["bookings"] += 1
        return original_find(self, *args, **kwargs)

    monkeypatch.setattr(collection_cls, "find", counting_find)
    result = await ex.check_room_availability(_day(10), _day(12), "standard")
    monkeypatch.undo()

    assert queries["bookings"] == 1, "availability + alternatives must use one bookings query"
    assert result["available"] is False
    assert [a["room_type"] for a in result["alternative_room_types"]] == ["deluxe"]
    # Nights 10 and 11 are taken: the first free 2-night stay starts on day 12 (check-out day).
    assert result["alternative_dates"][0]["check_in_date"] == _day(12)
    assert len(result["alternative_dates"]) <= 3


async def test_room_free_again_on_checkout_day(db):
    await _room(db, "101")
    ex = HotelToolExecutor()
    await ex.create_booking(guest_name="A", guest_phone="+8801700000001", room_type="standard",
                            check_in_date=_day(10), check_out_date=_day(12))
    assert (await ex.check_room_availability(_day(12), _day(13), "standard"))["available"]
    assert not (await ex.check_room_availability(_day(11), _day(13), "standard"))["available"]


# ── Registry ──────────────────────────────────────────────

def test_one_schema_per_tool():
    names = [s["function"]["name"] for s in AI_TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    assert {"check_room_availability", "create_booking", "take_message"} <= set(names)


async def test_unknown_tool_is_refused(db):
    assert json.loads(await run_tool(VOICE, "drop_database", {}))["error"].startswith("Unknown tool")


async def test_slow_read_tool_times_out(db, monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(registry, "READ_TIMEOUT_S", 0.05)
    monkeypatch.setattr(registry.get_tool_executor(), "execute", slow)
    result = json.loads(await run_tool(VOICE, "get_hotel_info", {"info_type": "general"}))
    assert result["error"] == "timeout"


async def test_slow_write_tool_is_not_cancelled(db, monkeypatch):
    finished = asyncio.Event()

    async def slow_booking(*args, **kwargs):
        await asyncio.sleep(0.2)
        finished.set()
        return json.dumps({"success": True})

    monkeypatch.setattr(registry, "WRITE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(registry.get_tool_executor(), "execute", slow_booking)
    result = json.loads(await run_tool(VOICE, "create_booking", {"guest_name": "A"}))
    assert result["error"] == "still_processing"   # the model is told NOT to retry
    await drain_background_tasks()
    assert finished.is_set()                        # …and the booking still completed


async def test_empty_arguments_are_dropped(db, monkeypatch):
    seen = {}

    async def capture(name, args, caller_phone=None):
        seen.update(args)
        return "{}"

    monkeypatch.setattr(registry.get_tool_executor(), "execute", capture)
    await run_tool(VOICE, "get_hotel_info", {"info_type": "general", "extra": "", "other": None})
    assert seen == {"info_type": "general"}


# ── take_message ──────────────────────────────────────────

async def test_take_message_saves_for_the_team(db):
    r = json.loads(await run_tool(VOICE, "take_message", {
        "caller_name": "Rahim", "callback_number": "+8801711111111", "message": "Wants a late check-out"}))
    assert r["success"]
    doc = await db.messages.find_one({"message_id": r["message_id"]})
    assert doc["status"] == "new" and doc["channel"] == "voice" and doc["call_id"] == "call-test"


async def test_take_message_needs_a_message(db):
    r = json.loads(await run_tool(VOICE, "take_message", {"caller_name": "Rahim", "callback_number": "+1555"}))
    assert r["error"] == "message_required"


async def test_take_message_notifies_owner_in_background(db, monkeypatch):
    from config import settings

    sent = []

    async def fake_send(phone, text, phone_number_id):
        sent.append((phone, text))
        return True

    import core.integrations.whatsapp as wa
    from core.notifications import drain

    monkeypatch.setattr(settings, "OWNER_NOTIFY_WHATSAPP", "+8801900000000")
    monkeypatch.setattr(wa, "send_text", fake_send)
    await run_tool(VOICE, "take_message", {"caller_name": "Rahim", "callback_number": "+8801711111111",
                                           "message": "Call me back", "urgent": True})
    await drain_background_tasks()
    await drain()
    assert sent and sent[0][0] == "+8801900000000" and "URGENT" in sent[0][1]
