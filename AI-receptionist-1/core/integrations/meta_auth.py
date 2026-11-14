"""
Meta (WhatsApp Cloud API) webhook signature verification.

Meta signs the RAW request body with the App Secret (App Dashboard → Settings → Basic) and
sends it as `X-Hub-Signature-256: sha256=<hex>`. This is NOT the verify token.
"""

import hashlib
import hmac
import logging

from fastapi import HTTPException, Request

from config import settings

logger = logging.getLogger(__name__)


def meta_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def is_valid_meta_signature(body: bytes, header: str) -> bool:
    secret = (settings.META_APP_SECRET or "").strip()
    if not secret or not header:
        return False
    return hmac.compare_digest(header.strip(), meta_signature(secret, body))


async def verify_meta(request: Request) -> bytes:
    """FastAPI dependency: returns the raw body when the signature is valid, else 403."""
    body = await request.body()  # RAW bytes — never parse before verifying
    if not (settings.META_APP_SECRET or "").strip():
        # The production config guard refuses to boot without META_APP_SECRET, so this
        # branch is reachable only in development/staging.
        if settings.APP_ENV == "development":
            logger.warning("META_APP_SECRET not set — accepting unsigned WhatsApp webhook (development only)")
            return body
        raise HTTPException(status_code=403, detail="invalid signature")
    if not is_valid_meta_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("Rejected WhatsApp webhook with invalid signature")
        raise HTTPException(status_code=403, detail="invalid signature")
    return body
