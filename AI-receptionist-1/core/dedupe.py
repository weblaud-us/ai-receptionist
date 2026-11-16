"""
"Have we already handled this?" across every API replica (Roadmap Task 3.4).

    if not await first_time("wa", message_id): return     # a retried webhook

Redis `SET key NX EX ttl` — atomic, so exactly one replica wins. Without Redis (or while it is
down) a bounded in-process table is used, which is correct for a single API process.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional

from core import redis as redis_store

DEFAULT_TTL_S = 24 * 3600
_MEMORY_MAX = 5000
_seen: "OrderedDict[str, float]" = OrderedDict()      # key → expiry


def _first_time_memory(key: str, ttl_s: int) -> bool:
    now = time.time()
    while _seen:
        oldest, expires = next(iter(_seen.items()))
        if expires > now and len(_seen) <= _MEMORY_MAX:
            break
        _seen.popitem(last=False)
    if _seen.get(key, 0) > now:
        return False
    _seen[key] = now + ttl_s
    _seen.move_to_end(key)
    return True


async def first_time(namespace: str, item_id: Optional[str], ttl_s: int = DEFAULT_TTL_S) -> bool:
    """True exactly once per (namespace, id) within `ttl_s`. An empty id is always "first"."""
    if not item_id:
        return True
    key = f"seen:{namespace}:{item_id}"
    r = redis_store.get_redis()
    if r is not None:
        try:
            return bool(await r.set(key, "1", nx=True, ex=ttl_s))
        except Exception:
            redis_store.warn_unavailable("dedupe")
    return _first_time_memory(key, ttl_s)


def reset() -> None:
    _seen.clear()
