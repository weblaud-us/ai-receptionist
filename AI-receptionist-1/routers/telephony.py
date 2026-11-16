"""
Last-resort phone fallback (Roadmap Task 3.5).

    POST /telephony/fallback        ← Twilio trunk "Disaster Recovery URL"

Twilio calls this only when it cannot deliver a call to LiveKit (LiveKit unreachable, the SIP
trunk refusing calls). The caller must still reach the business, so the answer is TwiML that
rings the business's own transfer number, or — if it has none — asks the caller to try again.

The request is signed by Twilio like every other webhook. It never touches LiveKit, the voice
worker, Redis or an LLM. A trunk has a single Disaster Recovery URL, so in production it points
at `fallback_app.py` running on a separate host (deploy/README.md) — if it pointed at the main
host, a host outage would leave no fallback at all.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse

from core.integrations.twilio_auth import verify_twilio
from core.phone import mask_phone
from core.tenancy import resolve_by_phone_number
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

telephony_router = APIRouter(prefix="/telephony", tags=["Telephony"])

BUSY_MESSAGE = ("Sorry, we can't take your call right now because of a technical problem. "
                "Please try again in a few minutes.")
_NUMBER_RE = re.compile(r"\+?\d{7,15}")


def dialed_number(raw: str) -> Optional[str]:
    """'+15551234567', 'sip:+15551234567@trunk.pstn.twilio.com' → '+15551234567'."""
    m = _NUMBER_RE.search(raw or "")
    return ("+" + m.group(0).lstrip("+")) if m else None


def fallback_twiml(tenant: Optional[TenantConfig]) -> str:
    resp = VoiceResponse()
    number = tenant.transfer.number.strip() if tenant else ""
    if number:
        # answerOnBridge: the caller keeps hearing ringing until staff pick up.
        resp.dial(number, answer_on_bridge=True, timeout=25)
        resp.say(BUSY_MESSAGE)      # reached only if nobody answered
    else:
        resp.say(BUSY_MESSAGE)
    resp.hangup()
    return str(resp)


@telephony_router.api_route("/fallback", methods=["GET", "POST"], dependencies=[Depends(verify_twilio)])
async def fallback(request: Request) -> Response:
    params = dict(await request.form()) if request.method == "POST" else dict(request.query_params)
    number = dialed_number(str(params.get("To") or params.get("Called") or ""))
    tenant: Optional[TenantConfig] = None
    try:
        tenant = await resolve_by_phone_number(number)
    except Exception:
        logger.error("Fallback: tenant lookup failed", exc_info=True)
    # This endpoint being hit at all means calls are not reaching the voice agent.
    logger.error("Telephony fallback used for %s (business %s): calls are not reaching LiveKit",
                 mask_phone(number), tenant.id if tenant else "unknown")
    try:
        import sentry_sdk

        sentry_sdk.capture_message("Telephony fallback used: calls are not reaching LiveKit", level="error")
    except Exception:
        pass
    return Response(content=fallback_twiml(tenant), media_type="application/xml")
