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
from core.log import configure_logging, request_id_var
from core.observability import init_sentry
from database.mongodb import connect_db, disconnect_db, get_db
from services.reminders import get_booking_reminder_service
from utils.auth import require_admin

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)
init_sentry("api")


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
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("%s AI Receptionist starting (env=%s)", settings.HOTEL_NAME, settings.APP_ENV)

    await connect_db()

    from ai.llm_provider import get_llm_provider
    get_llm_provider()
    reminder_service = get_booking_reminder_service()
    reminder_service.start()

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

    await reminder_service.stop()
    await disconnect_db()
    logger.info("AI Receptionist stopped")


_docs = not settings.is_production
app = FastAPI(
    title=f"{settings.HOTEL_NAME} - AI Receptionist",
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
        # Dashboard auth uses a Bearer header, not cookies; credentials only with explicit origins.
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


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
    """Dependencies reachable (Mongo now, Redis from Phase 3). 503 if any fail."""
    checks = {}
    try:
        await asyncio.wait_for(get_db().command("ping"), timeout=1.0)
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "fail"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded", "checks": checks})


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health():
    """Backwards-compatible alias of /readyz for existing uptime monitors."""
    return await readyz()


# ─────────────────────────────────────────────
# Debug (admin only)
# ─────────────────────────────────────────────

@app.get("/debug/routes", tags=["Debug"], dependencies=[Depends(require_admin)])
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


@app.get("/debug/rooms", tags=["Debug"], dependencies=[Depends(require_admin)])
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


@app.get("/debug/bookings", tags=["Debug"], dependencies=[Depends(require_admin)])
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
