"""
send_info_link — text the caller a link (booking page, menu, directions, payment) by SMS or WhatsApp.

Roadmap Task 2.7: **only with consent**, and the consent is logged on the contact. The links come
from the tenant's settings (`tenant.links`); directions fall back to a maps link built from the
business address, so it works for every business with an address. The tool is offered to the
model only when at least one link can be sent (see `available_kinds`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote_plus

from core.notifications import notify_contact
from core.phone import mask_phone
from core.tenancy.models import TenantConfig

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

KINDS = ("booking", "menu", "directions", "payment", "website")

SEND_INFO_LINK_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "send_info_link",
        "description": (
            "Text the caller a link: the online booking page, the menu, directions to the business, "
            "a payment page or the website. First ask whether they'd like it sent by text to their "
            "number, and call this only after they say yes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(KINDS), "description": "Which link to send"},
                "consent": {"type": "boolean", "description": "True only if the caller agreed to receive a text"},
                "phone": {"type": "string", "description": "Number to text, with country code, if not the caller's own"},
            },
            "required": ["kind", "consent"],
        },
    },
}


def link_for(t: TenantConfig, kind: str) -> Optional[str]:
    if kind == "website":
        return t.website or None
    if kind == "directions":
        if t.links.directions:
            return t.links.directions
        if t.address_text:
            return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{t.name} {t.address_text}")
        return None
    return getattr(t.links, kind, "") or None


def available_kinds(t: TenantConfig) -> List[str]:
    return [k for k in KINDS if link_for(t, k)]


def _label(t: TenantConfig, kind: str) -> str:
    return {"booking": "book online", "menu": "see the menu", "directions": "find us",
            "payment": "pay online", "website": "visit our website"}.get(kind, kind)


async def send_info_link(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    t = ctx.tenant_cfg
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return {"error": "unknown_kind", "message": f"kind must be one of: {', '.join(KINDS)}."}
    url = link_for(t, kind)
    if not url:
        return {"error": "no_link", "message": "The business hasn't set up that link. Offer to take a message instead."}
    if not bool(args.get("consent")):
        return {"error": "consent_required",
                "message": "Ask the caller whether they'd like the link sent by text, and get a yes first."}
    phone = str(args.get("phone") or ctx.caller_phone or "").strip()[:32]
    if not phone:
        return {"error": "phone_required", "message": "Ask which number to text, with the country code."}

    repos = ctx.repos()
    contact = await repos.contacts.upsert_by_phone(phone, {})
    if contact.get("do_not_contact"):
        return {"error": "do_not_contact", "message": "This number has asked not to be contacted. Read the information out instead."}

    text = f"{t.name}: here's the link to {_label(t, kind)}: {url}"
    ok = await notify_contact(t, phone, text)
    if not ok:
        return {"error": "send_failed", "message": "The text couldn't be sent right now. Read the information out instead."}

    now = datetime.now(timezone.utc)
    consent = {"granted": True, "at": now, "source": f"{ctx.channel}_call" if ctx.channel != "whatsapp" else "whatsapp",
               "call_id": ctx.call_id, "kind": kind}
    await repos.contacts.update_one({"phone": phone}, {"$set": {"consent.sms": consent, "last_seen_at": now}})
    logger.info("Sent %s link to %s", kind, mask_phone(phone))
    return {"success": True, "kind": kind, "message": "Sent. Tell the caller to check their phone."}
