"""
Phase 3 follow-ups: guest ID numbers encrypted at rest (Task 3.8), tenant settings cache shared
across processes (Task 3.4), greeting audio shared across workers (Task 3.4), the stand-alone
telephony fallback mode (Task 3.5) and the concurrent-call limit bounds (Task 3.6).
"""

import pytest
from pydantic import ValidationError

from agent import greeting
from config import Settings
from core import crypto
from core.sensitive import id_number_fields, public_guest, reveal_id_number
from core.tenancy import resolver
from core.tenancy.models import Limits, TenantConfig
from migrations import m0004_encrypt_guest_ids as m0004

ADMIN_KEY = "admin-key-0123456789abcdef0123456789abcdef"


@pytest.fixture
def admin(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_SECRET_KEY", ADMIN_KEY)
    client.headers["X-Admin-Key"] = ADMIN_KEY
    return client


# ── Guest ID numbers ────────────────────────────────────────────────────────


def test_id_number_is_encrypted_and_only_shown_masked():
    fields = id_number_fields(" AB1234567 ")
    assert fields["id_number_last4"] == "4567"
    assert "AB1234567" not in fields["id_number_enc"]
    assert crypto.decrypt(fields["id_number_enc"]) == "AB1234567"
    doc = {"phone": "+8801700000000", **fields}
    view = public_guest(doc)
    assert view["id_number_masked"] == "•••4567"
    assert not {"id_number", "id_number_enc", "id_number_last4"} & set(view)
    assert reveal_id_number(doc) == "AB1234567"
    assert id_number_fields("") == {"id_number_enc": None, "id_number_last4": None}


def test_legacy_plaintext_is_masked_on_the_way_out():
    view = public_guest({"phone": "+1", "id_number": "NID99887766"})
    assert view["id_number_masked"] == "•••7766" and "id_number" not in view


async def test_admin_patch_stores_ciphertext_and_returns_mask(admin, db):
    await db.guests.insert_one({"phone": "+8801755555555", "name": "Rahim", "id_number": "OLDPLAIN1234"})
    r = admin.patch("/admin/guests/by-phone", params={"phone": "+8801755555555"},
                    json={"id_number": "P9876543"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id_number_masked"] == "•••6543"
    assert "P9876543" not in r.text and "OLDPLAIN1234" not in r.text
    stored = await db.guests.find_one({"phone": "+8801755555555"})
    assert "id_number" not in stored                          # legacy plaintext removed
    assert crypto.decrypt(stored["id_number_enc"]) == "P9876543"
    detail = admin.get("/admin/guests/by-phone", params={"phone": "+8801755555555"})
    assert detail.status_code == 200 and "P9876543" not in detail.text


async def test_migration_encrypts_plaintext_and_is_idempotent(db):
    await db.guests.insert_many([
        {"phone": "+1", "id_number": "A1111"},
        {"phone": "+2", "id_number": ""},
        {"phone": "+3", "name": "no id"},
    ])
    dry = await m0004.run(db, apply=False)
    assert dry == {"plaintext_found": 1, "converted": 0}
    assert (await db.guests.find_one({"phone": "+1"}))["id_number"] == "A1111"     # dry run writes nothing

    assert await m0004.run(db, apply=True) == {"plaintext_found": 1, "converted": 1}
    one = await db.guests.find_one({"phone": "+1"})
    assert "id_number" not in one and crypto.decrypt(one["id_number_enc"]) == "A1111"
    assert "id_number" not in await db.guests.find_one({"phone": "+2"})
    assert await m0004.run(db, apply=True) == {"plaintext_found": 0, "converted": 0}


# ── Tenant cache across processes ───────────────────────────────────────────


def _tenant(tid: str, name: str) -> dict:
    return TenantConfig(_id=tid, name=name, slug=tid.replace("_", "-"), status="active").to_doc()


@pytest.fixture
def fresh_resolver(monkeypatch):
    resolver._clear_local()
    monkeypatch.setattr(resolver, "_generation", (0.0, None))
    yield
    resolver._clear_local()


async def test_another_process_saving_clears_our_cache(db, redis, fresh_resolver, monkeypatch):
    await db.tenants.insert_one(_tenant("ten_a", "Before"))
    assert (await resolver.resolve_by_id("ten_a")).name == "Before"

    # Another replica saves the settings: the database changes and it bumps the generation.
    await db.tenants.update_one({"_id": "ten_a"}, {"$set": {"name": "After"}})
    assert (await resolver.resolve_by_id("ten_a")).name == "Before"     # still cached here
    await redis.incr(resolver.GENERATION_KEY)

    monkeypatch.setattr(resolver, "GENERATION_CHECK_S", 0.0)             # don't wait the 1 s
    assert (await resolver.resolve_by_id("ten_a")).name == "After"


async def test_invalidate_bumps_the_shared_generation(db, redis, fresh_resolver):
    resolver.invalidate("ten_a")
    await resolver.drain_pending()
    assert await redis.get(resolver.GENERATION_KEY) == "1"


async def test_without_redis_invalidate_is_local_only(db, fresh_resolver):
    resolver.invalidate("ten_a")
    await resolver.drain_pending()                                      # nothing queued, nothing raised


async def test_redis_outage_keeps_serving_the_cache(db, fresh_resolver, monkeypatch):
    from core import redis as redis_store

    class Down:
        async def get(self, *_):
            raise ConnectionError("down")

    await db.tenants.insert_one(_tenant("ten_b", "Cached"))
    redis_store.use_for_tests(Down())
    try:
        monkeypatch.setattr(resolver, "GENERATION_CHECK_S", 0.0)
        assert (await resolver.resolve_by_id("ten_b")).name == "Cached"
        assert (await resolver.resolve_by_id("ten_b")).name == "Cached"
    finally:
        redis_store.use_for_tests(None)


# ── Greeting audio shared across workers ────────────────────────────────────


async def test_greeting_rendered_by_one_worker_is_used_by_another(redis, tmp_path, monkeypatch):
    monkeypatch.setattr(greeting, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(greeting, "_memory", {})
    pcm = b"\x01\x02" * 4800
    await greeting._store_shared("k1", pcm, 24000, 1)                    # worker A rendered it

    assert greeting.load("k1") is None                                   # worker B: not on its disk
    assert await greeting.load_shared("k1") == (pcm, 24000, 1)
    assert greeting.load("k1") == (pcm, 24000, 1)                        # now cached locally too


async def test_shared_greeting_misses_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(greeting, "CACHE_DIR", tmp_path)
    assert await greeting.load_shared("missing") is None                 # no Redis at all


# ── Stand-alone telephony fallback and limits ───────────────────────────────

SAFE_PROD = dict(APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.example.com",
                 VERIFY_TWILIO_SIGNATURE=True, WEBHOOK_BASE_URL="https://fallback.example.com",
                 META_APP_SECRET="", ENABLE_WHATSAPP=False, LOG_LEVEL="INFO", REQUIRE_ADMIN_MFA=True,
                 COOKIE_SECURE=True, REDIS_URL="")


def test_fallback_host_boots_without_redis_but_the_full_api_does_not():
    assert Settings(_env_file=None, **SAFE_PROD, TELEPHONY_FALLBACK_ONLY=True).is_production
    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(_env_file=None, **SAFE_PROD)


def test_fallback_app_refuses_to_start_without_the_flag():
    import importlib
    import sys

    sys.modules.pop("fallback_app", None)
    with pytest.raises(SystemExit, match="TELEPHONY_FALLBACK_ONLY"):
        importlib.import_module("fallback_app")


def test_concurrent_call_limit_bounds():
    assert Limits().max_concurrent_calls == 3
    with pytest.raises(ValidationError):
        Limits(max_concurrent_calls=0)
    with pytest.raises(ValidationError):
        Limits(max_concurrent_calls=501)
