"""
Clean LLM text before it is spoken (Roadmap Task 1.5, plan A2.6).

LiveKit's built-in transforms already drop markdown and emoji; this covers what they don't:
web addresses, '&', bullets and stray symbols that TTS would read aloud ("asterisk", "hash").

Numbers are spoken the way a receptionist says them, whatever the voice. Deepgram Aura (the
default for AU / UK practices) has no text normalisation of its own, and the output below reads
just as well on ElevenLabs:
    10:30am     → "10 30 a.m."          14:00  → "2 p.m."
    $250 / £85  → "250 dollars" / "85 pounds"      $49.50 → "49 dollars and 50 cents"
    0412 345 678 → "0 4 1 2, 3 4 5, 6 7 8"         +44 20 7946 0958 → "plus 4 4, 2 0, ..."

The text arrives as a token stream, so cleaning is safe on any partial chunk, and number
normalisation holds back only a trailing run that could still be part of a number ("10", ":30",
" am") until the next non-number text arrives: about one token of delay, and only around digits.
"""

from __future__ import annotations

import re
from typing import AsyncIterable, AsyncIterator, List

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


# ── Numbers ──────────────────────────────────────────────────────────────────

_CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"),
             "€": ("euro", "euros", "cent", "cents")}
_PRICE_RE = re.compile(r"(?<![\w$£€])(?:AU|A|US|NZ)?([$£€])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?![\d,])")
_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?:\s?([aApP])\.?\s?[mM]\.?)?(?![\d:])")
_AMPM_RE = re.compile(r"(?<![\d:])(1[0-2]|0?[1-9])\s?([aApP])\.?\s?[mM]\.?(?![a-zA-Z])")
_PHONE_RE = re.compile(r"(?<![\w$£€.,:])\+?\d(?:[ \-]?\d){7,14}(?![\w])")

# A trailing run that may still grow into one of the above: hold it back.
_HOLD_RE = re.compile(
    r"(?:(?:AU|A|US|NZ)?[$£€]|\+|\b(?:AU|A|US|NZ))?\d[\d ,.:\-]*(?:\s?[aApP]\.?\s?(?:[mM]\.?)?)?\s?$"
    r"|(?:(?:AU|A|US|NZ)?[$£€]|\+)\s?$")
MAX_HOLD = 40


def _price(m: re.Match) -> str:
    one, many, sub_one, sub_many = _CURRENCY[m.group(1)]
    whole = int(m.group(2).replace(",", ""))
    out = f"{whole} {one if whole == 1 else many}"
    cents = int(m.group(3) or 0)
    if cents:
        out += f" and {cents} {sub_one if cents == 1 else sub_many}"
    return out


def _time(m: re.Match) -> str:
    hour, minute, half = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
    if not half:
        if hour == 0:
            hour, half = 12, "a"
        elif hour >= 13:
            hour, half = hour - 12, "p"
        elif hour == 12:
            half = "p"
    mins = "" if minute == "00" else (f" oh {int(minute)}" if minute.startswith("0") else f" {minute}")
    suffix = f" {half}.m." if half else ("" if mins else " o'clock")
    return f"{hour}{mins}{suffix}"


def _ampm(m: re.Match) -> str:
    return f"{int(m.group(1))} {m.group(2).lower()}.m."


def _digit_groups(raw: str) -> List[str]:
    groups = [g for g in re.split(r"[ \-]", raw.lstrip("+")) if g]
    if len(groups) == 1:                      # one unbroken run: group it the way people read it
        d = groups[0]
        if len(d) == 10:
            return [d[:4], d[4:7], d[7:]]     # AU mobile 0412 345 678, UK landline-ish
        if len(d) == 11:
            return [d[:5], d[5:8], d[8:]]     # UK 07700 900 123
        return [d[i:i + 3] for i in range(0, len(d), 3)]
    return groups


def _phone(m: re.Match) -> str:
    raw = m.group(0)
    spoken = ", ".join(" ".join(g) for g in _digit_groups(raw))
    return ("plus " if raw.startswith("+") else "") + spoken


def normalize_numbers(text: str) -> str:
    """Times, prices and phone numbers in words a TTS reads naturally. Complete text only."""
    if not text or not any(c.isdigit() for c in text):
        return text
    text = _PRICE_RE.sub(_price, text)
    text = _TIME_RE.sub(_time, text)
    text = _AMPM_RE.sub(_ampm, text)
    return _PHONE_RE.sub(_phone, text)


class NumberBuffer:
    """Feed it stream chunks; it returns what is safe to speak now and keeps a possible number."""

    def __init__(self) -> None:
        self._buf = ""

    def push(self, chunk: str) -> str:
        self._buf += chunk
        m = _HOLD_RE.search(self._buf)
        if m and len(self._buf) - m.start() <= MAX_HOLD:
            ready, self._buf = self._buf[:m.start()], self._buf[m.start():]
        else:
            ready, self._buf = self._buf, ""
        return normalize_numbers(ready)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return normalize_numbers(rest)


async def clean_stream(text: AsyncIterable[str]) -> AsyncIterator[str]:
    numbers = NumberBuffer()
    async for chunk in text:
        ready = numbers.push(clean_for_speech(chunk))
        if ready:
            yield ready
    rest = numbers.flush()
    if rest:
        yield rest
