"""
The opt-out listener (Roadmap Task 4.5, "hard-coded safety in code, not just the prompt").

Every final transcript of what the callee says is checked here. A match puts the number on the
do-not-call list and ends the call even if the model missed it. It errs on the side of opting
people out: losing a lead to a false match costs nothing, calling someone who said stop does.
"""

from __future__ import annotations

import re

_PATTERNS = (
    r"\b(stop|quit|cease)\s+(calling|phoning|ringing|contacting)\b",
    r"\b(do\s+not|don'?t|never|please\s+don'?t)\s+(ever\s+)?(call|phone|ring|contact)\s+"
    r"(me|us|here|this\s+(number|line|business)|again|back|anymore|any\s+more)\b",
    r"\bdo\s+not\s+call\b",                                   # "put me on your do not call list"
    r"\b(remove|delete|take|get)\s+(me|us|my\s+number|this\s+number|our\s+number)\s+(off|from)\b",
    r"\b(remove|delete)\s+(me|us|my\s+number|this\s+number|our\s+number)\b",
    r"\boff\s+(your|the|this|that)\s+(call\s+|calling\s+|contact\s+)?list\b",
    r"\bno\s+more\s+calls\b",
    r"\blose\s+(my|this|our)\s+number\b",
    r"\bunsubscribe\b",
    r"\bopt(ing)?\s*(me\s+)?out\b",
    r"\bnot\s+interested\b.*\b(don'?t|do\s+not|stop)\s+call",
)
_OPT_OUT = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)

GOODBYE = "I'm sorry to have bothered you. I've taken you off our list and we won't call again. Goodbye."


def is_opt_out(text: str) -> bool:
    return bool(_OPT_OUT.search(" ".join((text or "").replace("’", "'").split())))
