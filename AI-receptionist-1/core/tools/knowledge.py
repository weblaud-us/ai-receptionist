"""
Knowledge base (Roadmap Task 2.6).

Small knowledge bases (≤ KB_INLINE_MAX items) go straight into the prompt's business-facts block
— no tool call, no latency. Larger ones expose `answer_from_knowledge`, which today does a
keyword-overlap search over `knowledge_items`; Atlas Vector Search slots in behind the same
function later. Every miss is logged to `unanswered_questions` so the owner can add the answer.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from core.tenancy.ids import new_id

if TYPE_CHECKING:
    from core.tools.registry import ToolContext

logger = logging.getLogger(__name__)

KB_INLINE_MAX_ITEMS = 60
KB_INLINE_MAX_CHARS = 6000

ANSWER_FROM_KNOWLEDGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "answer_from_knowledge",
        "description": (
            "Look up the business's own answers (policies, prices, parking, what they offer…) for a "
            "question not covered by the facts you already have. Returns the closest saved answers."
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The caller's question, in full"}},
            "required": ["question"],
        },
    },
}

_STOP = {"the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "we", "it", "to", "of", "in", "on",
         "for", "and", "or", "what", "how", "much", "can", "have", "with", "at", "be", "my", "me", "there"}


def _stem(w: str) -> str:
    """Crude English stemming so 'parking' matches 'park' and 'cards' matches 'card'."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            return w + "y" if suffix == "ies" else w
    return w


def _tokens(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1}


def question_key(question: str) -> str:
    """Normalised form so "Is there parking?" and "is there parking" group as one unanswered question."""
    return " ".join(sorted(_tokens(question)))


def rank_items(items: List[Dict[str, Any]], question: str, n: int = 3) -> List[Dict[str, Any]]:
    return [it for _, it in rank_scored(items, question, n)]


def rank_scored(items: List[Dict[str, Any]], question: str, n: int = 3) -> List[Tuple[int, Dict[str, Any]]]:
    q = _tokens(question)
    if not q:
        return []
    scored = []
    for it in items:
        words = _tokens(f"{it.get('question', '')} {' '.join(it.get('tags') or [])}")
        answer_words = _tokens(it.get("answer", ""))
        score = 2 * len(q & words) + len(q & answer_words)
        if score:
            scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    return scored[:n]


def kb_chars(items: List[Dict[str, Any]]) -> int:
    return sum(len(i.get("question", "")) + len(i.get("answer", "")) for i in items)


def fits_inline(items: List[Dict[str, Any]]) -> bool:
    return len(items) <= KB_INLINE_MAX_ITEMS and kb_chars(items) <= KB_INLINE_MAX_CHARS


async def answer_from_knowledge(ctx: "ToolContext", args: Dict[str, Any]) -> Dict[str, Any]:
    question = str(args.get("question") or "").strip()[:500]
    if not question:
        return {"error": "question_required", "message": "Ask what they want to know."}
    repos = ctx.repos()
    items = await repos.knowledge.find({"active": {"$ne": False}}, limit=1000,
                                       projection={"question": 1, "answer": 1, "tags": 1})
    hits = rank_items(items, question)
    if not hits:
        await log_unanswered(ctx, question)
        return {"found": False,
                "message": "Nothing saved about that. Say you don't have that information and offer take_message."}
    return {"found": True, "answers": [{"question": h.get("question"), "answer": h.get("answer")} for h in hits]}


async def log_unanswered(ctx: "ToolContext", question: str) -> None:
    await record_unanswered(ctx.repos(), question, channel=ctx.channel, call_id=ctx.call_id,
                            caller_phone=ctx.caller_phone)


async def record_unanswered(repos: Any, question: str, *, channel: str, call_id: Any = None,
                            caller_phone: Any = None, source: str = "tool") -> bool:
    """Add a question to the owner's "unanswered" list, once per call. Never raises."""
    question = " ".join((question or "").split())[:500]
    if not question:
        return False
    try:
        if call_id and await repos.unanswered.find_one({"call_id": call_id, "question": question}, {"_id": 1}):
            return False
        await repos.unanswered.insert_one({
            "_id": new_id("uq"), "question": question, "qkey": question_key(question),
            "channel": channel, "call_id": call_id, "caller_phone": caller_phone,
            "status": "open", "source": source, "created_at": datetime.now(timezone.utc),
        })
        return True
    except Exception:
        logger.debug("Could not log unanswered question", exc_info=True)
        return False


# What the assistant says when it doesn't know (BASE_VOICE_RULES: "never invent… say someone will call
# back"). A caller turn followed by one of these is a question the knowledge base should answer.
_DONT_KNOW_RE = re.compile(
    r"\b(i\s+don'?t\s+have\s+(that|this|any)\s+(information|info|detail)|i'?m\s+not\s+sure|"
    r"i\s+don'?t\s+know|i\s+can'?t\s+(answer|tell\s+you|confirm)|"
    r"(that|this)\s+(isn'?t|is\s+not)\s+something\s+i\s+(have|know)|"
    r"i\s+(don'?t|do\s+not)\s+have\s+details|someone\s+will\s+(call|get)\s+(you\s+)?back\s+(about|on|with))",
    re.IGNORECASE)


def questions_not_answered(transcript: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    """Caller questions the assistant said it couldn't answer, from a finished conversation."""
    found: List[str] = []
    last_user = ""
    for item in transcript:
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        if item.get("role") == "user":
            last_user = text
        elif last_user and _DONT_KNOW_RE.search(text):
            if last_user not in found:
                found.append(last_user[:500])
            last_user = ""
        else:
            last_user = ""
        if len(found) >= limit:
            break
    return found
