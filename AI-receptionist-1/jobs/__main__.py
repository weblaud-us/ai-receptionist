"""`python -m jobs` runs the worker; `python -m jobs dead [--clear]` shows jobs that failed for good."""

from __future__ import annotations

import asyncio
import json
import sys

from config import settings


async def _dead(clear: bool) -> int:
    from core import redis as redis_store
    from jobs.queue import DEAD_LETTER_KEY

    r = redis_store.get_redis()
    if r is None:
        print("REDIS_URL is not set: there is no job queue.")
        return 1
    rows = await r.lrange(DEAD_LETTER_KEY, 0, -1)
    for row in rows:
        e = json.loads(row)
        print(f"{e.get('at')}  {e.get('job')}  tries={e.get('tries')}  {e.get('error')}")
    print(f"{len(rows)} dead job(s)")
    if clear and rows:
        await r.delete(DEAD_LETTER_KEY)
        print("Cleared.")
    await redis_store.close()
    return 0


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "dead":
        raise SystemExit(asyncio.run(_dead("--clear" in args)))
    if not settings.redis_enabled:
        raise SystemExit("REDIS_URL is not set. Without Redis the API runs the background jobs itself.")
    from arq.worker import run_worker

    from jobs.worker import WorkerSettings, install_log_filter

    install_log_filter()          # before arq logs its first job
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
