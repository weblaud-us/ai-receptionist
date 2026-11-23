"""
Encryption for secrets we must store (OAuth refresh tokens, TOTP secrets) — Roadmap Task 2.8.

Fernet (AES-128-CBC + HMAC-SHA256). The key is TOKEN_ENCRYPTION_KEY (32 url-safe base64 bytes:
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Development without a key derives one from SECRET_KEY; production refuses to boot without it
(config._production_guard, audit SEC-16) and never derives one.
Rotate by putting the new key first in a comma-separated list: old tokens still decrypt.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import settings


class DecryptionError(Exception):
    pass


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys = [k.strip() for k in (settings.TOKEN_ENCRYPTION_KEY or "").split(",") if k.strip()]
    if not keys:
        if settings.is_production:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is required in production")
        derived = base64.urlsafe_b64encode(hashlib.sha256(f"token-key:{settings.SECRET_KEY}".encode()).digest())
        keys = [derived.decode()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise DecryptionError("stored secret can't be decrypted (wrong TOKEN_ENCRYPTION_KEY?)") from e


def reset_cache() -> None:
    _fernet.cache_clear()
