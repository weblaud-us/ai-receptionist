"""scripts/voice_bakeoff.py: the offline parts (no provider calls)."""

import struct

from scripts.voice_bakeoff import LINES, Candidate, cartesia_candidates, telephone


def test_telephone_is_8khz_band_limited_pcm():
    tone = b"".join(struct.pack("<h", int(8000 * ((i // 12) % 2 * 2 - 1))) for i in range(24_000))  # 1 s
    out = telephone(tone)
    assert len(out) == 8000 * 2                     # 1 s at 8 kHz, 16-bit
    assert telephone(b"") == b""


def test_both_countries_have_the_same_lines_and_local_details():
    assert [i for i, _ in LINES["AU"]] == [i for i, _ in LINES["GB"]]
    au, gb = dict(LINES["AU"]), dict(LINES["GB"])
    assert "000" in au["emergency"] and "999" in gb["emergency"]
    assert "$" in au["price"] and "£" in gb["price"]


def test_candidate_slugs_are_path_safe():
    c = Candidate("elevenlabs", "eleven_v4_turbo", "abc", "Charlotte (warm)", "AU")
    assert c.slug == "au-elevenlabs-eleven-v4-turbo-charlotte-warm"


async def test_cartesia_picks_local_accents_and_keeps_the_current_default(monkeypatch):
    from config import settings
    from core.integrations import cartesia

    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "")
    assert await cartesia_candidates(["AU"], ["sonic-3"], ["v1"], 2) == []
    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "ca")
    monkeypatch.setattr(settings, "CARTESIA_VOICE_ID_AU", "ca-current")

    async def library():
        return [{"voice_id": "au1", "name": "Sydney Receptionist", "accent": "Australian", "gender": "female"},
                {"voice_id": "au2", "name": "Bondi Mate", "accent": "Australian", "gender": "male"},
                {"voice_id": "gb1", "name": "London Lady", "accent": "British", "gender": "female"}]
    monkeypatch.setattr(cartesia, "fetch_voices", library)
    au = await cartesia_candidates(["AU"], ["sonic-3"], [], 2)
    assert [c.voice_id for c in au] == ["ca-current", "au1", "au2"]           # current default + 2 local
    assert {c.model for c in au} == {"sonic-3"}
    assert [c.voice_id for c in await cartesia_candidates(["AU"], ["sonic-3"], ["x9"], 2)] == ["x9"]


def test_cartesia_accent_comes_from_name_and_description():
    from core.integrations.cartesia import _shape, accent_of

    assert accent_of("Sophie", "Warm Australian receptionist") == "Australian"
    assert accent_of("British Lady", "") == "British"
    assert accent_of("Ana", "Neutral narrator") is None
    assert _shape({"id": "x", "name": "Hola", "language": "es"}) is None             # English voices only
    assert _shape({"id": "y", "name": "Aussie Guy", "language": "en"})["accent"] == "Australian"
