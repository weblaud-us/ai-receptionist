"""
The receptionist Agent (Roadmap Tasks 1.5–1.7).

- Greeting: played from the pre-rendered cache (agent/greeting.py), interruptible, added to the
  chat context so the model knows it already greeted.
- Speech clean-up: tts_node strips symbols/URLs from the LLM stream before TTS.
- Voice-only tools: end_call and transfer_to_human need the LiveKit call itself. All business
  tools come from core.tools.registry via agent/tools.py.
- Silence: "are you still there?", then a polite goodbye if the line stays quiet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterable, Optional

from livekit import api, rtc
from livekit.agents import Agent, ModelSettings, RunContext, function_tool, get_job_context
from livekit.agents import llm as lk_llm

from agent import greeting
from agent.prompt import build_voice_prompt
from agent.speech import clean_stream
from agent.state import CallState
from agent.tools import shared_tools
from core.phone import mask_phone
from core.tools.hotel import run_in_background

logger = logging.getLogger(__name__)

SILENCE_GOODBYE_AFTER_S = 8.0


def _sip_participant(room: rtc.Room) -> Optional[rtc.RemoteParticipant]:
    return next((p for p in room.remote_participants.values()
                 if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP), None)


async def hang_up(reason: str) -> None:
    """End the call for everyone. Deleting the room disconnects the SIP leg too."""
    job = get_job_context(required=False)
    if job is None:
        return
    try:
        await job.api.room.delete_room(api.DeleteRoomRequest(room=job.room.name))
    except Exception:
        logger.warning("delete_room failed (%s); shutting the job down", reason, exc_info=True)
        job.shutdown(reason=reason)


class ReceptionistAgent(Agent):
    def __init__(self, state: CallState) -> None:
        super().__init__(
            instructions=build_voice_prompt(state.profile, caller_phone=state.caller_phone),
            tools=shared_tools(state.profile),
        )
        self.state = state

    # ── Greeting ────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        text = self.state.profile.greeting_text
        tts = self.session.tts
        if tts is None:
            if isinstance(self.session.llm, lk_llm.RealtimeModel):  # speech-to-speech: model speaks it
                self.session.generate_reply(instructions=f"Greet the caller with exactly: {text}")
            else:                                                   # text-only sessions (evals)
                self.session.say(text, add_to_chat_ctx=True)
            return
        key = greeting.cache_key(text, tts, self.state.profile.voice_id)
        cached = greeting.load(key) or await greeting.load_shared(key)
        if cached is not None:
            self.session.say(text, audio=greeting.frames(cached), allow_interruptions=True)
        else:
            self.session.say(text, allow_interruptions=True)
            run_in_background(greeting.render(key, text, tts))  # cached for the next call

    async def refresh_instructions(self) -> None:
        """Rebuild the prompt once caller history has loaded (only the tail changes)."""
        await self.update_instructions(build_voice_prompt(
            self.state.profile, caller_phone=self.state.caller_phone, contact=self.state.contact))

    # ── Speech clean-up ─────────────────────────────────────────────────

    def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings) -> Any:
        return Agent.default.tts_node(self, clean_stream(text), model_settings)

    # ── Voice-only tools ────────────────────────────────────────────────

    @function_tool()
    async def end_call(self, context: RunContext) -> None:
        """End the phone call. Use only after you have said goodbye and the caller has nothing else."""
        self.state.set_outcome("completed")
        self.state.ended_reason = "agent_hangup"
        await context.wait_for_playout()  # let the goodbye finish
        if get_job_context(required=False) is None:
            context.session.shutdown()     # text-only test runs
            return
        await hang_up("agent_hangup")

    @function_tool()
    async def transfer_to_human(self, context: RunContext, reason: str) -> str:
        """Transfer the caller to a staff member. Use when they ask for a person, are upset, or need
        something you cannot do. Before calling this, tell the caller you are connecting them.

        Args:
            reason: One short sentence for the staff member about what the caller needs.
        """
        profile = self.state.profile
        if not profile.transfer_open():
            return "No one is available to take the call right now. Apologise and offer to take a message."
        job = get_job_context(required=False)
        sip = _sip_participant(job.room) if job else None
        if job is None or sip is None:
            return "Transfers only work on phone calls. Offer to take a message instead."

        await context.wait_for_playout()  # "connecting you now" finishes first
        logger.info("Transferring call %s to %s (%s)", self.state.call_id,
                    mask_phone(profile.transfer_number), reason[:120])
        try:
            await job.api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                room_name=job.room.name,
                participant_identity=sip.identity,
                transfer_to=f"tel:{profile.transfer_number}",
                play_dialtone=False,
            ))
        except Exception:
            logger.warning("SIP transfer failed for call %s", self.state.call_id, exc_info=True)
            self.state.transfer = "failed"
            return "The transfer failed. Apologise and offer to take a message instead."
        self.state.transfer = "ok"
        self.state.set_outcome("transferred", override=True)
        self.state.ended_reason = "transferred"
        return "Transferred. The call has left you; say nothing more."


class UnassignedNumberAgent(Agent):
    """A call to a number no tenant owns (Task 2.4): one neutral sentence, then hang up."""

    def __init__(self, state: CallState) -> None:
        super().__init__(instructions="Say nothing.", tools=[])
        self.state = state

    async def on_enter(self) -> None:
        self.state.set_outcome("unassigned", override=True)
        self.state.ended_reason = "unassigned_number"
        handle = self.session.say(self.state.profile.greeting_text, allow_interruptions=False)
        await handle.wait_for_playout()
        if get_job_context(required=False) is None:
            self.session.shutdown()
            return
        await hang_up("unassigned_number")


BUSY_MESSAGE = "Thanks for calling {name}. All of our lines are busy right now. Please call back in a few minutes."
BUSY_TRANSFER_MESSAGE = "Thanks for calling {name}. All of our lines are busy, so I'm connecting you to the team now."


class BusyLineAgent(Agent):
    """
    The business is already at its concurrent-call limit (Task 3.6). No LLM turn: connect the
    caller to the staff line when one is open, otherwise one polite sentence and hang up.
    """

    outcome = "busy"
    reason = "concurrency_limit"
    message = BUSY_MESSAGE
    transfer_message = BUSY_TRANSFER_MESSAGE

    def __init__(self, state: CallState) -> None:
        super().__init__(instructions="Say nothing.", tools=[])
        self.state = state

    async def on_enter(self) -> None:
        profile = self.state.profile
        self.state.set_outcome(self.outcome, override=True)
        self.state.ended_reason = self.reason
        job = get_job_context(required=False)
        sip = _sip_participant(job.room) if job else None
        if job is not None and sip is not None and profile.transfer_open():
            handle = self.session.say(self.transfer_message.format(name=profile.tenant.name),
                                      allow_interruptions=False)
            await handle.wait_for_playout()
            try:
                await job.api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                    room_name=job.room.name, participant_identity=sip.identity,
                    transfer_to=f"tel:{profile.transfer_number}", play_dialtone=False))
                self.state.transfer = "ok"
                self.state.ended_reason = "transferred"
                return
            except Exception:
                self.state.transfer = "failed"
                logger.warning("Busy-line transfer failed for call %s", self.state.call_id, exc_info=True)
        handle = self.session.say(self.message.format(name=profile.tenant.name), allow_interruptions=False)
        await handle.wait_for_playout()
        if job is None:
            self.session.shutdown()
            return
        await hang_up(self.reason)


UNAVAILABLE_MESSAGE = ("Thanks for calling {name}. We can't take your call right now. "
                       "Please call back a little later. Goodbye.")
UNAVAILABLE_TRANSFER_MESSAGE = "Thanks for calling {name}. I'm connecting you to the team now."


class ServiceUnavailableAgent(BusyLineAgent):
    """
    The business is suspended, cancelled (inside its grace period) or over a hard minute cap (Phase 5).
    Never silence: forward to the staff line when it is open, otherwise one neutral sentence. The
    caller is never told why — billing is between us and the business.
    """

    outcome = "unavailable"
    message = UNAVAILABLE_MESSAGE
    transfer_message = UNAVAILABLE_TRANSFER_MESSAGE

    def __init__(self, state: CallState, reason: str = "suspended") -> None:
        super().__init__(state)
        self.reason = f"billing_{reason}"


class SilenceWatcher:
    """
    Roadmap Task 1.6.d. AgentSession marks the user "away" after VOICE_USER_AWAY_TIMEOUT seconds
    of silence on both sides. First we ask if they're still there; if the line stays quiet for
    another SILENCE_GOODBYE_AFTER_S seconds, we say goodbye and hang up.
    """

    def __init__(self, session: Any, state: CallState) -> None:
        self.session = session
        self.state = state
        self._task: Optional[asyncio.Task[None]] = None

        @session.on("user_state_changed")
        def _on_user_state(ev: Any) -> None:
            if ev.new_state == "away":
                if self._task is None or self._task.done():
                    self._task = asyncio.create_task(self._handle())
            elif self._task and not self._task.done():
                self._task.cancel()

    async def _handle(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            handle = self.session.generate_reply(
                instructions="The caller has gone quiet. Briefly ask if they're still there.")
            await handle.wait_for_playout()
            await asyncio.sleep(SILENCE_GOODBYE_AFTER_S)
            if self.session.user_state != "away":
                return
            self.state.ended_reason = "silence"
            goodbye = self.session.say("I'll let you go for now. Please call back any time. Goodbye!",
                                       allow_interruptions=False)
            await goodbye.wait_for_playout()
            await hang_up("silence")
