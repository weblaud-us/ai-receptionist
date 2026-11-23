"""APP_ENV picks the phone provider; scripts.setup_telephony builds the matching LiveKit objects."""

import pytest
from pydantic import ValidationError

from config import Settings
from scripts import setup_telephony as st

SAFE_PROD = dict(
    APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
    REDIS_URL="redis://:test-redis-pw@redis:6379/0",
    VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://api.example.com",
    META_APP_SECRET="meta-secret", LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True,
)
LK = dict(LIVEKIT_URL="wss://abc123.livekit.cloud", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s",
          LIVEKIT_AGENT_NAME="receptionist")


def _dev(**o) -> Settings:
    return Settings(_env_file=None, APP_ENV="development", **{**LK, **o})


def _prod(**o) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PROD, **LK, **o})


# ── APP_ENV → provider ──────────────────────────────────

def test_development_uses_softphone():
    assert _dev().telephony_provider == "softphone"


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_use_twilio(env):
    s = _prod() if env == "production" else Settings(_env_file=None, APP_ENV="staging")
    assert s.telephony_provider == "twilio"


def test_explicit_provider_overrides_app_env():
    assert _dev(TELEPHONY_PROVIDER="twilio").telephony_provider == "twilio"


def test_production_refuses_softphone():
    with pytest.raises(ValidationError, match="softphone"):
        _prod(TELEPHONY_PROVIDER="softphone")


def test_softphone_numbers_are_split_and_trimmed():
    assert _dev(SOFTPHONE_TEST_NUMBERS=" +15550000001 , +15550000002,").softphone_numbers == [
        "+15550000001", "+15550000002"]


# ── builders ────────────────────────────────────────────

def test_softphone_inbound_trunk():
    s = _dev(SOFTPHONE_SIP_USERNAME="15557654321", SOFTPHONE_SIP_PASSWORD="pw")
    t = st.build_inbound_trunk(s, s.softphone_numbers)
    assert t.name == "softphone-inbound-development"
    assert list(t.numbers) == ["+15550000001"]
    assert (t.auth_username, t.auth_password) == ("15557654321", "pw")
    assert t.krisp_enabled


def test_twilio_inbound_trunk_uses_twilio_credentials():
    s = _prod(TWILIO_SIP_INBOUND_USERNAME="tw", TWILIO_SIP_INBOUND_PASSWORD="tw-pw")
    t = st.build_inbound_trunk(s, ["+1 (555) 123-4567", "+15551234567"])
    assert t.name == "twilio-inbound-production"
    assert list(t.numbers) == ["+15551234567"]          # normalised and de-duplicated
    assert (t.auth_username, t.auth_password) == ("tw", "tw-pw")


def test_dispatch_rule_makes_call_rooms_for_the_agent():
    s = _dev(LIVEKIT_AGENT_NAME="front-desk")
    r = st.build_dispatch_rule(s, ["ST_abc"])
    assert r.name == "receptionist-development"
    assert list(r.trunk_ids) == ["ST_abc"]
    assert r.rule.dispatch_rule_individual.room_prefix == "call-"
    assert [a.agent_name for a in r.room_config.agents] == ["front-desk"]


def test_outbound_trunk_points_at_twilio_termination():
    s = _prod(TWILIO_TERMINATION_URI="acme.pstn.twilio.com",
              TWILIO_SIP_OUTBOUND_USERNAME="u", TWILIO_SIP_OUTBOUND_PASSWORD="p")
    t = st.build_outbound_trunk(s, ["+15551234567"])
    assert (t.name, t.address) == ("twilio-outbound-production", "acme.pstn.twilio.com")
    assert (t.auth_username, t.auth_password) == ("u", "p")


# ── idempotence + preflight ─────────────────────────────

def test_same_ignores_server_fields():
    s = _dev(SOFTPHONE_SIP_USERNAME="u", SOFTPHONE_SIP_PASSWORD="p")
    desired = st.build_inbound_trunk(s, s.softphone_numbers)
    stored = st.build_inbound_trunk(s, s.softphone_numbers)
    stored.sip_trunk_id = "ST_123"
    assert st.same(stored, desired)
    stored.numbers.append("+15550000009")
    assert not st.same(stored, desired)


def test_softphone_needs_credentials():
    assert any("SOFTPHONE_SIP_USERNAME" in p for p in st.problems(_dev()))
    ok = _dev(SOFTPHONE_SIP_USERNAME="u", SOFTPHONE_SIP_PASSWORD="p")
    assert st.problems(ok) == []


def test_sip_host_from_livekit_cloud_url():
    assert st.sip_host_from_livekit_url("wss://abc123.livekit.cloud") == "abc123.sip.livekit.cloud"
    assert st.sip_host_from_livekit_url("wss://lk.mycompany.com") is None
