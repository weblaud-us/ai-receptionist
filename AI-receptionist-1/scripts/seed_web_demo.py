"""
Create (or refresh) the two fictional demo practices the landing page's "Talk to it now" demo calls:

    Harbourline Dental (AU, Sydney)  — ten_demo_au
    Wimpole Dental     (UK, London)  — ten_demo_gb

    uv run python -m scripts.seed_web_demo

They match the landing page's sample calls (landing-page/src/data/scenarios.ts). Each is open around the
clock (a visitor can book at 11 pm), has the dental template's services and FAQs, no practice system,
no alerts, and a concurrent-call cap that keeps the demo's cost bounded. Run it again to reset their
settings; bookings and contacts are cleared automatically (WEB_DEMO_DIARY_RETENTION_HOURS).

Then set in .env and restart the API and worker:
    WEB_DEMO_ENABLED=true
    WEB_DEMO_TENANT_AU=ten_demo_au
    WEB_DEMO_TENANT_GB=ten_demo_gb
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, List

from config import settings

DEMOS: List[Dict[str, Any]] = [
    {"tenant_id": "ten_demo_au", "slug": "demo-harbourline-dental", "name": "Harbourline Dental", "country": "AU",
     "timezone": "Australia/Sydney", "currency": "AUD", "phone": "+61 2 5550 0123",     # ACMA fictional range
     "address": {"line1": "12 Harbour Street", "city": "Sydney", "region": "NSW", "postcode": "2000", "country": "AU"},
     "dentists": ["Dr Priya Patel", "Dr Tom Nguyen"]},
    {"tenant_id": "ten_demo_gb", "slug": "demo-wimpole-dental", "name": "Wimpole Dental", "country": "GB",
     "timezone": "Europe/London", "currency": "GBP", "phone": "+44 20 7946 0123",       # Ofcom drama range
     "address": {"line1": "40 Wimpole Street", "city": "London", "region": "", "postcode": "W1G 8YW", "country": "GB"},
     "dentists": ["Dr Ada Okafor", "Dr James Hughes"]},
]

ALWAYS_OPEN = [{"dow": d, "open": "00:00", "close": "24:00"} for d in range(7)]
DEMO_NOTE = ("This is a live demo of an AI receptionist for dental practices, and the practice is fictional. "
             "Treat the caller as a patient, but if they ask what you are or how you work, say you're the demo "
             "of an AI receptionist that answers a practice's phone, books into its diary and takes messages.")


def settings_for(demo: Dict[str, Any], max_concurrent: int) -> Dict[str, Any]:
    """Everything that makes a practice a demo practice (applied on create and on every re-run)."""
    return {
        "status": "active",
        "hours": ALWAYS_OPEN,
        "address": demo["address"],
        "agent": {"name": "Ava", "max_call_minutes": max(1, -(-settings.WEB_DEMO_MAX_SECONDS // 60)),
                  "custom_instructions": DEMO_NOTE, "transcript_retention_days": settings.WEB_DEMO_TRANSCRIPT_DAYS},
        "notifications": {"owner_email": "", "owner_phone": "", "owner_whatsapp": "", "on_new_booking": [],
                          "on_message": [], "on_urgent": [], "daily_digest": False, "monthly_report": False,
                          "weekly_report": False},
        "limits": {"max_concurrent_calls": max_concurrent, "included_minutes": 0},
        "billing": {"plan": "manual", "hard_cap": False},
    }


async def seed(db: Any, *, max_concurrent: int = 5) -> List[str]:
    from core.repositories import Repos
    from core.repositories.base import utcnow
    from core.services.tenant_admin import create_tenant
    from core.tenancy import invalidate
    from core.tenancy.ids import new_id

    done = []
    for demo in DEMOS:
        extra = settings_for(demo, max_concurrent)
        existing = await db.tenants.find_one({"_id": demo["tenant_id"]})
        if existing is None:
            await create_tenant(name=demo["name"], vertical="dental", timezone=demo["timezone"], country=demo["country"],
                                currency=demo["currency"], phone=demo["phone"], status="active",
                                region="eu" if demo["country"] == "GB" else "us", tenant_id=demo["tenant_id"],
                                slug=demo["slug"], extra=extra, seed_template=True)
        else:
            # Re-run: reset the demo settings, keep the voice (and anything else) an admin chose since.
            agent = {**(existing.get("agent") or {}), **extra["agent"]}
            await db.tenants.update_one({"_id": demo["tenant_id"]}, {"$set": {**extra, "agent": agent}})
        repos = Repos(db, demo["tenant_id"])
        for i, name in enumerate(demo["dentists"]):
            if not await repos.resources.find_one({"name": name}):
                await repos.resources.insert_one({"_id": new_id("res"), "name": name, "kind": "staff", "hours": None,
                                                  "capacity": 1, "active": True, "sort": i, "updated_at": utcnow()})
        done.append(demo["tenant_id"])
    invalidate()
    return done


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-concurrent", type=int, default=5, help="demo calls at once, per practice")
    args = parser.parse_args()

    from core.log import configure_logging
    from database.mongodb import connect_db, disconnect_db, get_db

    configure_logging("WARNING", "text")
    await connect_db()
    try:
        ids = await seed(get_db(), max_concurrent=args.max_concurrent)
    finally:
        await disconnect_db()
    print("Demo practices ready:", ", ".join(ids))
    print("Set in .env, then restart the API and the voice worker:")
    print("  WEB_DEMO_ENABLED=true\n  WEB_DEMO_TENANT_AU=ten_demo_au\n  WEB_DEMO_TENANT_GB=ten_demo_gb")


if __name__ == "__main__":
    asyncio.run(main())
