"""
What the agent says while a slow tool runs (plan A2.1), so the caller never sits in dead silence
while the practice diary answers.

    async with context.with_filler(source_for(name, state), delay=DELAY_S, max_steps=1):
        result = await run_tool(...)

livekit-agents' RunContext.with_filler only speaks once the session has been idle for DELAY_S
while the tool is still running: a lookup answered from the cache (core/services/pms_cache.py)
finishes first and the caller hears no filler at all. Phrases rotate so one call never hears the
same line twice in a row, and they are English only: a caller speaking another language gets the
thinking sound instead of an English aside.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, Optional, Tuple

DELAY_S = 0.7          # past this, silence starts to feel like a dropped line

PHRASES: Dict[str, Tuple[str, ...]] = {
    "check_availability": (
        "Sure, let me take a quick look at what we've got.",
        "One moment, I'm just checking the diary.",
        "Let me have a look for you.",
    ),
    "book_appointment": (
        "Just popping that into the system for you now.",
        "Lovely, I'm booking that in now.",
    ),
    "reschedule_appointment": (
        "No problem, I'm moving that over now.",
        "One moment while I change that for you.",
    ),
    "cancel_appointment": (
        "Okay, I'm cancelling that for you now.",
        "One moment while I take care of that.",
    ),
    "find_my_appointments": (
        "One moment, I'm pulling that up.",
        "Let me find that for you.",
    ),
    # Hotel vertical
    "check_room_availability": (
        "Let me check what rooms we have.",
        "One moment, I'm looking at availability.",
    ),
    "create_booking": ("Just booking that in for you now.",),
}


def pick(name: str, used: list[str], rng: Optional[random.Random] = None) -> Optional[str]:
    """A phrase for this tool that this call hasn't heard yet; once all have been used, any but the
    last one. None when the tool has no filler."""
    pool = PHRASES.get(name)
    if not pool:
        return None
    r = rng or random
    fresh = [p for p in pool if p not in used]
    if not fresh:
        fresh = [p for p in pool if not used or p != used[-1]] or list(pool)
    choice = r.choice(fresh)
    used.append(choice)
    return choice


def speaks_english(state: Any) -> bool:
    profile = getattr(state, "profile", None)
    lang = str(getattr(profile, "tts_language", "") or getattr(profile, "language", "") or "en")
    return lang.lower().startswith("en")


def source_for(name: str, state: Any) -> Optional[Callable[[int], Optional[str]]]:
    """The `source` for RunContext.with_filler, or None when this tool shouldn't get a filler."""
    if name not in PHRASES or not speaks_english(state):
        return None
    found = getattr(state, "fillers_used", None)
    used: list[str] = found if isinstance(found, list) else []
    if found is None:
        try:
            state.fillers_used = used
        except AttributeError:
            pass

    def source(step: int) -> Optional[str]:
        return pick(name, used)

    return source
