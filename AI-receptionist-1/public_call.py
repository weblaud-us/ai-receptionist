"""
Public call page: the ONLY thing to put behind a tunnel or on the internet.

    uvicorn public_call:app --host 0.0.0.0 --port 8010 --proxy-headers

It serves just the call page (/talk, /call) and its token endpoint (/livekit/token); no admin API,
no dashboard login, no webhooks, no /docs. The full app (main.py) stays on your machine.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from core import redis as redis_store
from core.log import configure_logging
from database.mongodb import connect_db, disconnect_db
from routers.livekit import livekit_router

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db(ensure_indexes=False)   # only needed to look up a business by id; lazy, never blocks start
    logger.info("Public call page is up (env=%s)", settings.APP_ENV)
    yield
    await redis_store.close()
    await disconnect_db()


app = FastAPI(title="Receptionist public call page", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(livekit_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
