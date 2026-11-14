"""
The shared AI tools (core.tools.registry) exposed to the LiveKit agent — Roadmap Tasks 1.10, 2.7.

Each OpenAI-format schema becomes a LiveKit raw-schema tool whose handler calls run_tool(),
the same function WhatsApp uses. The set of tools comes from the tenant's vertical. No tool is
defined twice; ownership checks and timeouts apply identically on every channel.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import RawFunctionTool, Tool, Toolset

from agent.profile import VoiceProfile
from core.tools.registry import run_tool, schemas_for

logger = logging.getLogger(__name__)

# A successful call to one of these tells us how the call went (stored on the call record).
_OUTCOMES = {
    "create_booking": "booked", "book_appointment": "booked",
    "reschedule_appointment": "rescheduled",
    "cancel_booking": "cancelled", "cancel_appointment": "cancelled",
    "take_message": "message", "capture_lead": "message",
}


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


def shared_tools(profile: Optional[VoiceProfile] = None) -> list[Tool | Toolset]:
    tenant = profile.tenant if profile else None
    large_kb = profile.large_kb if profile else False
    return [_make_tool(s["function"]) for s in schemas_for(tenant, large_kb=large_kb)]
