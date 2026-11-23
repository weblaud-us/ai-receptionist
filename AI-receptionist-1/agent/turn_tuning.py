"""
Patient pauses while the caller reads out digits or spells something (plan A2.3).

People pause between groups when they read out a mobile number, a date of birth, a postcode or an
email address, and the normal end-of-turn window cuts them off mid-number. Right after the agent
ASKS for one of those, the next caller turn gets a longer window; once that turn is in, the
tenant's normal window comes back, so the rest of the call stays snappy.

    tuner = attach_turn_tuning(session, normal=session_factory.endpointing(profile))
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Applied for exactly one caller turn after the agent asks for digits or a spelling.
PATIENT_ENDPOINTING = {"mode": "dynamic", "min_delay": 0.8, "max_delay": 4.0}

_ASKS_FOR_DIGITS = re.compile(
    r"\b("
    r"(phone|mobile|contact|callback|call[- ]back|best|cell)\s+number"
    r"|number\s+(to|we can|i can)\s+(reach|call|text)"
    r"|date of birth|birth ?date|birthday|\bdob\b"
    r"|post ?code|postal code|zip ?code"
    r"|e-?mail( address)?"
    r"|spell|spelling|letter by letter"
    r"|medicare|nhs number|health fund (number|card)|membership number|card number|reference number"
    r"|booking (number|reference)"
    r")\b",
    re.IGNORECASE,
)


def asks_for_digits(text: str) -> bool:
    """True when the agent's last sentence is a question asking for a number, a date or a spelling."""
    if not text or "?" not in text:
        return False
    # The question itself: the last sentence that ends in "?" (not "I'll text it to your mobile.").
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    questions = [s for s in sentences if s.endswith("?")]
    return bool(questions) and bool(_ASKS_FOR_DIGITS.search(questions[-1]))


class TurnTuner:
    """Framework-free (testable): `apply(opts)` is session.update_options(endpointing_opts=opts)."""

    def __init__(self, apply: Any, normal: dict[str, Any]) -> None:
        self._apply = apply
        self._normal = dict(normal)
        self.patient = False

    def _set(self, opts: dict[str, Any]) -> None:
        try:
            self._apply(dict(opts))
        except Exception:                     # noqa: BLE001 — tuning must never break a call
            logger.debug("Couldn't change endpointing", exc_info=True)

    def on_assistant_message(self, text: str) -> None:
        if asks_for_digits(text):
            if not self.patient:
                self.patient = True
                self._set(PATIENT_ENDPOINTING)
        elif self.patient:                    # asked something else instead: back to normal
            self.patient = False
            self._set(self._normal)

    def on_user_message(self) -> None:
        if self.patient:
            self.patient = False
            self._set(self._normal)


def _text(item: Any) -> str:
    content = getattr(item, "text_content", None)
    if isinstance(content, str):
        return content
    parts = getattr(item, "content", None) or []
    return " ".join(p for p in parts if isinstance(p, str))


def attach_turn_tuning(session: Any, normal: dict[str, Any]) -> Optional[TurnTuner]:
    if not hasattr(session, "update_options"):
        return None
    tuner = TurnTuner(lambda opts: session.update_options(endpointing_opts=opts), normal)

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        if role == "assistant":
            tuner.on_assistant_message(_text(item))
        elif role == "user":
            tuner.on_user_message()

    return tuner
