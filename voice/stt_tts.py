"""
Speech-to-Text (STT) and Text-to-Speech (TTS)
OpenAI Whisper STT + ElevenLabs TTS only.
Local/offline models removed for clean production code.
"""
import io
import logging
import asyncio
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
            logger.info("🎙 STT result (%d chars): %s", len(text), text[:80])
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
# TTS — Text to Speech
# ─────────────────────────────────────────────

class BaseTTS(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        pass

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        audio = await self.synthesize(text)
        chunk_size = 4096
        for i in range(0, len(audio), chunk_size):
            yield audio[i:i + chunk_size]


class ElevenLabsTTS(BaseTTS):
    """ElevenLabs — most natural human-like voice."""

    def __init__(self):
        from elevenlabs import AsyncElevenLabs
        self.client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
        self.voice_id = settings.ELEVENLABS_VOICE_ID
        logger.info("🔊 TTS: ElevenLabs (voice: %s)", self.voice_id)

    async def synthesize(self, text: str) -> bytes:
        if not text:
            return b""
        try:
            audio = self.client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                model_id="eleven_turbo_v2",
                output_format="ulaw_8000",
            )
            chunks = []
            async for chunk in audio:
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception as e:
            logger.error("ElevenLabs TTS error: %s", e, exc_info=True)
            # Fallback to OpenAI TTS if ElevenLabs fails
            return await _openai_tts_fallback(text)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
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
            audio = await _openai_tts_fallback(text)
            yield audio


class OpenAITTS(BaseTTS):
    """OpenAI TTS — fallback when ElevenLabs is not configured."""

    def __init__(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.voice = getattr(settings, "OPENAI_TTS_VOICE", "shimmer")
        self.model = getattr(settings, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        logger.info("🔊 TTS: OpenAI (%s / %s)", self.model, self.voice)

    async def synthesize(self, text: str) -> bytes:
        if not text:
            return b""
        try:
            response = await self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
                response_format="mp3",
            )
            return response.content
        except Exception as e:
            logger.error("OpenAI TTS error: %s", e, exc_info=True)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        async with self.client.audio.speech.with_streaming_response.create(
            model=self.model, voice=self.voice, input=text, response_format="mp3",
        ) as r:
            async for chunk in r.iter_bytes(chunk_size=4096):
                yield chunk


async def _openai_tts_fallback(text: str) -> bytes:
    """Emergency fallback using OpenAI TTS."""
    if not settings.OPENAI_API_KEY:
        return b""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        voice = getattr(settings, "OPENAI_TTS_VOICE", "shimmer")
        model = getattr(settings, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        response = await client.audio.speech.create(
            model=model, voice=voice, input=text, response_format="mp3",
        )
        return response.content
    except Exception as e:
        logger.error("OpenAI TTS fallback failed: %s", e)
        return b""


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
        if settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_VOICE_ID:
            try:
                _tts_instance = ElevenLabsTTS()
            except (ModuleNotFoundError, ImportError) as e:
                if settings.OPENAI_API_KEY:
                    logger.warning(
                        "ElevenLabs unavailable (%s) — falling back to OpenAI TTS",
                        e,
                    )
                    _tts_instance = OpenAITTS()
                else:
                    raise RuntimeError(
                        "ELEVENLABS_* is configured but elevenlabs package is not importable. "
                        "Install elevenlabs or set OPENAI_API_KEY for fallback."
                    ) from e
        elif settings.OPENAI_API_KEY:
            logger.warning("ElevenLabs not configured — using OpenAI TTS as fallback")
            _tts_instance = OpenAITTS()
        else:
            raise RuntimeError(
                "No TTS configured. Set ELEVENLABS_API_KEY or OPENAI_API_KEY in .env"
            )
    return _tts_instance
