"""
Migration 0005 — encrypt clinical free text at rest (dental, UK GDPR Art. 9 / APP 11). Idempotent.

    python -m migrations.m0005_encrypt_clinical_text            # dry run: counts only
    python -m migrations.m0005_encrypt_clinical_text --apply    # deploy/deploy.sh runs this on every deploy

For every business with clinical encryption (all dental practices, and anyone who turned
`privacy.encrypt_clinical_text` on):
- leads: plaintext `problem` / `notes` → `problem_enc` / `notes_enc`;
- calls: a plaintext `transcript` → `transcript_enc`.
New data is written encrypted already (core/tools/dental.py, agent/persistence.py); this catches
a business that switched to the dental vertical, or data from before this release.
Needs TOKEN_ENCRYPTION_KEY in production, like m0004.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, List

from config import settings
from core.log import configure_logging
from core.sensitive import LEAD_TEXT_FIELDS, seal
from core.tenancy.models import TenantConfig
from database.mongodb import connect_db, disconnect_db, get_db


async def _tenant_ids(db: Any) -> List[str]:
    out = []
    async for doc in db.tenants.find({}):
        try:
            if TenantConfig.from_doc(doc).clinical_encryption:
                out.append(doc["_id"])
        except ValueError:
            continue
    return out


def _plain(fields: tuple) -> Dict[str, Any]:
    return {"$or": [{f: {"$exists": True, "$nin": [None, "", []]}} for f in fields]}


async def run(db: Any, *, apply: bool) -> Dict[str, int]:
    stats = {"tenants": 0, "leads_found": 0, "leads_sealed": 0, "calls_found": 0, "calls_sealed": 0}
    for tid in await _tenant_ids(db):
        stats["tenants"] += 1
        for coll, fields, key in (("leads", LEAD_TEXT_FIELDS, "leads"), ("calls", ("transcript",), "calls")):
            q = {"tenant_id": tid, **_plain(fields)}
            stats[f"{key}_found"] += await db[coll].count_documents(q)  # scoped: tenant_id
            if not apply:
                continue
            async for doc in db[coll].find(q):  # scoped: tenant_id
                sealed = seal({f: doc.get(f) for f in fields if f in doc}, fields)
                res = await db[coll].update_one({"_id": doc["_id"], "tenant_id": tid},  # scoped: tenant_id
                                                {"$set": sealed, "$unset": {f: "" for f in fields}})
                stats[f"{key}_sealed"] += int(res.modified_count)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        db = get_db()
        stats = await run(db, apply=False)
        if settings.is_production and len(settings.TOKEN_ENCRYPTION_KEY) < 40 and (
                stats["leads_found"] or stats["calls_found"]):
            raise SystemExit("Clinical text to encrypt: set TOKEN_ENCRYPTION_KEY first (.env.example)")
        if args.apply:
            stats = await run(db, apply=True)
    finally:
        await disconnect_db()
    print(("APPLIED: " if args.apply else "DRY RUN: ") + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    asyncio.run(main())
