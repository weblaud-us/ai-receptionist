"""
Deepgram Aura voices a business can pick (plan A2.5). Aura runs on the same key as the speech-to-text,
answers in roughly 100 ms, and costs a fraction of ElevenLabs, so it is the default for new AU / UK
dental practices, in an accent that sounds like their own front desk.

The voice id is the Aura model name. ⚠ VERIFY against Deepgram's current Aura-2 catalogue before a
release (names and accents are taken from Deepgram's voice list; there are no hosted previews).
"""

from __future__ import annotations

from typing import Any, Dict, List

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

# The voice a new practice starts with, by country.
AURA_DEFAULT_FOR_COUNTRY = {"AU": "aura-2-theia-en", "GB": "aura-2-pandora-en"}


def aura_catalogue() -> List[Dict[str, Any]]:
    return [{**v, "preview_url": None, "category": "aura"} for v in AURA_VOICES]


def is_aura_model(voice_id: str) -> bool:
    return (voice_id or "").startswith("aura-")
