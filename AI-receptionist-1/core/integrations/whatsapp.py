"""
One WhatsApp sender (Roadmap Task 2.9) — replaces the three copies in meta_handler, reminders
and the hotel tools. Meta Cloud API, text only; media helpers stay in whatsapp/meta_handler.py.

Free-form text is delivered only inside the 24-hour customer-service window; anything else needs
an approved template (Phase 2.9 follow-up). Returns False on any failure, never raises.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from config import settings
from core.phone import digits, mask_phone

logger = logging.getLogger(__name__)

CHUNK = 1500


def base_url() -> str:
    version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
    return f"https://graph.facebook.com/{version}"


def chunk(message: str, size: int = CHUNK) -> List[str]:
    message = message or ""
    if len(message) <= size:
        return [message]
    parts: List[str] = []
    while message:
        cut = message.rfind("\n", 0, size) if len(message) > size else len(message)
        if cut <= 0:
            cut = size
        parts.append(message[:cut].rstrip())
        message = message[cut:].lstrip()
    return [p for p in parts if p]


def configured(phone_number_id: Optional[str] = None) -> bool:
    return bool((settings.META_WA_ACCESS_TOKEN or "").strip()) and bool(
        (phone_number_id or settings.META_WA_PHONE_NUMBER_ID or "").strip())


async def send_text(to: str, message: str, phone_number_id: Optional[str] = None) -> bool:
    """Send a text message from the given business number (or the platform default)."""
    token = (settings.META_WA_ACCESS_TOKEN or "").strip()
    pnid = (phone_number_id or settings.META_WA_PHONE_NUMBER_ID or "").strip()
    if not token or not pnid:
        logger.warning("WhatsApp send skipped: token or phone_number_id missing")
        return False
    dest = digits(to)
    if len(dest) < 7:
        logger.warning("WhatsApp send skipped: bad destination %s", mask_phone(to))
        return False
    url = f"{base_url()}/{pnid}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for part in chunk(message):
                payload = {"messaging_product": "whatsapp", "to": dest, "type": "text", "text": {"body": part}}
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    logger.error("WhatsApp send to %s failed: HTTP %s", mask_phone(to), resp.status_code)
                    return False
        logger.info("WhatsApp sent to %s", mask_phone(to))
        return True
    except Exception:
        logger.error("WhatsApp send to %s errored", mask_phone(to), exc_info=True)
        return False
