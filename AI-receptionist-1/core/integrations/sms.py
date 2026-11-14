"""
Transactional SMS via Twilio's REST API (Roadmap Task 2.9).

⚠ US traffic from 10-digit numbers needs A2P 10DLC registration (or a verified toll-free
number) before carriers deliver it; other countries have their own sender rules. Sends go from
TWILIO_SMS_FROM (the platform's verified number) unless the tenant has its own. Never raises.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings
from core.phone import digits, mask_phone

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_SMS_FROM)


async def send_sms(to: str, body: str, sender: Optional[str] = None) -> bool:
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        logger.info("SMS skipped (Twilio not configured) → %s", mask_phone(to))
        return False
    frm = (sender or settings.TWILIO_SMS_FROM or "").strip()
    if not frm:
        logger.info("SMS skipped (no TWILIO_SMS_FROM) → %s", mask_phone(to))
        return False
    dest = digits(to)
    if len(dest) < 7:
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data={"To": f"+{dest}", "From": frm, "Body": body[:1500]},
                                     auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        if resp.status_code >= 400:
            logger.error("SMS to %s failed: HTTP %s", mask_phone(to), resp.status_code)
            return False
        logger.info("SMS sent to %s", mask_phone(to))
        return True
    except Exception:
        logger.error("SMS to %s errored", mask_phone(to), exc_info=True)
        return False
