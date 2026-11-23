"""
Re-encrypt every stored secret with the newest TOKEN_ENCRYPTION_KEY (audit SEC-16).

Stored secrets are any string under a field ending in `_enc`: 2FA secrets, PMS API keys, calendar
refresh tokens, sealed patient fields (problem, notes, transcripts, summaries, symptoms).

    # 1. Put the new key FIRST, keep the old one(s) after it:  TOKEN_ENCRYPTION_KEY=<new>,<old>
    #    A deployment that never set the key used one derived from SECRET_KEY: add --include-derived.
    uv run python -m scripts.rotate_encryption_key                     # dry run: counts only
    uv run python -m scripts.rotate_encryption_key --apply             # re-encrypt with <new>
    # 2. When it reports 0 unreadable, drop the old key(s) from TOKEN_ENCRYPTION_KEY.

Never prints a key or a value. Safe to re-run: already-rotated values are rotated again harmlessly.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
from typing import Any, Dict, List, Tuple

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import settings


def _keys(include_derived: bool) -> MultiFernet:
    keys = [k.strip() for k in (settings.TOKEN_ENCRYPTION_KEY or "").split(",") if k.strip()]
    if not keys:
        raise SystemExit("Set TOKEN_ENCRYPTION_KEY to the NEW key first (then the old ones, comma-separated).")
    fernets = [Fernet(k.encode()) for k in keys]
    if include_derived:
        derived = base64.urlsafe_b64encode(hashlib.sha256(f"token-key:{settings.SECRET_KEY}".encode()).digest())
        fernets.append(Fernet(derived))
    return MultiFernet(fernets)


def rotate_doc(doc: Any, mf: MultiFernet, path: str = "") -> Tuple[Dict[str, str], int]:
    """({dotted path: rotated token}, unreadable count) for every `*_enc` string in `doc`."""
    out: Dict[str, str] = {}
    bad = 0
    if isinstance(doc, dict):
        for k, v in doc.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and str(k).endswith("_enc") and v:
                try:
                    out[p] = mf.rotate(v.encode("ascii")).decode("ascii")
                except (InvalidToken, ValueError):
                    bad += 1
            elif isinstance(v, (dict, list)):
                sub, b = rotate_doc(v, mf, p)
                out.update(sub)
                bad += b
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            sub, b = rotate_doc(v, mf, f"{path}.{i}")
            out.update(sub)
            bad += b
    return out, bad


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the re-encrypted values (default: dry run)")
    parser.add_argument("--include-derived", action="store_true",
                        help="also accept the old key derived from SECRET_KEY (deployments that never set one)")
    args = parser.parse_args()
    mf = _keys(args.include_derived)
    from database.mongodb import connect_db, get_db

    await connect_db(ensure_indexes=False)
    db = get_db()
    totals: List[Tuple[str, int, int]] = []
    for name in sorted(await db.list_collection_names()):
        rotated = unreadable = 0
        async for doc in db[name].find({}):
            sets, bad = rotate_doc(doc, mf)
            unreadable += bad
            if sets:
                rotated += len(sets)
                if args.apply:
                    await db[name].update_one({"_id": doc["_id"]}, {"$set": sets})
        if rotated or unreadable:
            totals.append((name, rotated, unreadable))
    for name, rotated, unreadable in totals:
        print(f"{name:<28} {'re-encrypted' if args.apply else 'would re-encrypt'} {rotated:>6}   unreadable {unreadable}")
    if not totals:
        print("No encrypted values found.")
    if any(u for _, _, u in totals):
        print("\nSome values can't be read with these keys: add the key they were written with and run again.")
    elif not args.apply:
        print("\nDry run. Run again with --apply to write.")


if __name__ == "__main__":
    asyncio.run(main())
