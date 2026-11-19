"""
Run the per-business jobs once by hand: calendar sync, reminders, daily digest, transcript and
recording purge, the monthly report and the weekly pilot report. The jobs worker runs the same
thing every TENANT_JOBS_POLL_SECONDS.

    uv run python -m scripts.tenant_jobs_tick
    uv run python -m scripts.tenant_jobs_tick --at 2026-12-01T15:00:00Z   # pretend it's then (try the monthly report)

`--at` changes what "now" means for this run only, so reports due at that moment are sent (and
claimed for that period: they won't be sent again for it).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from config import settings
from core.log import configure_logging


async def main(at: Optional[datetime]) -> None:
    from database.mongodb import connect_db, disconnect_db
    from services.tenant_jobs import run_once

    configure_logging(settings.LOG_LEVEL, "text")
    await connect_db(ensure_indexes=False)
    try:
        print(json.dumps(await run_once(at), indent=2))
    finally:
        await disconnect_db()


def _when(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the per-business jobs once.")
    parser.add_argument("--at", type=_when, default=None, help="ISO time to treat as now (UTC if no offset)")
    asyncio.run(main(parser.parse_args().at))
