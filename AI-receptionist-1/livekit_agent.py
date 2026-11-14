"""
LiveKit AI Voice Agent — livekit-agents 1.5.2
Fixed: TTS audio frame generation (using OpenAI TTS instead of ElevenLabs plugin)

ROOT CAUSE (TTS Issue):
  livekit-plugins-elevenlabs v1.5.2 has compatibility issues with LiveKit's 
  audio frame pipeline. Changed to OpenAI TTS which is stable and reliable.

ROOT CAUSE (Activity Race):
  RealtimeModel creates its own WebSocket activity immediately on connect.
  session.start() ALSO creates an activity internally via asyncio.gather(*tasks).
  Both call _update_activity() simultaneously → race → RuntimeError.

FIX:
  1. Use pipeline mode (VAD + Whisper STT + GPT-4o + OpenAI TTS) — no race.
  2. Greeting moved to Agent.on_enter() — runs AFTER session is fully started.
  3. RealtimeModel kept as opt-in via LIVEKIT_USE_REALTIME=true in .env.
  4. No session.say() after session.start() — on_enter() handles it safely.
  5. Reduced VAD delays (min 0.3s, max 3s) to prevent double responses.

Start:
    python livekit_agent.py dev
"""

import logging
import warnings
from typing import Annotated

warnings.filterwarnings("ignore", message="Field .model_name.", category=UserWarning)
warnings.filterwarnings("ignore", message="Field .model_provider.", category=UserWarning)

from dotenv import load_dotenv
load_dotenv(override=True)

from config import settings
from core.log import configure_logging
from core.observability import init_sentry

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
init_sentry("agent")

from ai.prompts import GREETING_VOICE, get_system_prompt
from database.mongodb import connect_db
from database.tenancy import get_hotel_profile

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, function_tool
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero as lk_silero

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Hotel Receptionist Agent
# ─────────────────────────────────────────────────────────────────────────────

class HotelReceptionistAgent(Agent):
    """
    AI hotel receptionist.
    Tools defined as @function_tool methods.
    on_enter() sends the greeting AFTER session is fully started — no race condition.
    """

    def __init__(self, instructions: str, caller_phone: str | None = None):
        super().__init__(instructions=instructions)
        from ai.tools import get_tool_executor
        self._executor = get_tool_executor()
        # Browser calls (WhatsApp link) have no verified phone number, so booking lookups
        # require booking ID + name. SIP calls will pass the caller's number here (Phase 1).
        self._caller_phone = caller_phone

    async def _run(self, tool: str, **args) -> str:
        """All tools go through execute(): it enforces caller-ownership rules on bookings."""
        return await self._executor.execute(
            tool, {k: v for k, v in args.items() if v not in ("", None)},
            caller_phone=self._caller_phone,
        )

    # ── Greeting ──────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """
        Called by livekit-agents AFTER session.start() completes.
        Greeting is sent ONCE here — the system prompt instructs the LLM
        never to re-greet on subsequent turns.
        """
        profile = get_hotel_profile()
        # The greeting discloses that this is an AI assistant (legal requirement).
        greeting = GREETING_VOICE.format(
            hotel_name=profile["name"], receptionist_name=profile["receptionist_name"],
        )
        logger.info("🗣 on_enter → sending greeting (once only)")
        await self.session.say(greeting, allow_interruptions=True)

    # ── Hotel Tools ───────────────────────────────────────────────────────────

    @function_tool(description=(
        "Check which hotel rooms are available for given dates. "
        "Always call this before quoting prices or confirming bookings."
    ))
    async def check_room_availability(
        self,
        check_in_date: Annotated[str, "Check-in date YYYY-MM-DD"],
        check_out_date: Annotated[str, "Check-out date YYYY-MM-DD"],
        room_type: Annotated[str, "standard|deluxe|suite|executive|presidential"] = "",
        adults: Annotated[int, "Number of adults"] = 1,
        children: Annotated[int, "Number of children"] = 0,
    ) -> str:
        return await self._run(
            "check_room_availability", check_in_date=check_in_date, check_out_date=check_out_date,
            room_type=room_type.lower(), adults=adults, children=children,
        )

    @function_tool(description="Get detailed pricing for a specific room type including taxes.")
    async def get_room_pricing(
        self,
        room_type: Annotated[str, "Room type e.g. suite"],
        check_in_date: Annotated[str, "Check-in YYYY-MM-DD"],
        check_out_date: Annotated[str, "Check-out YYYY-MM-DD"],
    ) -> str:
        return await self._run(
            "get_room_pricing", room_type=room_type.lower(),
            check_in_date=check_in_date, check_out_date=check_out_date,
        )

    @function_tool(description=(
        "Create a confirmed hotel room booking. "
        "Call ONLY after confirming all details with the guest."
    ))
    async def create_booking(
        self,
        guest_name: Annotated[str, "Full name of guest"],
        guest_phone: Annotated[str, "Phone with country code e.g. +8801XXXXXXXXX"],
        room_type: Annotated[str, "standard|deluxe|suite|executive|presidential"],
        check_in_date: Annotated[str, "Check-in YYYY-MM-DD"],
        check_out_date: Annotated[str, "Check-out YYYY-MM-DD"],
        adults: Annotated[int, "Number of adults"] = 1,
        children: Annotated[int, "Number of children"] = 0,
        special_requests: Annotated[str, "Any special requests"] = "",
    ) -> str:
        return await self._run(
            "create_booking", guest_name=guest_name, guest_phone=guest_phone,
            room_type=room_type.lower(), check_in_date=check_in_date,
            check_out_date=check_out_date, adults=adults, children=children,
            special_requests=special_requests,
        )

    @function_tool(description=(
        "Get booking details. Needs the booking ID plus the full name on the booking "
        "(or its check-in date) to prove it is the guest's own booking."
    ))
    async def get_booking_details(
        self,
        booking_id: Annotated[str, "Booking reference ID"] = "",
        guest_name: Annotated[str, "Full name on the booking"] = "",
        check_in_date: Annotated[str, "Check-in date YYYY-MM-DD"] = "",
        guest_phone: Annotated[str, "Guest phone number"] = "",
    ) -> str:
        return await self._run(
            "get_booking_details", booking_id=booking_id, guest_name=guest_name,
            check_in_date=check_in_date, guest_phone=guest_phone,
        )

    @function_tool(description=(
        "Cancel an existing booking. Needs the booking ID plus the full name on the booking."
    ))
    async def cancel_booking(
        self,
        booking_id: Annotated[str, "Booking reference ID"],
        guest_name: Annotated[str, "Full name on the booking"] = "",
        check_in_date: Annotated[str, "Check-in date YYYY-MM-DD"] = "",
        cancellation_reason: Annotated[str, "Reason"] = "",
    ) -> str:
        return await self._run(
            "cancel_booking", booking_id=booking_id, guest_name=guest_name,
            check_in_date=check_in_date, cancellation_reason=cancellation_reason,
        )

    @function_tool(description="Get hotel info: amenities, policies, location.")
    async def get_hotel_info(
        self,
        info_type: Annotated[str, "general|amenities|policies|location|rooms_overview"] = "general",
    ) -> str:
        return await self._run("get_hotel_info", info_type=info_type)

    @function_tool(description="Send booking confirmation to guest's WhatsApp.")
    async def send_booking_confirmation_whatsapp(
        self,
        booking_id: Annotated[str, "Booking ID"],
        whatsapp_number: Annotated[str, "WhatsApp number with country code"],
        guest_name: Annotated[str, "Full name on the booking"] = "",
    ) -> str:
        return await self._run(
            "send_booking_confirmation_whatsapp", booking_id=booking_id,
            whatsapp_number=whatsapp_number, guest_name=guest_name,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Session builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    base = get_system_prompt(
        channel="voice",
        hotel_name=settings.HOTEL_NAME,
        receptionist_name=settings.RECEPTIONIST_NAME,
        hotel_address=settings.HOTEL_ADDRESS,
        checkin_time=settings.HOTEL_CHECKIN_TIME,
        checkout_time=settings.HOTEL_CHECKOUT_TIME,
        currency=settings.HOTEL_CURRENCY,
        hotel_phone=settings.HOTEL_PHONE,
    )
    # Append a hard no-regreet rule specific to LiveKit (stateless LLM turns)
    base += (
        "\n\nCRITICAL — LIVEKIT CALL RULE:\n"
        "The greeting is played ONCE automatically via on_enter(). "
        "NEVER repeat 'Thank you for calling', 'This is [name], the hotel's virtual assistant', "
        "or any hotel welcome introduction again — even if the caller says Hi or Hello. "
        "Respond to Hi/Hello with only 'How may I help you?' and proceed immediately."
    )
    return base


def _build_pipeline_session() -> AgentSession:
    """
    Build STT→LLM→TTS pipeline session.
    Stable approach — no RealtimeModel race condition.
    Uses OpenAI TTS as default (more stable with LiveKit).
    ElevenLabs available but may have frame generation issues with LiveKit plugin.
    """
    # OpenAI TTS is more stable with LiveKit agent audio frame generation
    tts = lk_openai.TTS(
        model=settings.OPENAI_TTS_MODEL,
        voice=settings.OPENAI_TTS_VOICE,
    )
    logger.info("🔊 LiveKit TTS: OpenAI (%s)", settings.OPENAI_TTS_VOICE)

    return AgentSession(
        vad=lk_silero.VAD.load(),
        stt=lk_openai.STT(model="whisper-1"),
        llm=lk_openai.LLM(model=settings.OPENAI_MODEL),
        tts=tts,
        min_endpointing_delay=0.3,   # Reduced: 0.3s silence before processing
        max_endpointing_delay=3.0,   # Reduced: max 3s utterance (less delay)
        allow_interruptions=True,
    )


def _build_realtime_session() -> AgentSession:
    """
    OpenAI Realtime API — native audio (~300ms latency).
    Enable with LIVEKIT_USE_REALTIME=true in .env.
    NOTE: greet via on_enter(), NOT session.say() after start().
    """
    from livekit.plugins.openai.realtime import RealtimeModel
    voice_model    = (settings.OPENAI_VOICE_MODEL or "gpt-4o-realtime-preview").strip()
    realtime_voice = (settings.OPENAI_REALTIME_VOICE or "shimmer").strip()
    logger.info("🎙 LiveKit LLM: OpenAI Realtime (%s / %s)", voice_model, realtime_voice)
    return AgentSession(
        llm=RealtimeModel(model=voice_model, voice=realtime_voice),
    )


def _prewarm(_proc) -> None:
    try:
        lk_silero.VAD.load()
        logger.info("✅ Silero VAD pre-warmed")
    except Exception as e:
        logger.warning("Silero VAD pre-warm failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint — runs once per LiveKit room
# ─────────────────────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext) -> None:
    logger.info("🎙️  LiveKit agent entrypoint — room: %s", ctx.room.name)

    await connect_db()
    await ctx.connect()

    system_prompt = _build_system_prompt()
    hotel_agent   = HotelReceptionistAgent(instructions=system_prompt)

    # Choose session type
    use_realtime = str(getattr(settings, "LIVEKIT_USE_REALTIME", "false")).lower() == "true"

    if use_realtime:
        logger.info("🚀 Using OpenAI Realtime session")
        session = _build_realtime_session()
    else:
        logger.info("🚀 Using pipeline session (VAD+Whisper+GPT-4o+OpenAI TTS)")
        session = _build_pipeline_session()

    logger.info("=" * 55)
    logger.info("  Hotel         : %s", settings.HOTEL_NAME)
    logger.info("  Receptionist  : %s", settings.RECEPTIONIST_NAME)
    logger.info("  Mode          : %s", "realtime" if use_realtime else "pipeline")
    logger.info("=" * 55)

    # FIX: session.start() only — greeting is sent by on_enter() AFTER start completes.
    # NEVER call session.say() immediately after session.start() with RealtimeModel
    # because _update_activity races internally and raises RuntimeError.
    await session.start(agent=hotel_agent, room=ctx.room)
    logger.info("✅ AgentSession started — on_enter() will send greeting")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from livekit.agents import cli

    missing = [v for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
               if not getattr(settings, v, "")]
    if missing:
        raise RuntimeError(f"Missing LiveKit env vars: {missing}")
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY required in .env")

    use_realtime = str(getattr(settings, "LIVEKIT_USE_REALTIME", "false")).lower() == "true"
    mode = f"OpenAI Realtime ({settings.OPENAI_VOICE_MODEL})" if use_realtime else \
           "Pipeline (Whisper -> GPT-4o -> ElevenLabs)"

    logger.info("=" * 55)
    logger.info("  %s — LiveKit AI Receptionist", settings.HOTEL_NAME)
    logger.info("  LiveKit : %s", settings.LIVEKIT_URL)
    logger.info("  Mode    : %s", mode)
    logger.info("=" * 55)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=_prewarm,
        ws_url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        port=settings.LIVEKIT_WORKER_PORT,
    ))
