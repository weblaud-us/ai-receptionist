"""
The voice prompt for one call (Roadmap Tasks 1.5 and 2.5).

Built once per call, never per turn, by `core.prompts.builder`: base rules (identical for every
tenant, so providers cache them) → vertical rules → the tenant's business facts and FAQs → fenced
owner preferences → the dynamic "Right now" block last.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from agent.profile import VoiceProfile
from core.prompts import builder

STATIC_RULES = builder.BASE_VOICE_RULES


def build_voice_prompt(
    profile: VoiceProfile,
    *,
    now: Optional[datetime] = None,
    caller_phone: Optional[str] = None,
    contact: Optional[dict[str, Any]] = None,
) -> str:
    return builder.build_voice_prompt(profile.tenant, profile.snapshot, now=now,
                                      caller_phone=caller_phone, contact=contact)
