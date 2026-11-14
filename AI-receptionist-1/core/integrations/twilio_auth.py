"""
Twilio webhook signature verification (X-Twilio-Signature).

Behind Nginx / a load balancer, request.url is the INTERNAL url, but Twilio signed the PUBLIC
url. We therefore rebuild the url from WEBHOOK_BASE_URL + path (+ query) before validating.
"""

import logging
from typing import Mapping, Optional

from fastapi import HTTPException, Request, WebSocket
from twilio.request_validator import RequestValidator

from config import settings

logger = logging.getLogger(__name__)


def _validator() -> Optional[RequestValidator]:
    token = (settings.TWILIO_AUTH_TOKEN or "").strip()
    return RequestValidator(token) if token else None


def _public_url(path: str, query: str, scheme: Optional[str] = None) -> str:
    base = (settings.WEBHOOK_BASE_URL or "").strip().rstrip("/")
    if scheme and "://" in base:
        base = f"{scheme}://{base.split('://', 1)[1]}"
    url = base + path
    return f"{url}?{query}" if query else url


def is_valid_twilio_signature(url: str, params: Mapping[str, str], signature: str) -> bool:
    validator = _validator()
    if validator is None or not signature:
        return False
    try:
        return bool(validator.validate(url, dict(params), signature))
    except Exception:
        logger.warning("Twilio signature validation raised", exc_info=True)
        return False


async def verify_twilio(request: Request) -> None:
    """FastAPI dependency for Twilio HTTP webhooks. 403 on a missing or forged signature."""
    if not settings.VERIFY_TWILIO_SIGNATURE:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    params = {k: str(v) for k, v in (await request.form()).items()} if request.method == "POST" else {}
    url = _public_url(request.url.path, request.url.query)
    if not is_valid_twilio_signature(url, params, signature):
        logger.warning("Rejected Twilio webhook with invalid signature: %s", request.url.path)
        raise HTTPException(status_code=403, detail="invalid signature")


def verify_twilio_websocket(websocket: WebSocket) -> bool:
    """
    Check X-Twilio-Signature on a Media Streams WebSocket upgrade, before accept().

    ⚠ VERIFY against Twilio's Media Streams security docs on the first real call. We accept a
    signature computed over either the wss:// or https:// form of the public url. If real calls
    are rejected, set VERIFY_TWILIO_WS_SIGNATURE=false (the HTTP webhook stays protected, and a
    stream can only attach to a CallSid that passed /voice/incoming).
    """
    if not (settings.VERIFY_TWILIO_SIGNATURE and settings.VERIFY_TWILIO_WS_SIGNATURE):
        return True
    signature = websocket.headers.get("X-Twilio-Signature", "")
    query = websocket.url.query
    for scheme in ("wss", "https"):
        if is_valid_twilio_signature(_public_url(websocket.url.path, query, scheme), {}, signature):
            return True
    logger.warning("Rejected Twilio media stream with invalid signature")
    return False
