"""
MongoDB Async Connection via Motor.
Collections: rooms, bookings, room_nights, guests, conversations, call_sessions (legacy),
calls (voice agent), messages (take_message), and the Phase 2 tenant collections (tenants, users,
phone_numbers, services, resources, appointments, slot_locks, contacts, knowledge_items, …) and
the Phase 4 outbound dialer (sales_leads, campaigns, call_attempts, dnc_list, dnc_registry,
consent_records) and Phase 5 billing (stripe_events, number_orders, email_verifications,
phone_verifications, deletion_log).

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
        maxPoolSize=settings.MONGODB_MAX_POOL,
        minPoolSize=min(5, settings.MONGODB_MAX_POOL),
        # Audit SRE-10: a write is acknowledged once most replica-set members have it, so a primary
        # failover can't roll back a booking or a payment the caller was told about. Retried once
        # automatically across a failover. (Majority of one on a single development node.)
        w="majority",
        retryWrites=True,
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

    # Room numbers are unique per business, not globally (m0003 drops the old room_number_1).
    await db.rooms.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("room_number", ASCENDING)], unique=True,
                   name="uniq_tenant_room_number"),
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
        IndexModel([("tenant_id", ASCENDING), ("phone", ASCENDING)], unique=True, name="uniq_tenant_phone"),
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
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
    ])

    await _create_tenant_indexes(db)
    await _create_outbound_indexes(db)
    await _create_billing_indexes(db)
    logger.info("✅ DB indexes created")


async def _create_billing_indexes(db: AsyncIOMotorDatabase):
    """Phase 5: billing, self-serve signup, number provisioning, offboarding."""

    await db.tenants.create_indexes([
        IndexModel([("billing.stripe_customer_id", ASCENDING)], name="billing_customer",
                   partialFilterExpression={"billing.stripe_customer_id": {"$gt": ""}}),
    ])
    await db.stripe_events.create_indexes([IndexModel([("received_at", DESCENDING)])])   # _id = Stripe event id
    await db.number_orders.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("e164", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)]),
    ])
    await db.email_verifications.create_indexes([
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ])
    await db.phone_verifications.create_indexes([IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0)])
    await db.usage_events.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("kind", ASCENDING), ("at", ASCENDING)]),
        IndexModel([("call_id", ASCENDING), ("kind", ASCENDING)]),
    ])
    await db.deletion_log.create_indexes([IndexModel([("tenant_id", ASCENDING)])])


async def _create_outbound_indexes(db: AsyncIOMotorDatabase):
    """Phase 4: the outbound sales dialer (Roadmap Task 4.2)."""

    await db.sales_leads.create_indexes([
        IndexModel([("owner", ASCENDING), ("phone", ASCENDING)], unique=True, name="uniq_owner_phone"),
        # the dialer's claim: one campaign's due leads, oldest first
        IndexModel([("campaign_id", ASCENDING), ("status", ASCENDING), ("next_eligible_at", ASCENDING)]),
        IndexModel([("status", ASCENDING), ("next_eligible_at", ASCENDING)]),
        IndexModel([("phone", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
    ])
    await db.campaigns.create_indexes([
        IndexModel([("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
    ])
    await db.call_attempts.create_indexes([
        IndexModel([("campaign_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("lead_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("from", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("call_id", ASCENDING)], unique=True),
    ])
    # The internal do-not-call list. UNIQUE on phone; rows are never deleted.
    await db.dnc_list.create_indexes([IndexModel([("phone", ASCENDING)], unique=True)])
    await db.dnc_registry.create_indexes([
        IndexModel([("phone", ASCENDING), ("list", ASCENDING)], unique=True, name="uniq_phone_list"),
        IndexModel([("list", ASCENDING)]),
    ])
    await db.consent_records.create_indexes([IndexModel([("phone", ASCENDING), ("at", DESCENDING)])])


async def _create_tenant_indexes(db: AsyncIOMotorDatabase):
    """Phase 2 collections. Every compound index starts with tenant_id (Roadmap Task 2.2)."""

    await db.tenants.create_indexes([
        IndexModel([("slug", ASCENDING)], unique=True),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("integrations.whatsapp.phone_number_id", ASCENDING)]),
    ])
    await db.users.create_indexes([
        IndexModel([("email", ASCENDING)], unique=True),
        IndexModel([("memberships.tenant_id", ASCENDING)]),
    ])
    await db.sessions.create_indexes([                    # refresh tokens (hashed)
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ])
    await db.phone_numbers.create_indexes([
        IndexModel([("e164", ASCENDING)], unique=True),
        IndexModel([("tenant_id", ASCENDING)]),
    ])
    await db.services.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("active", ASCENDING), ("sort", ASCENDING)]),
    ])
    await db.resources.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("active", ASCENDING)]),
    ])
    await db.schedule_exceptions.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("start", ASCENDING)]),
    ])
    await db.waitlist.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)]),
    ])
    await db.waitlist_offers.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("slot_key", ASCENDING), ("status", ASCENDING)]),
    ])
    await db.escalations.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("next_at", ASCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("created_at", DESCENDING)]),
    ])
    await db.pms_compensations.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("next_at", ASCENDING)]),
    ])
    await db.appointments.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("start", ASCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("resource_id", ASCENDING), ("start", ASCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("contact_id", ASCENDING), ("start", DESCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("phone", ASCENDING), ("start", DESCENDING)]),
        # Callers find their own booking by name + date of birth (SEC-06); only booked-with-DOB rows.
        IndexModel([("tenant_id", ASCENDING), ("dob_check", ASCENDING), ("start", ASCENDING)],
                   partialFilterExpression={"dob_check": {"$type": "string"}}, name="tenant_dob_check_start"),
        IndexModel([("appointment_id", ASCENDING)], unique=True),
    ])
    # One document per resource per 5-minute block. The UNIQUE index is what makes double booking
    # impossible for every vertical (generalised room_nights).
    await db.slot_locks.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("resource_id", ASCENDING), ("block", ASCENDING)],
                   unique=True, name="uniq_tenant_resource_block"),
        IndexModel([("appointment_id", ASCENDING)]),
        # Locks are claims on future time; once a block is two days past nothing can book it, so
        # Mongo drops the row (audit P0-16: the collection never grew a cleanup).
        IndexModel([("block", ASCENDING)], expireAfterSeconds=2 * 86400, name="ttl_block_past"),
    ])
    await db.contacts.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("phone", ASCENDING)], unique=True),
        IndexModel([("tenant_id", ASCENDING), ("last_seen_at", DESCENDING)]),
    ])
    await db.knowledge_items.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("active", ASCENDING), ("updated_at", DESCENDING)]),
    ])
    await db.unanswered_questions.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
    ])
    await db.leads.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("created_at", DESCENDING)]),
    ])
    # Dental patient recall: one row per patient per cycle (the cycle is the last visit it follows).
    await db.recalls.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("pms_patient_id", ASCENDING), ("cycle", ASCENDING)], unique=True),
        IndexModel([("tenant_id", ASCENDING), ("status", ASCENDING), ("next_send_at", ASCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("phone", ASCENDING)]),
        IndexModel([("phone", ASCENDING), ("status", ASCENDING)]),     # a platform-wide STOP cancels every practice's
    ])
    await db.usage_events.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("invoiced", ASCENDING), ("at", ASCENDING)]),
    ])
    await db.audit_log.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)]),
        # The hash chain (PRIV-10): one row per position, so two writers can't both take seq N.
        IndexModel([("tenant_id", ASCENDING), ("seq", ASCENDING)], unique=True,
                   partialFilterExpression={"seq": {"$exists": True}}, name="uniq_tenant_audit_seq"),
    ])
    await db.password_resets.create_indexes([
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ])
    await db.invites.create_indexes([
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("tenant_id", ASCENDING), ("email", ASCENDING)]),
    ])
    # Legacy hotel collections and the shared ones: tenant-prefixed indexes for scoped queries.
    await db.rooms.create_indexes([IndexModel([("tenant_id", ASCENDING), ("is_active", ASCENDING)])])
    await db.bookings.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("check_in_date", ASCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("guest_phone", ASCENDING)]),
    ])
    await db.conversations.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("phone", ASCENDING), ("updated_at", DESCENDING)]),
    ])
    await db.calls.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("started_at", DESCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("outcome", ASCENDING), ("started_at", DESCENDING)]),
        IndexModel([("tenant_id", ASCENDING), ("caller_phone", ASCENDING), ("started_at", DESCENDING)]),
    ])
    # Phase 6: consent revocation evidence and data subject request log
    await db.consent_events.create_indexes([
        IndexModel([("phone", ASCENDING), ("at", DESCENDING)]),
        IndexModel([("phone", ASCENDING), ("scope", ASCENDING), ("at", DESCENDING)]),   # is_opted_out
        IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)]),
    ])
    await db.dsr_requests.create_indexes([
        IndexModel([("tenant_id", ASCENDING), ("completed_at", DESCENDING)]),
    ])
    await db.contacts.create_indexes([IndexModel([("phone", ASCENDING)])])   # platform-wide opt-outs
    # Phase 7: owner feedback, transcript reviews, eval cases
    await db.owner_feedback.create_indexes([IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)])])
    await db.call_reviews.create_indexes([
        IndexModel([("call_id", ASCENDING)], unique=True),
        IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)]),
    ])
    await db.eval_cases.create_indexes([IndexModel([("status", ASCENDING), ("created_at", DESCENDING)])])
    await db.tenants.create_indexes([IndexModel([("pilot.active", ASCENDING)])])


_create_indexes = create_indexes  # backwards-compatible name (tests, migrations)
