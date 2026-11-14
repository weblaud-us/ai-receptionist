"""
take_message — record a message for the business when no one can take the call.

Stored in the `messages` collection (the dashboard lists them) and, when OWNER_NOTIFY_WHATSAPP
is set, pushed to the owner's WhatsApp in the background. The caller never waits on that send.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from config import settings
from core.phone import mask_phone
from core.tools.hotel import run_in_background
from database.mongodb import get_db

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

TAKE_MESSAGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "take_message",
        "description": (
            "Save a message for the hotel team, who will call the guest back. Use when the guest "
            "asks for a person and no one is available, wants something you cannot do, or asks a "
            "question you cannot answer from the facts or a tool. Read the callback number back to "
            "the guest and get a yes before calling this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caller_name": {"type": "string", "description": "The guest's name"},
                "callback_number": {"type": "string", "description": "Number to call back, with country code"},
                "message": {"type": "string", "description": "What the guest needs, in one or two sentences"},
                "urgent": {"type": "boolean", "description": "True only if the guest says it is urgent"},
            },
            "required": ["caller_name", "callback_number", "message"],
        },
    },
}


def new_message_id() -> str:
    return f"MSG{uuid.uuid4().hex[:8].upper()}"


async def take_message(
    ctx: "ToolContext",
    caller_name: str = "",
    callback_number: str = "",
    message: str = "",
    urgent: bool = False,
) -> Dict[str, Any]:
    caller_name = (caller_name or "").strip()[:120]
    callback_number = (callback_number or ctx.caller_phone or "").strip()[:32]
    message = (message or "").strip()[:2000]
    if not message:
        return {"error": "message_required", "message": "Ask the guest what the message is."}
    if not callback_number:
        return {"error": "callback_number_required", "message": "Ask for a number to call back."}

    doc = {
        "message_id": new_message_id(),
        "caller_name": caller_name or "Unknown",
        "callback_number": callback_number,
        "caller_phone": ctx.caller_phone,          # the verified line it came from, if any
        "message": message,
        "urgent": bool(urgent),
        "channel": ctx.channel,
        "call_id": ctx.call_id,
        "status": "new",
        "created_at": datetime.now(timezone.utc),
    }
    await get_db().messages.insert_one(doc)
    logger.info("Message %s saved (urgent=%s, callback %s)", doc["message_id"], doc["urgent"],
                mask_phone(callback_number))

    if settings.OWNER_NOTIFY_WHATSAPP.strip():
        run_in_background(_notify_owner(doc))

    return {
        "success": True,
        "message_id": doc["message_id"],
        "message": "Saved. Tell the guest the team will call them back as soon as possible.",
    }


async def _notify_owner(doc: Dict[str, Any]) -> None:
    try:
        from whatsapp.meta_handler import _send_meta_whatsapp

        text = (
            f"{'🔴 URGENT — ' if doc['urgent'] else ''}New message ({doc['channel']})\n"
            f"From: {doc['caller_name']}\nCall back: {doc['callback_number']}\n\n{doc['message']}"
        )
        for owner in (n.strip() for n in settings.OWNER_NOTIFY_WHATSAPP.split(",")):
            if owner:
                await _send_meta_whatsapp(owner, text, None)
    except Exception:
        logger.warning("Owner notification for %s failed", doc.get("message_id"), exc_info=True)
