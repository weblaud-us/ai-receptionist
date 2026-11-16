"""
ElevenLabs voice catalogue, for the dashboard's voice picker (Roadmap Task 2.10, "voice pick
with audio previews"). One request per hour; the result is shared by every tenant.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_S = 3600
MAX_VOICES = 80
_cache: Dict[str, Any] = {"at": 0.0, "voices": None}


def configured() -> bool:
    return bool((settings.ELEVENLABS_API_KEY or "").strip())


def reset_cache() -> None:
    _cache.update(at=0.0, voices=None)


def _shape(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    voice_id, name = raw.get("voice_id"), raw.get("name")
    if not voice_id or not name:
        return None
    labels = raw.get("labels") or {}
    return {
        "voice_id": str(voice_id),
        "name": str(name)[:80],
        "preview_url": raw.get("preview_url") or None,
        "gender": labels.get("gender"),
        "accent": labels.get("accent"),
        "age": labels.get("age"),
        "description": (labels.get("description") or raw.get("description") or "")[:120] or None,
        "category": raw.get("category"),
    }


async def fetch_voices() -> List[Dict[str, Any]]:
    """GET /v1/voices — the account's library (premade + cloned). Raises on HTTP errors."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://api.elevenlabs.io/v1/voices",
                                headers={"xi-api-key": settings.ELEVENLABS_API_KEY.strip()})
    resp.raise_for_status()
    voices = [v for v in (_shape(r) for r in resp.json().get("voices", []) if isinstance(r, dict)) if v]
    voices.sort(key=lambda v: (v["category"] != "cloned", v["name"].lower()))
    return voices[:MAX_VOICES]


async def list_voices(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Cached catalogue; an empty list when ElevenLabs isn't configured or is unreachable."""
    if not configured():
        return []
    now = now if now is not None else time.time()
    if _cache["voices"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return list(_cache["voices"])
    try:
        voices = await fetch_voices()
    except Exception:
        logger.warning("ElevenLabs voice list failed", exc_info=True)
        return list(_cache["voices"] or [])
    _cache.update(at=now, voices=voices)
    return list(voices)
