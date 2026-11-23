"""Voice (TTS) order is controlled from .env: VOICE_TTS_PROVIDER, then VOICE_TTS_FALLBACKS."""

import pytest
from pydantic import ValidationError

from config import Settings


def _s(**o) -> Settings:
    return Settings(_env_file=None, APP_ENV="development", **o)


def test_deepgram_first_then_kokoro_then_openai():
    s = _s(VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="kokoro,openai")
    assert s.tts_order("deepgram") == ["deepgram", "kokoro", "openai"]


def test_business_choice_goes_first_then_env_order():
    s = _s(VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="kokoro,openai")
    # a business left on the default "elevenlabs" still falls through to the env order
    assert s.tts_order("elevenlabs") == ["elevenlabs", "deepgram", "kokoro", "openai"]


def test_duplicates_and_case_are_handled():
    s = _s(VOICE_TTS_PROVIDER="kokoro", VOICE_TTS_FALLBACKS=" Kokoro , OPENAI,openai ")
    assert s.tts_order("kokoro") == ["kokoro", "openai"]


def test_default_order_starts_with_deepgram_aura_and_keeps_every_fallback():
    # Plan 2.6 / VOICE-04: Deepgram Aura is the AU/UK default; the others stay as fallbacks.
    fields = Settings.model_fields         # code defaults (tests/conftest.py overrides the env order)
    assert fields["VOICE_TTS_PROVIDER"].default == "deepgram"
    assert fields["VOICE_TTS_FALLBACKS"].default == "deepgram,cartesia,elevenlabs,kokoro,openai"
    s = _s(VOICE_TTS_FALLBACKS=fields["VOICE_TTS_FALLBACKS"].default)
    assert s.tts_order("elevenlabs") == ["elevenlabs", "deepgram", "cartesia", "kokoro", "openai"]


def test_typo_in_fallbacks_fails_at_startup():
    with pytest.raises(ValidationError, match="unknown voice"):
        _s(VOICE_TTS_FALLBACKS="kokoro,elevenlab")


def test_availability_follows_keys_and_url():
    s = _s(DEEPGRAM_API_KEY="dg", KOKORO_BASE_URL="http://kokoro:8880/v1", OPENAI_API_KEY="",
           ELEVENLABS_API_KEY="", CARTESIA_API_KEY="")
    assert s.tts_available("deepgram") and s.tts_available("kokoro")
    assert not s.tts_available("elevenlabs") and not s.tts_available("cartesia") and not s.tts_available("openai")
    assert not s.tts_available("nonsense")


def test_kokoro_is_off_without_a_url():
    assert not _s().tts_available("kokoro")
