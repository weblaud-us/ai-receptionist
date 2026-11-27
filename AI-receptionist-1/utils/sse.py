"""
Server-sent events for long jobs that report progress (the website import).

    return progress_response(lambda emit: site_import.import_site(url, on_progress=emit),
                             result=lambda draft: {"draft": draft}, on_error=_import_error)

The job runs as a task feeding a queue; each event goes out as one `data:` frame the moment it's put.
A `: ping` comment every 10 s keeps proxies (Cloudflare, Caddy, nginx) from closing a quiet connection,
for example while the LLM is thinking. It ends with {"type": "result", ...} or {"type": "error",
"status", "message"}. If the browser goes away, the job is cancelled.

The dashboards read it with fetch + a stream reader (packages/shared/src/lib/api.ts `streamEvents`),
because EventSource can't send the Authorization header.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

from fastapi.responses import StreamingResponse

from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

PING_S = 10.0
HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",          # nginx: don't buffer this response
    "Connection": "keep-alive",
}

Emit = Callable[[Dict[str, Any]], Awaitable[None]]


def frame(event: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(to_jsonable(event), ensure_ascii=False)}\n\n".encode("utf-8")


async def events(job: Callable[[Emit], Awaitable[Any]], *, result: Callable[[Any], Dict[str, Any]],
                 on_error: Callable[[BaseException], Dict[str, Any]], ping_s: float = PING_S) -> AsyncIterator[bytes]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: Dict[str, Any]) -> None:
        await queue.put(event)

    task = asyncio.create_task(job(emit))
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, task}, timeout=ping_s, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield frame(getter.result())
                continue
            getter.cancel()
            if task in done:
                while not queue.empty():
                    yield frame(queue.get_nowait())
                exc = task.exception()
                if exc is None:
                    yield frame({"type": "result", **result(task.result())})
                else:
                    yield frame({"type": "error", **on_error(exc)})
                return
            yield b": ping\n\n"
    finally:
        if not task.done():
            task.cancel()


def progress_response(job: Callable[[Emit], Awaitable[Any]], *, result: Callable[[Any], Dict[str, Any]],
                      on_error: Callable[[BaseException], Dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(events(job, result=result, on_error=on_error), media_type="text/event-stream",
                             headers=HEADERS)
