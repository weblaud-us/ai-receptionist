"""
The shared AI tools (core.tools.registry) exposed to the LiveKit agent — Roadmap Task 1.10.

Each OpenAI-format schema becomes a LiveKit raw-schema tool whose handler calls run_tool(),
the same function WhatsApp uses. No tool is defined twice; ownership checks and timeouts apply
identically on every channel.
"""

import json
import logging
from typing import Any

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import RawFunctionTool, Tool, Toolset

from core.tools.registry import AI_TOOL_SCHEMAS, run_tool

logger = logging.getLogger(__name__)

# A successful call to one of these tells us how the call went (stored on the call record).
_OUTCOMES = {"create_booking": "booked", "take_message": "message", "cancel_booking": "cancelled"}


def _succeeded(result: str) -> bool:
    try:
        return bool(json.loads(result).get("success"))
    except (ValueError, AttributeError):
        return False


def _make_tool(schema: dict[str, Any]) -> RawFunctionTool:
    name = schema["name"]

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        state = context.userdata
        result = await run_tool(state.tool_context(), name, dict(raw_arguments))
        if name in _OUTCOMES and _succeeded(result):
            state.set_outcome(_OUTCOMES[name])
        return result

    handler.__name__ = name
    return function_tool(handler, raw_schema=schema)


def shared_tools() -> list[Tool | Toolset]:
    return [_make_tool(s["function"]) for s in AI_TOOL_SCHEMAS]
