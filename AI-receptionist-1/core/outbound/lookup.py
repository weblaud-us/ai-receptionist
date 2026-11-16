"""
Line type of a number (landline / mobile / VoIP) from Twilio Lookup v2 `line_type_intelligence`.

💰 Paid per lookup, so the answer is cached on the lead for SALES_LINE_TYPE_MAX_AGE_DAYS. The
default is 15 days rather than the roadmap's 90: numbers are ported from landline to mobile, and
calling a recently ported mobile with an AI voice is exactly what the gate exists to prevent.

None means "could not find out" — the gate treats that as not allowed and tries again later.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings
from core.phone import mask_phone

logger = logging.getLogger(__name__)

LOOKUP_URL = "https://lookups.twilio.com/v2/PhoneNumbers/{number}"
TIMEOUT_S = 8.0

_TYPES = {
    "landline": "landline", "mobile": "mobile", "fixedvoip": "fixed_voip", "nonfixedvoip": "non_fixed_voip",
    "tollfree": "toll_free", "personal": "personal", "pager": "pager", "voicemail": "voicemail",
    "premium": "premium", "sharedcost": "premium", "uan": "premium", "unknown": "unknown",
}


def configured() -> bool:
    return all(settings.twilio_lookup_credentials)


def normalise_type(raw: Optional[str]) -> str:
    return _TYPES.get((raw or "").replace("_", "").replace("-", "").lower(), "unknown")


async def line_type(e164: str) -> Optional[str]:
    if not configured():
        return None
    sid, token = settings.twilio_lookup_credentials
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0)) as client:
            resp = await client.get(LOOKUP_URL.format(number=e164), params={"Fields": "line_type_intelligence"},
                                    auth=(sid, token))
        if resp.status_code == 404:
            return "unknown"                     # Twilio does not know the number: not a line we may call
        if resp.status_code >= 400:
            logger.warning("Line-type lookup for %s failed: HTTP %s", mask_phone(e164), resp.status_code)
            return None
        info = (resp.json() or {}).get("line_type_intelligence") or {}
        if info.get("error_code"):
            return None
        return normalise_type(info.get("type"))
    except Exception:
        logger.warning("Line-type lookup for %s errored", mask_phone(e164), exc_info=True)
        return None
