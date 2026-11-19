"""
Prometheus metrics for the API (Roadmap Task 3.7): GET /metrics, Bearer METRICS_TOKEN.

Two groups:

1. HTTP — a counter and a latency histogram per route template, recorded by `http_middleware`.
2. Calls — computed from the `calls` collection at scrape time (cached 15 s), because every call
   runs in its own short-lived worker process that Prometheus cannot scrape. These are the numbers
   on the morning dashboard and behind the alerts in deploy/monitoring/alerts.yml:

   receptionist_calls_active                      calls in progress
   receptionist_calls_last_hour{channel,outcome}  finished calls in the last 60 min
   receptionist_calls_short_last_hour             calls that ended in < 10 s (a broken-call signal)
   receptionist_turn_latency_seconds{kind,quantile}   response latency, last 15 min
   receptionist_greeting_latency_seconds{quantile}    time to first audio, last 15 min
   receptionist_provider_errors_last_hour{kind}       LLM / STT / TTS errors seen during calls
   receptionist_provider_fallbacks_last_hour{kind}    times a call switched to a fallback provider
   receptionist_transfers_last_hour{result}           transfer_to_human results
   receptionist_dependency_up{name}                   1 / 0 for mongo and redis
   receptionist_jobs_dead                             jobs that failed every retry
   receptionist_outbound_attempts_last_hour{outcome}  sales dialer results (Phase 4)
   receptionist_outbound_calls_active                 sales calls in progress

The voice worker exposes LiveKit's own process metrics separately (LIVEKIT_PROMETHEUS_PORT).
No metric carries a phone number, a name or a tenant id.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from config import settings

logger = logging.getLogger(__name__)

CACHE_S = 15.0
SHORT_CALL_S = 10.0
LATENCY_WINDOW = timedelta(minutes=15)
COUNT_WINDOW = timedelta(hours=1)
STALE_CALL = timedelta(hours=3)             # an "in progress" call older than this is a crashed worker

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                          buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))

_cache: Tuple[float, bytes] = (0.0, b"")


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None for no data."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    return round(data[max(0, min(len(data) - 1, math.ceil(pct / 100 * len(data)) - 1))], 3)


def _route(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"       # never the raw path: unbounded label values


async def http_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = _route(request)
        if route != "/metrics":
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - start)


def call_stats(calls: List[Dict[str, Any]], *, now: datetime) -> Dict[str, Any]:
    """Aggregate call documents (pure; tested). `calls` = finished calls from the last hour."""
    latency_from = now - LATENCY_WINDOW
    by_outcome: Dict[Tuple[str, str], int] = {}
    errors: Dict[str, int] = {}
    fallbacks: Dict[str, int] = {}
    transfers: Dict[str, int] = {}
    plain: List[float] = []
    tool: List[float] = []
    greeting: List[float] = []
    short = 0
    for c in calls:
        key = (str(c.get("channel") or "phone"), str(c.get("outcome") or "completed"))
        by_outcome[key] = by_outcome.get(key, 0) + 1
        # Outbound sales calls are often short by design (voicemail, hang up): not a broken-call signal.
        if ((c.get("duration_s") or 0) < SHORT_CALL_S and key[1] not in ("unassigned", "busy")
                and c.get("direction") != "outbound"):
            short += 1
        for kind, n in (c.get("provider_errors") or {}).items():
            errors[kind] = errors.get(kind, 0) + int(n or 0)
        for kind, n in (c.get("fallbacks") or {}).items():
            fallbacks[kind] = fallbacks.get(kind, 0) + int(n or 0)
        if c.get("transfer"):
            transfers[str(c["transfer"])] = transfers.get(str(c["transfer"]), 0) + 1
        ended = c.get("ended_at")
        if isinstance(ended, datetime):
            ended = ended if ended.tzinfo else ended.replace(tzinfo=timezone.utc)
            if ended < latency_from:
                continue
        for t in c.get("turns") or []:
            if isinstance(t.get("latency_s"), (int, float)):
                (tool if t.get("tool_turn") else plain).append(float(t["latency_s"]))
        if isinstance(c.get("greeting_latency_s"), (int, float)):
            greeting.append(float(c["greeting_latency_s"]))
    return {"by_outcome": by_outcome, "short": short, "errors": errors, "fallbacks": fallbacks,
            "transfers": transfers, "plain": plain, "tool": tool, "greeting": greeting}


def render_call_metrics(stats: Dict[str, Any], *, active: int, deps: Dict[str, bool], dead_jobs: int,
                        outbound: Optional[Dict[str, Any]] = None) -> bytes:
    reg = CollectorRegistry()
    if outbound is not None:
        og = Gauge("receptionist_outbound_attempts_last_hour", "Sales dialer attempts finished in the last hour",
                   ["outcome"], registry=reg)
        for outcome, n in outbound.get("by_outcome", {}).items():
            og.labels(outcome).set(n)
        Gauge("receptionist_outbound_calls_active", "Sales calls in progress",
              registry=reg).set(outbound.get("active", 0))
    Gauge("receptionist_calls_active", "Calls in progress", registry=reg).set(active)
    g = Gauge("receptionist_calls_last_hour", "Finished calls in the last hour", ["channel", "outcome"], registry=reg)
    for (channel, outcome), n in stats["by_outcome"].items():
        g.labels(channel, outcome).set(n)
    Gauge("receptionist_calls_short_last_hour", "Calls shorter than 10 s in the last hour",
          registry=reg).set(stats["short"])
    lat = Gauge("receptionist_turn_latency_seconds", "Response latency per turn, last 15 min",
                ["kind", "quantile"], registry=reg)
    for kind, values in (("plain", stats["plain"]), ("tool", stats["tool"])):
        for q, pct in (("0.5", 50), ("0.95", 95)):
            v = percentile(values, pct)
            if v is not None:
                lat.labels(kind, q).set(v)
    Gauge("receptionist_turns_measured", "Turns behind the latency quantiles", registry=reg).set(
        len(stats["plain"]) + len(stats["tool"]))
    gl = Gauge("receptionist_greeting_latency_seconds", "Call start to first agent audio, last 15 min",
               ["quantile"], registry=reg)
    for q, pct in (("0.5", 50), ("0.95", 95)):
        v = percentile(stats["greeting"], pct)
        if v is not None:
            gl.labels(q).set(v)
    for name, help_, data in (
        ("receptionist_provider_errors_last_hour", "Provider errors during calls", stats["errors"]),
        ("receptionist_provider_fallbacks_last_hour", "Switches to a fallback provider", stats["fallbacks"]),
    ):
        pg = Gauge(name, help_, ["kind"], registry=reg)
        for kind in ("llm", "stt", "tts"):
            pg.labels(kind).set(data.get(kind, 0))
    tg = Gauge("receptionist_transfers_last_hour", "transfer_to_human results", ["result"], registry=reg)
    for result in ("ok", "failed"):
        tg.labels(result).set(stats["transfers"].get(result, 0))
    dg = Gauge("receptionist_dependency_up", "1 when the dependency answers", ["name"], registry=reg)
    for name, up in deps.items():
        dg.labels(name).set(1 if up else 0)
    Gauge("receptionist_jobs_dead", "Jobs that failed every retry", registry=reg).set(dead_jobs)
    return generate_latest(reg)


async def _collect() -> bytes:
    from core import redis as redis_store
    from database.mongodb import get_db
    from jobs.queue import DEAD_LETTER_KEY

    now = datetime.now(timezone.utc)
    deps: Dict[str, bool] = {}
    calls: List[Dict[str, Any]] = []
    active = 0
    outbound: Optional[Dict[str, Any]] = None
    try:
        db = get_db()
        calls = await asyncio.wait_for(db.calls.find(  # cross-tenant: platform metrics, counts only
            {"status": "completed", "ended_at": {"$gte": now - COUNT_WINDOW}},
            {"channel": 1, "outcome": 1, "duration_s": 1, "ended_at": 1, "greeting_latency_s": 1, "direction": 1,
             "turns.latency_s": 1, "turns.tool_turn": 1, "provider_errors": 1, "fallbacks": 1, "transfer": 1},
        ).limit(5000).to_list(5000), timeout=5)
        active = await asyncio.wait_for(db.calls.count_documents(  # cross-tenant: platform metrics, counts only
            {"status": "in_progress", "started_at": {"$gte": now - STALE_CALL}}), timeout=5)
        outbound = await asyncio.wait_for(_outbound_stats(db, now), timeout=5)
        deps["mongo"] = True
    except Exception:
        logger.warning("Call metrics unavailable", exc_info=True)
        deps["mongo"] = False
    dead = 0
    if redis_store.enabled():
        deps["redis"] = await redis_store.ping()
        if deps["redis"]:
            try:
                dead = int(await redis_store.get_redis().llen(DEAD_LETTER_KEY))  # type: ignore[union-attr]
            except Exception:
                dead = 0
    return render_call_metrics(call_stats(calls, now=now), active=active, deps=deps, dead_jobs=dead,
                               outbound=outbound)


async def _outbound_stats(db: Any, now: datetime) -> Dict[str, Any]:
    by_outcome: Dict[str, int] = {}
    async for row in db.call_attempts.aggregate([
        {"$match": {"status": "done", "ended_at": {"$gte": now - COUNT_WINDOW}}},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}}},
    ]):
        by_outcome[str(row["_id"])] = int(row["n"])
    active = await db.call_attempts.count_documents(
        {"status": {"$in": ["dispatched", "dialing", "in_progress"]}, "created_at": {"$gte": now - timedelta(hours=1)}})
    return {"by_outcome": by_outcome, "active": active}


def _authorised(request: Request) -> bool:
    token = settings.METRICS_TOKEN
    if not token:
        return settings.is_development           # no token: local use only
    header = request.headers.get("authorization", "")
    return header.lower().startswith("bearer ") and hmac.compare_digest(
        header[7:].strip().encode("utf-8"), token.encode("utf-8"))


async def metrics_endpoint(request: Request) -> Response:
    if not _authorised(request):
        raise HTTPException(status_code=404, detail="Not Found")    # don't reveal that it exists
    global _cache
    at, body = _cache
    if time.monotonic() - at > CACHE_S or not body:
        body = await _collect()
        _cache = (time.monotonic(), body)
    return Response(content=generate_latest() + body, media_type=CONTENT_TYPE_LATEST)


def reset_cache() -> None:
    global _cache
    _cache = (0.0, b"")
