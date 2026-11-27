"""
Transactional e-mail via Resend (Roadmap Task 2.9). Set up SPF, DKIM and DMARC on EMAIL_FROM's
domain before the first customer e-mail. Without RESEND_API_KEY the message is logged (dev).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.EMAIL_FROM)


async def send_email(to: str, subject: str, text: str, html: Optional[str] = None, *,
                     attachments: Optional[List[Dict[str, str]]] = None, reply_to: str = "") -> bool:
    """`attachments`: [{"filename", "content" (base64), "content_type"?}] (e.g. a calendar invite)."""
    if not to or "@" not in to:
        return False
    if not configured():
        logger.info("E-mail (not sent, RESEND_API_KEY unset): to=%s subject=%s", to, subject)
        return False
    payload: Dict[str, object] = {"from": settings.EMAIL_FROM, "to": [to], "subject": subject[:200], "text": text}
    if html:
        payload["html"] = html
    if attachments:
        payload["attachments"] = [{k: a[k] for k in ("filename", "content", "content_type") if a.get(k)}
                                  for a in attachments]
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.resend.com/emails", json=payload,
                                     headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
        if resp.status_code >= 400:
            logger.error("E-mail to %s failed: HTTP %s %s", to, resp.status_code, resp.text[:300])
            return False
        return True
    except Exception:
        logger.error("E-mail to %s errored", to, exc_info=True)
        return False
