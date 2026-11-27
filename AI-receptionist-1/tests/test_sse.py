"""Server-sent events for long jobs (utils/sse.py)."""

import asyncio
import json

from utils import sse


def _run(job, ping_s=0.05):
    async def go():
        out = []
        async for chunk in sse.events(job, result=lambda r: {"value": r},
                                      on_error=lambda e: {"status": 500, "message": str(e)}, ping_s=ping_s):
            out.append(chunk.decode())
        return out
    return asyncio.run(go())


def test_events_then_result_in_order_with_pings_while_quiet():
    async def job(emit):
        await emit({"type": "step", "n": 1})
        await asyncio.sleep(0.12)                 # quiet: at least one ping goes out
        await emit({"type": "step", "n": 2})
        return 42
    out = _run(job)
    data = [json.loads(c[6:]) for c in out if c.startswith("data: ")]
    assert data == [{"type": "step", "n": 1}, {"type": "step", "n": 2}, {"type": "result", "value": 42}]
    assert any(c.startswith(": ping") for c in out)


def test_a_failing_job_ends_with_an_error_event():
    async def job(emit):
        await emit({"type": "step"})
        raise ValueError("nope")
    data = [json.loads(c[6:]) for c in _run(job) if c.startswith("data: ")]
    assert data[-1] == {"type": "error", "status": 500, "message": "nope"}
