"""
LiveKit agent worker — one job per call (Roadmap Task 1.4.c, tenant-aware since Phase 2).

    python -m agent.worker dev      # local development
    python -m agent.worker start    # production (systemd unit: receptionist-agent)

Phone calls arrive through the SIP trunk + dispatch rule (telephony/), browser calls through a
signed link or the dashboard's test call; both dispatch to LIVEKIT_AGENT_NAME and run the same
agent for whichever tenant owns the dialled number (or is named in the browser token).

What happens per call, in order — nothing slow sits before the greeting:
    connect → caller attributes → tenant + business snapshot (cached 60 s) → session → greeting
    → in the background: caller history, contact bookkeeping, periodic flush, recording.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import settings
from core.log import call_id_var, configure_logging
from core.observability import init_sentry

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
init_sentry("agent")

from livekit import rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AgentServer,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    JobProcess,
    cli,
    room_io,
)
from livekit.plugins import noise_cancellation, silero  # noqa: E402

from agent.persistence import CallRecorder, load_contact, record_contact_call  # noqa: E402
from agent.profile import VoiceProfile, resolve_profile, unassigned_profile  # noqa: E402
from agent.receptionist import ReceptionistAgent, SilenceWatcher, UnassignedNumberAgent  # noqa: E402
from agent.recording import start_recording  # noqa: E402
from agent.session_factory import build_session  # noqa: E402
from agent.state import CallState  # noqa: E402
from agent.telemetry import attach_telemetry  # noqa: E402
from core.phone import mask_phone  # noqa: E402
from database import mongodb  # noqa: E402

logger = logging.getLogger("agent.worker")

CONTACT_LOAD_TIMEOUT_S = 2.0
TENANT_RESOLVE_TIMEOUT_S = 3.0
_db_loop: Optional[asyncio.AbstractEventLoop] = None


async def ensure_db() -> None:
    """
    One Mongo client per worker process (Task 1.9.1), created inside the job's event loop and
    reused by later jobs on the same loop. No index builds here — the migration does that.
    """
    global _db_loop
    loop = asyncio.get_running_loop()
    if mongodb.is_connected() and _db_loop is loop:
        return
    if mongodb.is_connected():
        await mongodb.disconnect_db()  # bound to an old loop
    await mongodb.connect_db(ensure_indexes=False)
    _db_loop = loop


def prewarm(proc: JobProcess) -> None:
    """Once per worker process, before any job: load heavy models here, never per call."""
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(
    ws_url=settings.LIVEKIT_URL or None,
    api_key=settings.LIVEKIT_API_KEY or None,
    api_secret=settings.LIVEKIT_API_SECRET or None,
    num_idle_processes=settings.LIVEKIT_NUM_IDLE_PROCESSES,
    port=settings.LIVEKIT_WORKER_PORT,
    setup_fnc=prewarm,
)


def _caller_attributes(participant: rtc.RemoteParticipant) -> dict[str, Any]:
    attrs = dict(participant.attributes or {})
    is_sip = participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    if is_sip:
        # ⚠ Confirm attribute names on the first real SIP call (Roadmap Task 1.2.c). Keys only:
        # values carry phone numbers.
        logger.info("SIP participant attributes: %s", sorted(attrs))
    return {
        "is_sip": is_sip,
        "dialed": attrs.get("sip.trunkPhoneNumber") if is_sip else None,
        "caller": attrs.get("sip.phoneNumber") if is_sip else None,
        "sip_call_id": attrs.get("sip.callID"),
        "sip_trunk_id": attrs.get("sip.trunkID"),
        "tenant_id": None if is_sip else attrs.get("tenant_id"),   # browser calls: from the signed token
    }


async def _resolve(caller: dict[str, Any]) -> Optional[VoiceProfile]:
    try:
        return await asyncio.wait_for(
            resolve_profile(dialed_number=caller["dialed"], tenant_id=caller["tenant_id"]),
            TENANT_RESOLVE_TIMEOUT_S)
    except Exception:
        logger.error("Tenant resolution failed (dialed %s)", mask_phone(caller["dialed"]), exc_info=True)
        return None


def _call_state(ctx: JobContext, caller: dict[str, Any], profile: VoiceProfile) -> CallState:
    return CallState(
        call_id=ctx.room.name,
        profile=profile,
        channel="phone" if caller["is_sip"] else "browser",
        caller_phone=caller["caller"],
        dialed_number=caller["dialed"],
        sip_call_id=caller["sip_call_id"],
        sip_trunk_id=caller["sip_trunk_id"],
    )


async def _load_history(agent: ReceptionistAgent, state: CallState) -> None:
    try:
        state.contact = await asyncio.wait_for(load_contact(state), CONTACT_LOAD_TIMEOUT_S)
        if state.contact:
            await agent.refresh_instructions()
    except Exception:
        logger.debug("Caller history unavailable", exc_info=True)


async def _start_background_audio(ctx: JobContext, session: Any, profile: VoiceProfile) -> None:
    ambience = profile.tenant.agent.ambience or settings.VOICE_AMBIENT_SOUND
    if not (settings.VOICE_THINKING_SOUND or ambience):
        return
    player = BackgroundAudioPlayer(
        thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.5),
                        AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.4)]
        if settings.VOICE_THINKING_SOUND else None,
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.15) if ambience else None,
    )
    try:
        await player.start(room=ctx.room, agent_session=session)
    except Exception:
        logger.warning("Background audio unavailable", exc_info=True)


async def _record(ctx: JobContext, state: CallState) -> None:
    if not state.profile.recording:
        return
    state.recording = await start_recording(ctx.api, state)


def _alert_unassigned(caller: dict[str, Any], room: str) -> None:
    logger.warning("Call %s to an unassigned number %s", room, mask_phone(caller["dialed"]) or "(browser)")
    try:
        import sentry_sdk

        sentry_sdk.capture_message("Call to unassigned number", level="warning")
    except Exception:
        pass


@server.rtc_session(agent_name=settings.LIVEKIT_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    call_id_var.set(ctx.room.name)
    await ctx.connect()                       # no database work before this line
    db_ready = asyncio.create_task(ensure_db())
    participant = await ctx.wait_for_participant()
    caller = _caller_attributes(participant)
    await db_ready                            # the client is lazy; this is near-instant
    profile = await _resolve(caller)

    if profile is None:                       # unknown number: neutral message, hang up, alert
        _alert_unassigned(caller, ctx.room.name)
        state = _call_state(ctx, caller, unassigned_profile())
        session = build_session(ctx.proc.userdata, state)
        recorder = CallRecorder(state)
        ctx.add_shutdown_callback(recorder.finish)
        await session.start(agent=UnassignedNumberAgent(state), room=ctx.room,
                            room_options=room_io.RoomOptions(delete_room_on_close=True))
        return

    state = _call_state(ctx, caller, profile)
    logger.info("Call %s started for %s (%s, caller %s)", state.call_id, profile.business_id, state.channel,
                mask_phone(state.caller_phone) or "unknown")

    session = build_session(ctx.proc.userdata, state)  # providers pre-warm when the agent starts

    attach_telemetry(session, state)
    recorder = CallRecorder(state)
    recorder.attach(session)
    SilenceWatcher(session, state)
    agent = ReceptionistAgent(state)

    async def _on_shutdown(reason: str) -> None:
        await recorder.finish(reason)
    ctx.add_shutdown_callback(_on_shutdown)

    use_nc = state.channel == "phone" and settings.VOICE_NOISE_CANCELLATION
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony() if use_nc else None,
            ),
            delete_room_on_close=True,        # caller hangs up → room (and job) closes
        ),
    )

    recorder.start()
    asyncio.create_task(_load_history(agent, state))
    asyncio.create_task(record_contact_call(state))
    asyncio.create_task(_start_background_audio(ctx, session, profile))
    asyncio.create_task(_record(ctx, state))


def main() -> None:
    missing = [v for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY")
               if not getattr(settings, v)]
    if missing:
        raise SystemExit(f"Missing settings for the voice agent: {', '.join(missing)}")
    logger.info("Voice agent '%s' (LLM %s, STT %s, TTS %s)", settings.LIVEKIT_AGENT_NAME,
                settings.VOICE_LLM_MODEL, settings.VOICE_STT_PROVIDER, settings.VOICE_TTS_PROVIDER)
    cli.run_app(server)


if __name__ == "__main__":
    main()
