"""Plan A2.1: what the agent says while a slow lookup runs."""

import random
from types import SimpleNamespace

from agent import fillers


def _state(lang="en-AU"):
    return SimpleNamespace(profile=SimpleNamespace(tts_language=lang), fillers_used=[])


def test_slow_tools_get_a_filler_and_others_dont():
    s = _state()
    assert fillers.source_for("check_availability", s) is not None
    assert fillers.source_for("book_appointment", s) is not None
    assert fillers.source_for("take_message", s) is None          # fast, and the agent is mid-sentence anyway
    assert fillers.source_for("end_call", s) is None


def test_a_call_never_hears_the_same_line_twice_in_a_row():
    s = _state()
    src = fillers.source_for("check_availability", s)
    said = [src(i) for i in range(len(fillers.PHRASES["check_availability"]))]
    assert len(set(said)) == len(said)                            # every phrase once before any repeat
    for _ in range(20):
        before = s.fillers_used[-1]
        assert src(0) != before


def test_phrases_are_shared_across_tools_in_one_call():
    s = _state()
    fillers.source_for("check_availability", s)(0)
    fillers.source_for("book_appointment", s)(0)
    assert len(s.fillers_used) == 2


def test_non_english_callers_get_no_english_aside():
    assert fillers.source_for("check_availability", _state("es-ES")) is None
    assert fillers.source_for("check_availability", _state("en-GB")) is not None


def test_pick_is_deterministic_with_a_seeded_rng():
    used: list = []
    a = fillers.pick("find_my_appointments", used, random.Random(1))
    assert a in fillers.PHRASES["find_my_appointments"] and used == [a]
    assert fillers.pick("nope", used) is None


def test_the_prompt_no_longer_asks_the_llm_to_announce_lookups():
    from core.prompts.builder import BASE_VOICE_RULES

    assert "Let me check" not in BASE_VOICE_RULES
    assert "don't announce it first" in BASE_VOICE_RULES
