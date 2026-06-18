"""
Hotel AI Receptionist - FastAPI Application
Entry point: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from database.mongodb import connect_db, disconnect_db
from services.reminders import get_booking_reminder_service


def _configure_console_encoding() -> None:
    """Ensure console streams can safely print Unicode logs on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_configure_console_encoding()


def _get_log_level() -> int:
    configured = (settings.LOG_LEVEL or "INFO").upper()
    return getattr(logging, configured, logging.INFO)


def _quiet_noisy_loggers() -> None:
    noisy = [
        "pymongo",
        "pymongo.connection",
        "pymongo.topology",
        "pymongo.serverSelection",
        "pymongo.command",
    ]
    for name in noisy:
        logging.getLogger(name).setLevel(logging.WARNING)


logging.basicConfig(
    level=_get_log_level(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("hotel_ai.log", encoding="utf-8"),
    ],
    force=True,
)
logger = logging.getLogger(__name__)
_quiet_noisy_loggers()

# Import routers with error handling
try:
    from voice.twilio_handler import router as voice_router
    logger.info("[OK] Voice router imported")
except Exception as e:
    logger.error(f"[FAIL] Failed to import voice router: {e}", exc_info=True)
    voice_router = None

try:
    from whatsapp.meta_handler import router as meta_whatsapp_router
    logger.info("[OK] Meta WhatsApp router imported")
except Exception as e:
    logger.error(f"[FAIL] Failed to import Meta WhatsApp router: {e}", exc_info=True)
    meta_whatsapp_router = None

try:
    from routers.api import bookings_router, rooms_router
    logger.info("[OK] API routers imported")
except Exception as e:
    logger.error(f"[FAIL] Failed to import API routers: {e}", exc_info=True)
    bookings_router     = None
    rooms_router        = None
try:
    from routers.livekit import livekit_router
    logger.info("[OK] LiveKit router imported")
except Exception as e:
    logger.error(f"[FAIL] Failed to import LiveKit router: {e}", exc_info=True)
    livekit_router = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{settings.HOTEL_NAME} - AI Receptionist starting")
    logger.info(f"   LLM Provider : OpenAI ({settings.OPENAI_MODEL})")
    logger.info(f"   STT Provider : OpenAI Whisper")
    logger.info(f"   TTS Provider : ElevenLabs / OpenAI")
    logger.info(f"   Environment  : {settings.APP_ENV}")

    await connect_db()

    from ai.llm_provider import get_llm_provider
    get_llm_provider()
    reminder_service = get_booking_reminder_service()
    reminder_service.start()

    # Pre-warm TTS audio cache so the first caller hears the greeting instantly.
    try:
        from voice.stt_tts import get_tts
        from voice.twilio_handler import _ensure_audio_cache
        asyncio.create_task(_ensure_audio_cache(get_tts()))
        logger.info("🔥 TTS audio cache pre-warm scheduled")
    except Exception as _pw_err:
        logger.warning("TTS prewarm skipped: %s", _pw_err)

    logger.info(f"{settings.HOTEL_NAME} AI Receptionist is LIVE on port {settings.PORT}")
    yield

    await reminder_service.stop()
    await disconnect_db()
    logger.info("Hotel AI Receptionist stopped")


app = FastAPI(
    title=f"{settings.HOTEL_NAME} - AI Receptionist",
    description="""
    Agentic AI Hotel Receptionist System

    - Voice Calls: Real-time AI phone calls via Twilio + STT + TTS
    - WhatsApp: AI-powered messaging with rich confirmations
    - Booking Engine: Full room availability and booking management
    - MongoDB: Persistent guest data and conversation history

    LLM: OpenAI GPT-4o | STT: OpenAI Whisper | TTS: ElevenLabs
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc) if settings.DEBUG else "Contact support"},
    )


if voice_router:
    app.include_router(voice_router)
    logger.info("Voice router registered")
else:
    logger.warning("Voice router NOT registered")

if meta_whatsapp_router:
    app.include_router(meta_whatsapp_router)
    logger.info("Meta WhatsApp router registered")
else:
    logger.warning("Meta WhatsApp router NOT registered")

if bookings_router:
    app.include_router(bookings_router)
    logger.info("Bookings router registered")
else:
    logger.warning("Bookings router NOT registered")

if rooms_router:
    app.include_router(rooms_router)
    logger.info("Rooms router registered")
else:
    logger.warning("Rooms router NOT registered")

if livekit_router:
    app.include_router(livekit_router)
    logger.info("LiveKit router registered")
else:
    logger.warning("LiveKit router NOT registered")


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": f"{settings.HOTEL_NAME} AI Receptionist",
        "status": "running",
        "version": "1.0.0",
        "env": settings.APP_ENV,
        "llm": f"openai/{settings.OPENAI_MODEL}",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    from database.mongodb import get_db

    try:
        db = get_db()
        await db.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "healthy",
        "database": db_status,
        "hotel": settings.HOTEL_NAME,
    }


@app.get("/debug/routes", tags=["Debug"])
async def debug_routes():
    """List all registered routes"""
    routes = []
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            routes.append({
                "path": route.path,
                "methods": list(route.methods) if route.methods else [],
                "name": route.name if hasattr(route, "name") else None
            })
    return {"total_routes": len(routes), "routes": sorted(routes, key=lambda x: x["path"])}


@app.get("/debug/rooms", tags=["Debug"])
async def debug_rooms():
    """Check rooms in database - for debugging"""
    from database.mongodb import get_db
    
    try:
        db = get_db()
        rooms = await db.rooms.find({}).to_list(length=200)
        
        # Count by type
        room_counts = {}
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
            "message": "Run 'python -m utils.seed_rooms' to populate rooms" if len(rooms) == 0 else "Rooms loaded successfully"
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/debug/bookings", tags=["Debug"])
async def debug_bookings():
    """Check bookings in database - for debugging"""
    from database.mongodb import get_db
    
    try:
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
            ]
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )
