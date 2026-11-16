"""One Mongo client per worker process, shared by the receptionist and the sales worker."""

from __future__ import annotations

import asyncio
from typing import Optional

from database import mongodb

_db_loop: Optional[asyncio.AbstractEventLoop] = None


async def ensure_db() -> None:
    """
    One Mongo client per worker process (Task 1.9.1), created inside the job's event loop and
    reused by later jobs on the same loop. No index builds here — the migration does that.
    """
    global _db_loop
    loop = asyncio.get_running_loop()
    if mongodb.is_connected() and _db_loop is loop:
        return
    if mongodb.is_connected():
        await mongodb.disconnect_db()  # bound to an old loop
    await mongodb.connect_db(ensure_indexes=False)
    _db_loop = loop
