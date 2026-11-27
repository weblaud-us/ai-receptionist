"""
Migration 0006 — sales offers (SALES_DIALER_PLAN.md Phase 1A). Idempotent.

    python -m migrations.m0006_sales_offers            # dry run: what would change
    python -m migrations.m0006_sales_offers --apply    # deploy/deploy.sh runs this on every deploy

- Seeds the two starting offers, each with a published version 1, when they don't exist yet:
  off_laudline (the original LaudLine scripts) and off_weblaud_studio (WebLaud's custom AI and
  software services). An offer that already exists is never touched: edits made in the dashboard win.
- Backfills `legal_review.material` on campaigns signed off before snapshots were kept, so a later
  change to their script, numbers or call type is noticed (models.legal_review_valid).
Existing campaigns are not moved onto the new offers: they keep the built-in scripts until someone
picks an offer for them (which needs a fresh legal review).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from core.log import configure_logging
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.offer_seeds import seeds
from database.mongodb import connect_db, disconnect_db, get_db


async def run(db: Any, *, apply: bool, now: datetime | None = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = {"offers_seeded": 0, "offers_existing": 0, "reviews_backfilled": 0}
    for offer_id, body in seeds().items():
        if await db[of.OFFERS].find_one({"_id": offer_id}, {"_id": 1}):
            stats["offers_existing"] += 1
            continue
        stats["offers_seeded"] += 1
        if apply:
            await of.create_offer(db, of.OfferSettings.model_validate(body), by="migration:m0006", offer_id=offer_id, now=now)
            await of.publish(db, offer_id, by="migration:m0006", now=now)
    query = {"legal_review": {"$ne": None}, "legal_review.material": {"$exists": False}}
    async for campaign in db[m.CAMPAIGNS].find(query):
        stats["reviews_backfilled"] += 1
        if apply:
            await db[m.CAMPAIGNS].update_one({"_id": campaign["_id"]},
                                             {"$set": {"legal_review.material": m.material_snapshot(campaign)}})
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
