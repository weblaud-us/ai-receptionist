"""
Migration 0009 — a draft offer for businesses with no website (2026-11-28). Idempotent.

    python -m migrations.m0009_sales_website_offer            # dry run: what would change
    python -m migrations.m0009_sales_website_offer --apply    # deploy/deploy.sh runs this on every deploy

- Adds off_weblaud_websites ("WebLaud: a website for your business") as a DRAFT when it doesn't exist
  yet. It is never published here: nobody is called with it until a person checks it in Offers,
  confirms its facts (they are marked as written by AI, so publishing refuses until each one is
  confirmed), publishes it and runs its test calls.
- An offer that already exists is never touched: edits made in the dashboard win.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from core.log import configure_logging
from core.outbound import offers as of
from core.outbound.offer_seeds import drafts
from database.mongodb import connect_db, disconnect_db, get_db


async def run(db: Any, *, apply: bool, now: datetime | None = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = {"drafts_added": 0, "offers_existing": 0}
    for offer_id, body in drafts().items():
        if await db[of.OFFERS].find_one({"_id": offer_id}, {"_id": 1}):
            stats["offers_existing"] += 1
            continue
        stats["drafts_added"] += 1
        if apply:
            settings_ = of.OfferSettings.model_validate(body)
            await of.create_offer(db, settings_, by="migration:m0009", offer_id=offer_id, now=now,
                                  ai_unconfirmed=list(of.fact_texts(settings_)))
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        stats = await run(get_db(), apply=args.apply)
    finally:
        await disconnect_db()
    print(("APPLIED: " if args.apply else "DRY RUN: ") + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    asyncio.run(main())
