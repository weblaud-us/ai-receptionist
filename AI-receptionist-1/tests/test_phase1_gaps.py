"""Gaps closed after the voice/dentist-view build: country default, and more as they land."""

import pytest

from config import settings
from core.auth import service as auth_service
from core.prompts.builder import emergency_number
from core.tenancy.models import TenantConfig
from tests.conftest import livekit_agents_importable

PASSWORD = "correct horse battery"


def test_a_tenant_without_a_country_is_never_told_to_dial_911():
    t = TenantConfig.model_validate({"_id": "t1", "name": "Smiles", "slug": "smiles"})
    assert t.country == ""
    assert emergency_number(t.country) == "112"      # works on mobiles in AU, UK and the EU


async def test_signup_requires_an_explicit_country(db, client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ENABLED", True)
    auth_service.reset_rate_limits()
    body = {"email": "owner@salon.example", "password": PASSWORD, "business_name": "Salon", "vertical": "salon",
            "timezone": "Europe/London", "plan": "growth", "accept_terms": True}
    r = client.post("/signup", json=body)
    assert r.status_code == 422 and "country" in r.text


# ── 1.7 turn-taking config ─────────────────────────────────────────────────

def test_adaptive_barge_in_falls_back_to_vad_without_livekit_keys(monkeypatch):
    if not livekit_agents_importable():
        pytest.skip("livekit-agents can't be imported on this machine")
    from agent import session_factory as sf

    monkeypatch.setattr(settings, "VOICE_INTERRUPT_MODE", "adaptive")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "")
    assert sf.interruption_mode() == "vad"
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "secret")
    assert sf.interruption_mode() == "adaptive"


def test_check_env_warns_when_cloud_turn_taking_has_no_keys(monkeypatch):
    from scripts import check_env

    monkeypatch.setattr(settings, "VOICE_TURN_DETECTOR", "v1")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "")
    level, message = check_env.check_turn_taking()
    assert level == "WARN" and "VOICE_TURN_DETECTOR=v1" in message
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "secret")
    assert check_env.check_turn_taking()[0] == "OK"


def test_verification_country_check_uses_the_calling_code():
    from routers.billing import _number_in_country

    assert _number_in_country("+61412345678", "AU") and _number_in_country("+447700900123", "GB")
    assert _number_in_country("+14165550123", "CA") and not _number_in_country("+447700900123", "AU")
    assert not _number_in_country("+0", "AU")
