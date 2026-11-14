"""
Build the AgentSession for one call (Roadmap Tasks 1.3 and 1.4.b).

    STT  Deepgram Nova-3 streaming         (fallback: OpenAI gpt-4o-mini-transcribe)
    LLM  VOICE_LLM_MODEL, streamed, tools   (OpenAI; one warm client per process)
    TTS  ElevenLabs Flash v2.5              (Cartesia / OpenAI as automatic fallbacks)
    Turn LiveKit turn detector + Silero VAD (VAD loaded once per process in prewarm)

Every provider is a LiveKit plugin, so swapping one is a one-line change here. API keys are
passed explicitly from settings — the worker never depends on .env being in os.environ.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import openai as openai_sdk
from livekit.agents import AgentSession, inference
from livekit.agents import tts as lk_tts
from livekit.plugins import cartesia, deepgram, elevenlabs
from livekit.plugins import openai as lk_openai

from agent.profile import VoiceProfile
from agent.state import CallState
from config import settings

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4

# One OpenAI client per worker process, bound to the loop it was created in. Reusing it keeps
# the HTTP/TLS connection to the LLM warm between calls (Task 1.4.d). Within a call, LiveKit
# pre-warms STT, LLM and TTS itself when the agent starts.
_openai_client: Optional[openai_sdk.AsyncClient] = None
_openai_loop: Optional[asyncio.AbstractEventLoop] = None


def openai_client() -> openai_sdk.AsyncClient:
    global _openai_client, _openai_loop
    loop = asyncio.get_running_loop()
    if _openai_client is None or _openai_loop is not loop:
        _openai_client = openai_sdk.AsyncClient(api_key=settings.OPENAI_API_KEY, max_retries=0)
        _openai_loop = loop
    return _openai_client


def build_stt(p: VoiceProfile) -> Any:
    if settings.VOICE_STT_PROVIDER == "deepgram":
        if settings.DEEPGRAM_API_KEY:
            kwargs: dict[str, Any] = {
                "model": settings.VOICE_STT_MODEL,
                "language": p.stt_language,
                "api_key": settings.DEEPGRAM_API_KEY,
                "interim_results": True,
                "filler_words": True,       # helps the turn detector
            }
            if settings.VOICE_STT_MODEL.startswith("nova-3") and p.keyterms:
                kwargs["keyterm"] = list(p.keyterms)  # business/agent names STT should expect
            return deepgram.STT(**kwargs)
        logger.warning("VOICE_STT_PROVIDER=deepgram but DEEPGRAM_API_KEY is empty — using OpenAI STT (slower)")
    return lk_openai.STT(model="gpt-4o-mini-transcribe", language=p.tts_language,
                         api_key=settings.OPENAI_API_KEY)


def _tts_for(provider: str, p: VoiceProfile) -> Optional[Any]:
    if provider == "elevenlabs" and settings.ELEVENLABS_API_KEY:
        return elevenlabs.TTS(
            voice_id=p.voice_id,
            model=settings.ELEVENLABS_TTS_MODEL,
            api_key=settings.ELEVENLABS_API_KEY,
            language=p.tts_language,
            apply_text_normalization="auto",
        )
    if provider == "cartesia" and settings.CARTESIA_API_KEY:
        kwargs: dict[str, Any] = {"api_key": settings.CARTESIA_API_KEY, "model": settings.CARTESIA_MODEL,
                                  "language": p.tts_language}
        if settings.CARTESIA_VOICE_ID:
            kwargs["voice"] = settings.CARTESIA_VOICE_ID
        return cartesia.TTS(**kwargs)
    if provider == "openai" and settings.OPENAI_API_KEY:
        return lk_openai.TTS(model=settings.OPENAI_TTS_MODEL, voice=settings.OPENAI_TTS_VOICE,
                             api_key=settings.OPENAI_API_KEY)
    return None


def build_tts(p: VoiceProfile) -> Any:
    """The tenant's provider first, then any other configured provider as an automatic fallback."""
    primary = p.voice_provider if not p.tenant.is_legacy else settings.VOICE_TTS_PROVIDER
    order = [primary] + [x for x in ("elevenlabs", "cartesia", "openai") if x != primary]
    engines = [e for e in (_tts_for(name, p) for name in order) if e is not None]
    if not engines:
        raise RuntimeError("No TTS provider configured (ELEVENLABS_API_KEY, CARTESIA_API_KEY or OPENAI_API_KEY)")
    if _tts_for(primary, p) is None:
        logger.warning("TTS provider %s has no API key — falling back", primary)
    return engines[0] if len(engines) == 1 else lk_tts.FallbackAdapter(engines)


def build_llm(p: VoiceProfile) -> Any:
    return lk_openai.LLM(
        model=p.llm_model,
        client=openai_client(),
        temperature=settings.VOICE_LLM_TEMPERATURE,
        parallel_tool_calls=True,
        prompt_cache_key=f"voice-{p.business_id}",   # same static prefix → cache hits across calls
    )


def turn_handling(p: Optional[VoiceProfile] = None) -> dict[str, Any]:
    ep = p.tenant.agent.endpointing if p else None
    return {
        "turn_detection": inference.TurnDetector(
            api_key=settings.LIVEKIT_API_KEY, api_secret=settings.LIVEKIT_API_SECRET),
        "endpointing": {
            "mode": "dynamic",
            "min_delay": ep.min_delay if ep else settings.VOICE_ENDPOINTING_MIN_DELAY,
            "max_delay": ep.max_delay if ep else settings.VOICE_ENDPOINTING_MAX_DELAY,
        },
        "interruption": {
            "enabled": True,
            "min_duration": 0.5,                # ignore coughs and clicks
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,  # "mm-hmm" doesn't kill the sentence
        },
        "preemptive_generation": {"enabled": True, "preemptive_tts": False},
    }


def build_session(proc_userdata: dict[str, Any], state: CallState) -> AgentSession:
    p = state.profile
    vad = proc_userdata.get("vad")
    if settings.LIVEKIT_USE_REALTIME:
        from livekit.plugins.openai.realtime import RealtimeModel

        logger.info("Realtime (speech-to-speech) mode: %s", settings.OPENAI_VOICE_MODEL)
        return AgentSession(
            userdata=state, vad=vad,
            llm=RealtimeModel(model=settings.OPENAI_VOICE_MODEL, voice=settings.OPENAI_REALTIME_VOICE,
                              api_key=settings.OPENAI_API_KEY),
            user_away_timeout=settings.VOICE_USER_AWAY_TIMEOUT,
            max_tool_steps=MAX_TOOL_STEPS,
        )

    return AgentSession(
        userdata=state,
        vad=vad,
        stt=build_stt(p),
        llm=build_llm(p),
        tts=build_tts(p),
        turn_handling=turn_handling(p),  # type: ignore[arg-type]
        user_away_timeout=settings.VOICE_USER_AWAY_TIMEOUT,
        max_tool_steps=MAX_TOOL_STEPS,
    )
