"""
Voice agent logic that doesn't need the LiveKit runtime (runs everywhere, including Windows):
profile, prompt, speech clean-up, latency telemetry, call persistence, greeting cache.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from agent import greeting
from agent.persistence import CallRecorder, call_document, load_contact
from agent.profile import parse_hours, load_profile
from agent.prompt import STATIC_RULES, build_voice_prompt
from agent.speech import clean_for_speech, clean_stream
from agent.state import CallState
from agent.telemetry import TurnTracker, percentile, summarise, turn_latency


def _profile(**overrides):
    from dataclasses import replace

    return replace(load_profile(), **overrides)


def _state(**overrides) -> CallState:
    return CallState(call_id="call-abc", profile=overrides.pop("profile", _profile()), **overrides)


# Dhaka is UTC+6: 04:00 UTC = 10:00 local, 16:00 UTC = 22:00 local.
MORNING = datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)


# ── Profile ──────────────────────────────────────────────

def test_greeting_discloses_ai_and_is_short():
    text = _profile().greeting_text
    assert "virtual assistant" in text and "Grand Azure Hotel" in text
    assert len(text.split()) <= 20          # ≤ ~4 s spoken
    assert "recorded" not in text


def test_greeting_announces_recording():
    assert "may be recorded" in _profile(recording=True).greeting_text


def test_parse_hours():
    assert parse_hours("09:00-21:00") is not None
    assert parse_hours("") is None and parse_hours("nonsense") is None and parse_hours("25:00-26:00") is None
    assert parse_hours("00:00-24:00") is not None


def test_transfer_window():
    assert not _profile(transfer_number="").transfer_open(MORNING)
    assert _profile(transfer_number="+8801900000000").transfer_open(NIGHT)          # no hours = any time
    day = _profile(transfer_number="+8801900000000", transfer_hours="09:00-21:00")
    assert day.transfer_open(MORNING) and not day.transfer_open(NIGHT)
    overnight = _profile(transfer_number="+8801900000000", transfer_hours="20:00-06:00")
    assert overnight.transfer_open(NIGHT) and not overnight.transfer_open(MORNING)


# ── Prompt ───────────────────────────────────────────────

def test_static_prefix_is_identical_across_calls():
    """Provider prompt caching needs a byte-identical prefix."""
    a = build_voice_prompt(_profile(), now=MORNING, caller_phone="+8801711111111")
    b = build_voice_prompt(_profile(), now=NIGHT, contact={"name": "Rahim Uddin", "summary": "x"})
    assert a.startswith(STATIC_RULES) and b.startswith(STATIC_RULES)
    assert a.split("# Right now")[0] == b.split("# Right now")[0]


def test_prompt_is_honest_and_short():
    assert "You are an AI assistant" in STATIC_RULES
    assert "sir" not in STATIC_RULES.lower().split()
    assert len(STATIC_RULES) <= 2600


def test_dynamic_tail():
    browser = build_voice_prompt(_profile(), now=MORNING)
    assert "browser call" in browser
    phone = build_voice_prompt(_profile(), now=MORNING, caller_phone="+8801711111111",
                               contact={"name": "Rahim Uddin", "summary": "suite booking"})
    assert "+8801711111111" in phone and "Rahim" in phone and "Uddin" not in phone
    assert phone.rstrip().endswith("History: suite booking")
    assert "Saturday, 14 November 2026" in phone and "10:00 AM" in phone


# ── Speech clean-up ──────────────────────────────────────

@pytest.mark.parametrize("raw, spoken", [
    ("**Great!** Rooms & suites", " Great! Rooms and suites"),
    ("See https://example.com/book now", "See our website now"),
    ("• Pool", " Pool"),
    (" me", " me"),                       # chunk spacing survives
])
def test_clean_for_speech(raw, spoken):
    assert clean_for_speech(raw) == spoken


async def test_clean_stream_drops_empty_chunks():
    async def chunks():
        for c in ["Let", " me", "**", " check."]:
            yield c

    assert [c async for c in clean_stream(chunks())] == ["Let", " me", " ", " check."]


# ── Telemetry ────────────────────────────────────────────

def test_percentile_and_latency():
    assert percentile([], 50) is None
    assert percentile([0.5, 1.0, 1.5, 2.0], 50) == 1.0
    assert percentile([0.5, 1.0, 1.5, 2.0], 95) == 2.0
    assert turn_latency({"e2e_latency": 0.8}) == 0.8
    assert turn_latency({"end_of_turn_delay": 0.3, "llm_node_ttft": 0.4, "tts_node_ttfb": 0.2}) == pytest.approx(0.9)
    assert turn_latency({}) is None


def test_turn_tracker():
    state = _state()
    t = TurnTracker(state)
    t.on_assistant_message({"e2e_latency": 9.9})        # greeting: not a turn
    t.on_user_message()
    t.on_assistant_message({"e2e_latency": 0.8})        # plain turn
    t.on_assistant_message({"e2e_latency": 3.0})        # second message, same turn: ignored
    t.on_user_message()
    t.on_assistant_message({"e2e_latency": 0.9})        # "Let me check." …
    t.on_tools_executed(["check_room_availability"])    # … then a tool → tool turn
    t.on_user_message()
    t.on_tools_executed(["get_hotel_info"])             # tool before any reply → tool turn
    t.on_assistant_message({"e2e_latency": 1.4})

    assert [r["tool_turn"] for r in state.turns] == [False, True, True]
    assert state.tools_used == ["check_room_availability", "get_hotel_info"]
    s = summarise(state.turns)
    assert s["turns"] == 3 and s["p50_s"] == 0.8 and s["tool_turns"] == 2 and s["tool_p50_s"] == 0.9


def test_greeting_latency_recorded_once():
    state = _state()
    t = TurnTracker(state)
    t.on_agent_speaking()
    first = state.greeting_latency_s
    t.on_agent_speaking()
    assert first is not None and state.greeting_latency_s == first


def test_outcome_ranking():
    state = _state()
    state.set_outcome("booked")
    state.set_outcome("completed")
    assert state.outcome == "booked"
    state.set_outcome("transferred")
    assert state.outcome == "transferred"


# ── Persistence ──────────────────────────────────────────

def test_call_document_shapes():
    state = _state(caller_phone="+8801711111111")
    live = call_document(state, final=False)
    assert live["status"] == "in_progress" and "ended_at" not in live
    final = call_document(state, final=True)
    assert final["status"] == "completed" and final["outcome"] == "completed" and final["duration_s"] >= 0


async def test_recorder_writes_final_record(db):
    state = _state(caller_phone="+8801711111111")
    state.transcript.append({"role": "user", "text": "hi"})
    state.turns.append({"turn": 1, "latency_s": 0.7, "tool_turn": False})
    recorder = CallRecorder(state)
    recorder.start()
    await recorder.finish("participant_disconnected")
    doc = await db.calls.find_one({"call_id": "call-abc"})
    assert doc["status"] == "completed" and doc["latency"]["p50_s"] == 0.7
    assert doc["ended_reason"] == "participant_disconnected" and doc["transcript"][0]["text"] == "hi"


async def test_load_contact(db):
    await db.guests.insert_one({"phone": "+8801711111111", "name": "Rahim Uddin"})
    await db.bookings.insert_one({"booking_id": "HTL1", "guest_phone": "+8801711111111", "room_type": "suite",
                                  "check_in_date": "2026-12-01", "status": "confirmed",
                                  "created_at": datetime.now(timezone.utc)})
    contact = await load_contact(_state(caller_phone="01711111111"))   # national format still matches
    assert contact["name"] == "Rahim Uddin" and "HTL1" in contact["summary"]
    assert await load_contact(_state(caller_phone=None)) is None


# ── Greeting cache ───────────────────────────────────────

async def test_greeting_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(greeting, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(greeting, "_memory", {})
    pcm = b"\x01\x00" * 24000                                   # 1 s mono at 24 kHz
    greeting._store("k1", pcm, 24000, 1)
    monkeypatch.setattr(greeting, "_memory", {})                # force a disk read
    entry = greeting.load("k1")
    assert entry is not None and entry[1:] == (24000, 1)
    frames = [f async for f in greeting.frames(entry)]
    assert len(frames) == 50 and all(f.samples_per_channel == 480 for f in frames)


def test_greeting_cache_key_changes_with_voice():
    class FakeTTS:
        provider, model, sample_rate = "elevenlabs", "eleven_flash_v2_5", 24000

    assert greeting.cache_key("hi", FakeTTS(), "voice-a") != greeting.cache_key("hi", FakeTTS(), "voice-b")


async def test_greeting_render_caches_synthesised_audio(tmp_path, monkeypatch):
    from livekit import rtc

    monkeypatch.setattr(greeting, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(greeting, "_memory", {})

    class Ev:
        def __init__(self):
            self.frame = rtc.AudioFrame(b"\x00\x00" * 480, 24000, 1, 480)

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            async def gen():
                for _ in range(3):
                    await asyncio.sleep(0)
                    yield Ev()
            return gen()

    class FakeTTS:
        def synthesize(self, text):
            return Stream()

    await greeting.render("k2", "hello", FakeTTS())
    assert greeting.load("k2")[0] == b"\x00\x00" * 480 * 3


def test_latency_report_against_targets(capsys):
    from scripts.latency_report import report

    calls = [{"turns": [{"latency_s": 0.8, "tool_turn": False}, {"latency_s": 1.5, "tool_turn": True}],
              "greeting_latency_s": 0.5, "outcome": "booked"},
             {"turns": [{"latency_s": 2.4, "tool_turn": False}], "greeting_latency_s": 0.6, "outcome": "completed"}]
    results = report(calls)
    out = capsys.readouterr().out
    assert results["plain_p50"] == 0.8 and results["plain_p95"] == 2.4 and results["tool_p50"] == 1.5
    assert "FAIL" in out and "PASS" in out
