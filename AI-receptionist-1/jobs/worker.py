"""
The jobs worker (arq): everything that must not run inside an API request or a phone call.

    python -m jobs                       # or: arq jobs.worker.WorkerSettings
    arq --check jobs.worker.WorkerSettings   # exit 0 while a worker is alive (container health check)

| Job                     | When                              | What                                             |
|-------------------------|-----------------------------------|--------------------------------------------------|
| tenant_jobs_tick        | every TENANT_JOBS_POLL_SECONDS    | calendar sync, appointment reminders, owner       |
|                         |                                   | digests, transcript retention (services.tenant_jobs) |
| booking_reminders_tick  | every BOOKING_REMINDER_POLL_MINUTES | hotel day-before WhatsApp reminders             |
| whatsapp_inbound        | queued by the Meta webhook        | read the message, run the agent, send the reply  |
| sales_dialer_tick       | every 20 s                        | outbound sales dialer: gate leads, start calls   |

Rules every job follows:
- Idempotent. Cron jobs are unique per tick across all workers (arq), and each reminder / digest
  is additionally claimed with an atomic database update, so running two workers, or a retry,
  never sends anything twice.
- Retries with exponential backoff (30 s, 60 s, 120 s … capped at 10 min) up to the job's
  `max_tries`; after the last failure the job is written to the dead-letter list
  (`python -m jobs dead`) and reported to Sentry.
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Set

from arq import Retry, cron, func
from arq.connections import RedisSettings

from config import settings
from core.log import configure_logging, redact
from core.observability import capture, init_sentry
from jobs.queue import DEAD_LETTER_KEY, DEAD_LETTER_MAX

logger = logging.getLogger("jobs")

BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 600

JobFn = Callable[..., Awaitable[Any]]


class HideJobArguments(logging.Filter):
    """arq logs every job as `name(<repr of its arguments>)`. A WhatsApp payload holds message text
    and phone numbers, so the arguments are replaced with `…` before the line is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and "→" in str(record.msg) and "(%s)" in str(record.msg):
            record.args = (*args[:2], "…", *args[3:])
        return True


def install_log_filter() -> None:
    log = logging.getLogger("arq.worker")
    if not any(isinstance(f, HideJobArguments) for f in log.filters):
        log.addFilter(HideJobArguments())


def backoff_s(attempt: int) -> int:
    """Delay before retry number `attempt` (1-based): 30, 60, 120 … capped."""
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** max(0, attempt - 1))


async def record_dead(redis: Any, name: str, ctx: Dict[str, Any], exc: BaseException) -> None:
    """Keep the last DEAD_LETTER_MAX failed jobs. No arguments are stored — they can hold PII."""
    entry = {"job": name, "job_id": ctx.get("job_id"), "tries": ctx.get("job_try"),
             "error": redact(f"{type(exc).__name__}: {exc}")[:500],
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        await redis.lpush(DEAD_LETTER_KEY, json.dumps(entry))
        await redis.ltrim(DEAD_LETTER_KEY, 0, DEAD_LETTER_MAX - 1)
    except Exception:
        logger.warning("Could not record dead job %s", name, exc_info=True)


def job(name: str, *, max_tries: int = 1) -> Callable[[JobFn], JobFn]:
    """Wrap a job: retry with backoff, then dead-letter. `max_tries=1` = never retry."""

    def decorate(fn: JobFn) -> JobFn:
        @functools.wraps(fn)
        async def run(ctx: Dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(ctx, *args, **kwargs)
            except Retry:
                raise
            except Exception as exc:
                attempt = int(ctx.get("job_try") or 1)
                if attempt < max_tries:
                    delay = backoff_s(attempt)
                    logger.warning("Job %s failed (try %d of %d); retrying in %ds", name, attempt, max_tries,
                                   delay, exc_info=True)
                    raise Retry(defer=delay) from exc
                logger.error("Job %s failed for good after %d tries", name, attempt, exc_info=True)
                capture(exc, job=name)
                if ctx.get("redis") is not None:
                    await record_dead(ctx["redis"], name, ctx, exc)
                raise

        return run

    return decorate


# ── Jobs ─────────────────────────────────────────────────────────────────────

@job("tenant_jobs_tick")
async def tenant_jobs_tick(ctx: Dict[str, Any]) -> Dict[str, int]:
    from services.tenant_jobs import run_once

    if not settings.ENABLE_TENANT_JOBS:
        return {}
    stats = await run_once()
    if any(stats.get(k) for k in ("reminders", "digests", "purged", "synced")):
        logger.info("Tenant jobs: %s", stats)
    return stats


@job("booking_reminders_tick")
async def booking_reminders_tick(ctx: Dict[str, Any]) -> None:
    from services.reminders import get_booking_reminder_service

    await get_booking_reminder_service().run_once()


@job("whatsapp_inbound")
async def whatsapp_inbound(ctx: Dict[str, Any], payload: Dict[str, Any]) -> None:
    # Not retried: each message id is marked as seen before it is processed, so a retry would
    # skip it anyway, and replying twice is worse than a rare missed reply.
    from whatsapp.meta_handler import _handle_webhook_payload

    await _handle_webhook_payload(payload)


@job("sales_dialer_tick")
async def sales_dialer_tick(ctx: Dict[str, Any]) -> Dict[str, int]:
    # Not retried: the next tick is 20 s away, and a retry could dial the same slot twice.
    from core.outbound import dialer
    from database.mongodb import get_db

    stats = await dialer.tick(get_db())
    if stats.get("dialed") or stats.get("reaped") or stats.get("completed"):
        logger.info("Sales dialer: %s", stats)
    return stats


# ── Worker ───────────────────────────────────────────────────────────────────

async def startup(ctx: Dict[str, Any]) -> None:
    from database.mongodb import connect_db

    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    install_log_filter()
    init_sentry("jobs")
    await connect_db(ensure_indexes=False)
    logger.info("Jobs worker started (env=%s)", settings.APP_ENV)


async def shutdown(ctx: Dict[str, Any]) -> None:
    from core import redis as redis_store
    from database.mongodb import disconnect_db

    await redis_store.close()
    await disconnect_db()
    logger.info("Jobs worker stopped")


def every_minutes(step: float) -> Set[int]:
    """Minutes of the hour for a job that runs every `step` minutes (1–60)."""
    return set(range(0, 60, max(1, min(60, round(step)))))


class WorkerSettings:
    # keep_result=0: arq stores a job's arguments with its result; a WhatsApp payload must not linger in Redis.
    functions = [func(whatsapp_inbound, name="whatsapp_inbound", max_tries=1, timeout=180, keep_result=0)]
    cron_jobs = [
        cron(tenant_jobs_tick, name="tenant_jobs_tick", minute=every_minutes(settings.TENANT_JOBS_POLL_SECONDS / 60),
             run_at_startup=True, timeout=280),
        cron(booking_reminders_tick, name="booking_reminders_tick",
             minute=every_minutes(settings.BOOKING_REMINDER_POLL_MINUTES), run_at_startup=True, timeout=600),
        # Always scheduled: with OUTBOUND_DIALER_ENABLED=false the tick only tidies up and returns.
        cron(sales_dialer_tick, name="sales_dialer_tick", second={0, 20, 40}, timeout=18, keep_result=0),
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL.strip() or "redis://localhost:6379/0")
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600
    health_check_interval = 30
