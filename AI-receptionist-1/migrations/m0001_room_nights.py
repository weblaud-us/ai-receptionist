"""
Migration 0001 — backfill `room_nights` from existing bookings and report double bookings.

Idempotent: re-running only adds nights that are missing. Run once after deploying Phase 0:

    python -m migrations.m0001_room_nights            # dry run: report only
    python -m migrations.m0001_room_nights --apply    # write room_nights

Any CONFLICT printed below is an existing double booking (two active bookings on the same room
and night). The unique index keeps the first one; fix the others by hand (move or cancel them)
from the dashboard, then re-run.
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.log import configure_logging
from database.mongodb import connect_db, disconnect_db, get_db

ACTIVE = ["confirmed", "pending", "checked_in"]


def nights(check_in: str, check_out: str) -> list[str]:
    start, end = date.fromisoformat(str(check_in)[:10]), date.fromisoformat(str(check_out)[:10])
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


async def run(apply: bool) -> int:
    db = get_db()
    claims: dict[tuple[str, str], list[str]] = defaultdict(list)
    bookings = await db.bookings.find(
        {"status": {"$in": ACTIVE}},
        {"booking_id": 1, "room_id": 1, "check_in_date": 1, "check_out_date": 1, "created_at": 1},
    ).sort("created_at", 1).to_list(length=None)

    for b in bookings:
        for n in nights(b["check_in_date"], b["check_out_date"]):
            claims[(str(b["room_id"]), n)].append(b["booking_id"])

    conflicts = {k: v for k, v in claims.items() if len(v) > 1}
    print(f"Active bookings: {len(bookings)} | room-nights: {len(claims)} | conflicts: {len(conflicts)}")
    for (room_id, night), ids in sorted(conflicts.items(), key=lambda kv: kv[0][1]):
        print(f"  CONFLICT room={room_id} night={night}: kept {ids[0]}, also booked by {', '.join(ids[1:])}")

    if not apply:
        print("Dry run — nothing written. Re-run with --apply.")
        return len(conflicts)

    written = 0
    now = datetime.utcnow()
    for (room_id, night), ids in claims.items():
        res = await db.room_nights.update_one(
            {"room_id": room_id, "night": night},
            {"$setOnInsert": {"booking_id": ids[0], "created_at": now}},
            upsert=True,
        )
        written += 1 if res.upserted_id is not None else 0
    print(f"Inserted {written} room-night claims.")
    return len(conflicts)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write room_nights (default: dry run)")
    args = parser.parse_args()
    configure_logging("WARNING", "text")
    await connect_db()
    try:
        conflicts = await run(args.apply)
    finally:
        await disconnect_db()
    raise SystemExit(1 if conflicts else 0)


if __name__ == "__main__":
    asyncio.run(main())
