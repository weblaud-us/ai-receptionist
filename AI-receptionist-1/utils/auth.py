"""
Legacy admin authentication (pre-Phase-2). Kept for the debug routes and old tokens;
Phase 2 dashboard auth lives in core/auth (users, roles, refresh cookies).

- Login with ADMIN_USERNAME / ADMIN_PASSWORD (from .env) -> signed JWT (HS256, SECRET_KEY).
- `require_admin` FastAPI dependency accepts either
    * `Authorization: Bearer <jwt>`            (dashboard)
    * `X-Admin-Key: <ADMIN_SECRET_KEY>`        (scripts / backwards compatible)
Stdlib only — no extra dependency.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

from fastapi import Header, HTTPException

from config import settings

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS = {"", "change-in-production"}


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: bytes) -> str:
    key = settings.SECRET_KEY.encode("utf-8")
    return _b64e(hmac.new(key, signing_input, hashlib.sha256).digest())


def create_access_token(username: str) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds)."""
    ttl = max(1, int(settings.ADMIN_TOKEN_TTL_MIN)) * 60
    now = int(time.time())
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64e(json.dumps(
        {"sub": username, "iat": now, "exp": now + ttl, "role": "admin"},
        separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{_sign(signing_input)}", ttl


def decode_access_token(token: str) -> Optional[dict]:
    """Return the payload if the token is valid and unexpired, else None."""
    try:
        header_b64, payload_b64, sig = token.split(".")
        expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"))
        if not hmac.compare_digest(sig, expected):
            return None
        if json.loads(_b64d(header_b64)).get("alg") != "HS256":
            return None
        payload = json.loads(_b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time check against the .env admin credentials."""
    if not settings.ADMIN_PASSWORD:
        return False
    user_ok = hmac.compare_digest(username.encode("utf-8"), settings.ADMIN_USERNAME.encode("utf-8"))
    pass_ok = hmac.compare_digest(password.encode("utf-8"), settings.ADMIN_PASSWORD.encode("utf-8"))
    return user_ok and pass_ok


def auth_configured() -> bool:
    """Login is only enabled with a password AND a non-default signing key."""
    return bool(settings.ADMIN_PASSWORD) and settings.SECRET_KEY not in _DEFAULT_SECRETS


async def require_admin(
    authorization: str = Header(default=""),
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
) -> dict:
    """
    Platform-admin guard for the remaining debug routes. Accepts Phase 2 access tokens of platform
    admins, pre-Phase-2 dashboard tokens, and X-Admin-Key. Tenant routes use core.auth instead.
    """
    from core.auth.deps import current_user

    principal = await current_user(authorization=authorization, x_admin_key=x_admin_key)
    if not principal.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrators only")
    return {"sub": principal.email or principal.user_id, "role": "admin"}
