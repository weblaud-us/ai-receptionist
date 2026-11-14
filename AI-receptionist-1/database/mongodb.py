"""
MongoDB Async Connection via Motor — Single-Tenant Setup.
Collections: rooms, bookings, room_nights, guests, conversations, call_sessions (legacy),
calls (voice agent), messages (take_message).

Indexes are created by `python -m migrations.m0002_indexes` (run by deploy.sh), not at every
startup — index builds slow down boot and must never run inside a call. In development the API
still creates them on startup for convenience.
"""

import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_db(ensure_indexes: Optional[bool] = None):
    """
    Create the process-wide client. Call once per process, inside its event loop.
    ensure_indexes: None = only in development (production runs the migration on deploy).
    """
    global _client, _db
    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        maxPoolSize=50,
        minPoolSize=5,
    )
    _db = _client[settings.MONGODB_DB_NAME]
    if ensure_indexes if ensure_indexes is not None else settings.is_development:
        await _create_indexes(_db)
    logger.info("✅ MongoDB connected → db=%s", settings.MONGODB_DB_NAME)


def is_connected() -> bool:
    return _db is not None


async def disconnect_db():
    global _client, _db
    if _client:
        _client.close()
        _client, _db = None, None
        logger.info("MongoDB disconnected")


def get_client() -> AsyncIOMotorClient:
    return _client


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB not initialised — call connect_db() first")
    return _db


async def create_indexes(db: AsyncIOMotorDatabase):
    """Create indexes for all collections. Idempotent."""

    await db.rooms.create_indexes([
        IndexModel([("room_number", ASCENDING)], unique=True),
        IndexModel([("room_type", ASCENDING)]),
        IndexModel([("is_active", ASCENDING)]),
    ])

    await db.bookings.create_indexes([
        IndexModel([("booking_id", ASCENDING)], unique=True),
        IndexModel([("guest_phone", ASCENDING)]),
        IndexModel([("room_id", ASCENDING)]),
        IndexModel([("check_in_date", ASCENDING), ("check_out_date", ASCENDING)]),
        IndexModel([("check_in_date", ASCENDING), ("status", ASCENDING),
                    ("one_day_reminder_sent", ASCENDING)]),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
    ])

    # One document per (room, night) a booking occupies. The UNIQUE index is what makes double
    # booking impossible: the database refuses the second claim. Backfill existing bookings
    # with `python -m migrations.m0001_room_nights`.
    await db.room_nights.create_indexes([
        IndexModel([("room_id", ASCENDING), ("night", ASCENDING)], unique=True,
                   name="uniq_room_night"),
        IndexModel([("booking_id", ASCENDING)]),
    ])

    await db.guests.create_indexes([
        IndexModel([("phone", ASCENDING)], unique=True),
        IndexModel([("email", ASCENDING)]),
        IndexModel([("name", ASCENDING)]),
    ])

    await db.conversations.create_indexes([
        IndexModel([("session_id", ASCENDING)], unique=True),
        IndexModel([("phone", ASCENDING)]),
        IndexModel([("channel", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
        IndexModel([("status", ASCENDING)]),
    ])

    await db.call_sessions.create_indexes([
        IndexModel([("call_sid", ASCENDING)], unique=True),
        IndexModel([("phone", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
    ])

    # Voice agent call records (one per LiveKit room) and messages left for the business.
    await db.calls.create_indexes([
        IndexModel([("call_id", ASCENDING)], unique=True),
        IndexModel([("started_at", DESCENDING)]),
        IndexModel([("caller_phone", ASCENDING)]),
        IndexModel([("outcome", ASCENDING)]),
    ])

    await db.messages.create_indexes([
        IndexModel([("message_id", ASCENDING)], unique=True),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)]),
    ])

    logger.info("✅ DB indexes created")


_create_indexes = create_indexes  # backwards-compatible name (tests, migrations)
