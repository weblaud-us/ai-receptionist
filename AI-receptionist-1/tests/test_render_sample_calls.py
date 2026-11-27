"""scripts/render_sample_calls.py: the offline parts (parsing the landing page's scripts, timing, mixing)."""

from scripts.render_sample_calls import SCENARIOS_TS, Line, mix, parse_scenarios, place
from scripts.voice_bakeoff import RATE


def test_parses_every_scenario_line_but_not_the_trace():
    scenarios = parse_scenarios(SCENARIOS_TS.read_text(encoding="utf-8"))
    assert {"implant-price", "regional", "emergency"} <= set(scenarios)
    au = scenarios["emergency"]["au"]
    assert [ln.who for ln in au] == ["patient", "assistant", "patient"]
    assert au[1].text.startswith("Please hang up and call 000 now.")
    assert "999" in scenarios["emergency"]["uk"][1].text
    assert scenarios["regional"]["au"][0].text == "Hi, do you take Bupa? And can I claim on the spot?"


def test_lines_keep_their_time_unless_the_previous_one_is_still_talking():
    lines = [Line(0, "assistant", "a"), Line(4, "patient", "b"), Line(5, "assistant", "c")]
    starts, duration = place(lines, [3.0, 2.5, 1.0])
    assert starts == [0, 4, 6.85]                     # 4 + 2.5 + 0.35 gap
    assert duration == 8.7


def test_mix_puts_each_line_at_its_start():
    one = b"\x01\x00" * RATE                          # 1 s of a constant sample
    out = mix([one, one], [0.0, 2.0], 3.5)
    assert len(out) == int(3.5 * RATE) * 2
    assert out[: 2] == b"\x01\x00" and out[int(1.5 * RATE) * 2: int(1.5 * RATE) * 2 + 2] == b"\x00\x00"
    assert out[int(2.5 * RATE) * 2: int(2.5 * RATE) * 2 + 2] == b"\x01\x00"
