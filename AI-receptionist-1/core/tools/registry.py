"""
One tool implementation for every AI channel (Roadmap Task 1.10).

- `AI_TOOL_SCHEMAS` — the OpenAI-format schemas. WhatsApp sends them to the LLM as-is; the voice
  agent turns each one into a LiveKit raw-schema tool (`agent/tools.py`). One schema, one handler.
- `run_tool()` — the only way an AI calls a tool. It applies, in one place:
    * caller-ownership checks on bookings (inside HotelToolExecutor.execute, Task 0.9),
    * a hard timeout on read tools so a slow database never leaves a caller in silence,
    * write tools are shielded instead: cancelling a half-finished booking could leave a room
      blocked, and a "timeout" answer could make the model retry and book twice.

Voice-only actions that need the LiveKit call (end_call, transfer_to_human) live in agent/.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.tools.hotel import HOTEL_TOOLS, HotelToolExecutor, run_in_background
from core.tools.messages import TAKE_MESSAGE_SCHEMA, take_message

logger = logging.getLogger(__name__)

AI_TOOL_SCHEMAS: List[Dict[str, Any]] = [*HOTEL_TOOLS, TAKE_MESSAGE_SCHEMA]
TOOL_NAMES = frozenset(s["function"]["name"] for s in AI_TOOL_SCHEMAS)

WRITE_TOOLS = frozenset({"create_booking", "cancel_booking", "take_message"})
READ_TIMEOUT_S = 4.0
WRITE_TIMEOUT_S = 10.0

_TIMEOUT_READ = {
    "error": "timeout",
    "message": "That lookup took too long. Apologise briefly and offer to try again or take a message.",
}
_TIMEOUT_WRITE = {
    "error": "still_processing",
    "message": (
        "This is taking longer than usual and may still go through. Do NOT try again. Tell the "
        "guest the team will confirm shortly, and offer to take a message."
    ),
}


@dataclass(frozen=True)
class ToolContext:
    """Who is asking, and from where. Built once per call/conversation."""

    channel: str                        # "voice" | "browser" | "whatsapp"
    caller_phone: Optional[str] = None  # verified number (SIP caller ID / WhatsApp sender), if any
    call_id: Optional[str] = None


_executor: Optional[HotelToolExecutor] = None


def get_tool_executor() -> HotelToolExecutor:
    global _executor
    if _executor is None:
        _executor = HotelToolExecutor()
    return _executor


def openai_tool_schemas() -> List[Dict[str, Any]]:
    return AI_TOOL_SCHEMAS


async def _dispatch(ctx: ToolContext, name: str, args: Dict[str, Any]) -> str:
    if name == "take_message":
        try:
            return json.dumps(await take_message(ctx, **args), default=str)
        except TypeError as e:
            return json.dumps({"error": "invalid_arguments", "message": str(e)[:200]})
        except Exception:
            logger.error("Tool take_message failed", exc_info=True)
            return json.dumps({"error": "tool_failed", "message": "Couldn't save the message. Apologise."})
    return await get_tool_executor().execute(name, args, caller_phone=ctx.caller_phone)


async def run_tool(ctx: ToolContext, name: str, args: Optional[Dict[str, Any]]) -> str:
    """Run an AI tool call and return its JSON result. Never raises."""
    if name not in TOOL_NAMES:
        return json.dumps({"error": f"Unknown tool: {name}"})
    clean = {k: v for k, v in dict(args or {}).items() if v not in ("", None)}

    if name in WRITE_TOOLS:
        task = run_in_background(_dispatch(ctx, name, clean))
        try:
            return await asyncio.wait_for(asyncio.shield(task), WRITE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("Tool %s still running after %.0fs (left to finish)", name, WRITE_TIMEOUT_S)
            return json.dumps(_TIMEOUT_WRITE)

    try:
        return await asyncio.wait_for(_dispatch(ctx, name, clean), READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("Tool %s timed out after %.0fs", name, READ_TIMEOUT_S)
        return json.dumps(_TIMEOUT_READ)
