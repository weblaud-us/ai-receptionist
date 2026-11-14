"""
Clean LLM text before it is spoken (Roadmap Task 1.5).

LiveKit's built-in transforms already drop markdown and emoji; this covers what they don't:
web addresses, '&', bullets and stray symbols that TTS would read aloud ("asterisk", "hash").
Number reading is left to ElevenLabs' apply_text_normalization="auto".

The text arrives as a token stream, so every rule must be safe on a partial chunk.
"""

from __future__ import annotations

import re
from typing import AsyncIterable, AsyncIterator

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_SYMBOLS_RE = re.compile(r"[*#_`~|<>\[\]{}•▪◦●]")


def clean_for_speech(text: str) -> str:
    if not text:
        return text
    text = _URL_RE.sub("our website", text)
    text = text.replace("&", " and ")
    text = _SYMBOLS_RE.sub(" ", text)
    # Collapse runs of spaces we introduced, but keep a single leading/trailing space: chunk
    # boundaries carry the word spacing ("Let" + " me" + " check").
    return re.sub(r" {2,}", " ", text)


async def clean_stream(text: AsyncIterable[str]) -> AsyncIterator[str]:
    async for chunk in text:
        cleaned = clean_for_speech(chunk)
        if cleaned:
            yield cleaned
