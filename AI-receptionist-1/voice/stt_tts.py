"""
Speech-to-Text (STT) and Text-to-Speech (TTS)
STT  : OpenAI Whisper
TTS  : ElevenLabs ONLY  (output_format="ulaw_8000" → no ffmpeg, no audioop)
"""
import logging
import tempfile
import os
from abc import ABC, abstractmethod
from typing import Optional, AsyncIterator

from config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# STT — Speech to Text
# ─────────────────────────────────────────────

class BaseSTT(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, language: str = "en") -> str:
        pass


class OpenAIWhisperSTT(BaseSTT):
    """OpenAI Whisper — best accuracy for hotel conversations."""

    def __init__(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        logger.info("🎙 STT: OpenAI Whisper (whisper-1)")

    async def transcribe(self, audio_bytes: bytes, language: str = "en") -> str:
        if not audio_bytes or len(audio_bytes) < 100:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            with open(tmp, "rb") as af:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=af,
                    language=language,
                    response_format="text",
                )
            text = (transcript or "").strip()
            logger.debug("🎙 STT result (%d chars): %s", len(text), text[:80])  # transcripts: DEBUG only
            return text
        except Exception as e:
            logger.error("Whisper STT error: %s", e, exc_info=True)
            return ""
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass


# ─────────────────────────────────────────────
# TTS — ElevenLabs Only
# ─────────────────────────────────────────────

class BaseTTS(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Return raw 8kHz mulaw bytes ready for Twilio."""
        pass

    async def prewarm(self, phrases: list[str]) -> dict[str, bytes]:
        """Pre-synthesise a list of short phrases. Returns {text: mulaw_bytes}."""
        cache: dict[str, bytes] = {}
        for phrase in phrases:
            try:
                cache[phrase] = await self.synthesize(phrase)
                logger.info("🔥 Pre-warmed TTS: %s", phrase[:60])
            except Exception as e:
                logger.warning("Prewarm failed for '%s': %s", phrase, e)
        return cache

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        audio = await self.synthesize(text)
        chunk_size = 4096
        for i in range(0, len(audio), chunk_size):
            yield audio[i:i + chunk_size]


class ElevenLabsTTS(BaseTTS):
    """
    ElevenLabs TTS — primary and only voice provider.
    output_format='ulaw_8000' → returns 8kHz mulaw directly.
    No ffmpeg. No audioop. Zero conversion overhead.
    """

    def __init__(self):
        from elevenlabs import AsyncElevenLabs
        if not settings.ELEVENLABS_API_KEY:
            raise RuntimeError("ELEVENLABS_API_KEY is not set in .env")
        if not settings.ELEVENLABS_VOICE_ID:
            raise RuntimeError("ELEVENLABS_VOICE_ID is not set in .env")
        self.client   = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
        self.voice_id = settings.ELEVENLABS_VOICE_ID
        logger.info("🔊 TTS: ElevenLabs (voice_id=%s)", self.voice_id)

    async def synthesize(self, text: str) -> bytes:
        """Returns 8kHz mulaw bytes — Twilio ready, no conversion needed."""
        if not text:
            return b""
        try:
            audio_iter = self.client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                model_id="eleven_turbo_v2",
                output_format="ulaw_8000",   # ← mulaw directly, no ffmpeg ever
            )
            chunks = []
            async for chunk in audio_iter:
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception as e:
            logger.error("ElevenLabs TTS error: %s", e, exc_info=True)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        if not text:
            return
        try:
            async for chunk in self.client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                model_id="eleven_turbo_v2",
                output_format="ulaw_8000",
            ):
                yield chunk
        except Exception as e:
            logger.error("ElevenLabs stream error: %s", e)


# ─────────────────────────────────────────────
# Factories — singletons
# ─────────────────────────────────────────────

_stt_instance: Optional[BaseSTT] = None
_tts_instance: Optional[BaseTTS] = None


def get_stt() -> BaseSTT:
    global _stt_instance
    if _stt_instance is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is required for STT (OpenAI Whisper). "
                "Set it in your .env file."
            )
        _stt_instance = OpenAIWhisperSTT()
    return _stt_instance


def get_tts() -> BaseTTS:
    global _tts_instance
    if _tts_instance is None:
        _tts_instance = ElevenLabsTTS()
    return _tts_instance
