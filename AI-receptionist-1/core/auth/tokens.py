"""
Tokens.

- Access token: HS256 JWT signed with SECRET_KEY, ACCESS_TOKEN_TTL_MIN (15 min). Carries the user,
  the active tenant and the role in that tenant, so tenant checks need no database hit.
- Refresh token: 256 random bits, stored as a SHA-256 hash in `sessions` with an expiry; sent to
  the browser in an httpOnly cookie, rotated on every refresh, revoked on logout.
- One-time tokens (password reset, invites) use the same opaque + hashed scheme.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from config import settings

ACCESS_TOKEN_TYPE = "access"
LEGACY_ADMIN_SUB = "env-admin"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: bytes) -> str:
    return _b64e(hmac.new(settings.SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest())


def encode_jwt(claims: Dict[str, Any], ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + max(1, ttl_seconds)}
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    return f"{header}.{body}.{_sign(signing_input)}"


def decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    """The payload if the signature is valid and the token unexpired, else None."""
    try:
        header_b64, payload_b64, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(f"{header_b64}.{payload_b64}".encode("ascii"))):
            return None
        if json.loads(_b64d(header_b64)).get("alg") != "HS256":
            return None
        payload = json.loads(_b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def access_token(*, user_id: str, email: str, tenant_id: Optional[str], role: Optional[str],
                 platform_admin: bool, name: str = "", mfa: bool = False) -> tuple[str, int]:
    ttl = max(1, int(settings.ACCESS_TOKEN_TTL_MIN)) * 60
    return encode_jwt({"typ": ACCESS_TOKEN_TYPE, "sub": user_id, "email": email, "name": name,
                       "tid": tenant_id, "role": role, "pa": bool(platform_admin), "mfa": bool(mfa)}, ttl), ttl


MFA_TOKEN_TYPE = "mfa"
MFA_TOKEN_TTL_S = 5 * 60


def mfa_challenge_token(user_id: str) -> str:
    """Proves the password step passed; exchanged with a TOTP code at /auth/mfa/verify."""
    return encode_jwt({"typ": MFA_TOKEN_TYPE, "sub": user_id}, MFA_TOKEN_TTL_S)


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
