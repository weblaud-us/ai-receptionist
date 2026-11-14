"""
The business a call is for, as plain data — built once at call start, read from memory after.

Phase 1 is single-tenant: the profile comes from settings. Phase 2 replaces `load_profile()`
with a lookup of the tenant that owns the dialled number (`sip.trunkPhoneNumber`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from config import settings
from core.timeutils import business_now
from database.tenancy import get_hotel_profile

_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class VoiceProfile:
    business_id: str
    business_name: str
    business_type: str
    agent_name: str
    address: str
    phone: str
    timezone: str
    currency: str
    checkin_time: str
    checkout_time: str
    stt_language: str
    tts_language: str
    llm_model: str
    voice_id: str
    transfer_number: str = ""
    transfer_hours: str = ""
    recording: bool = False
    keyterms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def greeting_text(self) -> str:
        # ⚖ AI disclosure in the first sentence (EU AI Act, US state bot laws, UAE TDRA). Keep ≤ 4 s.
        text = (f"Thanks for calling {self.business_name}, this is {self.agent_name}, "
                f"the hotel's virtual assistant.")
        if self.recording:
            text += " This call may be recorded."
        return text + " How can I help?"

    def transfer_open(self, now: Optional[datetime] = None) -> bool:
        """True when transfer_to_human may ring staff right now (business-local time)."""
        if not self.transfer_number:
            return False
        hours = parse_hours(self.transfer_hours)
        if hours is None:
            return True
        start, end = hours
        local = business_now(self.timezone, now).time()
        if start <= end:
            return start <= local < end
        return local >= start or local < end      # overnight window, e.g. 20:00-06:00


def parse_hours(spec: str) -> Optional[tuple[time, time]]:
    """'09:00-21:00' → (09:00, 21:00). Empty or malformed → None (no restriction)."""
    m = _HOURS_RE.match(spec or "")
    if not m:
        return None
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    if h2 == 24 and m2 == 0:
        h2, m2 = 23, 59
    try:
        return time(h1, m1), time(h2, m2)
    except ValueError:
        return None


def _language_base(language: str) -> str:
    """'en-US' → 'en'; 'multi' → 'en' (TTS needs a concrete language)."""
    base = (language or "en").split("-")[0].lower()
    return "en" if base == "multi" else base


def load_profile(dialed_number: Optional[str] = None) -> VoiceProfile:
    """Phase 1: the single hotel. `dialed_number` becomes the tenant key in Phase 2."""
    hotel = get_hotel_profile()
    return VoiceProfile(
        business_id=hotel["hotel_id"],
        business_name=hotel["name"],
        business_type="hotel",
        agent_name=hotel["receptionist_name"],
        address=hotel["address"],
        phone=hotel["phone"],
        timezone=hotel["timezone"],
        currency=hotel["currency"],
        checkin_time=hotel["checkin_time"],
        checkout_time=hotel["checkout_time"],
        stt_language=settings.VOICE_LANGUAGE,
        tts_language=_language_base(settings.VOICE_LANGUAGE),
        llm_model=settings.VOICE_LLM_MODEL,
        voice_id=settings.ELEVENLABS_VOICE_ID,
        transfer_number=settings.TRANSFER_PHONE_NUMBER.strip(),
        transfer_hours=settings.TRANSFER_HOURS.strip(),
        recording=settings.ENABLE_CALL_RECORDING,
        # Words STT should expect: the business and agent names, and the room types.
        keyterms=(hotel["name"], hotel["receptionist_name"], "deluxe", "suite", "presidential"),
    )
