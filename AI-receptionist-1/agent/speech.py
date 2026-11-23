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

import logging
import re
from typing import AsyncIterable, AsyncIterator, Callable, List, Optional

logger = logging.getLogger(__name__)

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


# ── Clinical guard (audit VOICE-05) ──────────────────────────────────────────
# The prompt forbids medicines, doses and diagnoses; this is the last line of defence if the model
# says one anyway. It holds back only the last couple of words (no sentence buffering, so no added
# latency), and on a hit says one safe line instead and drops the rest of that sentence.

_MEDICINES = (r"ibuprofen|paracetamol|acetaminophen|panadol|panadeine|nurofen|advil|aspirin|codeine|tramadol|"
              r"oxycodone|amoxicillin|amoxycillin|augmentin|metronidazole|penicillin|clindamycin|antibiotics?|"
              r"painkillers?|pain ?killers?|anti-?inflammator(?:y|ies)|orajel|bonjela|clove oil")
_CONDITIONS = r"abscess|infection|infected|cavity|cavities|decay|gum disease|periodontitis|gingivitis|a crack|cracked|dry socket|pulpitis"
_GUARD_RE = re.compile(
    rf"\b(?:{_MEDICINES})\b"
    r"|\b\d+(?:\.\d+)?\s?(?:mg|milligrams?|mcg|micrograms?|ml|millilitres?|milliliters?)\b"
    r"|\b(?:\d+|one|two|three|four)\s(?:tablets?|capsules?|pills?)\b"
    rf"|\b(?:you(?:'ve| have)?|it(?:'s| is)|that(?:'s| is)|sounds like|could be|might be|probably)\s+"
    rf"(?:(?:probably|likely|just|definitely|possibly|got|have|has|an?|be|like)\s+){{0,3}}(?:{_CONDITIONS})\b",
    re.I)
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
SAFE_LINE = " The dentist will advise you on that when they see you."
GUARD_WINDOW_WORDS = 6          # the longest phrase: "you have probably got an abscess"
_WORD_RE = re.compile(r"\S+")
_STARTER_RE = re.compile(r"^(?:you(?:'ve)?|it(?:'s)?|that(?:'s)?|sounds|could|might|probably|take|"
                         r"\d[\d.,]*|one|two|three|four)$", re.I)


def _hold_from(buf: str) -> int:
    """Where to stop releasing text: the earliest word in the window that could start a match, or
    the last word if it may still be growing ("ibu" → "ibuprofen"). Everything else goes now."""
    words = list(_WORD_RE.finditer(buf))
    if not words:
        return len(buf)
    hold = len(buf) if buf[-1].isspace() else words[-1].start()
    for w in words[-GUARD_WINDOW_WORDS:]:
        if _STARTER_RE.match(w.group(0).strip(".,!?;:")):
            return min(hold, w.start())
    return hold


class ClinicalGuard:
    """Feed it stream chunks; it replaces medicine / dose / diagnosis talk with SAFE_LINE."""

    def __init__(self, on_hit: Optional[Callable[[str], None]] = None) -> None:
        self._buf = ""
        self._skipping = False
        self._on_hit = on_hit

    def _scan(self, final: bool) -> str:
        out = ""
        while True:
            if self._skipping:
                end = _SENTENCE_END_RE.search(self._buf)
                if not end:
                    if final:
                        self._buf = ""
                    return out
                self._buf = self._buf[end.end():]
                self._skipping = False
                out += " " if self._buf and not self._buf.startswith(" ") else ""
                continue
            m = _GUARD_RE.search(self._buf)
            if m:
                logger.warning("Clinical guard replaced speech (matched %r)", m.group(0)[:40])
                if self._on_hit:
                    self._on_hit(m.group(0))
                start = max(self._buf.rfind(".", 0, m.start()), self._buf.rfind("?", 0, m.start()),
                            self._buf.rfind("!", 0, m.start()))
                out += self._buf[:start + 1] + SAFE_LINE   # what came before this sentence, then the safe line
                self._buf = self._buf[m.end():]
                self._skipping = True
                continue
            if final:
                out, self._buf = out + self._buf, ""
                return out
            # Release everything except what could still turn into a match: from the earliest
            # trigger word in the last few words ("you…", "sounds…", "400…"), or an unfinished word.
            cut = _hold_from(self._buf)
            out, self._buf = out + self._buf[:cut], self._buf[cut:]
            return out

    def push(self, chunk: str) -> str:
        self._buf += chunk
        return self._scan(final=False)

    def flush(self) -> str:
        return self._scan(final=True)


async def clean_stream(text: AsyncIterable[str], on_guard: Optional[Callable[[str], None]] = None
                       ) -> AsyncIterator[str]:
    guard = ClinicalGuard(on_guard)
    numbers = NumberBuffer()
    async for chunk in text:
        ready = numbers.push(guard.push(clean_for_speech(chunk)))
        if ready:
            yield ready
    rest = numbers.push(guard.flush()) + numbers.flush()
    if rest:
        yield rest
