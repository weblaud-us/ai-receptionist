"""
Landing-page demo calls (core/services/web_demo.py): a hard time cap with one friendly wrap-up line.

The call is otherwise the demo practice's ordinary receptionist, so the visitor hears exactly what a
patient would. The recorder adds the call to the visitor's lead when it ends (agent/persistence.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, List

from agent.receptionist import hang_up

logger = logging.getLogger(__name__)

WRAP_UP_BEFORE_S = 15
WRAP_UP_LINE = ("We're nearly out of demo time. If you'd like me answering your practice's phone, "
                "the sign-up button is just below. Thanks so much for trying me!")


def start_timers(session: Any, max_seconds: int,
                 on_end: Callable[[str], Any] = hang_up) -> List[asyncio.TimerHandle]:
    """Say the wrap-up line WRAP_UP_BEFORE_S before the cap, then end the call at the cap.
    Returns the timer handles so a call that ends sooner can cancel them."""
    loop = asyncio.get_running_loop()

    def wrap_up() -> None:
        try:
            session.say(WRAP_UP_LINE, allow_interruptions=False, add_to_chat_ctx=True)
        except Exception:
            logger.debug("Demo wrap-up line failed", exc_info=True)

    return [loop.call_later(max(max_seconds - WRAP_UP_BEFORE_S, 5), wrap_up),
            loop.call_later(max_seconds, lambda: asyncio.ensure_future(on_end("demo_time_limit")))]
