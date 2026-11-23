"""
Undo a practice-diary (PMS) booking that this system failed to save (audit P0-09).

`SchedulingService.book` creates the appointment in Cliniko/Dentally first, then saves it here.
If that save fails it cancels the PMS booking straight away (two tries). If that fails too, the
booking is written to `pms_compensations` and the tenant jobs tick keeps trying with backoff for
up to MAX_AGE, so the diary never keeps a booking nobody here knows about. The owner is alerted
when it is first queued (scheduling.py) and again if it gives up.

Only what's needed to cancel is kept: the PMS appointment id and the start time. No patient details.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from core.repositories import Repos
from core.services import scheduling_provider as sp
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30),
           timedelta(hours=1))                    # then every hour
MAX_AGE = timedelta(hours=24)
REASON = "Booking could not be saved; rolled back"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def next_delay(attempts: int) -> timedelta:
    return BACKOFF[min(attempts, len(BACKOFF) - 1)]


async def record(repos: Repos, external: Dict[str, Any], start: datetime) -> str:
    now = utcnow()
    doc_id = new_id("pmsc")
    await repos.pms_compensations.insert_one({
        "_id": doc_id, "provider": external.get("provider"), "external_id": str(external["id"]),
        "appointment_start": start, "status": "pending", "attempts": 0, "created_at": now,
        "next_at": now + next_delay(0), "last_error": None})
    return doc_id


async def run_due(t: TenantConfig, now: datetime) -> int:
    """Retry every due rollback for one tenant. Returns how many were cancelled in the PMS."""
    now = _aware(now)
    repos = Repos(get_db(), t.id)
    due = await repos.pms_compensations.find({"status": "pending", "next_at": {"$lte": now}}, limit=20)
    if not due:
        return 0
    pms = sp.provider_for(t)
    done = 0
    for row in due:
        # Claim it, so a second jobs worker doesn't retry the same one in parallel.
        claimed = await repos.pms_compensations.update_one(
            {"_id": row["_id"], "status": "pending", "attempts": row["attempts"]},
            {"$set": {"next_at": now + next_delay(row["attempts"] + 1)}, "$inc": {"attempts": 1}})
        if claimed.modified_count == 0:
            continue
        error = "practice diary is no longer connected" if pms is None else None
        if pms is not None:
            try:
                await pms.cancel_appointment(row["external_id"], REASON)
            except Exception as e:                      # noqa: BLE001 — keep retrying until MAX_AGE
                error = str(e)[:300]
        if error is None:
            await repos.pms_compensations.update_one({"_id": row["_id"]}, {"$set": {"status": "done", "done_at": now}})
            logger.warning("Rolled back PMS appointment %s for %s on retry", row["external_id"], t.id)
            done += 1
        elif now - _aware(row["created_at"]) >= MAX_AGE:
            await repos.pms_compensations.update_one({"_id": row["_id"]},
                                                     {"$set": {"status": "gave_up", "last_error": error}})
            await _alert_gave_up(t, row)
        else:
            await repos.pms_compensations.update_one({"_id": row["_id"]}, {"$set": {"last_error": error}})
    return done


async def _alert_gave_up(t: TenantConfig, row: Dict[str, Any]) -> None:
    from zoneinfo import ZoneInfo

    from core.notifications import notify_owner

    when = _aware(row["appointment_start"]).astimezone(ZoneInfo(t.timezone)).strftime("%a %d %b %H:%M")
    logger.error("Gave up rolling back PMS appointment %s for %s", row["external_id"], t.id)
    await notify_owner(t, "on_new_booking",
                       f"Action needed: we couldn't cancel a booking at {when} in your practice diary "
                       f"(appointment {row['external_id']}) that wasn't saved here. Please check it and cancel "
                       "it there if the patient doesn't expect it.",
                       subject="Please check your practice diary", urgent=True)
