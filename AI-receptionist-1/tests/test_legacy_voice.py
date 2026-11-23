"""Audit P0-10 / P0-11: the old Twilio media-stream (hotel) voice path can't take calls."""

import pytest
from pydantic import ValidationError

from config import Settings

SAFE = dict(APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
            REDIS_URL="redis://redis:6379/0", VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://api.example.com",
            META_APP_SECRET="s", LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True)


@pytest.mark.skipif(Settings().LEGACY_TWILIO_VOICE, reason="this machine's .env turns the legacy voice path on")
def test_a_number_pointed_at_the_old_webhook_gets_gone_not_the_hotel_agent(client):
    r = client.post("/voice/incoming", data={"From": "+61412345678"})
    assert r.status_code == 410 and "SIP trunk" in r.json()["detail"]


def test_production_refuses_the_legacy_voice_path():
    assert Settings(_env_file=None, **SAFE).is_production
    with pytest.raises(ValidationError, match="LEGACY_TWILIO_VOICE"):
        Settings(_env_file=None, **{**SAFE, "LEGACY_TWILIO_VOICE": True})
