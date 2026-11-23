"""
Move AU / UK practices that are still on their country's *default* Aura voice to Cartesia, in the
same accent (CARTESIA_VOICE_ID_AU / _GB). A practice that picked a voice itself is left alone.

    uv run python -m scripts.upgrade_voices            # dry run: lists what would change
    uv run python -m scripts.upgrade_voices --apply    # make the change

The moved practices get voice_id "" on Cartesia, so they follow the platform's AU / UK default from
then on. Aura stays in the fallback chain in the same accent. Needs CARTESIA_API_KEY (or --force).
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, List

from config import settings
from core.voices import AURA_DEFAULT_FOR_COUNTRY

PROVIDER = "cartesia"      # the platform's main voice


def needs_upgrade(doc: Dict[str, Any]) -> bool:
    """On Aura with the stock voice for its country (or none) — i.e. nobody chose it."""
    country = str(doc.get("country") or "").upper()
    stock = AURA_DEFAULT_FOR_COUNTRY.get(country)
    agent = doc.get("agent") or {}
    return bool(stock) and agent.get("voice_provider") == "deepgram" and (agent.get("voice_id") or "") in ("", stock)


async def upgrade(db: Any, *, apply: bool) -> List[Dict[str, Any]]:
    """The practices that move (and, with apply, move them). Returns [{id, name, country, from}]."""
    moved: List[Dict[str, Any]] = []
    query = {"agent.voice_provider": "deepgram", "country": {"$in": list(AURA_DEFAULT_FOR_COUNTRY)},
             "billing.deleted_at": None}
    async for doc in db.tenants.find(query, {"name": 1, "country": 1, "agent": 1}):
        if not needs_upgrade(doc):
            continue
        moved.append({"id": doc["_id"], "name": doc.get("name", ""), "country": doc.get("country"),
                      "from": (doc.get("agent") or {}).get("voice_id") or "(default)"})
        if apply:
            from core.repositories.base import utcnow

            await db.tenants.update_one({"_id": doc["_id"]}, {"$set": {
                "agent.voice_provider": PROVIDER, "agent.voice_id": "", "updated_at": utcnow()}})
    if apply and moved:
        from core.tenancy import invalidate

        invalidate()
    return moved


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the change (default: dry run)")
    parser.add_argument("--force", action="store_true", help="even without CARTESIA_API_KEY on this host")
    args = parser.parse_args()
    if not settings.CARTESIA_API_KEY and not args.force:
        raise SystemExit("CARTESIA_API_KEY isn't set: practices would just fall back to Aura. Set it, or --force.")
    for cc in ("AU", "GB"):
        if not getattr(settings, f"CARTESIA_VOICE_ID_{cc}"):
            print(f"Note: CARTESIA_VOICE_ID_{cc} is empty, so {cc} practices get "
                  f"{settings.CARTESIA_VOICE_ID or 'the Cartesia default voice'}. Pick one with scripts/voice_bakeoff.py first.")

    from core.log import configure_logging
    from database.mongodb import connect_db, disconnect_db, get_db

    configure_logging("WARNING", "text")
    await connect_db(ensure_indexes=False)
    try:
        moved = await upgrade(get_db(), apply=args.apply)
    finally:
        await disconnect_db()
    for m in moved:
        print(f"  {m['country']}  {m['id']}  {m['name']}  (was {m['from']})")
    verb = "Moved" if args.apply else "Would move"
    print(f"{verb} {len(moved)} practice(s) to Cartesia." + ("" if args.apply else " Run with --apply to do it."))


if __name__ == "__main__":
    asyncio.run(main())
