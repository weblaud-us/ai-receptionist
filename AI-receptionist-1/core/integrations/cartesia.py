"""
Cartesia voice catalogue, for the dashboard's voice picker and scripts/voice_bakeoff.py. One fetch per
hour, shared by every tenant. Cartesia has no accent field, so the accent comes from the voice's name
and description ("Australian", "British"…).

⚠ VERIFY the response shape against Cartesia's current API (GET /voices, Cartesia-Version below): it is
read defensively (a bare list, or {"data": [...], "has_more": …}).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

API = "https://api.cartesia.ai"
VERSION = "2025-04-16"           # the version the LiveKit plugin speaks
CACHE_TTL_S = 3600
MAX_PAGES = 5
_cache: Dict[str, Any] = {"at": 0.0, "voices": None}

ACCENTS = {
    "Australian": ("australian", "aussie"),
    "British": ("british", "english", "london", "uk "),
    "American": ("american", "us "),
}


def configured() -> bool:
    return bool((settings.CARTESIA_API_KEY or "").strip())


def reset_cache() -> None:
    _cache.update(at=0.0, voices=None)


def accent_of(name: str, description: str) -> Optional[str]:
    text = f" {name} {description} ".lower()
    return next((accent for accent, words in ACCENTS.items() if any(w in text for w in words)), None)


def _shape(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    voice_id, name = raw.get("id"), raw.get("name")
    if not voice_id or not name:
        return None
    language = str(raw.get("language") or "")
    if language and not language.startswith("en"):
        return None
    description = str(raw.get("description") or "")
    return {
        "voice_id": str(voice_id),
        "name": str(name)[:80],
        "preview_url": raw.get("preview_file_url") or raw.get("preview_url") or None,
        "gender": raw.get("gender"),
        "accent": accent_of(str(name), description),
        "description": description[:120] or None,
        "category": "cartesia",
    }


async def fetch_voices() -> List[Dict[str, Any]]:
    """English voices the account can use (public library + its own). Raises on HTTP errors."""
    headers = {"X-API-Key": settings.CARTESIA_API_KEY.strip(), "Cartesia-Version": VERSION}
    out: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {"limit": 100}
    async with httpx.AsyncClient(timeout=15) as client:
        for _ in range(MAX_PAGES):
            resp = await client.get(f"{API}/voices", headers=headers, params=params)
            resp.raise_for_status()
            body = resp.json()
            rows = body if isinstance(body, list) else body.get("data") or []
            out += [v for v in (_shape(r) for r in rows if isinstance(r, dict)) if v]
            if isinstance(body, list) or not body.get("has_more") or not rows:
                break
            params["starting_after"] = body.get("next_page") or rows[-1].get("id")
    out.sort(key=lambda v: (v["accent"] is None, v["accent"] or "", v["name"].lower()))
    return out


async def list_voices(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Cached catalogue; empty when Cartesia isn't configured or is unreachable."""
    if not configured():
        return []
    now = now if now is not None else time.time()
    if _cache["voices"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return list(_cache["voices"])
    try:
        voices = await fetch_voices()
    except Exception:
        logger.warning("Cartesia voice list failed", exc_info=True)
        return list(_cache["voices"] or [])
    _cache.update(at=now, voices=voices)
    return list(voices)
