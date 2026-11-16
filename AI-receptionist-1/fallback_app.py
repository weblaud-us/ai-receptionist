"""
Stand-alone telephony fallback (Roadmap Task 3.5) — run it somewhere OTHER than the main host.

    TELEPHONY_FALLBACK_ONLY=true uvicorn fallback_app:app --host 0.0.0.0 --port 8003 --proxy-headers

A Twilio Elastic SIP trunk has exactly one Disaster Recovery URL. If it points at the main host
and that host is down, there is no fallback at all. Point it here instead: a tiny app on a
different provider (Render / Fly.io / a second VPS) that only serves POST /telephony/fallback —
ring the business's staff line, or apologise. It needs MongoDB (Atlas is independent of the main
host) and nothing else: no Redis, no LiveKit, no LLM.

Environment for this host (see deploy/README.md, "Independent telephony fallback"):
    TELEPHONY_FALLBACK_ONLY=true      required; the full API refuses to start with it
    WEBHOOK_BASE_URL=https://<this app's public URL>   Twilio signs the URL it calls
    TWILIO_AUTH_TOKEN, MONGODB_URI, MONGODB_DB_NAME, SECRET_KEY, CORS_ORIGINS, APP_ENV=production
    ENABLE_WHATSAPP=false, SENTRY_DSN (recommended: every use of this app is an incident)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from core.log import configure_logging
from core.observability import init_sentry
from database.mongodb import connect_db, disconnect_db
from routers.telephony import telephony_router

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)
init_sentry("telephony-fallback")

if not settings.TELEPHONY_FALLBACK_ONLY:
    raise SystemExit("fallback_app runs only with TELEPHONY_FALLBACK_ONLY=true (on its own host)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db(ensure_indexes=False)
    logger.info("Telephony fallback is up (env=%s)", settings.APP_ENV)
    yield
    await disconnect_db()


app = FastAPI(title="Receptionist telephony fallback", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(telephony_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
