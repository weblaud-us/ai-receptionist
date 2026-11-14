"""
Test setup.

Environment variables are set BEFORE anything imports `config`, and they override any local
.env file, so tests never use real credentials. MongoDB is an in-memory mock (mongomock-motor);
it enforces unique indexes, which is what the double-booking test relies on.
"""

import os

_TEST_ENV = {
    "APP_ENV": "development",
    "DEBUG": "false",
    "LOG_LEVEL": "WARNING",
    "LOG_FORMAT": "text",
    "SECRET_KEY": "test-secret-key-0123456789abcdef0123456789",
    "ADMIN_SECRET_KEY": "",
    "ADMIN_PASSWORD": "",
    "CORS_ORIGINS": "",
    "WEBHOOK_BASE_URL": "https://receptionist.example.com",
    "CALL_BASE_URL": "https://receptionist.example.com",
    "VERIFY_TWILIO_SIGNATURE": "true",
    "VERIFY_TWILIO_WS_SIGNATURE": "true",
    "TWILIO_AUTH_TOKEN": "test-twilio-auth-token",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_PHONE_NUMBER": "",
    "META_APP_SECRET": "test-meta-app-secret",
    "META_WA_ACCESS_TOKEN": "",
    "META_WA_VERIFY_TOKEN": "test-verify-token",
    "LIVEKIT_URL": "wss://livekit.example.com",
    "LIVEKIT_API_KEY": "APItestkey",
    "LIVEKIT_API_SECRET": "test-livekit-secret-0123456789abcdef",
    "OPENAI_API_KEY": "sk-test",
    "ELEVENLABS_API_KEY": "",
    "SENTRY_DSN": "",
    "MONGODB_DB_NAME": "receptionist_test",
    "HOTEL_TIMEZONE": "Asia/Dhaka",
    "ENABLE_WHATSAPP": "true",
    "ENABLE_ONE_DAY_BOOKING_REMINDER": "false",
    "ENABLE_CALL_RECORDING": "false",
    "DEEPGRAM_API_KEY": "",
    "CARTESIA_API_KEY": "",
    "TRANSFER_PHONE_NUMBER": "",
    "TRANSFER_HOURS": "",
    "OWNER_NOTIFY_WHATSAPP": "",
    "HOTEL_NAME": "Grand Azure Hotel",
    "RECEPTIONIST_NAME": "Aria",
    "REQUIRE_ADMIN_MFA": "false",
    "ENABLE_TENANT_JOBS": "false",
    "GOOGLE_OAUTH_CLIENT_ID": "",
    "GOOGLE_OAUTH_CLIENT_SECRET": "",
    "TOKEN_ENCRYPTION_KEY": "",
    "TWILIO_SMS_FROM": "",
    "RESEND_API_KEY": "",
}
os.environ.update(_TEST_ENV)

import functools  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import database.mongodb as mongodb  # noqa: E402


@functools.lru_cache(maxsize=1)
def livekit_agents_importable() -> bool:
    """
    livekit-agents' native libraries crash the interpreter on import on some Windows builds.
    Probe in a child process so a crash can't take pytest down; LiveKit-dependent test modules
    skip themselves when this is False (they always run in CI on Linux).
    """
    try:
        return subprocess.run([sys.executable, "-c", "import livekit.agents"], capture_output=True,
                              timeout=180).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
async def db():
    """A fresh in-memory database with the production indexes, installed as get_db()."""
    client = AsyncMongoMockClient()
    database = client[os.environ["MONGODB_DB_NAME"]]
    await mongodb._create_indexes(database)
    previous = mongodb._db
    mongodb._db = database
    try:
        yield database
    finally:
        mongodb._db = previous


@pytest.fixture
def app(db):
    """The FastAPI app without its lifespan (no real DB connection, no background services)."""
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)  # not used as a context manager → lifespan is not started
