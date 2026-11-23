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


# ── Phase 3: SEC-01 client address ─────────────────────────────────────────

def _request(headers: dict, peer: str = "203.0.113.9"):
    from starlette.requests import Request

    scope = {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": (peer, 1234)}
    return Request(scope)


def test_a_visitor_cant_pick_their_own_address(monkeypatch):
    from core.ratelimit import client_ip

    monkeypatch.setattr(settings, "BEHIND_CLOUDFLARE", False)
    forged = _request({"CF-Connecting-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"})
    assert client_ip(forged) == "203.0.113.9"           # uvicorn's peer, headers ignored
    monkeypatch.setattr(settings, "BEHIND_CLOUDFLARE", True)
    assert client_ip(forged) == "1.2.3.4"               # only when Cloudflare really is in front


async def test_the_unscoped_legacy_handle_is_refused_once_tenants_exist(db, monkeypatch):
    from core.repositories.scoped import LegacyScopeError, ScopedDB, scoped_db
    from core.tenancy import resolver
    from core.tenancy.models import LEGACY_TENANT_ID

    legacy = TenantConfig.model_validate({"_id": LEGACY_TENANT_ID, "name": "Hotel", "slug": "hotel"})
    monkeypatch.setattr(resolver, "_legacy_mode", (0.0, True))
    assert not isinstance(scoped_db(legacy), ScopedDB)             # pre-migration single hotel: raw db
    monkeypatch.setattr(resolver, "_legacy_mode", (0.0, False))     # tenants exist now
    with pytest.raises(LegacyScopeError):
        scoped_db(legacy)
    monkeypatch.setattr(resolver, "_legacy_mode", (0.0, True))
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(LegacyScopeError):
        scoped_db(legacy)


def test_production_needs_a_real_encryption_key():
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from config import Settings

    safe = dict(APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
                REDIS_URL="redis://redis:6379/0", VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://api.example.com",
                META_APP_SECRET="s", LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True, COOKIE_SECURE=True)
    assert Settings(_env_file=None, **safe, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()).is_production
    for bad in ("", "not-a-fernet-key", f"{Fernet.generate_key().decode()},broken"):
        with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
            Settings(_env_file=None, **safe, TOKEN_ENCRYPTION_KEY=bad)


def test_key_rotation_reencrypts_every_enc_field_and_counts_unreadable():
    from cryptography.fernet import Fernet, MultiFernet

    from scripts.rotate_encryption_key import rotate_doc

    old, new, other = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    doc = {"_id": "x", "mfa": {"secret_enc": old.encrypt(b"s1").decode()},
           "items": [{"problem_enc": old.encrypt(b"p").decode()}, {"name": "keep"}],
           "api_key_enc": other.encrypt(b"k").decode(), "plain": "untouched"}
    sets, bad = rotate_doc(doc, MultiFernet([new, old]))
    assert set(sets) == {"mfa.secret_enc", "items.0.problem_enc"} and bad == 1
    assert new.decrypt(sets["mfa.secret_enc"].encode()) == b"s1"      # now readable with the new key alone
