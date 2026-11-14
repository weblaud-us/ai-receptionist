"""
Migration 0002 — create every collection index. Idempotent; deploy.sh runs it on each deploy.

    python -m migrations.m0002_indexes

Indexes used to be built on every API start and on every LiveKit call (the old worker called
connect_db() per job). Building them here, once, keeps boot fast and keeps index builds out of
live calls (Roadmap Task 1.9.2).
"""

import asyncio

from core.log import configure_logging
from database.mongodb import connect_db, disconnect_db


async def main() -> None:
    configure_logging("INFO", "text")
    await connect_db(ensure_indexes=True)
    await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
