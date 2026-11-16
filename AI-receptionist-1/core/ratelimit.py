"""
Rate limits shared by every API replica (Roadmap Tasks 3.4 and 3.8).

    await allow("pubcall:ip:1.2.3.4", limit=10, window_s=3600)   # record a hit; False = over the limit
    await count(key, window_s) / await hit(key, window_s) / await clear(key)   # login failures
    await allow_fixed("api:ten_1", limit=600, window_s=60)       # cheap counter for high-volume keys

State lives in Redis (sorted sets for the exact sliding window, a counter for the fixed window).
Without REDIS_URL, or while Redis is unreachable, the same limits are kept in this process's
memory — still enforced, just per process — so a Redis outage never locks anyone out and never
switches limiting off.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Request

from core import redis as redis_store

_PREFIX = "rl:"


class SlidingWindow:
    """In-memory limiter (one process). Also the fallback when Redis is missing or down."""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._fixed: Dict[str, Tuple[int, int]] = {}      # key → (window number, count)

    def _recent(self, key: str, window_s: float, now: Optional[float]) -> Tuple[Deque[float], float]:
        t = time.time() if now is None else now
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= t - window_s:
            q.popleft()
        return q, t

    def allow(self, key: str, limit: int, window_s: float, now: Optional[float] = None) -> bool:
        """Record a hit and return True if `key` is still within `limit` hits per `window_s` seconds."""
        q, t = self._recent(key, window_s, now)
        if len(q) >= limit:
            return False
        q.append(t)
        return True

    def count(self, key: str, window_s: float, now: Optional[float] = None) -> int:
        return len(self._recent(key, window_s, now)[0])

    def hit(self, key: str, window_s: float, now: Optional[float] = None) -> None:
        q, t = self._recent(key, window_s, now)
        q.append(t)

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)
        self._fixed.pop(key, None)

    def allow_fixed(self, key: str, limit: int, window_s: int, now: Optional[float] = None) -> bool:
        window = int((time.time() if now is None else now) // window_s)
        seen_window, n = self._fixed.get(key, (window, 0))
        n = n + 1 if seen_window == window else 1
        self._fixed[key] = (window, n)
        if len(self._fixed) > 50_000:                      # bound memory: forget finished windows
            self._fixed = {k: v for k, v in self._fixed.items() if v[0] == window}
        return n <= limit

    def reset(self) -> None:
        self._hits.clear()
        self._fixed.clear()


memory = SlidingWindow()
public_calls = memory        # pre-Phase-3 name, kept for callers and tests


async def allow(key: str, limit: int, window_s: float) -> bool:
    """Record a hit; True while `key` has made at most `limit` hits in the last `window_s` seconds."""
    r = redis_store.get_redis()
    if r is not None:
        try:
            now, member, k = time.time(), uuid.uuid4().hex, _PREFIX + key
            async with r.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(k, 0, now - window_s)
                pipe.zadd(k, {member: now})
                pipe.zcard(k)
                pipe.expire(k, int(window_s) + 1)
                _, _, total, _ = await pipe.execute()
            if total > limit:
                await r.zrem(k, member)
                return False
            return True
        except Exception:
            redis_store.warn_unavailable("rate limit")
    return memory.allow(key, limit, window_s)


async def count(key: str, window_s: float) -> int:
    r = redis_store.get_redis()
    if r is not None:
        try:
            k = _PREFIX + key
            async with r.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(k, 0, time.time() - window_s)
                pipe.zcard(k)
                _, total = await pipe.execute()
            return int(total)
        except Exception:
            redis_store.warn_unavailable("rate limit")
    return memory.count(key, window_s)


async def hit(key: str, window_s: float) -> None:
    r = redis_store.get_redis()
    if r is not None:
        try:
            k = _PREFIX + key
            async with r.pipeline(transaction=True) as pipe:
                pipe.zadd(k, {uuid.uuid4().hex: time.time()})
                pipe.expire(k, int(window_s) + 1)
                await pipe.execute()
            return
        except Exception:
            redis_store.warn_unavailable("rate limit")
    memory.hit(key, window_s)


async def clear(key: str) -> None:
    memory.clear(key)
    r = redis_store.get_redis()
    if r is not None:
        try:
            await r.delete(_PREFIX + key, _PREFIX + "f:" + key)
        except Exception:
            redis_store.warn_unavailable("rate limit")


async def allow_fixed(key: str, limit: int, window_s: int = 60) -> bool:
    """One INCR per request: for limits that are hit on every API call."""
    r = redis_store.get_redis()
    if r is not None:
        try:
            k = f"{_PREFIX}f:{key}:{int(time.time() // window_s)}"
            async with r.pipeline(transaction=True) as pipe:
                pipe.incr(k)
                pipe.expire(k, window_s + 1)
                n, _ = await pipe.execute()
            return int(n) <= limit
        except Exception:
            redis_store.warn_unavailable("rate limit")
    return memory.allow_fixed(key, limit, window_s)


def client_ip(request: Request) -> str:
    """The visitor's address. Behind Cloudflare / a proxy it is in a header; a forged header can
    only dodge a per-visitor limit, never a shared one."""
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))[:64]
