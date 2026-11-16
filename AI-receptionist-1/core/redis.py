"""
Redis client (Roadmap Task 3.4) — shared state for every API replica, voice worker and job worker.

    r = get_redis()          # None when REDIS_URL is empty (development, tests)
    if r is not None: await r.set(...)

Callers never require Redis: each feature built on it (core.ratelimit, core.dedupe,
core.concurrency, jobs.queue) has a documented behaviour when Redis is missing or down, so a
Redis outage degrades limits, never calls.

One client per event loop: every voice call runs in its own process and loop, and a client must
not be shared across loops.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 2.0
SOCKET_TIMEOUT_S = 2.0

_client: Optional[Any] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_override: Optional[Any] = None          # tests: an in-memory fake
_last_warning = 0.0


def enabled() -> bool:
    return _override is not None or settings.redis_enabled


def get_redis() -> Optional[Any]:
    """The client for the running loop, or None when Redis is not configured."""
    global _client, _loop
    if _override is not None:
        return _override
    if not settings.redis_enabled:
        return None
    loop = asyncio.get_running_loop()
    if _client is None or _loop is not loop:
        from redis.asyncio import Redis

        _client = Redis.from_url(
            settings.REDIS_URL.strip(),
            decode_responses=True,
            socket_connect_timeout=CONNECT_TIMEOUT_S,
            socket_timeout=SOCKET_TIMEOUT_S,
            health_check_interval=30,
        )
        _loop = loop
    return _client


async def ping(timeout: float = 1.0) -> bool:
    r = get_redis()
    if r is None:
        return False
    try:
        return bool(await asyncio.wait_for(r.ping(), timeout))
    except Exception:
        return False


async def close() -> None:
    global _client, _loop
    client, _client, _loop = _client, None, None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.debug("Redis close failed", exc_info=True)


def warn_unavailable(what: str) -> None:
    """Log a Redis failure at most once every 30 s — an outage must not flood the logs."""
    global _last_warning
    now = time.monotonic()
    if now - _last_warning > 30:
        _last_warning = now
        logger.warning("Redis unavailable (%s); using this process's own memory until it is back", what,
                       exc_info=True)


def use_for_tests(client: Optional[Any]) -> None:
    """Install (or remove, with None) a fake client. Tests only."""
    global _override
    _override = client
