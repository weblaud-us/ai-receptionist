"""
Provider health during a call (Roadmap Tasks 3.5 and 3.7).

Counts, on the call's state, every provider error the session reports and every time a
FallbackAdapter gives up on a provider and moves to the next one:

    state.provider_errors = {"tts": 2}       state.fallbacks = {"tts": 1}

Both are saved on the call document; the API turns them into the `receptionist_provider_*`
metrics that the "provider error rate" and "fallback active" alerts watch. Handlers only touch
memory — nothing here runs between the caller speaking and the agent answering.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import CallState

logger = logging.getLogger(__name__)

_KIND_BY_ERROR_TYPE = {"llm_error": "llm", "stt_error": "stt", "tts_error": "tts",
                       "realtime_model_error": "llm"}


def _bump(counter: dict[str, int], kind: str) -> None:
    counter[kind] = counter.get(kind, 0) + 1


def record_error(state: CallState, error: Any) -> None:
    kind = _KIND_BY_ERROR_TYPE.get(str(getattr(error, "type", "")))
    if kind is None:
        return
    _bump(state.provider_errors, kind)
    logger.warning("Provider error on call %s: %s (%s, recoverable=%s)", state.call_id, kind,
                   getattr(error, "label", "?"), getattr(error, "recoverable", "?"))


def record_availability(state: CallState, kind: str, provider: Any, available: bool) -> None:
    label = getattr(provider, "label", None) or type(provider).__name__
    if available:
        logger.info("Call %s: %s provider %s is back", state.call_id, kind, label)
        return
    _bump(state.fallbacks, kind)
    logger.warning("Call %s: %s provider %s failed; switching to the next one", state.call_id, kind, label)


def attach_provider_health(session: Any, state: CallState) -> None:
    """Subscribe to the session's error events and to each FallbackAdapter's availability events."""

    @session.on("error")
    def _on_error(ev: Any) -> None:
        record_error(state, getattr(ev, "error", None))

    for kind in ("llm", "stt", "tts"):
        component = getattr(session, kind, None)
        if component is None or not hasattr(component, "on"):
            continue

        def _on_availability(ev: Any, kind: str = kind) -> None:
            record_availability(state, kind, getattr(ev, kind, None), bool(getattr(ev, "available", True)))

        try:
            component.on(f"{kind}_availability_changed", _on_availability)   # FallbackAdapters only emit it
        except Exception:
            logger.debug("No availability events for %s", kind, exc_info=True)
