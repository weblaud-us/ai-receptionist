"""
AI Receptionist — FastAPI application.
Entry point: uvicorn main:app --host 127.0.0.1 --port 8002 --proxy-headers --forwarded-allow-ips=127.0.0.1
"""

import asyncio
import importlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from core import metrics, ratelimit
from core import redis as redis_store
from core.log import configure_logging, request_id_var
from core.observability import init_sentry
from database.mongodb import connect_db, disconnect_db, get_db
from services.reminders import get_booking_reminder_service
from utils.auth import require_admin

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)
init_sentry("api")

if settings.TELEPHONY_FALLBACK_ONLY:
    raise SystemExit("TELEPHONY_FALLBACK_ONLY=true is for fallback_app.py on its own host, not the full API")


def _import_router(module: str, attr: str):
    """
    Import a router. In development a broken router is logged and skipped so the rest of the
    app still boots; anywhere else it is fatal — a silently missing feature is worse than a
    failed deploy.
    """
    try:
        router = getattr(importlib.import_module(module), attr)
        logger.info("[OK] %s.%s imported", module, attr)
        return router
    except Exception:
        if not settings.is_development:
            raise
        logger.error("[FAIL] could not import %s.%s", module, attr, exc_info=True)
        return None


_ROUTERS = [
    ("voice.twilio_handler", "router"),
    ("whatsapp.meta_handler", "router"),
    ("routers.api", "bookings_router"),
    ("routers.api", "rooms_router"),
    ("routers.api", "calendar_router"),
    ("routers.admin", "admin_router"),
    ("routers.livekit", "livekit_router"),
    ("routers.auth", "auth_router"),          # Phase 2: users, sessions, invites
    ("routers.tenant", "tenant_router"),      # Phase 2: the business's own settings and data
    ("routers.platform", "platform_router"),  # Phase 2: platform super-admin
    ("routers.integrations", "integrations_router"),  # Phase 2: OAuth callbacks (Google Calendar)
    ("routers.telephony", "telephony_router"),        # Phase 3: Twilio disaster-recovery TwiML
    ("routers.sales", "sales_router"),                # Phase 4: outbound dialer (platform admin)
    ("routers.sales", "public_sales_router"),         # Phase 4: "get a call from our AI" consent form
    ("routers.signup", "public_plans_router"),        # Phase 5: plan catalogue
    ("routers.signup", "signup_router"),              # Phase 5: self-serve signup + e-mail verification
    ("routers.billing", "billing_router"),            # Phase 5: plan, usage, Checkout/Portal, numbers, export
    ("routers.billing", "stripe_webhook_router"),     # Phase 5: Stripe events
    ("routers.billing", "platform_billing_router"),   # Phase 5: costs, number orders, cancel
    ("routers.compliance", "compliance_router"),      # Phase 6: legal documents, DPA, data subject requests
    ("routers.compliance", "public_compliance_router"),    # Phase 6/7: legal list, site info (demo number…)
    ("routers.compliance", "platform_compliance_router"),  # Phase 6: request log, opt-outs
    ("routers.launch", "launch_router"),              # Phase 7: results report, owner feedback
    ("routers.launch", "platform_launch_router"),     # Phase 7: pilots, transcript review, eval cases, margins
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Receptionist starting (env=%s)", settings.APP_ENV)

    await connect_db()

    from ai.llm_provider import get_llm_provider
    get_llm_provider()
    reminder_service = get_booking_reminder_service()
    from services.tenant_jobs import get_tenant_jobs
    tenant_jobs = get_tenant_jobs()
    from core.outbound.dialer import dialer_loop
    if settings.redis_enabled:
        # Phase 3: reminders, digests and calendar sync run in the jobs worker (`python -m jobs`),
        # once, however many API replicas there are.
        if not await redis_store.ping(timeout=2.0):
            logger.error("REDIS_URL is set but Redis is not answering; limits fall back to process memory")
        logger.info("Background jobs run in the jobs worker (python -m jobs), not in this process")
        if settings.LEGACY_TWILIO_VOICE:
            logger.warning("LEGACY_TWILIO_VOICE=true keeps call state in this process: run ONE API "
                           "replica until phone numbers are on the SIP trunk and the flag is off")
    else:
        reminder_service.start()
        tenant_jobs.start()      # appointment reminders, owner digests, calendar sync
        dialer_loop.start()      # Phase 4: only when OUTBOUND_DIALER_ENABLED=true

    # Legacy phone path: pre-warm its TTS audio cache so the first caller hears the greeting.
    if settings.LEGACY_TWILIO_VOICE:
        try:
            from voice.stt_tts import get_tts
            from voice.twilio_handler import _ensure_audio_cache
            asyncio.create_task(_ensure_audio_cache(get_tts()))
        except Exception as _pw_err:
            logger.warning("TTS prewarm skipped: %s", _pw_err)

    logger.info("AI Receptionist is live on port %s", settings.PORT)
    yield

    await dialer_loop.stop()
    await tenant_jobs.stop()
    await reminder_service.stop()
    from jobs.queue import close as close_job_queue
    await close_job_queue()
    await redis_store.close()
    await disconnect_db()
    logger.info("AI Receptionist stopped")


_docs = not settings.is_production
app = FastAPI(
    title="AI Receptionist",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

_cors_origins = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        # Access tokens travel in a Bearer header; the refresh token is an httpOnly cookie scoped to
        # /auth, so credentials are allowed — only ever with explicit origins (never "*").
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


_CALL_PAGES = ("/call/", "/talk/")       # may be embedded in a business's own website
# Endpoints where a guess is worth something. Not /auth/refresh or /auth/logout: an office of
# dashboard users behind one NAT refreshes constantly and must never be locked out by it.
_GUESSABLE = frozenset({"/auth/login", "/auth/mfa/verify", "/auth/password/forgot", "/auth/password/reset",
                        "/auth/invite/accept", "/signup", "/signup/verify-email"})


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Per-IP limit on sign-in endpoints (Task 3.8) and security headers on every response."""
    path = request.url.path
    if (settings.AUTH_RATE_PER_IP_MIN > 0 and request.method == "POST" and path in _GUESSABLE
            and not await ratelimit.allow_fixed(f"auth:ip:{ratelimit.client_ip(request)}",
                                                settings.AUTH_RATE_PER_IP_MIN, 60)):
        return JSONResponse(status_code=429, content={"detail": "Too many requests. Try again in a minute."},
                            headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not path.startswith(_CALL_PAGES):
        response.headers.setdefault("X-Frame-Options", "DENY")
    if not settings.is_development:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.middleware("http")(metrics.http_middleware)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16])[:64]
    request.state.request_id = rid
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # The exception handler runs outside the request-id middleware, so restore the context.
    rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    try:
        logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    finally:
        request_id_var.reset(token)
    content = {"error": "internal_error", "request_id": rid}
    if settings.DEBUG and settings.is_development:
        content["detail"] = str(exc)
    return JSONResponse(status_code=500, content=content)


for _module, _attr in _ROUTERS:
    if _module == "voice.twilio_handler" and not settings.LEGACY_TWILIO_VOICE:
        logger.info("Legacy Twilio voice path disabled (phone calls go through the LiveKit SIP trunk)")
        continue
    _router = _import_router(_module, _attr)
    if _router is not None:
        app.include_router(_router)


# ─────────────────────────────────────────────
# Health — never expose versions, hostnames or config here.
# ─────────────────────────────────────────────

@app.get("/", tags=["Health"], include_in_schema=False)
async def root():
    return {"status": "ok"}


@app.get("/livez", tags=["Health"])
async def livez():
    """Process is up. Used for restart decisions — checks nothing else."""
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"])
async def readyz():
    """Dependencies reachable (Mongo, and Redis when configured). 503 if any fail."""
    checks = {}
    try:
        await asyncio.wait_for(get_db().command("ping"), timeout=1.0)
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "fail"
    if settings.redis_enabled:
        checks["redis"] = "ok" if await redis_store.ping(timeout=1.0) else "fail"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded", "checks": checks})


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health():
    """Backwards-compatible alias of /readyz for existing uptime monitors."""
    return await readyz()


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    """Prometheus scrape target (Bearer METRICS_TOKEN). See core/metrics.py."""
    return await metrics.metrics_endpoint(request)


# ─────────────────────────────────────────────
# Debug (development only)
# ─────────────────────────────────────────────
#
# /debug/rooms and /debug/bookings read the hotel collections without a tenant filter (guest
# names included), so they exist only in development, never on staging or production.

async def debug_routes():
    """List all registered routes"""
    routes = []
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            routes.append({
                "path": route.path,
                "methods": list(route.methods) if route.methods else [],
                "name": getattr(route, "name", None),
            })
    return {"total_routes": len(routes), "routes": sorted(routes, key=lambda x: x["path"])}


async def debug_rooms():
    """Check rooms in database - for debugging"""
    db = get_db()
    rooms = await db.rooms.find({}).to_list(length=200)
    room_counts: dict = {}
    for room in rooms:
        rt = room.get("room_type", "unknown")
        room_counts[rt] = room_counts.get(rt, 0) + 1
    return {
        "total_rooms": len(rooms),
        "rooms_by_type": room_counts,
        "sample_rooms": [
            {
                "room_number": r.get("room_number"),
                "room_type": r.get("room_type"),
                "is_active": r.get("is_active"),
                "capacity": r.get("capacity"),
            }
            for r in rooms[:5]
        ],
        "message": "Run 'python -m utils.seed_rooms' to populate rooms" if not rooms else "Rooms loaded successfully",
    }


async def debug_bookings():
    """Check bookings in database - for debugging"""
    db = get_db()
    bookings = await db.bookings.find({}).to_list(length=100)
    return {
        "total_bookings": len(bookings),
        "bookings": [
            {
                "booking_id": b.get("booking_id"),
                "guest_name": b.get("guest_name"),
                "room_type": b.get("room_type"),
                "check_in_date": b.get("check_in_date"),
                "check_out_date": b.get("check_out_date"),
                "status": b.get("status"),
            }
            for b in bookings[:10]
        ],
    }


if settings.is_development:
    for _path, _fn in (("/debug/routes", debug_routes), ("/debug/rooms", debug_rooms),
                       ("/debug/bookings", debug_bookings)):
        app.add_api_route(_path, _fn, methods=["GET"], tags=["Debug"], dependencies=[Depends(require_admin)])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.is_development and settings.DEBUG,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )
