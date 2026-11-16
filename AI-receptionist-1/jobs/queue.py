"""
Put a job on the queue from the API.

    if not await enqueue("whatsapp_inbound", payload):
        ...run it in this process instead...

Returns False when there is no Redis (development, tests) or Redis is unreachable, so the caller
keeps its in-process fallback and nothing is lost when the queue is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import settings
from core import redis as redis_store

logger = logging.getLogger(__name__)

DEAD_LETTER_KEY = "jobs:dead"
DEAD_LETTER_MAX = 1000

_pool: Optional[Any] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None


async def _get_pool() -> Any:
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is None or _pool_loop is not loop:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis_settings = RedisSettings.from_dsn(settings.REDIS_URL.strip())
        redis_settings.conn_timeout = int(redis_store.CONNECT_TIMEOUT_S)
        redis_settings.conn_retries = 0
        _pool = await create_pool(redis_settings)
        _pool_loop = loop
    return _pool


async def enqueue(name: str, *args: Any, defer_s: Optional[float] = None, job_id: Optional[str] = None) -> bool:
    """Queue job `name` (a function registered in jobs.worker). `job_id` makes it unique."""
    if not settings.redis_enabled:
        return False
    try:
        pool = await _get_pool()
        await pool.enqueue_job(name, *args, _defer_by=defer_s, _job_id=job_id)
        return True
    except Exception:
        global _pool
        _pool = None                      # reconnect next time
        logger.warning("Could not queue job %s; running it in this process instead", name, exc_info=True)
        return False


async def close() -> None:
    global _pool, _pool_loop
    pool, _pool, _pool_loop = _pool, None, None
    if pool is not None:
        try:
            await pool.aclose()
        except Exception:
            logger.debug("Job queue close failed", exc_info=True)
