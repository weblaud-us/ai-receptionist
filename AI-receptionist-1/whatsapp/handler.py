"""
Shared WhatsApp helpers for Meta-only messaging flow.
Twilio WhatsApp webhook/sender code has been removed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ai.prompts import CONVERSATION_SUMMARY_WHATSAPP, SINGLE_BOOKING_DETAIL
from config import settings
from database.models import Conversation, ConversationChannel, ConversationContext
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from core.repositories.scoped import scoped_db
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)


async def _get_or_create_whatsapp_session(db, phone: str, name: Optional[str],
                                          tenant: Optional[TenantConfig] = None) -> dict:
    """Get the active WhatsApp session for this customer *of this business*, or create one."""
    if tenant is not None:
        db = scoped_db(tenant)
    cutoff = datetime.utcnow() - timedelta(days=max(1, int(settings.CONVERSATION_MEMORY_DAYS)))

    existing = await db.conversations.find_one(
        {
            "phone": phone,
            "channel": "whatsapp",
            "status": "active",
            "updated_at": {"$gte": cutoff},
        },
        sort=[("updated_at", -1)],
    )
    if existing:
        return existing

    session_id = str(uuid.uuid4())
    conv = Conversation(
        session_id=session_id,
        phone=phone,
        channel=ConversationChannel.WHATSAPP,
        context=ConversationContext(
            guest_phone=phone,
            guest_name=name,
        ),
    )
    doc = conv.model_dump(by_alias=True)
    await db.conversations.insert_one(doc)
    logger.info("New Meta WhatsApp session: %s for %s", session_id, phone)
    return doc


async def send_conversation_summary_whatsapp(
    session_id: str,
    phone: str,
    channel: str = "call",
    hotel_id: Optional[str] = None,
) -> bool:
    """
    Send booking summary to customer WhatsApp after voice call/chat completion.
    Uses Meta Cloud API sender.
    """
    _ = hotel_id  # compatibility with existing call sites
    try:
        profile = get_hotel_profile()
        db = get_db()

        conversation = await db.conversations.find_one({"session_id": session_id})
        if not conversation:
            logger.warning("No conversation found for session %s", session_id)
            return False

        booking_ids = conversation.get("booking_ids", [])
        if not booking_ids:
            logger.info("No bookings in conversation %s, skipping summary", session_id)
            return False

        bookings = await db.bookings.find({"booking_id": {"$in": booking_ids}}).to_list(length=100)
        if not bookings:
            logger.warning("No booking documents found for IDs: %s", booking_ids)
            return False

        details = []
        for booking in bookings:
            children_count = int(booking.get("children", 0) or 0)
            children_str = f" + {children_count} children" if children_count > 0 else ""
            pricing = booking.get("pricing", {})
            total_amount = pricing.get("final_total", 0) or booking.get("total_amount", 0)
            currency = pricing.get("currency", profile["currency"])
            details.append(
                SINGLE_BOOKING_DETAIL.format(
                    booking_id=booking.get("booking_id", ""),
                    guest_name=booking.get("guest_name", ""),
                    room_type=str(booking.get("room_type", "")).title(),
                    room_number=booking.get("room_number", ""),
                    check_in_date=booking.get("check_in_date", ""),
                    check_out_date=booking.get("check_out_date", ""),
                    adults=booking.get("adults", 1),
                    children_str=children_str,
                    currency=currency,
                    total_amount=float(total_amount),
                )
            )

        plural = "s" if len(bookings) > 1 else ""
        channel_type = "call" if channel == "call" else "chat"
        summary_msg = CONVERSATION_SUMMARY_WHATSAPP.format(
            channel_type=channel_type,
            hotel_name=profile["name"],
            plural=plural,
            booking_details="\n".join(details),
            hotel_address=profile["address"],
            checkin_time=profile["checkin_time"],
            checkout_time=profile["checkout_time"],
        )

        from whatsapp.meta_handler import _send_meta_whatsapp

        success = await _send_meta_whatsapp(phone, summary_msg, None)
        if success:
            await db.conversations.update_one(
                {"session_id": session_id},
                {"$set": {"summary_sent": True, "summary_sent_at": datetime.utcnow()}},
            )
            logger.info("Conversation summary sent via Meta WhatsApp to %s for session %s", phone, session_id)
        return success
    except Exception as e:
        logger.error("Error sending conversation summary: %s", e, exc_info=True)
        return False
