"""
Run one billing tick by hand (Phase 5): usage refresh + alerts, Stripe overage meter, past-due
suspension, offboarding. The jobs worker runs the same thing every 15 minutes.

    uv run python -m scripts.billing_tick
"""

from __future__ import annotations

import asyncio
import json

from config import settings
from core.log import configure_logging


async def main() -> None:
    from core.billing.jobs import run_once
    from database.mongodb import connect_db, disconnect_db, get_db

    configure_logging(settings.LOG_LEVEL, "text")
    await connect_db(ensure_indexes=False)
    try:
        print(json.dumps(await run_once(get_db()), indent=2))
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
