"""
MongoDB Async Connection via Motor — Single-Tenant Setup.
Collections: rooms, bookings, guests, conversations, call_sessions
"""

import logging
import asyncio
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_db():
    global _client, _db
    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        maxPoolSize=50,
        minPoolSize=5,
    )
    _db = _client[settings.MONGODB_DB_NAME]
    await _create_indexes(_db)
    logger.info("✅ MongoDB connected → db=%s", settings.MONGODB_DB_NAME)


async def disconnect_db():
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB disconnected")


def get_client() -> AsyncIOMotorClient:
    return _client


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB not initialised — call connect_db() first")
    return _db


async def _create_indexes(db: AsyncIOMotorDatabase):
    """Create indexes for all collections."""

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

    logger.info("✅ DB indexes created")
