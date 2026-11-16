"""Per-call state: everything the call needs, in memory, so no turn waits on the database."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent.profile import VoiceProfile
from core.tools.registry import ToolContext

# A booking beats 'completed', a transfer beats everything.
_OUTCOME_RANK = {"completed": 0, "faq": 1, "cancelled": 2, "message": 3, "rescheduled": 4, "booked": 5,
                 "voicemail": 5, "transferred": 6, "busy": 7}


@dataclass
class CallState:
    call_id: str                              # the LiveKit room name
    profile: VoiceProfile
    channel: str = "phone"                    # "phone" (SIP) | "browser" (dashboard test / WhatsApp call link)
    direction: str = "inbound"                # "inbound" | "outbound" (Phase 4)
    caller_phone: Optional[str] = None        # SIP caller ID; None for browser calls
    dialed_number: Optional[str] = None       # sip.trunkPhoneNumber → the tenant's number
    sip_call_id: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    contact: Optional[dict[str, Any]] = None  # caller history, loaded in the background
    transcript: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)      # per-turn latency rows
    tools_used: list[str] = field(default_factory=list)
    greeting_latency_s: Optional[float] = None
    outcome: Optional[str] = None             # booked / transferred / message / cancelled / completed
    recording: Optional[dict[str, Any]] = None
    ended_reason: Optional[str] = None
    # Provider health during this call (Phase 3, Task 3.7): {"llm": 2, "tts": 1}
    provider_errors: dict[str, int] = field(default_factory=dict)
    fallbacks: dict[str, int] = field(default_factory=dict)      # switches to a fallback provider
    transfer: Optional[str] = None            # "ok" | "failed" once transfer_to_human was tried

    @property
    def tenant_id(self) -> str:
        return self.profile.tenant.id

    def tool_context(self) -> ToolContext:
        tool_channel = "voice" if self.channel == "phone" else "browser"
        return ToolContext(channel=tool_channel, caller_phone=self.caller_phone, call_id=self.call_id,
                           tenant=self.profile.tenant)

    def set_outcome(self, outcome: str, *, override: bool = False) -> None:
        """Keep the most meaningful outcome: a booking beats 'completed', a transfer beats both."""
        if override or self.outcome is None or _OUTCOME_RANK.get(outcome, 0) >= _OUTCOME_RANK.get(self.outcome, 0):
            self.outcome = outcome
