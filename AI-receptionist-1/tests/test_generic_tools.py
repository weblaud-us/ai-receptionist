"""Generic tools through run_tool: per-tenant tool sets, booking flow, leads, knowledge, ownership."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.notifications import drain
from core.repositories import Repos
from core.tenancy.models import HoursEntry, TenantConfig
from core.tools import registry
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool, schemas_for, tool_names_for


@pytest.fixture(autouse=True)
def _sms_on(monkeypatch):
    """These tests cover the SMS paths, which the platform has switched off (config.SMS_ENABLED)."""
    from config import settings as _settings

    monkeypatch.setattr(_settings, "SMS_ENABLED", True)


TZ = "America/Chicago"
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)   # Monday 08:00 local


def _tenant(vertical="salon", tenant_id="ten_salon", **kw) -> TenantConfig:
    t = TenantConfig(_id=tenant_id, name="Bella Salon", slug=tenant_id.replace("_", "-"), vertical=vertical,
                     timezone=TZ, phone="+15125550000",
                     hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(7)], **kw)
    t.scheduling.min_notice_minutes = 0
    return t


def _ctx(tenant, phone="+15125550100", channel="voice"):
    return ToolContext(channel=channel, caller_phone=phone, call_id="call-t", tenant=tenant, now=NOW)


async def _seed(db, tenant):
    repos = Repos(db, tenant.id)
    await repos.resources.insert_one({"_id": f"res_anna_{tenant.id}", "name": "Anna", "kind": "staff", "hours": None, "active": True})
    await repos.services.insert_one({"_id": f"svc_cut_{tenant.id}", "name": "Haircut", "duration_min": 45, "buffer_min": 0,
                                     "price": {"display": "from $45"}, "active": True, "sort": 1})
    await repos.services.insert_one({"_id": f"svc_beard_{tenant.id}", "name": "Beard trim", "duration_min": 20, "active": True, "sort": 2})
    return repos


async def _call(ctx, name, args):
    return json.loads(await run_tool(ctx, name, args))


@pytest.fixture(autouse=True)
async def _quiet_notifications(monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa

    sent = []

    async def fake(*a, **k):
        sent.append(a)
        return True

    monkeypatch.setattr(wa, "send_text", fake)
    monkeypatch.setattr(sms, "send_sms", fake)
    yield sent
    await drain_background_tasks()
    await drain()


# ── Tool sets ────────────────────────────────────────────

def test_tool_sets_follow_the_vertical():
    assert "capture_lead" in tool_names_for(_tenant("home_services")) and "capture_lead" not in tool_names_for(_tenant("salon"))
    assert "check_room_availability" in tool_names_for(_tenant("hotel")) and "book_appointment" not in tool_names_for(_tenant("hotel"))
    assert "answer_from_knowledge" not in tool_names_for(_tenant("salon"))
    assert "answer_from_knowledge" in tool_names_for(_tenant("salon"), large_kb=True)
    assert all(s["type"] == "function" for s in schemas_for(_tenant("restaurant")))
    assert {s.name for s in registry.registered_tools().values()} >= {"take_message", "book_appointment", "create_booking"}


async def test_tool_outside_the_vertical_is_unknown(db):
    assert (await _call(_ctx(_tenant("salon")), "check_room_availability", {"check_in_date": "2026-12-01", "check_out_date": "2026-12-02"}))["error"].startswith("Unknown tool")
    assert (await _call(_ctx(_tenant("hotel")), "book_appointment", {"slot_id": "x", "name": "y"}))["error"].startswith("Unknown tool")


# ── Booking flow ─────────────────────────────────────────

async def test_full_booking_flow(db, _quiet_notifications):
    t = _tenant()
    await _seed(db, t)
    ctx = _ctx(t)
    avail = await _call(ctx, "check_availability", {"service": "a haircut", "date": "2026-11-23", "time": "15:00"})
    assert avail["available"] and avail["service"] == "Haircut" and avail["price"] == "from $45"
    assert avail["slots"][0]["say"] == "today at 3:00 PM with Anna"
    assert len(avail["slots"]) == 3

    booked = await _call(ctx, "book_appointment", {"slot_id": avail["slots"][0]["slot_id"], "service": "haircut", "name": "Rahim Uddin"})
    assert booked["success"] and booked["when"] == "today at 3:00 PM" and booked["with"] == "Anna"
    await drain_background_tasks()
    await drain()
    assert len(_quiet_notifications) >= 1          # owner + caller notified in the background

    mine = await _call(ctx, "find_my_appointments", {})
    assert mine["found"] and "Haircut" in mine["appointments"][0]["say"]
    appt_id = mine["appointments"][0]["appointment_id"]

    again = await _call(ctx, "check_availability", {"service": "haircut", "date": "2026-11-23", "time": "15:00"})
    assert all(s["say"] != "today at 3:00 PM with Anna" for s in again["slots"])

    moved = await _call(ctx, "reschedule_appointment", {"appointment_id": appt_id, "new_slot_id": again["slots"][0]["slot_id"]})
    assert moved["success"]
    cancelled = await _call(ctx, "cancel_appointment", {"appointment_id": moved["appointment_id"], "reason": "sick"})
    assert cancelled["success"]
    assert (await _call(ctx, "find_my_appointments", {}))["found"] is False


async def test_service_and_date_validation(db):
    t = _tenant()
    await _seed(db, t)
    ctx = _ctx(t)
    assert (await _call(ctx, "check_availability", {"date": "2026-11-23"}))["error"] == "service_required"
    assert (await _call(ctx, "check_availability", {"service": "massage", "date": "2026-11-23"}))["error"] == "unknown_service"
    assert (await _call(ctx, "check_availability", {"service": "haircut", "date": "2026-11-01"}))["error"] == "date_in_past"
    assert (await _call(ctx, "check_availability", {"service": "haircut", "date": "2026-11-23", "resource": "Zed"}))["error"] == "unknown_resource"
    assert (await _call(ctx, "book_appointment", {"slot_id": "junk", "name": "x"}))["error"] == "bad_slot"


async def test_no_slots_offers_alternatives(db):
    t = _tenant()
    t.hours = [HoursEntry(dow=1, open="09:00", close="17:00")]   # Tuesdays only
    await _seed(db, t)
    r = await _call(_ctx(t), "check_availability", {"service": "haircut", "date": "2026-11-23"})   # Monday
    assert r["available"] is False and r["alternatives"] and r["alternatives"][0]["say"] == "tomorrow at 9:00 AM with Anna"


async def test_other_callers_cannot_touch_my_appointment(db):
    t = _tenant()
    await _seed(db, t)
    owner = _ctx(t, phone="+15125550100")
    avail = await _call(owner, "check_availability", {"service": "haircut", "date": "2026-11-24", "time": "10:00"})
    appt = await _call(owner, "book_appointment", {"slot_id": avail["slots"][0]["slot_id"], "service": "haircut", "name": "Rahim Uddin"})
    stranger = _ctx(t, phone="+15125559999")
    assert (await _call(stranger, "cancel_appointment", {"appointment_id": appt["appointment_id"]}))["error"] == "not_found"
    assert (await _call(stranger, "find_my_appointments", {}))["found"] is False
    # …unless they give the ID and the exact name on it (two factors).
    ok = await _call(stranger, "cancel_appointment", {"appointment_id": appt["appointment_id"], "name": "rahim uddin"})
    assert ok["success"]
    assert (await _call(_ctx(t, phone=None), "find_my_appointments", {}))["found"] is False   # browser: unknown number


async def test_tenants_are_isolated_through_tools(db):
    a, b = _tenant(tenant_id="ten_a"), _tenant(tenant_id="ten_b")
    await _seed(db, a)
    await _seed(db, b)
    ca, cb = _ctx(a), _ctx(b)
    av_a = await _call(ca, "check_availability", {"service": "haircut", "date": "2026-11-24", "time": "10:00"})
    appt = await _call(ca, "book_appointment", {"slot_id": av_a["slots"][0]["slot_id"], "service": "haircut", "name": "A"})
    assert (await _call(cb, "find_my_appointments", {}))["found"] is False           # same phone, other tenant
    assert (await _call(cb, "cancel_appointment", {"appointment_id": appt["appointment_id"], "name": "A"}))["error"] == "not_found"
    av_b = await _call(cb, "check_availability", {"service": "haircut", "date": "2026-11-24", "time": "10:00"})
    assert av_b["slots"][0]["say"] == av_a["slots"][0]["say"]                      # B's 10:00 is still free


# ── Restaurants, leads, knowledge, messages ───────────────

async def test_restaurant_party_size(db):
    t = _tenant("restaurant", tenant_id="ten_rest")
    t.scheduling.max_party_size = 6
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "tbl_2", "name": "Table 2", "kind": "table", "capacity": 2, "active": True})
    await repos.resources.insert_one({"_id": "tbl_6", "name": "Table 6", "kind": "table", "capacity": 6, "active": True})
    await repos.services.insert_one({"_id": "svc_res", "name": "Table reservation", "duration_min": 90, "active": True})
    ctx = _ctx(t)
    r = await _call(ctx, "check_availability", {"date": "2026-11-23", "time": "12:00", "party_size": 4})
    assert r["available"] and all(s["resource"] == "Table 6" for s in r["slots"])
    assert (await _call(ctx, "check_availability", {"date": "2026-11-23", "party_size": 12}))["error"] == "party_too_large"


async def test_capture_lead_saves_and_alerts(db, _quiet_notifications):
    t = _tenant("home_services", tenant_id="ten_plumb")
    t.notifications.owner_phone = "+15125550001"
    ctx = _ctx(t)
    r = await _call(ctx, "capture_lead", {"name": "Dana", "phone": "+15125550100", "address": "12 Oak St",
                                         "problem": "Water heater leaking", "urgency": "emergency"})
    assert r["success"]
    await drain_background_tasks()
    await drain()
    lead = await Repos(db, t.id).leads.find_one({"_id": r["lead_id"]})
    assert lead["urgency"] == "emergency" and lead["tenant_id"] == t.id
    assert _quiet_notifications                                  # urgent → owner alerted
    assert (await _call(ctx, "capture_lead", {"name": "x", "phone": "+1", "urgency": "today"}))["error"] == "problem_required"


async def test_knowledge_search_and_unanswered_log(db):
    t = _tenant()
    repos = Repos(db, t.id)
    await repos.knowledge.insert_one({"question": "Is there parking?", "answer": "Yes, behind the building.", "tags": ["parking"], "active": True})
    await repos.knowledge.insert_one({"question": "Do you do kids' haircuts?", "answer": "Yes, under 12s are $25.", "active": True})
    ctx = _ctx(t)
    r = await _call(ctx, "answer_from_knowledge", {"question": "where can I park my car"})
    assert r["found"] and "behind the building" in r["answers"][0]["answer"]
    miss = await _call(ctx, "answer_from_knowledge", {"question": "do you sell gift cards"})
    assert miss["found"] is False
    assert await repos.unanswered.count({"status": "open"}) == 1


async def test_take_message_is_tenant_scoped(db):
    a = _tenant(tenant_id="ten_a")
    r = await _call(_ctx(a), "take_message", {"caller_name": "R", "callback_number": "+1555", "message": "hi"})
    assert r["success"]
    assert await Repos(db, "ten_a").messages.count() == 1 and await Repos(db, "ten_b").messages.count() == 0


# ── Unanswered questions reach the owner whatever the knowledge-base size (B7) ──

async def test_take_message_logs_the_question_it_could_not_answer(db):
    t = _tenant(tenant_id="ten_q")
    assert "answer_from_knowledge" not in tool_names_for(t)           # small KB: no lookup tool at all
    r = await _call(_ctx(t), "take_message", {"caller_name": "Ann", "callback_number": "+15125550100",
                                               "message": "Wants to know about gift cards",
                                               "unanswered_question": "Do you sell gift cards?"})
    assert r["success"]
    rows = await Repos(db, "ten_q").unanswered.find({})
    assert [(x["question"], x["call_id"], x["source"]) for x in rows] == [("Do you sell gift cards?", "call-t", "tool")]
    # An ordinary message logs nothing
    await _call(_ctx(t), "take_message", {"caller_name": "Bo", "callback_number": "+15125550101", "message": "Call me"})
    assert await Repos(db, "ten_q").unanswered.count() == 1


def test_questions_the_assistant_said_it_could_not_answer_are_found_in_the_transcript():
    from core.tools.knowledge import questions_not_answered
    transcript = [
        {"role": "assistant", "text": "Thanks for calling Bella Salon. How can I help?"},
        {"role": "user", "text": "Do you do keratin treatments?"},
        {"role": "assistant", "text": "I don't have that information, but someone will call you back."},
        {"role": "user", "text": "OK. And are you open Saturday?"},
        {"role": "assistant", "text": "Yes, we're open Saturday from nine to four."},
        {"role": "user", "text": "Is there parking nearby?"},
        {"role": "assistant", "text": "I'm not sure about parking. Would you like me to take a message?"},
    ]
    assert questions_not_answered(transcript) == ["Do you do keratin treatments?", "Is there parking nearby?"]
    assert questions_not_answered([{"role": "assistant", "text": "I don't know"}]) == []     # no caller question


async def test_record_unanswered_skips_duplicates_within_a_call(db):
    from core.tools.knowledge import record_unanswered
    repos = Repos(db, "ten_d")
    assert await record_unanswered(repos, "Do you   take cards?", channel="voice", call_id="c1")
    assert not await record_unanswered(repos, "Do you take cards?", channel="voice", call_id="c1")
    assert await record_unanswered(repos, "Do you take cards?", channel="voice", call_id="c2")
    assert not await record_unanswered(repos, "   ", channel="voice", call_id="c3")
    assert await repos.unanswered.count() == 2
