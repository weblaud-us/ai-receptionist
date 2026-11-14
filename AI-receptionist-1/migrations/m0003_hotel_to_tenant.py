"""
Migration 0003 — the .env hotel becomes tenant #1 (Roadmap Task 2.11). Idempotent, re-runnable.

    python -m migrations.m0003_hotel_to_tenant --dry-run
    python -m migrations.m0003_hotel_to_tenant --owner-email owner@hotel.com [--owner-name "…"]

⚠ Take an Atlas snapshot first.

What it does:
  1. Creates the hotel `tenants` document from today's HOTEL_* / voice settings (vertical "hotel").
     Re-runs find it again by `migrated_from: "env"` and reuse its id.
  2. Routes the hotel's Twilio number to it in `phone_numbers`.
  3. Backfills `tenant_id` on rooms, bookings, room_nights, guests, conversations, calls, messages,
     call_sessions (documents with no tenant_id, or the pre-migration placeholder "default").
  4. Copies guests → contacts (upsert by phone, keeps the oldest first_seen_at).
  5. Creates the owner user (no password) and prints a one-time set-password link.
  6. Drops the old global unique indexes (rooms.room_number_1, guests.phone_1) and builds the
     per-tenant ones, then prints counts before / after.

After it has run, the resolver stops using .env for the business profile: remove the HOTEL_*
variables at your next deploy (they remain only as the legacy fallback).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import settings
from core.log import configure_logging
from core.phone import digits
from core.tenancy.ids import new_id
from core.tenancy.models import legacy_hotel_tenant
from database.mongodb import connect_db, create_indexes, disconnect_db, get_db

logger = logging.getLogger("m0003")

BACKFILL = ("rooms", "bookings", "room_nights", "guests", "conversations", "calls", "messages", "call_sessions",
            "usage_events")
OLD_UNIQUE_INDEXES = {"rooms": "room_number_1", "guests": "phone_1"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _counts(db: Any, tenant_id: Optional[str]) -> Dict[str, Dict[str, int]]:
    out = {}
    for name in BACKFILL + ("contacts",):
        total = await db[name].count_documents({})
        scoped = await db[name].count_documents({"tenant_id": tenant_id}) if tenant_id else 0
        out[name] = {"total": total, "tenant": scoped}
    return out


async def _hotel_tenant(db: Any, dry_run: bool) -> str:
    existing = await db.tenants.find_one({"migrated_from": "env"}, {"_id": 1})
    if existing:
        logger.info("Hotel tenant already exists: %s", existing["_id"])
        return existing["_id"]
    t = legacy_hotel_tenant()
    doc = t.to_doc()
    tenant_id = new_id("ten")
    slug_base = "".join(c if c.isalnum() else "-" for c in t.name.lower()).strip("-") or "hotel"
    slug, n = slug_base[:60], 2
    while await db.tenants.find_one({"slug": slug}):
        slug, n = f"{slug_base[:56]}-{n}", n + 1
    doc.update({"_id": tenant_id, "slug": slug, "status": "active", "migrated_from": "env",
                "created_at": utcnow(), "updated_at": utcnow()})
    if dry_run:
        logger.info("[dry-run] would create tenant %s (%s, slug %s)", tenant_id, t.name, slug)
    else:
        await db.tenants.insert_one(doc)
        logger.info("Created hotel tenant %s (%s)", tenant_id, t.name)
    return tenant_id


async def _phone_number(db: Any, tenant_id: str, dry_run: bool) -> None:
    raw = settings.TWILIO_PHONE_NUMBER.strip()
    if not raw:
        logger.info("TWILIO_PHONE_NUMBER is empty — assign the number later with POST /platform/phone-numbers")
        return
    e164 = "+" + digits(raw)
    row = await db.phone_numbers.find_one({"e164": e164})
    if row and row.get("tenant_id") not in (None, tenant_id) and row.get("status") != "released":
        logger.warning("%s already belongs to tenant %s — left unchanged", e164, row["tenant_id"])
        return
    if dry_run:
        logger.info("[dry-run] would route %s to %s", e164, tenant_id)
        return
    await db.phone_numbers.update_one(
        {"e164": e164},
        {"$set": {"e164": e164, "tenant_id": tenant_id, "purpose": "inbound", "provider": "twilio",
                  "status": "active", "assigned_at": utcnow()}},
        upsert=True)
    logger.info("Routed %s to the hotel tenant", e164)


async def _backfill(db: Any, tenant_id: str, dry_run: bool) -> None:
    missing = {"$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}, {"tenant_id": "default"}]}
    for name in BACKFILL:
        n = await db[name].count_documents(missing)
        if not n:
            continue
        if dry_run:
            logger.info("[dry-run] would set tenant_id on %d %s", n, name)
            continue
        res = await db[name].update_many(missing, {"$set": {"tenant_id": tenant_id}})
        logger.info("tenant_id set on %d %s", res.modified_count, name)


async def _guests_to_contacts(db: Any, tenant_id: str, dry_run: bool) -> None:
    copied = 0
    async for g in db.guests.find({"tenant_id": tenant_id} if not dry_run else {}):
        phone = g.get("phone")
        if not phone:
            continue
        copied += 1
        if dry_run:
            continue
        first = g.get("created_at") or utcnow()
        await db.contacts.update_one(
            {"tenant_id": tenant_id, "phone": phone},
            {"$set": {"name": g.get("name"), "email": g.get("email"), "last_seen_at": g.get("last_interaction") or first,
                      "legacy_guest_id": g.get("_id")},
             "$setOnInsert": {"_id": new_id("con"), "tenant_id": tenant_id, "phone": phone, "first_seen_at": first,
                              "created_at": first, "tags": [g["vip_tier"]] if g.get("vip_tier") not in (None, "standard") else [],
                              "notes": g.get("notes", ""), "do_not_contact": False,
                              "stats": {"calls": 0, "appointments": int(g.get("total_stays") or 0), "no_shows": 0}}},
            upsert=True)
    logger.info("%s %d guests → contacts", "[dry-run] would copy" if dry_run else "Copied", copied)


async def _owner(db: Any, tenant_id: str, email: Optional[str], name: str, dry_run: bool) -> None:
    if not email:
        logger.info("No --owner-email: create the owner later with POST /platform/tenants or a team invite")
        return
    if dry_run:
        logger.info("[dry-run] would create/attach owner %s", email)
        return
    from core.auth import service

    user = await db.users.find_one({"email": email.strip().lower()})
    if user:
        await service.add_membership(user["_id"], tenant_id, "owner")
        logger.info("Existing user %s is now an owner of the hotel", email)
        return
    await service.create_user(email=email, password=None, name=name, tenant_id=tenant_id, role="owner")
    token = await service.start_password_reset(email)
    print(f"\nOwner {email} created. Send them this one-time link (valid 1 hour):\n"
          f"  {service.dashboard_link(f'/reset-password?token={token}')}\n")


async def _indexes(db: Any, dry_run: bool) -> None:
    for coll, name in OLD_UNIQUE_INDEXES.items():
        info = await db[coll].index_information()
        if name in info and info[name].get("unique"):
            if dry_run:
                logger.info("[dry-run] would drop global unique index %s.%s", coll, name)
            else:
                await db[coll].drop_index(name)
                logger.info("Dropped global unique index %s.%s", coll, name)
    if not dry_run:
        await create_indexes(db)


async def migrate(*, dry_run: bool = False, owner_email: Optional[str] = None, owner_name: str = "") -> str:
    db = get_db()
    tenant_id = await _hotel_tenant(db, dry_run)
    before = await _counts(db, tenant_id)
    await _indexes(db, dry_run)
    await _phone_number(db, tenant_id, dry_run)
    await _backfill(db, tenant_id, dry_run)
    await _guests_to_contacts(db, tenant_id, dry_run)
    await _owner(db, tenant_id, owner_email, owner_name, dry_run)
    after = await _counts(db, tenant_id)

    print(f"\n{'DRY RUN — nothing written' if dry_run else 'Migration 0003 complete'} — tenant {tenant_id}\n")
    print(f"{'collection':<16}{'total':>8}{'tenant before':>15}{'tenant after':>14}")
    for name in after:
        print(f"{name:<16}{after[name]['total']:>8}{before[name]['tenant']:>15}{after[name]['tenant']:>14}")
    unscoped = {n: v["total"] - v["tenant"] for n, v in after.items() if n != "contacts" and v["total"] != v["tenant"]}
    if unscoped and not dry_run:
        print(f"\n⚠ Documents still without this tenant: {unscoped} (other tenants' data, or investigate)")
    return tenant_id


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--owner-email")
    parser.add_argument("--owner-name", default="")
    args = parser.parse_args()
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=False)
    try:
        await migrate(dry_run=args.dry_run, owner_email=args.owner_email, owner_name=args.owner_name)
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
