"""
Per-business concurrent call limit (Roadmap Task 3.6) — one tenant's spam storm must not use up
the capacity every other business depends on.

    if not await acquire(tenant_id, call_id, limit, ttl_s): → "all lines are busy" / transfer
    ...
    await release(tenant_id, call_id)                       # in the call's shutdown callback

Each live call is a member of a Redis sorted set scored by its start time. Members older than
`ttl_s` are dropped on every acquire, so a worker that crashed without releasing frees its slot by
itself. Every call runs in its own process, so this only works with Redis: without REDIS_URL, or
while Redis is unreachable, calls are never refused (answering matters more than the limit).
"""

from __future__ import annotations

import time

from core import redis as redis_store


def _key(tenant_id: str) -> str:
    return f"calls:active:{tenant_id}"


async def acquire(tenant_id: str, call_id: str, limit: int, ttl_s: int) -> bool:
    r = redis_store.get_redis()
    if r is None or limit <= 0:
        return True
    try:
        now, key = time.time(), _key(tenant_id)
        async with r.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - ttl_s)
            pipe.zadd(key, {call_id: now})
            pipe.zcard(key)
            pipe.expire(key, ttl_s + 60)
            _, _, total, _ = await pipe.execute()
        if total > limit:
            await r.zrem(key, call_id)
            return False
        return True
    except Exception:
        redis_store.warn_unavailable("call limit")
        return True


async def release(tenant_id: str, call_id: str) -> None:
    r = redis_store.get_redis()
    if r is None:
        return
    try:
        await r.zrem(_key(tenant_id), call_id)
    except Exception:
        redis_store.warn_unavailable("call limit")


async def active(tenant_id: str) -> int:
    r = redis_store.get_redis()
    if r is None:
        return 0
    try:
        return int(await r.zcard(_key(tenant_id)))
    except Exception:
        return 0
