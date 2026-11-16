"""
take_message — record a message for the business when no one can take the call.

Stored in the tenant's `messages` (the dashboard inbox lists them) and pushed to the owner over
the channels in tenant.notifications.on_message, in the background. The caller never waits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from core.integrations import webhooks
from core.notifications import fire, notify_owner
from core.phone import mask_phone

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

TAKE_MESSAGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "take_message",
        "description": (
            "Save a message for the team, who will call the person back. Use when the caller asks "
            "for a person and no one is available, wants something you cannot do, or asks a "
            "question you cannot answer from the facts or a tool. Read the callback number back "
            "and get a yes before calling this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caller_name": {"type": "string", "description": "The caller's name"},
                "callback_number": {"type": "string", "description": "Number to call back, with country code"},
                "message": {"type": "string", "description": "What the caller needs, in one or two sentences"},
                "urgent": {"type": "boolean", "description": "True only if the caller says it is urgent"},
            },
            "required": ["caller_name", "callback_number", "message"],
        },
    },
}


def new_message_id() -> str:
    return f"MSG{uuid.uuid4().hex[:8].upper()}"


async def take_message(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    caller_name = str(args.get("caller_name") or "").strip()[:120]
    callback_number = str(args.get("callback_number") or ctx.caller_phone or "").strip()[:32]
    message = str(args.get("message") or "").strip()[:2000]
    urgent = bool(args.get("urgent", False))
    if not message:
        return {"error": "message_required", "message": "Ask the caller what the message is."}
    if not callback_number:
        return {"error": "callback_number_required", "message": "Ask for a number to call back."}

    doc = {
        "message_id": new_message_id(),
        "caller_name": caller_name or "Unknown",
        "callback_number": callback_number,
        "caller_phone": ctx.caller_phone,          # the verified line it came from, if any
        "message": message,
        "urgent": urgent,
        "channel": ctx.channel,
        "call_id": ctx.call_id,
        "status": "new",
        "created_at": datetime.now(timezone.utc),
    }
    await ctx.repos().messages.insert_one(doc)
    logger.info("Message %s saved (urgent=%s, callback %s)", doc["message_id"], urgent, mask_phone(callback_number))

    text = (f"{'🔴 URGENT — ' if urgent else ''}New message ({ctx.channel})\n"
            f"From: {doc['caller_name']}\nCall back: {callback_number}\n\n{message}")
    fire(notify_owner(ctx.tenant_cfg, "on_message", text, subject="New message from a caller", urgent=urgent))
    webhooks.emit(ctx.tenant, "message.created", {k: v for k, v in doc.items() if k not in ("_id", "tenant_id")})

    return {
        "success": True,
        "message_id": doc["message_id"],
        "message": "Saved. Tell the caller the team will call them back as soon as possible.",
    }
