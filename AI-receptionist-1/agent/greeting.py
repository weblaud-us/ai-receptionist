"""
Pre-rendered greeting (Roadmap Task 1.6.a): the caller hears a voice within ~0.5 s of answering.

A cold TTS request can take ~2 s, so the greeting is synthesised once and kept as raw PCM —
in memory per process, on disk (temp_audio/greetings/) across processes on one machine, and in
Redis (Phase 3, Task 3.4) across every worker container, so a freshly deployed worker plays the
cached greeting on its first call. The cache key covers text, provider, model and voice, so
changing any of them re-renders. The first call after a change streams the greeting live and
renders the cache for the next.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from livekit import rtc

from core import redis as redis_store

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "temp_audio" / "greetings"
FRAME_MS = 20
SHARED_TTL_S = 30 * 24 * 3600
SHARED_TIMEOUT_S = 0.25          # the caller is waiting: past this, stream the greeting live
MAX_SHARED_BYTES = 2_000_000     # ~40 s of 24 kHz mono; anything bigger isn't a greeting

_memory: dict[str, tuple[bytes, int, int]] = {}   # key → (pcm int16, sample_rate, channels)
_rendering: set[str] = set()


def cache_key(text: str, tts: Any, voice: str) -> str:
    ident = json.dumps([text, getattr(tts, "provider", type(tts).__module__),
                        getattr(tts, "model", ""), voice, getattr(tts, "sample_rate", 0)])
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


def _paths(key: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{key}.pcm", CACHE_DIR / f"{key}.json"


def load(key: str) -> Optional[tuple[bytes, int, int]]:
    if key in _memory:
        return _memory[key]
    pcm_path, meta_path = _paths(key)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        entry = (pcm_path.read_bytes(), int(meta["sample_rate"]), int(meta["channels"]))
    except (OSError, ValueError, KeyError):
        return None
    _memory[key] = entry
    return entry


def _store(key: str, pcm: bytes, sample_rate: int, channels: int) -> None:
    _memory[key] = (pcm, sample_rate, channels)
    pcm_path, meta_path = _paths(key)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pcm_path.write_bytes(pcm)
        meta_path.write_text(json.dumps({"sample_rate": sample_rate, "channels": channels}), encoding="utf-8")
    except OSError:
        logger.warning("Could not write greeting cache to %s", CACHE_DIR, exc_info=True)


def _shared_key(key: str) -> str:
    return f"greeting:{key}"


async def load_shared(key: str) -> Optional[tuple[bytes, int, int]]:
    """The greeting another worker rendered (Redis). None if missing, too slow, or no Redis."""
    r = redis_store.get_redis()
    if r is None:
        return None
    try:
        raw = await asyncio.wait_for(r.get(_shared_key(key)), SHARED_TIMEOUT_S)
        if not raw:
            return None
        meta = json.loads(raw)
        entry = (base64.b64decode(meta["pcm"]), int(meta["sample_rate"]), int(meta["channels"]))
    except Exception:
        logger.debug("Shared greeting cache unavailable", exc_info=True)
        return None
    _store(key, *entry)                  # memory + disk for the next call on this machine
    return entry


async def _store_shared(key: str, pcm: bytes, sample_rate: int, channels: int) -> None:
    r = redis_store.get_redis()
    if r is None or len(pcm) > MAX_SHARED_BYTES:
        return
    try:
        value = json.dumps({"pcm": base64.b64encode(pcm).decode("ascii"), "sample_rate": sample_rate,
                            "channels": channels})
        await r.set(_shared_key(key), value, ex=SHARED_TTL_S)
    except Exception:
        redis_store.warn_unavailable("greeting cache")


async def frames(entry: tuple[bytes, int, int]) -> AsyncIterator[rtc.AudioFrame]:
    """Replay cached PCM as 20 ms frames."""
    pcm, sample_rate, channels = entry
    samples = sample_rate * FRAME_MS // 1000
    step = samples * channels * 2
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        n = len(chunk) // (channels * 2)
        if n:
            yield rtc.AudioFrame(chunk[: n * channels * 2], sample_rate, channels, n)


async def render(key: str, text: str, tts: Any) -> None:
    """Synthesise `text` and cache it. Safe to fire and forget; one render per key at a time."""
    if key in _rendering or load(key) is not None:
        return
    _rendering.add(key)
    try:
        parts: list[bytes] = []
        sample_rate = channels = 0
        async with tts.synthesize(text) as stream:
            async for ev in stream:
                frame = ev.frame
                sample_rate, channels = frame.sample_rate, frame.num_channels
                parts.append(bytes(frame.data))  # int16 PCM, raw bytes
        if parts and sample_rate:
            pcm = b"".join(parts)
            _store(key, pcm, sample_rate, channels)
            await _store_shared(key, pcm, sample_rate, channels)
            logger.info("Greeting cached (%s)", key)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Greeting render failed; it will be streamed live", exc_info=True)
    finally:
        _rendering.discard(key)
