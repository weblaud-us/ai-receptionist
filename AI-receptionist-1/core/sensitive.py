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


# ── Clinical free text (dental: UK GDPR Art. 9 / APP 11) ─────────────────────
#
# Health details a caller describes (symptoms, treatment notes) and the call transcript are stored
# as ciphertext for tenants with clinical encryption (TenantConfig.clinical_encryption: every
# dental practice). `seal` replaces a field with `<field>_enc`; `unseal` restores it for the
# screens and exports that show it. JSON inside, so lists (transcripts) round-trip.

SEALABLE = ("problem", "notes", "transcript")
LEAD_TEXT_FIELDS = ("problem", "notes")


def seal(doc: Dict[str, Any], fields: tuple = SEALABLE) -> Dict[str, Any]:
    """A copy of `doc` with each non-empty field in `fields` replaced by `<field>_enc`."""
    import json

    out = dict(doc)
    for f in fields:
        if f not in out:
            continue
        value = out.pop(f)
        if value in (None, "", [], {}):
            continue
        out[f"{f}_enc"] = crypto.encrypt(json.dumps(value, default=str, ensure_ascii=False))
    return out


def unseal(doc: Optional[Dict[str, Any]], fields: tuple = SEALABLE) -> Dict[str, Any]:
    """A copy of `doc` with sealed fields readable again. An unreadable value (wrong key) becomes None."""
    import json

    out = dict(doc or {})
    for f in fields:
        token = out.pop(f"{f}_enc", None)
        if not token:
            continue
        try:
            out[f] = json.loads(crypto.decrypt(token))
        except (crypto.DecryptionError, ValueError):
            out[f] = None
    return out


def drop_ciphertext(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Never send ciphertext to a browser: remove every `*_enc` field."""
    return {k: v for k, v in doc.items() if not (isinstance(k, str) and k.endswith("_enc"))}
