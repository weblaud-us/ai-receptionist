"""
Migration 0007 — the built-in CRM (SALES_SUITE_PLAN.md wave 3). Idempotent.

    python -m migrations.m0007_sales_crm            # dry run: what would change
    python -m migrations.m0007_sales_crm --apply    # deploy/deploy.sh runs this on every deploy

- Opens a deal for every lead that is already past "interested" and has none: interested →
  qualified, meeting booked → meeting_booked, a manual "proposal" / "won" stage → that stage. The deal
  sells the offer of the lead's last attempt.
- Gives leads without a fit score one from the rules (core/outbound/crm.rules_fit), so the dialer can
  call best-fit leads first.
Nothing is deleted or overwritten: leads that already have a deal, or a score, are left as they are.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from core.log import configure_logging
from core.outbound import crm
from core.outbound import models as m
from core.outbound import offers as of
from database.mongodb import connect_db, disconnect_db, get_db

_STAGE_FROM = {"interested": "qualified", "demo_booked": "meeting_booked"}


async def run(db: Any, *, apply: bool, now: datetime | None = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = {"deals_opened": 0, "leads_with_deals": 0, "fit_scored": 0}
    query = {"owner": m.OWNER_PLATFORM, "$or": [{"status": {"$in": list(_STAGE_FROM)}}, {"stage": {"$in": ["proposal", "won"]}}]}
    async for lead in db[m.LEADS].find(query):
        if await db[crm.DEALS].find_one({"lead_id": lead["_id"]}, {"_id": 1}):
            stats["leads_with_deals"] += 1
            continue
        stats["deals_opened"] += 1
        if not apply:
            continue
        stage = lead.get("stage") if lead.get("stage") in ("proposal", "won") else _STAGE_FROM.get(lead.get("status"), "qualified")
        last = await db[m.ATTEMPTS].find({"lead_id": lead["_id"]}).sort("created_at", -1).limit(1).to_list(1)
        attempt = last[0] if last else {}
        offer = await of.offer_for(db, attempt.get("offer_id"), attempt.get("offer_version"))
        await crm.create_deal(db, crm.DealInput(
            lead_id=lead["_id"], title=f"{lead.get('business_name') or 'Lead'} · {offer.get('name') or 'Offer'}"[:120],
            offer_id=of.offer_id_of(attempt), amount=float(offer.get("deal_value_default") or 0), stage=stage,  # type: ignore[arg-type]
            owner_id=str(lead.get("owner_id") or "")), by="migration:m0007", now=now, campaign_id=lead.get("campaign_id"),
            source="migration")
    async for lead in db[m.LEADS].find({"owner": m.OWNER_PLATFORM, "fit_score": {"$exists": False}, "status": {"$ne": "merged"}}):
        stats["fit_scored"] += 1
        if apply:
            campaign = await db[m.CAMPAIGNS].find_one({"_id": lead.get("campaign_id")}) if lead.get("campaign_id") else None
            fit = crm.rules_fit(lead, campaign or {})
            await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"fit": fit, "fit_score": fit["score"]}})
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
