"""
Read a verification code to a phone by voice call (Twilio Calls API with inline TwiML).

The platform sends no SMS, so owners verify their transfer number by answering a short call that
reads the 6-digit code twice. Placed from TWILIO_PHONE_NUMBER. Never raises; returns the call SID
or None. Nothing about the call is recorded.
"""

from __future__ import annotations

import logging
from typing import Optional
from xml.sax.saxutils import escape

import httpx

from config import settings
from core.phone import digits, mask_phone

logger = logging.getLogger(__name__)

# Amazon Polly voices Twilio offers, matched to the practice's country.
_VOICES = {"AU": ("Polly.Olivia-Neural", "en-AU"), "GB": ("Polly.Amy-Neural", "en-GB")}
_DEFAULT_VOICE = ("Polly.Joanna-Neural", "en-US")


def configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_PHONE_NUMBER)


def twiml(code: str, country: str = "") -> str:
    """'123456' → spoken '1, 2, 3, 4, 5, 6', twice, with a pause so the owner can grab a pen."""
    voice, lang = _VOICES.get((country or "").upper(), _DEFAULT_VOICE)
    spoken = escape(", ".join(code))
    say = f'<Say voice="{voice}" language="{lang}">'
    return ("<Response><Pause length=\"1\"/>"
            f"{say}Hello. Your verification code is: {spoken}.</Say><Pause length=\"1\"/>"
            f"{say}Again, your code is: {spoken}. Goodbye.</Say></Response>")


async def call_with_code(to: str, code: str, country: str = "") -> Optional[str]:
    if not configured():
        logger.info("Verification call skipped (Twilio voice not configured) → %s", mask_phone(to))
        return None
    dest = digits(to)
    if len(dest) < 7:
        return None
    url = f"{settings.twilio_api_base}/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Calls.json"
    data = {"To": f"+{dest}", "From": settings.TWILIO_PHONE_NUMBER, "Twiml": twiml(code, country),
            "Timeout": "30", "Record": "false"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data=data, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        if resp.status_code >= 400:
            logger.error("Verification call to %s failed: HTTP %s", mask_phone(to), resp.status_code)
            return None
        logger.info("Verification call placed to %s", mask_phone(to))
        try:
            return str(resp.json().get("sid") or "") or "placed"
        except ValueError:
            return "placed"
    except Exception:
        logger.error("Verification call to %s errored", mask_phone(to), exc_info=True)
        return None
