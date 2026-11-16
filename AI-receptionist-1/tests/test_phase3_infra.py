"""
Phase 3: shared state in Redis (rate limits, dedupe, per-business call limit), the job queue's
retry / dead-letter rules, atomic reminder claims, metrics, health checks, security headers and
the Twilio disaster-recovery TwiML.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from arq import Retry
from fastapi import HTTPException
from twilio.request_validator import RequestValidator

from agent.health import record_availability, record_error
from agent.persistence import call_document
from agent.profile import load_profile
from agent.state import CallState
from config import settings
from core import concurrency, dedupe, metrics, ratelimit
from core import redis as redis_store
from core.auth import service as auth_service
from jobs import worker as jobs_worker
from jobs.queue import DEAD_LETTER_KEY, enqueue
from routers.telephony import dialed_number, fallback_twiml
from services.reminders import BookingReminderService

# ── Rate limits ─────────────────────────────────────────────────────────────


async def test_sliding_limit_in_memory_without_redis():
    assert redis_store.get_redis() is None
    assert [await ratelimit.allow("k", 2, 60) for _ in range(3)] == [True, True, False]
    assert await ratelimit.allow("other", 2, 60)                    # keys are independent


async def test_sliding_limit_is_shared_through_redis(redis):
    assert [await ratelimit.allow("k", 2, 60) for _ in range(3)] == [True, True, False]
    assert await redis.zcard("rl:k") == 2                           # the refused hit is not kept
    assert ratelimit.memory.count("k", 60) == 0                     # nothing fell back to memory


async def test_count_hit_clear_through_redis(redis):
    await ratelimit.hit("fails", 900)
    await ratelimit.hit("fails", 900)
    assert await ratelimit.count("fails", 900) == 2
    await ratelimit.clear("fails")
    assert await ratelimit.count("fails", 900) == 0


async def test_fixed_window_limit(redis):
    assert [await ratelimit.allow_fixed("api:t1", 2, 60) for _ in range(3)] == [True, True, False]
    assert await ratelimit.allow_fixed("api:t2", 2, 60)


async def test_redis_outage_falls_back_to_memory_and_still_limits(monkeypatch):
    class Down:
        def pipeline(self, **_):
            raise ConnectionError("redis is down")

    redis_store.use_for_tests(Down())
    try:
        assert [await ratelimit.allow("k", 1, 60) for _ in range(2)] == [True, False]
        assert [await ratelimit.allow_fixed("f", 1, 60) for _ in range(2)] == [True, False]
    finally:
        redis_store.use_for_tests(None)


async def test_login_lockout_is_counted_in_redis(db, redis):
    for _ in range(auth_service.MAX_FAILS_PER_ACCOUNT):
        with pytest.raises(HTTPException) as e:
            await auth_service.login("nobody@example.com", "wrong", ip="10.0.0.1")
        assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        await auth_service.login("nobody@example.com", "wrong", ip="10.0.0.2")   # another replica / IP
    assert e.value.status_code == 429
    assert await redis.zcard("rl:login:acct:nobody@example.com") == auth_service.MAX_FAILS_PER_ACCOUNT


def test_auth_endpoints_are_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_PER_IP_MIN", 3)
    codes = [client.post("/auth/login", json={"email": "a@example.com", "password": "x"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    assert client.get("/livez").status_code == 200                 # only sign-in endpoints are limited
    assert client.post("/auth/refresh").status_code == 401         # refresh is never rate limited
    assert client.post("/auth/logout").status_code != 429


# ── Dedupe ──────────────────────────────────────────────────────────────────


async def test_first_time_in_memory():
    assert await dedupe.first_time("wa", "wamid.1")
    assert not await dedupe.first_time("wa", "wamid.1")
    assert await dedupe.first_time("wa", "wamid.2")
    assert await dedupe.first_time("wa", None)                      # no id: can't dedupe, let it through


async def test_first_time_uses_set_nx_with_a_ttl(redis):
    assert await dedupe.first_time("wa", "wamid.1")
    assert not await dedupe.first_time("wa", "wamid.1")
    assert 0 < await redis.ttl("seen:wa:wamid.1") <= dedupe.DEFAULT_TTL_S


async def test_whatsapp_retry_is_skipped(redis):
    from whatsapp.meta_handler import _seen_meta_message_recently

    assert not await _seen_meta_message_recently("wamid.9")
    assert await _seen_meta_message_recently("wamid.9")


# ── Per-business concurrent calls ───────────────────────────────────────────


async def test_call_limit_refuses_the_call_over_the_limit(redis):
    assert await concurrency.acquire("ten_1", "room-a", 2, 900)
    assert await concurrency.acquire("ten_1", "room-b", 2, 900)
    assert not await concurrency.acquire("ten_1", "room-c", 2, 900)
    assert await concurrency.active("ten_1") == 2                   # the refused call holds no slot
    assert await concurrency.acquire("ten_2", "room-x", 2, 900)     # another business is unaffected
    await concurrency.release("ten_1", "room-a")
    assert await concurrency.acquire("ten_1", "room-c", 2, 900)


async def test_crashed_call_frees_its_slot_after_the_ttl(redis):
    await redis.zadd("calls:active:ten_1", {"room-dead": 1.0})      # started long ago, never released
    assert await concurrency.acquire("ten_1", "room-a", 1, 900)
    assert await redis.zscore("calls:active:ten_1", "room-dead") is None


async def test_without_redis_calls_are_never_refused():
    assert await concurrency.acquire("ten_1", "room-a", 1, 900)
    assert await concurrency.acquire("ten_1", "room-b", 1, 900)


# ── Job queue ───────────────────────────────────────────────────────────────


def test_backoff_doubles_and_is_capped():
    assert [jobs_worker.backoff_s(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert jobs_worker.backoff_s(20) == jobs_worker.BACKOFF_MAX_S


async def test_failing_job_retries_then_goes_to_the_dead_letter_list(redis):
    @jobs_worker.job("flaky", max_tries=3)
    async def flaky(ctx):
        raise RuntimeError("provider said no to +15551234567")

    with pytest.raises(Retry) as e:
        await flaky({"job_try": 1, "redis": redis, "job_id": "j1"})
    assert e.value.defer_score == 30_000
    with pytest.raises(Retry):
        await flaky({"job_try": 2, "redis": redis, "job_id": "j1"})
    assert await redis.llen(DEAD_LETTER_KEY) == 0

    with pytest.raises(RuntimeError):
        await flaky({"job_try": 3, "redis": redis, "job_id": "j1"})
    dead = json.loads((await redis.lrange(DEAD_LETTER_KEY, 0, -1))[0])
    assert (dead["job"], dead["tries"], dead["job_id"]) == ("flaky", 3, "j1")
    assert "+15551234567" not in dead["error"]                      # redacted, and no arguments stored


async def test_successful_job_returns_its_result(redis):
    @jobs_worker.job("fine", max_tries=3)
    async def fine(ctx, x):
        return x * 2

    assert await fine({"job_try": 1, "redis": redis}, 21) == 42
    assert await redis.llen(DEAD_LETTER_KEY) == 0


async def test_enqueue_reports_false_without_redis_so_callers_run_inline():
    assert await enqueue("whatsapp_inbound", {}) is False


def test_worker_registers_the_jobs_and_schedule():
    ws = jobs_worker.WorkerSettings
    assert [f.name for f in ws.functions] == ["whatsapp_inbound"]
    assert {c.name for c in ws.cron_jobs} == {"tenant_jobs_tick", "booking_reminders_tick", "sales_dialer_tick"}
    assert jobs_worker.every_minutes(5) == set(range(0, 60, 5))
    assert jobs_worker.every_minutes(30) == {0, 30}
    assert jobs_worker.every_minutes(0.2) == set(range(60))         # never less than every minute


# ── Hotel reminders: claimed atomically, sent once ──────────────────────────


@pytest.fixture
def reminder_env(monkeypatch):
    sent = []

    async def fake_send(phone, message, phone_number_id):
        sent.append(phone)
        return True

    monkeypatch.setattr("services.reminders._send_meta_whatsapp", fake_send)
    return sent


async def _booking(db, **extra):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    doc = {"booking_id": "HTL1", "status": "confirmed", "guest_phone": "+8801712345678",
           "check_in_date": tomorrow, "check_out_date": tomorrow, "room_type": "deluxe", **extra}
    await db.bookings.insert_one(doc)
    return doc


async def test_reminder_is_sent_once_however_many_runners(db, reminder_env):
    await _booking(db)
    hotel = {"timezone": "UTC", "name": "Test Hotel"}
    service = BookingReminderService()
    await service._process_hotel(hotel)
    await service._process_hotel(hotel)                             # a second runner / the next tick
    assert reminder_env == ["+8801712345678"]
    saved = await db.bookings.find_one({"booking_id": "HTL1"})
    assert saved["one_day_reminder_sent"] is True and saved["one_day_reminder_claimed_at"]


async def test_reminder_claimed_by_another_runner_is_left_alone(db, reminder_env):
    await _booking(db, one_day_reminder_claimed_at=datetime.utcnow())
    await BookingReminderService()._process_hotel({"timezone": "UTC", "name": "Test Hotel"})
    assert reminder_env == []


async def test_stale_claim_whose_send_failed_is_retried(db, reminder_env):
    await _booking(db, one_day_reminder_claimed_at=datetime.utcnow() - timedelta(hours=2),
                   one_day_reminder_sent=False)
    await BookingReminderService()._process_hotel({"timezone": "UTC", "name": "Test Hotel"})
    assert reminder_env == ["+8801712345678"]


# ── Metrics and health ──────────────────────────────────────────────────────

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _call(**kw):
    return {"channel": "phone", "outcome": "booked", "duration_s": 95.0, "ended_at": NOW - timedelta(minutes=5),
            "greeting_latency_s": 0.6, "turns": [{"latency_s": 0.8, "tool_turn": False}], **kw}


def test_call_stats_counts_outcomes_latency_and_provider_health():
    stats = metrics.call_stats([
        _call(turns=[{"latency_s": 0.8, "tool_turn": False}, {"latency_s": 2.1, "tool_turn": True}],
              fallbacks={"tts": 1}, provider_errors={"tts": 2, "llm": 1}, transfer="failed"),
        _call(outcome="completed", duration_s=4.0),                                  # a broken-call signal
        _call(outcome="busy", duration_s=3.0),                                       # refused on purpose: not "short"
        _call(ended_at=NOW - timedelta(minutes=40), turns=[{"latency_s": 9.9}]),     # too old for latency
    ], now=NOW)
    assert stats["by_outcome"][("phone", "booked")] == 2
    assert stats["short"] == 1
    assert sorted(stats["plain"]) == [0.8, 0.8, 0.8] and stats["tool"] == [2.1]
    assert stats["errors"] == {"tts": 2, "llm": 1} and stats["fallbacks"] == {"tts": 1}
    assert stats["transfers"] == {"failed": 1}
    text = metrics.render_call_metrics(stats, active=3, deps={"mongo": True, "redis": False}, dead_jobs=2).decode()
    assert "receptionist_calls_active 3.0" in text
    assert 'receptionist_turn_latency_seconds{kind="tool",quantile="0.95"} 2.1' in text
    assert 'receptionist_provider_fallbacks_last_hour{kind="tts"} 1.0' in text
    assert 'receptionist_dependency_up{name="redis"} 0.0' in text
    assert "receptionist_jobs_dead 2.0" in text


def test_metrics_endpoint_needs_the_token_outside_development(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "m" * 32)
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    r = client.get("/metrics", headers={"Authorization": "Bearer " + "m" * 32})
    assert r.status_code == 200 and "receptionist_calls_active" in r.text
    monkeypatch.setattr(settings, "METRICS_TOKEN", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert client.get("/metrics").status_code == 404                # no token in production = off


def test_http_requests_are_counted_by_route_template(client):
    client.get("/livez")
    client.get("/call/some-room-id")
    text = client.get("/metrics").text                              # development: open without a token
    assert 'http_requests_total{method="GET",route="/livez",status="200"}' in text
    assert 'route="/call/{room_id}"' in text and "some-room-id" not in text


def test_readyz_reports_redis_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")       # nothing listens there
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"] == {"database": "ok", "redis": "fail"}


async def test_readyz_ok_with_redis(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake/0")
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["redis"] == "ok"


def test_security_headers(client):
    h = client.get("/livez").headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in h                     # development; set outside it
    assert "X-Frame-Options" not in client.get("/call/some-room-id").headers   # the call page may be embedded


# ── Provider health on the call record ──────────────────────────────────────


class _Err:
    def __init__(self, type_):
        self.type, self.label, self.recoverable = type_, "provider", False


def test_provider_errors_and_fallbacks_land_on_the_call_document():
    state = CallState(call_id="room-1", profile=load_profile())
    record_error(state, _Err("tts_error"))
    record_error(state, _Err("tts_error"))
    record_error(state, _Err("llm_error"))
    record_error(state, _Err("something_else"))                     # ignored
    record_availability(state, "tts", object(), available=False)
    record_availability(state, "tts", object(), available=True)     # recovery is not a fallback
    state.transfer = "ok"
    doc = call_document(state, final=True)
    assert doc["provider_errors"] == {"tts": 2, "llm": 1}
    assert doc["fallbacks"] == {"tts": 1} and doc["transfer"] == "ok"


def test_busy_outranks_every_other_outcome():
    state = CallState(call_id="room-1", profile=load_profile())
    state.set_outcome("busy")
    state.set_outcome("completed")
    assert state.outcome == "busy"


# ── Twilio disaster-recovery TwiML ──────────────────────────────────────────

FALLBACK_URL = "https://receptionist.example.com/telephony/fallback"
FORM = {"CallSid": "CA" + "0" * 32, "From": "+15557654321", "To": "sip:+15550001111@acme.pstn.twilio.com"}


def test_dialed_number_from_e164_or_sip_uri():
    assert dialed_number("+15550001111") == "+15550001111"
    assert dialed_number("sip:+15550001111@acme.pstn.twilio.com") == "+15550001111"
    assert dialed_number("15550001111") == "+15550001111"
    assert dialed_number("") is None


def test_fallback_twiml_rings_the_staff_line_or_apologises():
    tenant = load_profile().tenant.model_copy(deep=True)
    tenant.transfer.number = "+15559998888"
    xml = fallback_twiml(tenant)
    assert "<Dial" in xml and "+15559998888" in xml and "<Hangup" in xml
    assert "<Dial" not in fallback_twiml(None) and "<Say>" in fallback_twiml(None)


def test_fallback_rejects_unsigned_requests(client):
    assert client.post("/telephony/fallback", data=FORM).status_code == 403


def test_fallback_answers_signed_twilio_request(client):
    sig = RequestValidator("test-twilio-auth-token").compute_signature(FALLBACK_URL, FORM)
    r = client.post("/telephony/fallback", data=FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<Response>" in r.text and "<Hangup" in r.text


def test_job_arguments_never_reach_the_logs():
    import logging

    record = logging.LogRecord("arq.worker", logging.INFO, __file__, 1, "%6.2fs → %s(%s)%s",
                               (0.04, "abc:whatsapp_inbound", "{'from': '+15551234567', 'text': 'my card'}", ""),
                               None)
    jobs_worker.HideJobArguments().filter(record)
    assert record.getMessage() == "  0.04s → abc:whatsapp_inbound(…)"
    other = logging.LogRecord("arq.worker", logging.INFO, __file__, 1, "Starting worker for %d functions: %s",
                              (1, "whatsapp_inbound"), None)
    jobs_worker.HideJobArguments().filter(other)
    assert other.getMessage() == "Starting worker for 1 functions: whatsapp_inbound"
