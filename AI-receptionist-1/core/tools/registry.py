"""
One tool implementation for every AI channel and every tenant (Roadmap Tasks 1.10 and 2.7).

- Each tool module defines an OpenAI-format schema and an async handler `(ctx, args) -> dict`.
  They are registered here once; the vertical (core.prompts.verticals) says which tools a tenant's
  agent gets. WhatsApp sends the schemas to the LLM as-is; the voice agent turns each into a
  LiveKit raw-schema tool (`agent/tools.py`). One schema, one handler.
- `run_tool()` is the only way an AI calls a tool. It applies, in one place:
    * the tenant's tool allow-list,
    * caller-ownership checks (inside the handlers, Task 0.9),
    * a hard timeout on read tools so a slow database never leaves a caller in silence,
    * write tools are shielded instead: cancelling a half-finished booking could leave a slot
      blocked, and a "timeout" answer could make the model retry and book twice.

Voice-only actions that need the LiveKit call (end_call, transfer_to_human) live in agent/.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast

from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.tenancy.models import TenantConfig, legacy_hotel_tenant
from core.tools import knowledge, leads, links, messages, scheduling
from core.tools.hotel import HOTEL_TOOLS, HotelToolExecutor, run_in_background
from database.mongodb import get_db

logger = logging.getLogger(__name__)

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
        "caller the team will confirm shortly, and offer to take a message."
    ),
}


@dataclass(frozen=True)
class ToolContext:
    """Who is asking, from where, and for which business. Built once per call/conversation."""

    channel: str                             # "voice" | "browser" | "whatsapp"
    caller_phone: Optional[str] = None       # verified number (SIP caller ID / WhatsApp sender), if any
    call_id: Optional[str] = None
    tenant: Optional[TenantConfig] = None    # None → the legacy .env hotel
    now: Optional[datetime] = None           # tests pin the clock
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def tenant_cfg(self) -> TenantConfig:
        return self.tenant or legacy_hotel_tenant()

    @property
    def tenant_id(self) -> str:
        return self.tenant_cfg.id

    def repos(self) -> Repos:
        return Repos(get_db(), self.tenant_id)


Handler = Callable[[ToolContext, Dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: Dict[str, Any]          # {"type": "function", "function": {...}}
    handler: Handler
    write: bool = False


_REGISTRY: Dict[str, ToolSpec] = {}


def register_tool(schema: Dict[str, Any], handler: Handler, *, write: bool = False) -> None:
    name = schema["function"]["name"]
    if name in _REGISTRY:
        raise ValueError(f"tool {name} registered twice")
    _REGISTRY[name] = ToolSpec(name, schema, handler, write)


def registered_tools() -> Dict[str, ToolSpec]:
    return dict(_REGISTRY)


# ── Hotel plug-in: one executor per tenant ────────────────────────────────────

_executors: Dict[str, HotelToolExecutor] = {}


def get_tool_executor(tenant: Optional[TenantConfig] = None) -> HotelToolExecutor:
    t = tenant or legacy_hotel_tenant()
    key = f"{t.id}:{t.updated_at.isoformat() if t.updated_at and not t.is_legacy else ''}"
    ex = _executors.get(key)
    if ex is None:
        _executors.clear() if len(_executors) > 500 else None
        ex = _executors[key] = HotelToolExecutor(t)
    return ex


def _hotel_handler(name: str) -> Handler:
    async def handler(ctx: ToolContext, args: Dict[str, Any]) -> str:
        return await get_tool_executor(ctx.tenant).execute(name, args, caller_phone=ctx.caller_phone)
    handler.__name__ = name
    return handler


_HOTEL_WRITES = {"create_booking", "cancel_booking", "send_booking_confirmation_whatsapp"}
for _schema in cast(List[Dict[str, Any]], HOTEL_TOOLS):
    _n = _schema["function"]["name"]
    register_tool(_schema, _hotel_handler(_n), write=_n in _HOTEL_WRITES)

register_tool(messages.TAKE_MESSAGE_SCHEMA, messages.take_message, write=True)
register_tool(knowledge.ANSWER_FROM_KNOWLEDGE_SCHEMA, knowledge.answer_from_knowledge)
register_tool(leads.CAPTURE_LEAD_SCHEMA, leads.capture_lead, write=True)
register_tool(links.SEND_INFO_LINK_SCHEMA, links.send_info_link, write=True)
for _schema in cast(List[Dict[str, Any]], scheduling.SCHEDULING_TOOLS):
    _n = _schema["function"]["name"]
    register_tool(_schema, getattr(scheduling, _n),
                  write=_n in {"book_appointment", "reschedule_appointment", "cancel_appointment"})

WRITE_TOOLS = frozenset(s.name for s in _REGISTRY.values() if s.write)


# ── Per-tenant tool sets ──────────────────────────────────────────────────────

def tool_names_for(tenant: Optional[TenantConfig] = None, *, large_kb: bool = False) -> List[str]:
    t = tenant or legacy_hotel_tenant()
    names = [n for n in get_vertical(t.vertical).tools if n in _REGISTRY]
    if large_kb and "answer_from_knowledge" not in names:
        names.append("answer_from_knowledge")
    if not large_kb and "answer_from_knowledge" in names:
        names.remove("answer_from_knowledge")      # small KBs are inlined in the prompt (Task 2.6)
    if links.available_kinds(t) and "send_info_link" not in names:
        names.append("send_info_link")             # only when there is something to send (Task 2.7)
    return names


def schemas_for(tenant: Optional[TenantConfig] = None, *, large_kb: bool = False) -> List[Dict[str, Any]]:
    return [_REGISTRY[n].schema for n in tool_names_for(tenant, large_kb=large_kb)]


# Backwards-compatible names: the hotel's tool set (WhatsApp agent, tests).
AI_TOOL_SCHEMAS: List[Dict[str, Any]] = schemas_for(legacy_hotel_tenant())
TOOL_NAMES = frozenset(_REGISTRY)


def openai_tool_schemas(tenant: Optional[TenantConfig] = None, *, large_kb: bool = False) -> List[Dict[str, Any]]:
    return schemas_for(tenant, large_kb=large_kb)


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _dumps(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


async def _dispatch(ctx: ToolContext, name: str, args: Dict[str, Any]) -> str:
    spec = _REGISTRY[name]
    try:
        return _dumps(await spec.handler(ctx, args))
    except (ValueError, TypeError) as e:
        logger.warning("Tool %s got invalid arguments: %s", name, type(e).__name__)
        return json.dumps({"error": "invalid_arguments", "message": str(e)[:200]})
    except Exception:
        logger.error("Tool %s failed", name, exc_info=True)
        return json.dumps({"error": "tool_failed", "message": "Something went wrong. Apologise and offer to try again."})


async def run_tool(ctx: ToolContext, name: str, args: Optional[Dict[str, Any]]) -> str:
    """Run an AI tool call and return its JSON result. Never raises."""
    if name not in _REGISTRY or name not in tool_names_for(ctx.tenant, large_kb=True):
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
