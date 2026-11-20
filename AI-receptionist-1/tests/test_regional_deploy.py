"""Phase 6.3: regional settings (AU / UK stacks), Twilio edges, Cliniko shard default, dental preflight."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from config import Settings
from core.tenancy.models import TenantConfig
from scripts.check_env import dental_preflight

ROOT = Path(__file__).resolve().parents[2]
PROD = dict(APP_ENV="production", SECRET_KEY="s" * 40, CORS_ORIGINS="https://app.example.org",
            WEBHOOK_BASE_URL="https://api.example.org", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True,
            REDIS_URL="redis://redis:6379/0")


def _practice(country="AU", **kw) -> TenantConfig:
    base = dict(_id=f"ten_{country.lower()}", name="Harbour Smiles", slug=f"hs-{country.lower()}", vertical="dental",
                country=country, timezone="Australia/Sydney" if country == "AU" else "Europe/London",
                currency="AUD" if country == "AU" else "GBP",
                agent={"stt_language": "en-AU" if country == "AU" else "en-GB"})
    base.update(kw)
    return TenantConfig(**base)


def test_twilio_api_host_follows_edge_and_region():
    assert Settings().twilio_api_base == "https://api.twilio.com"
    assert Settings(TWILIO_EDGE="sydney").twilio_api_base == "https://api.sydney.au1.twilio.com"
    assert Settings(TWILIO_EDGE="dublin", TWILIO_REGION="ie1").twilio_api_base == "https://api.dublin.ie1.twilio.com"
    assert Settings(TWILIO_EDGE="ie1").twilio_api_base == "https://api.dublin.ie1.twilio.com"
    assert Settings(TWILIO_REGION="au1").twilio_api_base == "https://api.sydney.au1.twilio.com"
    assert Settings(DEPLOYMENT_REGION="ap-southeast-2").deployment_country == "AU"
    assert Settings(DEPLOYMENT_REGION="eu-west-2").deployment_country == "GB"


def test_production_refuses_unknown_edges_and_regions():
    with pytest.raises(ValueError, match="TWILIO_EDGE"):
        Settings(**PROD, TWILIO_EDGE="mars")
    with pytest.raises(ValueError, match="DEPLOYMENT_REGION"):
        Settings(**PROD, DEPLOYMENT_REGION="moon-1")


def test_cliniko_shard_defaults_from_settings(monkeypatch):
    from config import settings
    from core.integrations import cliniko

    monkeypatch.setattr(settings, "CLINIKO_SHARD", "uk1")
    assert cliniko.shard_of("plainkey") == "uk1" and cliniko.shard_of("key-au2") == "au2"


def test_dental_preflight_fails_closed_in_production():
    # The production guard would refuse this config outright; the preflight must catch it too (it also
    # runs before a deploy, against an env file nobody has booted yet).
    bad = Settings.model_construct(**PROD, TOKEN_ENCRYPTION_KEY="", VERIFY_TWILIO_SIGNATURE=False,
                                   DEPLOYMENT_REGION="ap-southeast-2", CLINIKO_USER_AGENT="")
    t = _practice(integrations={"pms": {"provider": "cliniko", "connected": True}})
    rows = {(level, check) for level, check, _ in dental_preflight(bad, [t])}
    assert ("FAIL", "Encryption") in rows and ("FAIL", "Twilio sigs") in rows and ("FAIL", "Cliniko") in rows
    assert ("WARN", "ZDR") in rows


def test_dental_preflight_passes_a_well_set_up_au_stack_and_flags_wrong_defaults():
    good = Settings(**PROD, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), VERIFY_TWILIO_SIGNATURE=True,
                    DEPLOYMENT_REGION="ap-southeast-2", TWILIO_EDGE="sydney", OPENAI_ZDR_ENABLED=True,
                    DEEPGRAM_ZDR_ENABLED=True, CLINIKO_USER_AGENT="AI Receptionist (ops@example.org)",
                    LEGACY_TWILIO_VOICE=False)      # explicit: Settings() also reads a developer .env
    rows = dental_preflight(good, [_practice()])
    assert all(level == "OK" for level, _, _ in rows), rows
    # A UK practice on the AU stack, with US defaults: every mismatch is reported.
    wrong = _practice("GB", timezone="America/New_York", currency="USD", agent={"stt_language": "en-US"})
    msg = next(m for level, check, m in dental_preflight(good, [wrong]) if check == "Practice")
    assert "timezone" in msg and "currency" in msg and "speech language" in msg and "eu-west-2" in msg
    uk_edge = Settings(**PROD, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), DEPLOYMENT_REGION="eu-west-2",
                       TWILIO_EDGE="sydney")
    assert any(check == "Twilio edge" for _, check, _ in dental_preflight(uk_edge, [_practice("GB")]))


def test_regional_profiles_and_env_example_document_the_settings():
    example = (ROOT / "AI-receptionist-1" / ".env.example").read_text(encoding="utf-8")
    for name in ("DEPLOYMENT_REGION=", "TWILIO_EDGE=", "OPENAI_ZDR_ENABLED=", "DEEPGRAM_ZDR_ENABLED=", "CLINIKO_SHARD=",
                 "DENTALLY_API_BASE_URL="):
        assert name in example, name
    au = (ROOT / "deploy" / "regions" / "ap-southeast-2.env.example").read_text(encoding="utf-8")
    uk = (ROOT / "deploy" / "regions" / "eu-west-2.env.example").read_text(encoding="utf-8")
    assert "DEPLOYMENT_REGION=ap-southeast-2" in au and "TWILIO_EDGE=sydney" in au and "CLINIKO_SHARD=au1" in au
    assert "DEPLOYMENT_REGION=eu-west-2" in uk and "TWILIO_EDGE=dublin" in uk and "CLINIKO_SHARD=uk1" in uk
    for profile in (au, uk):
        for line in profile.splitlines():
            key = line.split("=", 1)[0].strip()
            if line and not line.startswith("#") and key not in ("APP_ENV",):
                assert key in Settings.model_fields, f"{key} in a region profile is not a setting"
