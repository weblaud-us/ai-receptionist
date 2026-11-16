"""
Sensitive identity fields, encrypted at the application layer (Roadmap Task 3.8).

A passport / national ID number is never stored or returned in plain text:

    doc.update(id_number_fields("AB1234567"))    # → {"id_number_enc": "<fernet>", "id_number_last4": "4567"}
    public = public_guest(doc)                   # strips the ciphertext, keeps "•••4567"
    reveal_id_number(doc)                        # the plain value, for the rare screen that needs it

Encryption uses TOKEN_ENCRYPTION_KEY (core/crypto.py), the same key as OAuth tokens and 2FA secrets.
`migrations/m0004_encrypt_guest_ids.py` converts any plaintext `id_number` already in the database.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core import crypto

MAX_ID_LENGTH = 64
_HIDDEN = ("id_number", "id_number_enc")


def id_number_fields(value: Optional[str]) -> Dict[str, Any]:
    """The fields to $set for a new ID number; an empty value clears it."""
    value = (value or "").strip()[:MAX_ID_LENGTH]
    if not value:
        return {"id_number_enc": None, "id_number_last4": None}
    return {"id_number_enc": crypto.encrypt(value), "id_number_last4": value[-4:]}


def reveal_id_number(doc: Dict[str, Any]) -> Optional[str]:
    if doc.get("id_number_enc"):
        return crypto.decrypt(doc["id_number_enc"])
    plain = doc.get("id_number")              # not migrated yet
    return plain if isinstance(plain, str) and plain else None


def masked(last4: Optional[str]) -> Optional[str]:
    return f"•••{last4}" if last4 else None


def public_guest(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A guest document safe to return from the API: no ciphertext, no plaintext ID."""
    out = {k: v for k, v in doc.items() if k not in _HIDDEN}
    last4 = doc.get("id_number_last4")
    if not last4 and isinstance(doc.get("id_number"), str) and doc["id_number"]:
        last4 = doc["id_number"][-4:]          # legacy plaintext: show it masked, never whole
    out.pop("id_number_last4", None)
    out["id_number_masked"] = masked(last4)
    return out
