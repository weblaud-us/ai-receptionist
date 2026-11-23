"""
Voices by provider and country.

Cartesia (Sonic-3) is the platform's main voice; Deepgram Aura sits behind it as the fallback
(ElevenLabs stays available for a business that picks it). Each provider has an accent-matched default for AU and UK practices, so a
practice that never picked a voice — or whose own voice is on another provider — still sounds like
its own front desk, whichever engine is speaking. A voice id is only ever sent to its own provider.

Deepgram voices are listed here: Flux TTS ("flux-<voice>-en", served on /v2/speak) and Aura-2
("aura-2-<voice>-en", /v1/speak). The id is the model name. ⚠ VERIFY both against Deepgram's current
catalogues before a release. Defaults come from settings (CARTESIA_VOICE_ID_AU / _GB,
DEEPGRAM_TTS_MODEL[_AU / _GB], ELEVENLABS_VOICE_ID_AU / _GB), picked by ear with scripts/voice_bakeoff.py.
"""

from __future__ import annotations

from typing import Any, Dict, List

from config import settings

AURA_VOICES: List[Dict[str, Any]] = [
    {"voice_id": "aura-2-theia-en", "name": "Theia", "gender": "female", "accent": "Australian",
     "description": "Expressive, polite, sincere"},
    {"voice_id": "aura-2-hyperion-en", "name": "Hyperion", "gender": "male", "accent": "Australian",
     "description": "Caring, warm, empathetic"},
    {"voice_id": "aura-2-pandora-en", "name": "Pandora", "gender": "female", "accent": "British",
     "description": "Smooth, calm, melodic"},
    {"voice_id": "aura-2-draco-en", "name": "Draco", "gender": "male", "accent": "British",
     "description": "Warm, approachable, trustworthy"},
    {"voice_id": "aura-2-andromeda-en", "name": "Andromeda", "gender": "female", "accent": "American",
     "description": "Casual, expressive, comfortable"},
    {"voice_id": "aura-2-thalia-en", "name": "Thalia", "gender": "female", "accent": "American",
     "description": "Clear, confident, energetic"},
    {"voice_id": "aura-2-apollo-en", "name": "Apollo", "gender": "male", "accent": "American",
     "description": "Confident, comfortable, casual"},
    {"voice_id": "aura-2-helena-en", "name": "Helena", "gender": "female", "accent": "American",
     "description": "Caring, natural, friendly"},
]

# Flux TTS voices (Deepgram's conversation-native TTS, /v2/speak). From Deepgram's playground; the
# catalogue has 36 English voices — add more as you pick them.
FLUX_VOICES: List[Dict[str, Any]] = [
    {"voice_id": "flux-hannah-en", "name": "Hannah (Flux)", "gender": "female", "accent": "American",
     "description": "Warm, natural, conversational"},
    {"voice_id": "flux-alexis-en", "name": "Alexis (Flux)", "gender": "female", "accent": "American",
     "description": "Clear, friendly"},
    {"voice_id": "flux-sienna-en", "name": "Sienna (Flux)", "gender": "female", "accent": "American",
     "description": "Bright, upbeat"},
    {"voice_id": "flux-kit-en", "name": "Kit (Flux)", "gender": "male", "accent": "British",
     "description": "Easy-going, natural"},
    {"voice_id": "flux-colin-en", "name": "Colin (Flux)", "gender": "male", "accent": "British",
     "description": "Calm, reassuring"},
    {"voice_id": "flux-cliff-en", "name": "Cliff (Flux)", "gender": "male", "accent": "American",
     "description": "Steady, grounded"},
    {"voice_id": "flux-cole-en", "name": "Cole (Flux)", "gender": "male", "accent": "American",
     "description": "Relaxed, confident"},
]

# The Aura voice a practice in this country had before Flux (scripts/upgrade_voices.py treats it as
# "nobody chose it").
AURA_DEFAULT_FOR_COUNTRY = {"AU": "aura-2-theia-en", "GB": "aura-2-pandora-en"}


def aura_catalogue() -> List[Dict[str, Any]]:
    """Every Deepgram voice for the dashboard picker: Flux first, then Aura (no hosted previews)."""
    return ([{**v, "preview_url": None, "category": "flux"} for v in FLUX_VOICES]
            + [{**v, "preview_url": None, "category": "aura"} for v in AURA_VOICES])


def is_flux_tts(voice_id: str) -> bool:
    """A Flux TTS voice ("flux-<voice>-<lang>"); not the Flux speech-to-text models (flux-general-*)."""
    v = voice_id or ""
    return v.startswith("flux-") and not v.startswith("flux-general")


def is_aura_model(voice_id: str) -> bool:
    """A Deepgram voice id of either family (Aura or Flux TTS): the model name is the voice."""
    return (voice_id or "").startswith("aura-") or is_flux_tts(voice_id)


def default_voice(provider: str, country: str = "") -> str:
    """The voice a practice in `country` gets on `provider` when it has none of its own there.
    "" = let the provider use its own default."""
    c = (country or "").upper()
    if provider == "elevenlabs":
        by_country = {"AU": settings.ELEVENLABS_VOICE_ID_AU, "GB": settings.ELEVENLABS_VOICE_ID_GB}
        return by_country.get(c) or settings.ELEVENLABS_VOICE_ID
    if provider == "cartesia":
        by_country = {"AU": settings.CARTESIA_VOICE_ID_AU, "GB": settings.CARTESIA_VOICE_ID_GB}
        return by_country.get(c) or settings.CARTESIA_VOICE_ID
    if provider == "deepgram":
        by_country = {"AU": settings.DEEPGRAM_TTS_MODEL_AU, "GB": settings.DEEPGRAM_TTS_MODEL_GB}
        return by_country.get(c) or settings.DEEPGRAM_TTS_MODEL
    return ""


def voice_for(provider: str, own_provider: str, own_voice_id: str, country: str = "") -> str:
    """The voice to send `provider`: the business's own choice when it was made for this provider,
    else the country's default there. Never another provider's id (an Aura model name sent to
    ElevenLabs, or an ElevenLabs id sent to Cartesia, fails the call's voice)."""
    own = (own_voice_id or "").strip()
    # Deepgram ids are model names ("aura-2-…", "flux-…"); anything else is another provider's id.
    if own and own_provider == provider and is_aura_model(own) == (provider == "deepgram"):
        return own
    return default_voice(provider, country)
