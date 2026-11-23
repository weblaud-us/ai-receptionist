"""TELEPHONY_PROVIDER=livekit: a US number rented in LiveKit Cloud, routed by a trunk-less dispatch rule."""

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


def test_provider_can_be_chosen_in_development():
    assert _dev(TELEPHONY_PROVIDER="livekit").telephony_provider == "livekit"
    assert _dev().telephony_provider == "softphone"          # the default is unchanged


def test_production_accepts_livekit_numbers_but_still_refuses_softphone():
    assert Settings(_env_file=None, **{**SAFE_PROD, **LK, "TELEPHONY_PROVIDER": "livekit"}).is_production
    with pytest.raises(ValidationError, match="softphone"):
        Settings(_env_file=None, **{**SAFE_PROD, **LK, "TELEPHONY_PROVIDER": "softphone"})


def test_the_rule_has_its_own_name_and_no_trunk():
    s = _dev(TELEPHONY_PROVIDER="livekit")
    assert st.dispatch_rule_name(s) == "receptionist-development-number"
    assert st.dispatch_rule_name(_dev()) == "receptionist-development"      # softphone name untouched
    rule = st.build_dispatch_rule(s, [])
    assert list(rule.trunk_ids) == []
    assert rule.rule.dispatch_rule_individual.room_prefix == "call-"
    assert [a.agent_name for a in rule.room_config.agents] == ["receptionist"]


def test_it_needs_only_livekit_credentials():
    assert st.problems(_dev(TELEPHONY_PROVIDER="livekit")) == []                     # no softphone creds needed
    assert any("LIVEKIT_API_KEY" in p for p in st.problems(_dev(TELEPHONY_PROVIDER="livekit", LIVEKIT_API_KEY="")))
