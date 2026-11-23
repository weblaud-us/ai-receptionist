"""
Tamper-evident audit log (audit PRIV-10).

Every `audit_log` entry of a business gets `seq` (1, 2, 3, ...) and
    hash = HMAC-SHA256(key, prev_hash + canonical(entry))
so changing, deleting or inserting a row breaks every hash after it. The key is SECRET_KEY (an
attacker with database access but not the server's environment can't re-sign the chain); `kid`
records which key signed an entry, so after rotating SECRET_KEY older entries show as
"unverifiable" rather than "tampered".

    ok, problems = await verify(db, tenant_id)
    uv run python -m scripts.verify_audit_log [tenant_id]
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import settings

FIELDS = ("user_id", "action", "target_type", "target_id", "diff", "ip", "at")


def _key() -> bytes:
    return f"audit-chain:{settings.SECRET_KEY}".encode()


def key_id() -> str:
    return hashlib.sha256(_key()).hexdigest()[:8]


def _ms(at: Any) -> int:
    if isinstance(at, datetime):
        aware = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        return int(aware.timestamp() * 1000)
    return 0


def normalise(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What is stored is exactly what is hashed: millisecond times (Mongo's precision), JSON diffs."""
    out = dict(entry)
    at = out.get("at")
    if isinstance(at, datetime):
        out["at"] = at.replace(microsecond=(at.microsecond // 1000) * 1000)
    out["diff"] = json.loads(json.dumps(out.get("diff") or {}, default=str, sort_keys=True))
    return out


def _canonical(tenant_id: str, seq: int, entry: Dict[str, Any]) -> bytes:
    body = {k: entry.get(k) for k in FIELDS}
    body["at"] = _ms(entry.get("at"))
    return json.dumps({"tenant_id": tenant_id, "seq": seq, **body}, sort_keys=True, default=str,
                      separators=(",", ":")).encode()


def entry_hash(tenant_id: str, seq: int, entry: Dict[str, Any], prev_hash: str) -> str:
    return hmac.new(_key(), prev_hash.encode() + _canonical(tenant_id, seq, entry), hashlib.sha256).hexdigest()


def link(tenant_id: str, entry: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    seq = int(prev["seq"]) + 1 if prev else 1
    prev_hash = str(prev.get("hash") or "") if prev else ""
    return {**entry, "seq": seq, "prev_hash": prev_hash, "kid": key_id(),
            "hash": entry_hash(tenant_id, seq, entry, prev_hash)}


async def verify(db: Any, tenant_id: str) -> Tuple[bool, List[str]]:
    """Walk the chain in order. Problems name the first seq where it breaks."""
    problems: List[str] = []
    expected_seq, prev_hash = 1, ""
    kid = key_id()
    cursor = db.audit_log.find({"tenant_id": tenant_id, "seq": {"$exists": True}}).sort("seq", 1)  # scoped: tenant_id
    async for doc in cursor:
        seq = int(doc["seq"])
        if seq != expected_seq:
            problems.append(f"seq {expected_seq}: missing (next row is {seq})")
            expected_seq = seq
        if doc.get("prev_hash", "") != prev_hash:
            problems.append(f"seq {seq}: doesn't follow the previous entry")
        if doc.get("kid") != kid:
            problems.append(f"seq {seq}: signed with an older key, can't be checked")
        elif not hmac.compare_digest(str(doc.get("hash") or ""), entry_hash(tenant_id, seq, doc, prev_hash)):
            problems.append(f"seq {seq}: contents changed")
        prev_hash, expected_seq = str(doc.get("hash") or ""), seq + 1
        if len(problems) >= 20:
            break
    return not problems, problems
