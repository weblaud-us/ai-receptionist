"""
Warm transfer (plan Phase 2.2): the assistant rings the practice, briefs the staff member while the
caller hears hold music, and only then joins them. Uses LiveKit's WarmTransferTask
(livekit.agents.beta.workflows); this module holds the parts that don't need LiveKit, so they can
be tested anywhere.

A warm transfer needs an outbound SIP trunk (LIVEKIT_SIP_OUTBOUND_TRUNK_ID). Without one, or with
`transfer.warm` off, the call is transferred cold (SIP REFER), as before.
"""

from __future__ import annotations

import logging

from config import settings

logger = logging.getLogger(__name__)

RINGING_TIMEOUT_S = 25.0          # then the caller is back with the assistant, who offers a message
_warned_no_trunk = False


def warm_enabled(warm: bool) -> bool:
    global _warned_no_trunk
    if not warm:
        return False
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        if not _warned_no_trunk:
            logger.warning("Warm transfer is on but LIVEKIT_SIP_OUTBOUND_TRUNK_ID is empty; transferring cold")
            _warned_no_trunk = True
        return False
    return True


def brief_instructions(*, agent_name: str, business: str, reason: str, emergency: bool = False) -> str:
    """What the assistant says to the staff member before the caller is put through."""
    urgent = ("This is urgent: say so in your first sentence. " if emergency else "")
    return f"""# How to brief the staff member
You are {agent_name}, the AI receptionist on {business}'s phone line, calling a member of staff.
{urgent}Open with one short line, for example: "Hi, it's {agent_name} from the phone line, I've got a caller for you."
Then, in two or three short spoken sentences: who is calling (their name if they gave it) and what they
need. The reason noted during the call: {reason}
Don't read out the whole conversation, and never read out dates of birth, card details or phone numbers.
Ask if they can take the call now.
- If they say yes, call connect_to_caller.
- If they can't take it, call decline_transfer with their reason.
- If you hear a voicemail greeting or an answering machine, call voicemail_detected.
Answer their quick questions about the call from the conversation history, briefly."""


NO_ANSWER = ("Nobody at the practice could take the call just now. Apologise briefly and offer to take a "
             "message so the team can call back.")
CONNECTED = "Transferred. A staff member is now talking with the caller; say nothing more."
