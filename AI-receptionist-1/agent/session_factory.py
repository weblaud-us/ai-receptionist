"""
Build the AgentSession for one call (Roadmap Tasks 1.3 and 1.4.b).

    STT  Deepgram Nova-3 streaming         (fallback: OpenAI gpt-4o-mini-transcribe)
    LLM  VOICE_LLM_MODEL, streamed, tools   (OpenAI; one warm client per process;
                                             fallback: VOICE_LLM_FALLBACK_MODEL on Anthropic)
    TTS  VOICE_TTS_PROVIDER, then VOICE_TTS_FALLBACKS (Deepgram Aura / Kokoro / ElevenLabs / Cartesia / OpenAI)
    Turn LiveKit turn detector + Silero VAD (VAD loaded once per process in prewarm)

Phase 3 (Task 3.5): each of STT, LLM and TTS is a LiveKit FallbackAdapter when a second provider
is configured, so a provider's bad hour does not take calls down. A fallback is only built when
its key is set; with one provider the plain plugin is used. agent/health.py records every switch.

Every provider is a LiveKit plugin, so swapping one is a one-line change here. API keys are
passed explicitly from settings — the worker never depends on .env being in os.environ.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import openai as openai_sdk
from livekit.agents import AgentSession, inference
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.plugins import cartesia, deepgram, elevenlabs
from livekit.plugins import openai as lk_openai

from agent.profile import VoiceProfile
from agent.state import CallState
from config import settings
from core.voices import is_aura_model

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4
LLM_ATTEMPT_TIMEOUT_S = 6.0      # no first token by then → next LLM
STT_ATTEMPT_TIMEOUT_S = 8.0

# One OpenAI client per worker process, bound to the loop it was created in. Reusing it keeps
# the HTTP/TLS connection to the LLM warm between calls (Task 1.4.d). Within a call, LiveKit
# pre-warms STT, LLM and TTS itself when the agent starts.
_openai_client: Optional[openai_sdk.AsyncClient] = None
_openai_loop: Optional[asyncio.AbstractEventLoop] = None


def openai_client() -> openai_sdk.AsyncClient:
    global _openai_client, _openai_loop
    loop = asyncio.get_running_loop()
    if _openai_client is None or _openai_loop is not loop:
        # connect 2 s / total 15 s: a hung provider must fail fast enough for the fallback to matter
        _openai_client = openai_sdk.AsyncClient(
            api_key=settings.OPENAI_API_KEY, max_retries=0,
            timeout=openai_sdk.Timeout(15.0, connect=2.0))
        _openai_loop = loop
    return _openai_client


def _openai_stt(p: VoiceProfile) -> Any:
    return lk_openai.STT(model="gpt-4o-mini-transcribe", language=p.tts_language,
                         api_key=settings.OPENAI_API_KEY)


def build_stt(p: VoiceProfile, vad: Any = None) -> Any:
    """Deepgram streaming, with OpenAI speech-to-text behind it (needs the VAD to chunk audio)."""
    if settings.VOICE_STT_PROVIDER == "deepgram":
        if settings.DEEPGRAM_API_KEY:
            kwargs: dict[str, Any] = {
                "model": settings.VOICE_STT_MODEL,
                "language": p.stt_language,
                "api_key": settings.DEEPGRAM_API_KEY,
                "interim_results": True,
                "filler_words": True,       # helps the turn detector
                "mip_opt_out": settings.DEEPGRAM_MIP_OPT_OUT,   # no training on callers' audio
            }
            if settings.VOICE_STT_MODEL.startswith("nova-3") and p.keyterms:
                kwargs["keyterm"] = list(p.keyterms)  # business/agent names STT should expect
            primary = deepgram.STT(**kwargs)
            if settings.VOICE_STT_FALLBACK and settings.OPENAI_API_KEY and vad is not None:
                return lk_stt.FallbackAdapter([primary, _openai_stt(p)], vad=vad,
                                              attempt_timeout=STT_ATTEMPT_TIMEOUT_S)
            return primary
        logger.warning("VOICE_STT_PROVIDER=deepgram but DEEPGRAM_API_KEY is empty — using OpenAI STT (slower)")
    return _openai_stt(p)


def _tts_for(provider: str, p: VoiceProfile) -> Optional[Any]:
    if not settings.tts_available(provider):
        return None
    if provider == "elevenlabs":
        return elevenlabs.TTS(
            voice_id=p.voice_id,
            model=settings.ELEVENLABS_TTS_MODEL,
            api_key=settings.ELEVENLABS_API_KEY,
            language=p.tts_language,
            apply_text_normalization="auto",
        )
    if provider == "cartesia":
        kwargs: dict[str, Any] = {"api_key": settings.CARTESIA_API_KEY, "model": settings.CARTESIA_MODEL,
                                  "language": p.tts_language}
        if settings.CARTESIA_VOICE_ID:
            kwargs["voice"] = settings.CARTESIA_VOICE_ID
        return cartesia.TTS(**kwargs)
    if provider == "deepgram":     # Aura; same key as the streaming STT. The voice is the model name.
        return deepgram.TTS(model=aura_model(p), api_key=settings.DEEPGRAM_API_KEY)
    if provider == "kokoro":       # local Kokoro-82M behind Kokoro-FastAPI's OpenAI-compatible endpoint
        return lk_openai.TTS(model=settings.KOKORO_MODEL, voice=settings.KOKORO_VOICE,
                             base_url=settings.KOKORO_BASE_URL, api_key="not-needed")
    if provider == "openai":
        return lk_openai.TTS(model=settings.OPENAI_TTS_MODEL, voice=settings.OPENAI_TTS_VOICE,
                             api_key=settings.OPENAI_API_KEY)
    return None


def aura_model(p: VoiceProfile) -> str:
    """The business's own Aura voice when it picked Deepgram, else the platform default. A voice id
    chosen for another provider (an ElevenLabs id) is never sent to Deepgram."""
    own = p.tenant.agent.voice_id
    if p.voice_provider == "deepgram" and is_aura_model(own):
        return own
    return settings.DEEPGRAM_TTS_MODEL


def build_tts(p: VoiceProfile) -> Any:
    """Voices in the order set by .env (VOICE_TTS_PROVIDER, then VOICE_TTS_FALLBACKS); a business's own
    voice setting goes first. A voice with no key / URL is skipped; if one fails mid-call, LiveKit
    switches to the next."""
    primary = p.voice_provider if not p.tenant.is_legacy else settings.VOICE_TTS_PROVIDER
    order = [name for name in settings.tts_order(primary) if settings.tts_available(name)]
    engines = [e for e in (_tts_for(name, p) for name in order) if e is not None]
    if not engines:
        raise RuntimeError("No voice configured: set DEEPGRAM_API_KEY, KOKORO_BASE_URL, CARTESIA_API_KEY, "
                           "ELEVENLABS_API_KEY or OPENAI_API_KEY")
    logger.info("Voice order: %s", " → ".join(order))
    return engines[0] if len(engines) == 1 else lk_tts.FallbackAdapter(engines)


def _fallback_llm() -> Optional[Any]:
    """A second LLM on a different provider, so an OpenAI outage is survivable. None = not configured."""
    if not (settings.ANTHROPIC_API_KEY and settings.VOICE_LLM_FALLBACK_MODEL):
        return None
    from livekit.plugins import anthropic   # lazy: only deployments with the key load the plugin

    return anthropic.LLM(
        model=settings.VOICE_LLM_FALLBACK_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=settings.VOICE_LLM_TEMPERATURE,
        parallel_tool_calls=True,
        caching="ephemeral",
        max_retries=0,
    )


def build_llm(p: VoiceProfile) -> Any:
    primary = lk_openai.LLM(
        model=p.llm_model,
        client=openai_client(),
        temperature=settings.VOICE_LLM_TEMPERATURE,
        parallel_tool_calls=True,
        prompt_cache_key=f"voice-{p.business_id}",   # same static prefix → cache hits across calls
        store=settings.OPENAI_STORE,                  # health data: never kept for OpenAI evals/distillation
    )
    fallback = _fallback_llm()
    if fallback is None:
        return primary
    # No retry on the failed LLM (the caller is waiting): go straight to the next one.
    return lk_llm.FallbackAdapter([primary, fallback], attempt_timeout=LLM_ATTEMPT_TIMEOUT_S)


def endpointing(p: Optional[VoiceProfile] = None) -> dict[str, Any]:
    """The tenant's normal pause before replying (agent/turn_tuning.py lengthens it for digits)."""
    ep = p.tenant.agent.endpointing if p else None
    return {
        "mode": "dynamic",
        "min_delay": ep.min_delay if ep else settings.VOICE_ENDPOINTING_MIN_DELAY,
        "max_delay": ep.max_delay if ep else settings.VOICE_ENDPOINTING_MAX_DELAY,
    }


def turn_detector_version() -> str:
    """The cloud model needs the LiveKit key pair (an explicit version raises without it, where
    "auto" would quietly fall back), so without keys use the local model."""
    if settings.VOICE_TURN_DETECTOR == "v1" and not (settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        return "v1-mini"
    return settings.VOICE_TURN_DETECTOR


def interruption_mode() -> str:
    """The adaptive barge-in model (stops for "wait", talks through "mm-hmm") runs on LiveKit's
    inference service and needs the key pair. Without it, say so once and use plain VAD barge-in
    rather than let LiveKit drop the setting quietly. LiveKit also falls back to VAD by itself if
    the detector fails mid-call."""
    mode = settings.VOICE_INTERRUPT_MODE
    if mode == "adaptive" and not (settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        global _warned_interrupt
        if not _warned_interrupt:
            logger.warning("VOICE_INTERRUPT_MODE=adaptive needs LIVEKIT_API_KEY/SECRET; using VAD barge-in")
            _warned_interrupt = True
        return "vad"
    return mode


_warned_interrupt = False


def turn_handling(p: Optional[VoiceProfile] = None) -> dict[str, Any]:
    return {
        # Explicit version: left unset, `start` on our own hosts silently uses the smaller local model.
        "turn_detection": inference.TurnDetector(
            version=turn_detector_version(), local_fallback=True,  # type: ignore[arg-type]
            api_key=settings.LIVEKIT_API_KEY, api_secret=settings.LIVEKIT_API_SECRET),
        "endpointing": endpointing(p),
        "interruption": {
            "enabled": True,
            # Explicit too: unset, production mode turns the adaptive detector off.
            "mode": interruption_mode(),
            "min_duration": settings.VOICE_INTERRUPT_MIN_DURATION,   # a short "wait!" must stop the agent
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,  # a cough or "mm-hmm" doesn't kill the sentence
        },
        # Preemptive TTS: the first words are already synthesised when the turn is confirmed. Aura is
        # cheap, so the occasional discarded guess costs little and every turn starts sooner.
        "preemptive_generation": {"enabled": True, "preemptive_tts": settings.VOICE_PREEMPTIVE_TTS},
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
        stt=build_stt(p, vad),
        llm=build_llm(p),
        tts=build_tts(p),
        turn_handling=turn_handling(p),  # type: ignore[arg-type]
        user_away_timeout=settings.VOICE_USER_AWAY_TIMEOUT,
        max_tool_steps=MAX_TOOL_STEPS,
    )
