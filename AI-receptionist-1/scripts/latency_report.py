"""
Voice latency over the last N calls, against the Phase 1 targets (Roadmap Tasks 1.1, 1.12.c).

    python -m scripts.latency_report              # last 20 calls
    python -m scripts.latency_report --calls 50 --channel phone

Reads the per-turn rows the voice agent stores on each `calls` document. Run it after every
change to the voice pipeline: the numbers, not a feeling, decide whether a change helped.
"""

import argparse
import asyncio
from typing import Any, Optional

from agent.telemetry import STAGE_KEYS, percentile
from core.log import configure_logging
from database.mongodb import connect_db, disconnect_db, get_db

TARGETS = {  # metric → (target seconds, label)
    "plain_p50": (1.0, "Response p50, no tool"),
    "plain_p95": (1.8, "Response p95, no tool"),
    "tool_p50": (1.6, "Response p50, tool turns"),
    "greeting_p50": (0.7, "Greeting starts after connect"),
}


def _fmt(value: Optional[float]) -> str:
    return "   -  " if value is None else f"{value:5.2f}s"


def stage_breakdown(turns: list[dict[str, Any]]) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """p50 and p95 of each pipeline stage. A stage missing from every row is left out."""
    out: dict[str, tuple[Optional[float], Optional[float]]] = {}
    for key in STAGE_KEYS:
        values = [t[key] for t in turns if isinstance(t.get(key), (int, float))]
        if values:
            out[key] = (percentile(values, 50), percentile(values, 95))
    return out


def report(calls: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    turns = [t for c in calls for t in c.get("turns") or [] if t.get("latency_s") is not None]
    plain = [t["latency_s"] for t in turns if not t.get("tool_turn")]
    tool = [t["latency_s"] for t in turns if t.get("tool_turn")]
    greet = [c["greeting_latency_s"] for c in calls if c.get("greeting_latency_s") is not None]
    results = {
        "plain_p50": percentile(plain, 50), "plain_p95": percentile(plain, 95),
        "tool_p50": percentile(tool, 50), "greeting_p50": percentile(greet, 50),
    }
    print(f"{len(calls)} calls, {len(plain)} plain turns, {len(tool)} tool turns\n")
    for key, (target, label) in TARGETS.items():
        value = results[key]
        verdict = "  n/a" if value is None else (" PASS" if value <= target else " FAIL")
        print(f"  {label:<32} {_fmt(value)}   target ≤ {target:.1f}s {verdict}")
    for title, rows in (("plain turns", [t for t in turns if not t.get("tool_turn")]),
                        ("tool turns", [t for t in turns if t.get("tool_turn")])):
        if not rows:
            continue
        print(f"\n  Where the time goes, {title} ({len(rows)}):     p50      p95")
        for key, (p50, p95) in stage_breakdown(rows).items():
            print(f"    {key:<36} {_fmt(p50)}   {_fmt(p95)}")
    outcomes: dict[str, int] = {}
    for c in calls:
        outcomes[c.get("outcome") or "unknown"] = outcomes.get(c.get("outcome") or "unknown", 0) + 1
    print("\n  Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())))
    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calls", type=int, default=20)
    parser.add_argument("--channel", choices=["phone", "browser"])
    args = parser.parse_args()
    configure_logging("WARNING", "text")
    await connect_db(ensure_indexes=False)
    try:
        query: dict[str, Any] = {"status": "completed"}
        if args.channel:
            query["channel"] = args.channel
        calls = await get_db().calls.find(query, {"turns": 1, "greeting_latency_s": 1, "outcome": 1}) \
            .sort("started_at", -1).limit(args.calls).to_list(args.calls)
        report(calls)
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
