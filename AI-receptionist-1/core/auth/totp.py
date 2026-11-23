"""
TOTP two-factor codes (RFC 6238: HMAC-SHA1, 30-second steps, 6 digits) — Roadmap Task 2.10.
Works with Google Authenticator, 1Password, Authy… Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def matched_step(secret: str, code: str, *, now: Optional[float] = None, window: int = 1) -> Optional[int]:
    """The time step `code` belongs to (current ±`window` for clock drift), or None. Callers store the
    last step used and refuse it again (audit SEC-02): a code works once. Constant-time comparison."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    counter = int((now if now is not None else time.time()) // STEP)
    found = None
    for d in range(-window, window + 1):
        if hmac.compare_digest(code_at(secret, counter + d), code) and found is None:
            found = counter + d
    return found


def verify(secret: str, code: str, *, now: Optional[float] = None, window: int = 1) -> bool:
    return matched_step(secret, code, now=now, window=window) is not None


def otpauth_uri(secret: str, account: str, issuer: str = "AI Receptionist") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
