"""
Owner and caller notifications (Roadmap Task 2.9), fire-and-forget.

    notify_owner(tenant, "on_message", text)       → channels from tenant.notifications.on_message
    notify_contact(tenant, phone, text)            → WhatsApp (with consent) / SMS

Each channel is optional and failures are logged, never raised: a caller must never wait on, or
hear about, a notification problem.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from config import settings
from core.integrations import email as email_api
from core.integrations import sms as sms_api
from core.integrations import whatsapp as wa_api
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

_tasks: Set["asyncio.Task[Any]"] = set()


def fire(coro: Any) -> "asyncio.Task[Any]":
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _owner_whatsapp_numbers(t: TenantConfig) -> List[str]:
    raw = t.notifications.owner_whatsapp or (settings.OWNER_NOTIFY_WHATSAPP if t.is_legacy else "")
    return [n.strip() for n in raw.split(",") if n.strip()]


async def notify_owner(t: TenantConfig, event: str, text: str, *, subject: Optional[str] = None,
                       urgent: bool = False) -> Dict[str, bool]:
    """Send `text` to the owner over every channel configured for `event`. Returns per-channel result."""
    channels = set(getattr(t.notifications, event, None) or [])
    if urgent:
        channels |= set(t.notifications.on_urgent or [])
    results: Dict[str, bool] = {}
    prefix = f"{t.name}: "

    wa_numbers = _owner_whatsapp_numbers(t)
    if wa_numbers and ("whatsapp" in channels or "sms" in channels or t.is_legacy):
        pnid = t.integrations.whatsapp.phone_number_id or None
        ok = True
        for n in wa_numbers:
            ok = await wa_api.send_text(n, prefix + text, pnid) and ok
        results["whatsapp"] = ok

    if "sms" in channels and t.notifications.owner_phone:
        results["sms"] = await sms_api.send_sms(t.notifications.owner_phone, prefix + text)

    if "email" in channels and t.notifications.owner_email:
        results["email"] = await email_api.send_email(
            t.notifications.owner_email, subject or f"{t.name} — {event.replace('on_', '').replace('_', ' ')}", text)

    if "call" in channels and urgent and t.notifications.owner_phone:
        # Phase 3/4: place an outbound call through the LiveKit outbound trunk. For now, SMS again.
        results.setdefault("sms", await sms_api.send_sms(t.notifications.owner_phone, "URGENT — " + prefix + text))

    if not results:
        logger.info("No owner notification channel configured for %s (%s)", t.id, event)
    return results


async def notify_contact(t: TenantConfig, phone: str, text: str, *, prefer: str = "whatsapp") -> bool:
    """A confirmation or reminder to a caller. WhatsApp first when configured, else SMS."""
    pnid = t.integrations.whatsapp.phone_number_id or None
    if prefer == "whatsapp" and wa_api.configured(pnid):
        if await wa_api.send_text(phone, text, pnid):
            return True
    return await sms_api.send_sms(phone, text)
