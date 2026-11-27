"""The live "what the assistant is doing" feed for browser calls (agent/activity.py)."""

import asyncio
import json
from types import SimpleNamespace

from agent import activity


def _collect():
    sent: list[dict] = []

    async def send(text: str) -> None:
        sent.append(json.loads(text))
    return sent, activity.Activity(send)


async def _drain(feed: activity.Activity) -> None:
    if feed._last is not None:
        await feed._last


def test_a_booking_reports_running_then_done_with_safe_details():
    async def go():
        sent, feed = _collect()
        args = {"slot_id": "s1", "service": "Check-up", "name": "Ann Patient", "phone": "+61400000000"}
        step = await feed.started("book_appointment", args)
        await feed.finished(step, "book_appointment", args,
                            json.dumps({"success": True, "service": "Check-up", "when": "Tue 10 Dec, 10:30"}))
        await _drain(feed)
        return sent
    sent = asyncio.run(go())
    assert [e["status"] for e in sent] == ["running", "done"]
    assert sent[0]["id"] == sent[1]["id"] and sent[1]["label"] == "Booked the appointment"
    assert "Tue 10 Dec, 10:30" in sent[1]["detail"]
    text = json.dumps(sent)
    assert "+61400000000" not in text and "Ann Patient" not in text      # never a name or number


def test_a_red_flag_emergency_is_an_alert():
    async def go():
        sent, feed = _collect()
        args = {"symptom_summary": "swollen face, hard to swallow", "severity_level": "life_threatening",
                "swelling_or_bleeding_red_flag": True}
        step = await feed.started("triage_dental_emergency", args)
        await feed.finished(step, "triage_dental_emergency", args, json.dumps({"success": True}))
        await _drain(feed)
        return sent
    sent = asyncio.run(go())
    assert sent[-1]["status"] == "alert" and "Emergency" in sent[-1]["label"]
    assert "swallow" not in json.dumps(sent)                              # never the symptom


def test_availability_counts_the_times_found():
    status, label, detail = activity._availability({}, {"slots": [{"say": "Tuesday at 10:30"}, {"say": "Wed 9:00"}]})
    assert (status, label, detail) == ("done", "Found 2 free times", "Tuesday at 10:30")
    assert activity._availability({}, {"error": "pms_down"})[0] == "failed"


def test_unknown_tools_and_phone_calls_send_nothing():
    async def go():
        sent, feed = _collect()
        assert await feed.started("some_internal_tool", {}) is None
        return sent
    assert asyncio.run(go()) == []
    assert activity.for_state(SimpleNamespace(channel="phone")).enabled is False


def test_a_failing_sender_never_breaks_the_tool():
    async def boom(_text: str) -> None:
        raise RuntimeError("room gone")

    async def go():
        feed = activity.Activity(boom)
        step = await feed.started("check_availability", {"service": "Clean"})
        await feed.finished(step, "check_availability", {}, "not json")
        await _drain(feed)
    asyncio.run(go())   # no exception
