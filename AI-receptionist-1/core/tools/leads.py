"""
capture_lead — the home-services vertical's core tool: a job request with address and urgency.

Saved to `leads`, mirrored as a message so the inbox shows it, and pushed to the owner. Urgent
leads (flooding, gas, sparks, no heat in freezing weather) also trigger the urgent channels.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from core.notifications import fire, notify_owner
from core.tenancy.ids import new_id

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

CAPTURE_LEAD_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "capture_lead",
        "description": (
            "Record a job request so the team can call back with a quote or a visit time. Collect "
            "the name, callback number, service address, a short description of the problem and how "
            "urgent it is first, read the number and address back, and get a yes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string", "description": "Callback number with country code"},
                "address": {"type": "string", "description": "Service address"},
                "problem": {"type": "string", "description": "What needs doing, in one or two sentences"},
                "urgency": {"type": "string", "enum": ["emergency", "today", "this_week", "flexible"]},
                "preferred_time": {"type": "string", "description": "When they'd like the visit, in their words"},
            },
            "required": ["name", "phone", "problem", "urgency"],
        },
    },
}


async def capture_lead(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()[:120]
    phone = str(args.get("phone") or ctx.caller_phone or "").strip()[:32]
    problem = str(args.get("problem") or "").strip()[:1000]
    urgency = str(args.get("urgency") or "flexible")
    if not problem:
        return {"error": "problem_required", "message": "Ask what the problem is."}
    if not phone:
        return {"error": "phone_required", "message": "Ask for a callback number."}
    repos = ctx.repos()
    contact = await repos.contacts.upsert_by_phone(phone, {"name": name, "address": args.get("address")})
    lead = {
        "_id": new_id("lead"),
        "contact_id": contact.get("_id"),
        "name": name or "Unknown", "phone": phone,
        "address": str(args.get("address") or "").strip()[:300],
        "problem": problem, "urgency": urgency,
        "preferred_time": str(args.get("preferred_time") or "").strip()[:200],
        "status": "new", "channel": ctx.channel, "call_id": ctx.call_id,
        "created_at": datetime.now(timezone.utc),
    }
    await repos.leads.insert_one(lead)
    urgent = urgency in ("emergency", "today")
    text = (f"{'🔴 ' if urgent else ''}New job request ({urgency.replace('_', ' ')})\n"
            f"From: {lead['name']} — {phone}\nAddress: {lead['address'] or 'not given'}\n\n{problem}")
    if lead["preferred_time"]:
        text += f"\n\nPreferred time: {lead['preferred_time']}"
    fire(notify_owner(ctx.tenant_cfg, "on_message", text, subject="New job request", urgent=urgent))
    logger.info("Lead %s captured (%s)", lead["_id"], urgency)
    return {"success": True, "lead_id": lead["_id"],
            "message": ("Saved. Tell them someone will call back " +
                        ("as soon as possible." if urgent else "shortly to arrange a time."))}
