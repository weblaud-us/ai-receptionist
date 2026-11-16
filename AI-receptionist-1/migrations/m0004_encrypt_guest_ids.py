"""
Migration 0004 — encrypt plaintext guest ID numbers (Roadmap Task 3.8). Idempotent.

    python -m migrations.m0004_encrypt_guest_ids            # dry run: counts only
    python -m migrations.m0004_encrypt_guest_ids --apply    # deploy/deploy.sh runs this on every deploy

Every `guests` document with a plaintext `id_number` gets `id_number_enc` (Fernet, with
TOKEN_ENCRYPTION_KEY) and `id_number_last4`; the plaintext field is removed. Set
TOKEN_ENCRYPTION_KEY before running in production — without it the key is derived from SECRET_KEY
and rotating SECRET_KEY would make the values unreadable.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from config import settings
from core.log import configure_logging
from core.sensitive import id_number_fields
from database.mongodb import connect_db, disconnect_db, get_db

PLAINTEXT = {"id_number": {"$type": "string", "$ne": ""}}


async def run(db: Any, *, apply: bool) -> dict[str, int]:
    found = await db.guests.count_documents(PLAINTEXT)
    converted = 0
    if apply:
        async for doc in db.guests.find(PLAINTEXT, {"id_number": 1}):
            res = await db.guests.update_one(
                {"_id": doc["_id"], "id_number": doc["id_number"]},
                {"$set": id_number_fields(doc["id_number"]), "$unset": {"id_number": ""}})
            converted += int(res.modified_count)
    # Documents whose field is empty/null: just drop it.
    if apply:
        await db.guests.update_many({"id_number": {"$in": [None, ""]}}, {"$unset": {"id_number": ""}})
    return {"plaintext_found": found, "converted": converted}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        db = get_db()
        if (settings.is_production and len(settings.TOKEN_ENCRYPTION_KEY) < 40
                and await db.guests.count_documents(PLAINTEXT)):
            raise SystemExit("Plaintext guest ID numbers found: set TOKEN_ENCRYPTION_KEY first (.env.example)")
        stats = await run(db, apply=args.apply)
    finally:
        await disconnect_db()
    print(("APPLIED: " if args.apply else "DRY RUN: ") + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    asyncio.run(main())
