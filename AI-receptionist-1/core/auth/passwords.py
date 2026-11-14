"""argon2id password hashing. Never store or log a plaintext password."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()   # library defaults: argon2id, 64 MiB, t=3, p=4

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError, TypeError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:
        return True


def password_problem(password: str) -> str | None:
    """A reason the password is too weak, or None."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if password.lower() in {"password12", "1234567890", "qwertyuiop", "receptionist"}:
        return "That password is too common."
    return None
