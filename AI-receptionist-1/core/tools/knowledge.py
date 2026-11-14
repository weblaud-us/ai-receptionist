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
from typing import TYPE_CHECKING, Any, Dict, List

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


def rank_items(items: List[Dict[str, Any]], question: str, n: int = 3) -> List[Dict[str, Any]]:
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
    return [it for _, it in scored[:n]]


def fits_inline(items: List[Dict[str, Any]]) -> bool:
    total = sum(len(i.get("question", "")) + len(i.get("answer", "")) for i in items)
    return len(items) <= KB_INLINE_MAX_ITEMS and total <= KB_INLINE_MAX_CHARS


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
    try:
        await ctx.repos().unanswered.insert_one({
            "question": question[:500], "channel": ctx.channel, "call_id": ctx.call_id,
            "caller_phone": ctx.caller_phone, "status": "open", "created_at": datetime.now(timezone.utc),
        })
    except Exception:
        logger.debug("Could not log unanswered question", exc_info=True)
