"""
Post-call summary (Roadmap Task 2.2, `calls.summary`): one or two plain sentences the owner can
read in the dashboard, the daily digest and a `call.completed` webhook.

Runs once, after the call has ended, off the hot path. The LLM is optional: without an API key,
on an error or after SUMMARY_TIMEOUT_S, a deterministic summary built from the outcome and the
tools used is stored instead, so the field is never empty for a real conversation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

SUMMARY_TIMEOUT_S = 8.0
MAX_TRANSCRIPT_CHARS = 6000
MIN_ITEMS = 2

_OUTCOME_TEXT = {
    "booked": "The caller made a booking",
    "rescheduled": "The caller moved an existing booking",
    "cancelled": "The caller cancelled a booking",
    "message": "The caller left a message for the team",
    "transferred": "The call was transferred to a person",
    "faq": "The caller asked a question and was answered",
    "voicemail": "The caller left a voicemail",
    "hangup": "The caller hung up early",
    "spam": "The call looked like spam",
    "completed": "The caller's question was handled",
}

_PROMPT = (
    "Summarise this phone call between a business's AI receptionist and a caller for the business "
    "owner, in one or two plain sentences (at most 45 words). Say what the caller wanted and what "
    "happened (booked, message taken, transferred, question answered, nothing). Include names, "
    "dates and times that were agreed. No preamble, no quotes, no markdown."
)


def heuristic_summary(outcome: Optional[str], tools_used: list[str], transcript: list[dict[str, Any]],
                      duration_s: Optional[float]) -> str:
    text = _OUTCOME_TEXT.get(outcome or "completed", _OUTCOME_TEXT["completed"])
    first_user = next((i.get("text", "") for i in transcript if i.get("role") == "user" and i.get("text")), "")
    if first_user:
        snippet = " ".join(str(first_user).split())[:120]
        text += f'. They opened with: "{snippet}"'
    if duration_s:
        text += f". {round(duration_s / 60) or 1} min"
        if tools_used:
            text += f", tools: {', '.join(dict.fromkeys(tools_used))}"
    return text + "."


def transcript_text(transcript: list[dict[str, Any]], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    lines = []
    for item in transcript:
        role = "Caller" if item.get("role") == "user" else "Assistant"
        text = " ".join(str(item.get("text") or "").split())
        if text:
            lines.append(f"{role}: {text}")
    joined = "\n".join(lines)
    return joined[-limit:] if len(joined) > limit else joined


async def llm_summary(transcript: list[dict[str, Any]], *, model: Optional[str] = None,
                      prompt: Optional[str] = None) -> Optional[str]:
    if not settings.OPENAI_API_KEY:
        return None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, max_retries=0, timeout=SUMMARY_TIMEOUT_S)
    completion = await client.chat.completions.create(
        model=model or settings.OPENAI_MODEL, temperature=0.2, max_tokens=90,
        store=settings.OPENAI_STORE,          # audit P0-06: a clinical transcript is never kept by OpenAI
        messages=[{"role": "system", "content": prompt or _PROMPT},
                  {"role": "user", "content": transcript_text(transcript)}])
    text = (completion.choices[0].message.content or "").strip()
    return " ".join(text.split())[:400] or None


async def summarise_call(transcript: list[dict[str, Any]], *, outcome: Optional[str], tools_used: list[str],
                         duration_s: Optional[float], use_llm: bool = True,
                         prompt: Optional[str] = None) -> Optional[str]:
    """None for calls with no real conversation; otherwise always a sentence or two."""
    spoken = [i for i in transcript if i.get("text")]
    if len(spoken) < MIN_ITEMS:
        return None
    if use_llm and settings.OPENAI_API_KEY:
        try:
            call = llm_summary(spoken, prompt=prompt) if prompt else llm_summary(spoken)
            text = await asyncio.wait_for(call, SUMMARY_TIMEOUT_S)
            text = " ".join((text or "").split())[:400]
            if text:
                return text
        except Exception as e:
            logger.info("Call summary fell back to heuristic (%s)", type(e).__name__)
    return heuristic_summary(outcome, tools_used, spoken, duration_s)
