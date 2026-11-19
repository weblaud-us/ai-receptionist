"""Production config guard (0.4), business timezone (0.11), log redaction (0.13), AI disclosure (0.8)."""

import logging
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from config import Settings
from core.log import RedactionFilter, redact
from core.phone import mask_phone, phones_match
from core.timeutils import business_today

SAFE_PROD = dict(
    APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
    VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://api.example.com",
    META_APP_SECRET="meta-secret", LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True,
    REDIS_URL="redis://redis:6379/0",
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PROD, **overrides})


# ── 0.4 production guard ────────────────────────────────

def test_safe_production_config_boots():
    assert _settings().is_production


@pytest.mark.parametrize("override", [
    {"DEBUG": True},
    {"SECRET_KEY": "change-in-production"},
    {"SECRET_KEY": "short"},
    {"CORS_ORIGINS": "*"},
    {"CORS_ORIGINS": ""},
    {"REDIS_URL": ""},
    {"METRICS_TOKEN": "short"},
    {"VERIFY_TWILIO_SIGNATURE": False},
    {"META_APP_SECRET": ""},
    {"WEBHOOK_BASE_URL": "http://api.example.com"},
    {"LOG_LEVEL": "DEBUG"},
])
def test_production_refuses_unsafe_config(override):
    with pytest.raises(ValidationError):
        _settings(**override)


def test_defaults_are_safe():
    s = Settings(_env_file=None, APP_ENV="development")
    assert s.DEBUG is False and s.VERIFY_TWILIO_SIGNATURE is True and s.CORS_ORIGINS == ""
    assert Settings.model_fields["APP_ENV"].default == "production"


# ── 0.11 business timezone ──────────────────────────────

def test_business_today_across_midnight_utc():
    # 20:30 UTC on 1 Jan = 02:30 on 2 Jan in Dhaka (UTC+6) = 12:30 on 1 Jan in Los Angeles.
    now = datetime(2027, 1, 1, 20, 30, tzinfo=timezone.utc)
    assert business_today("Asia/Dhaka", now).isoformat() == "2027-01-02"
    assert business_today("America/Los_Angeles", now).isoformat() == "2027-01-01"


def test_business_today_bad_timezone_falls_back_to_utc():
    now = datetime(2027, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert business_today("Not/AZone", now).isoformat() == "2027-01-01"


# ── 0.13 redaction ──────────────────────────────────────

def test_redact_masks_phones_and_emails_but_not_dates_or_ids():
    text = "Call from +8801712345678 (rahim.uddin@example.com) for HTL1A2B3C4D on 2026-11-14"
    out = redact(text)
    assert "+8801712345678" not in out and "5678" in out
    assert "rahim.uddin@" not in out and "@example.com" in out
    assert "HTL1A2B3C4D" in out and "2026-11-14" in out


def test_redaction_filter_applies_to_log_args():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "from=%s", ("+15551234567",), None)
    RedactionFilter().filter(record)
    assert "15551234567" not in record.getMessage()


def test_phone_helpers():
    assert phones_match("+8801712345678", "01712345678")
    assert phones_match("whatsapp:+8801712345678", "8801712345678")
    assert not phones_match("+8801712345678", "+8801712345679")
    assert not phones_match("", "")
    assert mask_phone("+8801712345678").endswith("5678")


# ── 0.8 AI disclosure ───────────────────────────────────

def test_greetings_disclose_ai():
    from ai.prompts import GREETING_VOICE, GREETING_WHATSAPP

    assert "virtual assistant" in GREETING_VOICE
    assert "virtual assistant" in GREETING_WHATSAPP


def test_prompt_never_tells_the_model_to_pretend_to_be_human():
    from ai.prompts import get_system_prompt

    for channel in ("voice", "whatsapp"):
        p = get_system_prompt(channel, "Hotel", "Aria", "Addr", "14:00", "12:00", "BDT", "+880")
        assert "REAL HUMAN" not in p
        assert "NEVER say \"AI\"" not in p
        assert "AI assistant" in p


def test_style_filter_keeps_ai_disclosure():
    from ai.agent import get_voice_agent

    reply = "I'm an AI assistant for the hotel. Would you like me to take a message?"
    assert "AI assistant" in get_voice_agent()._enforce_style(reply)


def test_log_lines_carry_the_business_id():
    """Support can filter one business's logs: tenant_id rides on every record (ids only)."""
    import contextvars
    import json as _json

    from core.log import JsonFormatter, TextFormatter, set_tenant

    def emit():
        set_tenant("ten_abc")
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "Booked for %s", ("+15125550100",), None)
        RedactionFilter().filter(record)
        return record

    record = contextvars.copy_context().run(emit)        # isolated: doesn't leak into other tests
    payload = _json.loads(JsonFormatter().format(record))
    assert payload["tenant_id"] == "ten_abc" and "+15125550100" not in payload["msg"]
    assert "tenant_id=ten_abc" in TextFormatter().format(record)
