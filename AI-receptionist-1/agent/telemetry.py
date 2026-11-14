"""
Per-turn latency — the numbers Phase 1 is accepted on (Roadmap Tasks 1.1 and 1.12.c).

livekit-agents 1.8 attaches a MetricsReport to each assistant ChatMessage:
    e2e_latency         caller stopped speaking → agent audio starts   ← "response latency"
    end_of_turn_delay   end of speech → turn declared over
    transcription_delay end of speech → final transcript
    llm_node_ttft       LLM time to first token
    tts_node_ttfb       TTS time to first audio
(`metrics_collected` still fires but is deprecated in favour of these.)

A "turn" is the caller's utterance plus the first agent reply to it. Its latency is that first
reply's e2e_latency; it counts as a tool turn if any tool ran before the next caller utterance.
Rows are stored on the call document; scripts/latency_report.py aggregates them.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Iterable, Optional

from agent.state import CallState

logger = logging.getLogger(__name__)

_METRIC_KEYS = ("e2e_latency", "end_of_turn_delay", "transcription_delay", "llm_node_ttft", "tts_node_ttfb")


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None for no data."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    k = max(0, min(len(data) - 1, math.ceil(pct / 100 * len(data)) - 1))
    return round(data[k], 3)


def turn_latency(metrics: dict[str, Any]) -> Optional[float]:
    e2e = metrics.get("e2e_latency")
    if isinstance(e2e, (int, float)) and e2e >= 0:
        return float(e2e)
    parts = [metrics.get(k) for k in ("end_of_turn_delay", "llm_node_ttft", "tts_node_ttfb")]
    if all(isinstance(p, (int, float)) and p >= 0 for p in parts):
        return float(sum(parts))  # type: ignore[arg-type]
    return None


def summarise(turns: list[dict[str, Any]]) -> dict[str, Any]:
    plain = [t["latency_s"] for t in turns if t.get("latency_s") is not None and not t.get("tool_turn")]
    tool = [t["latency_s"] for t in turns if t.get("latency_s") is not None and t.get("tool_turn")]
    return {
        "turns": len(turns),
        "p50_s": percentile(plain, 50),
        "p95_s": percentile(plain, 95),
        "max_s": max(plain) if plain else None,
        "tool_turns": len(tool),
        "tool_p50_s": percentile(tool, 50),
        "tool_p95_s": percentile(tool, 95),
    }


class TurnTracker:
    """Turns conversation events into latency rows on `state.turns`. Framework-free (testable)."""

    def __init__(self, state: CallState) -> None:
        self.state = state
        self._awaiting_reply = False
        self._tool_before_reply = False
        self._current: Optional[dict[str, Any]] = None

    def on_user_message(self) -> None:
        self._awaiting_reply = True
        self._tool_before_reply = False
        self._current = None

    def on_assistant_message(self, metrics: dict[str, Any], interrupted: bool = False) -> None:
        if not self._awaiting_reply:
            return  # the greeting, or a second message in the same turn (e.g. after a tool)
        self._awaiting_reply = False
        row: dict[str, Any] = {"turn": len(self.state.turns) + 1, "tool_turn": self._tool_before_reply,
                               "interrupted": bool(interrupted)}
        for key in _METRIC_KEYS:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                row[key] = round(float(value), 3)
        latency = turn_latency(metrics)
        row["latency_s"] = round(latency, 3) if latency is not None else None
        self.state.turns.append(row)
        self._current = row

    def on_tools_executed(self, names: list[str]) -> None:
        self.state.tools_used.extend(names)
        if self._current is not None:
            self._current["tool_turn"] = True
            self._current["tools"] = [*self._current.get("tools", []), *names]
        elif self._awaiting_reply:
            self._tool_before_reply = True   # the model called a tool before saying anything

    def on_agent_speaking(self) -> None:
        if self.state.greeting_latency_s is None:
            self.state.greeting_latency_s = round(time.monotonic() - self.state.started_monotonic, 3)


def attach_telemetry(session: Any, state: CallState) -> TurnTracker:
    """Subscribe to the session's events. Handlers only touch memory."""
    tracker = TurnTracker(state)

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        if role == "user":
            tracker.on_user_message()
        elif role == "assistant":
            tracker.on_assistant_message(dict(getattr(item, "metrics", None) or {}),
                                         bool(getattr(item, "interrupted", False)))

    @session.on("function_tools_executed")
    def _on_tools(ev: Any) -> None:
        tracker.on_tools_executed([getattr(c, "name", "?") for c in getattr(ev, "function_calls", [])])

    @session.on("agent_state_changed")
    def _on_agent_state(ev: Any) -> None:
        if getattr(ev, "new_state", None) == "speaking":
            tracker.on_agent_speaking()

    return tracker
