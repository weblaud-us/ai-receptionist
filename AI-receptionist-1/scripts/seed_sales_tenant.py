"""
Create (or update) your own business as the sales dialer's tenant: the business the sales agent speaks
for and books discovery calls / demos into.

    uv run python -m scripts.seed_sales_tenant --timezone Asia/Dhaka
    uv run python -m scripts.seed_sales_tenant --timezone America/New_York --open 09:00 --close 17:00 \
        --owner-email you@example.com --transfer +15125550100

What it sets up (tenant id `ten_sales_weblaud`, vertical "professional", status active, no billing):
- opening hours = the hours a meeting can be booked, in --timezone (Mon–Fri 09:00–17:00 by default);
- one 15-minute service named SALES_DEMO_SERVICE ("Demo"), which every offer's meeting books by default;
- optional: an owner e-mail for "meeting booked" alerts, and a transfer number for transfer_to_founder.

Run it again with other options to change them; anything else you changed on the Platform page is kept.
Google Calendar is optional: connect it on the Platform page if booked meetings should block your diary.

Then set in .env and restart the API, jobs worker and sales worker:
    SALES_TENANT_ID=ten_sales_weblaud
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import settings

TENANT_ID = "ten_sales_weblaud"
SLUG = "weblaud-sales"
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def parse_days(text: str) -> List[int]:
    """"mon-fri" or "mon,tue,thu" → weekday numbers (0 = Monday)."""
    out: List[int] = []
    for part in text.lower().replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS or DAYS[a] > DAYS[b]:
                raise ValueError(f"bad day range: {part}")
            out.extend(range(DAYS[a], DAYS[b] + 1))
        elif part in DAYS:
            out.append(DAYS[part])
        elif part:
            raise ValueError(f"bad day: {part}")
    if not out:
        raise ValueError("no days given")
    return sorted(set(out))


def tenant_settings(*, name: str, timezone: str, days: List[int], open_: str, close: str,
                    owner_email: str, transfer: str) -> Dict[str, Any]:
    """Everything this script owns on the tenant (applied on create and on every re-run)."""
    alerts = ["email"] if owner_email else []
    return {
        "name": name,
        "timezone": timezone,
        "status": "active",
        "hours": [{"dow": d, "open": open_, "close": close} for d in days],
        "transfer": {"number": transfer, "only_during_hours": True, "hours": "", "warm": False},
        # E-mail only (no SMS): a "meeting booked" alert when an owner e-mail is given, nothing else.
        "notifications": {"owner_email": owner_email, "owner_phone": "", "owner_whatsapp": "",
                          "on_new_booking": alerts, "on_message": [], "on_urgent": [],
                          "daily_digest": False, "monthly_report": False, "weekly_report": False},
        "billing": {"plan": "manual", "hard_cap": False},
    }


async def seed(db: Any, *, name: str, timezone: str, country: str, currency: str, days: List[int],
               open_: str, close: str, owner_email: str = "", transfer: str = "") -> str:
    from core.repositories import Repos
    from core.repositories.base import utcnow
    from core.services.tenant_admin import create_tenant
    from core.tenancy import invalidate
    from core.tenancy.ids import new_id
    from core.tenancy.models import TenantConfig

    extra = tenant_settings(name=name, timezone=timezone, days=days, open_=open_, close=close,
                            owner_email=owner_email, transfer=transfer)
    existing = await db.tenants.find_one({"_id": TENANT_ID})
    if existing is None:
        await create_tenant(name=name, vertical="professional", timezone=timezone, country=country,
                            currency=currency, status="active", region="us", tenant_id=TENANT_ID,
                            slug=SLUG, extra=extra, seed_template=False)
    else:
        TenantConfig.from_doc({**existing, **extra})            # validate before writing
        await db.tenants.update_one({"_id": TENANT_ID}, {"$set": extra})

    service = settings.SALES_DEMO_SERVICE or "Demo"
    repos = Repos(db, TENANT_ID)
    if not await repos.services.find_one({"name": service}):
        await repos.services.insert_one({"_id": new_id("svc"), "name": service, "duration_min": 15,
                                         "buffer_min": 5, "active": True, "ai_bookable": True,
                                         "resource_ids": [], "sort": 0, "source": "sales_seed",
                                         "updated_at": utcnow()})
    invalidate()
    return TENANT_ID


def _check(args: argparse.Namespace) -> Optional[str]:
    try:
        ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Unknown timezone {args.timezone!r} (use an IANA name such as Asia/Dhaka or America/New_York)"
    if not re.match(r"^\d{2}:\d{2}$", args.open) or not re.match(r"^\d{2}:\d{2}$", args.close) or args.open >= args.close:
        return "--open and --close must be HH:MM with open before close"
    if args.transfer and not E164.match(args.transfer):
        return "--transfer must be in international format, e.g. +15125550100"
    if args.owner_email and "@" not in args.owner_email:
        return "--owner-email doesn't look like an e-mail address"
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timezone", required=True, help="where meetings are held, e.g. Asia/Dhaka")
    parser.add_argument("--name", default="WebLaud", help="business name shown on the Platform page")
    parser.add_argument("--country", default="US", help="ISO country of the business (default US: WebLaud LLC)")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--days", default="mon-fri", help='meeting days, e.g. "mon-fri" or "mon,tue,thu"')
    parser.add_argument("--open", default="09:00", help="first meeting time, HH:MM in --timezone")
    parser.add_argument("--close", default="17:00", help="meetings end by, HH:MM in --timezone")
    parser.add_argument("--owner-email", default="", help="e-mail for 'meeting booked' alerts (optional)")
    parser.add_argument("--transfer", default="", help="E.164 number for live transfers to you (optional)")
    args = parser.parse_args()
    problem = _check(args)
    if problem:
        parser.error(problem)

    from core.log import configure_logging
    from database.mongodb import connect_db, disconnect_db, get_db

    configure_logging("WARNING", "text")
    await connect_db()
    try:
        tid = await seed(get_db(), name=args.name.strip(), timezone=args.timezone, country=args.country.upper(),
                         currency=args.currency.upper(), days=parse_days(args.days), open_=args.open,
                         close=args.close, owner_email=args.owner_email.strip(), transfer=args.transfer.strip())
    finally:
        await disconnect_db()
    print(f"Sales tenant ready: {tid} ({args.name}, meetings {args.days} {args.open}-{args.close} {args.timezone})")
    print("Set in .env, then restart the API, the jobs worker and the sales worker:")
    print(f"  SALES_TENANT_ID={tid}")


if __name__ == "__main__":
    asyncio.run(main())
