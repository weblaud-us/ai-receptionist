"""
The outbound sales call (Roadmap Tasks 4.4 and 4.5): one job per dial.

The dialer (core/outbound/dialer.py) dispatches this agent into room `out-<attempt id>` with the
attempt, lead and campaign ids. In order:

    load attempt + lead + campaign → compliance gate again → session → answering-machine detection
    → dial the lead on the sales trunk → machine: hang up (or leave the consented voicemail)
                                        → person: the fixed opener, then the conversation
    → on shutdown: the call record, then the attempt's outcome (which moves the lead on)

Safety that does not depend on the model:
- The gate is re-run here, seconds before the dial.
- The opener is spoken from a template: it always says "AI", who is calling and how to opt out.
- Everything the callee says is checked for an opt-out (core/outbound/optout.py). A match writes the
  do-not-call list, says one fixed sentence and hangs up.
- After any opt-out the call is ended within DNC_HANG_UP_S seconds whatever the model does.
- A hard cap on call length, both at the SIP layer and with a timer here.

A person can listen in (hidden, receive-only) or take over from the dashboard. On take-over, and
for the whole of a `human_assisted` call, the agent stops speaking and never answers; it keeps
transcribing the callee so the call is still summarised.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Dict, Optional

from google.protobuf.duration_pb2 import Duration
from livekit import api, rtc
from livekit.agents import (
    AMD,
    Agent,
    JobContext,
    ModelSettings,
    RunContext,
    StopResponse,
    function_tool,
    get_job_context,
    room_io,
)
from livekit.agents import llm as lk_llm
from livekit.plugins import noise_cancellation
from livekit.plugins import openai as lk_openai

from agent.db import ensure_db
from agent.health import attach_provider_health
from agent.persistence import CallRecorder
from agent.profile import resolve_profile
from agent.receptionist import _sip_participant, hang_up
from agent.session_factory import build_session, openai_client
from agent.speech import clean_stream
from agent.state import CallState
from agent.telemetry import attach_telemetry
from config import settings
from core.log import call_id_var, set_tenant
from core.outbound import dialer, gate, optout, outcomes, persona, sales_tools
from core.outbound import models as m
from core.outbound.sales_tools import SalesContext
from core.phone import mask_phone
from database.mongodb import get_db

logger = logging.getLogger("agent.sales")

LEAD_IDENTITY = "lead"
OPERATOR_ROLE = "takeover"                  # participant attribute set by the API on a take-over token
OPERATOR_WAIT_S = 20.0
DNC_HANG_UP_S = 10.0
AMD_LLM_MODEL = "gpt-4.1-mini"              # on LiveKit's list of models evaluated for AMD

SUMMARY_PROMPT = (
    "Summarise this outbound sales call for the founder of the company that placed it, in one or two "
    "plain sentences (at most 45 words). Say who was reached (owner, gatekeeper, nobody), how they "
    "reacted, any objection, and the agreed next step with its date and time. No preamble, no markdown."
)


@dataclass
class SalesCallState(CallState):
    mode: str = "ai_cold_b2b_us"
    answered_at: Optional[datetime] = None
    amd: Optional[str] = None
    sip_status: Optional[int] = None
    final_outcome: Optional[str] = None      # set by the dial / AMD path; otherwise the tools decide
    detail: str = ""
    human_in_control: bool = False           # a person took over, or a human_assisted call


def _later(seconds: float, reason: str) -> asyncio.TimerHandle:
    return asyncio.get_running_loop().call_later(seconds, lambda: asyncio.ensure_future(hang_up(reason)))


class SalesAgent(Agent):
    def __init__(self, state: SalesCallState, sales: SalesContext, script: persona.Persona) -> None:
        super().__init__(instructions=persona.build_prompt(
            script, sales.lead, sales.campaign, can_transfer=bool(state.profile.transfer_number)))
        self.state = state
        self.sales = sales

    def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings) -> Any:
        return Agent.default.tts_node(self, clean_stream(text), model_settings)

    # ── Safety: runs on everything the callee says, before the model sees it ──

    async def on_user_turn_completed(self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage) -> None:
        if optout.is_opt_out(new_message.text_content or "") and not self.sales.opted_out:
            await self._opt_out("keyword_opt_out")
            if not self.state.human_in_control:
                asyncio.ensure_future(self._goodbye())
            raise StopResponse()
        if self.state.human_in_control or self.sales.opted_out:
            raise StopResponse()                 # a person is talking, or the call is ending: say nothing

    async def _opt_out(self, reason: str) -> None:
        await sales_tools.add_to_dnc(self.sales, {"reason": reason})
        if not self.state.human_in_control:
            _later(DNC_HANG_UP_S, "opt_out_timeout")     # the call ends even if nothing else works

    async def _goodbye(self) -> None:
        try:
            self.session.interrupt()
            handle = self.session.say(optout.GOODBYE, allow_interruptions=False)
            await handle.wait_for_playout()
        except Exception:
            logger.warning("Opt-out goodbye failed on call %s", self.state.call_id, exc_info=True)
        await hang_up("opt_out")

    # ── Tools ───────────────────────────────────────────────────────────────

    @function_tool()
    async def check_demo_slots(self, context: RunContext, date: str = "", time: str = "") -> str:
        """Find open 15-minute demo times in the next week. Call before offering any time.

        Args:
            date: Day they asked about, YYYY-MM-DD. Leave empty for the next available.
            time: Time they asked about, HH:MM 24-hour, if any.
        """
        return json.dumps(await sales_tools.check_demo_slots(self.sales, {"date": date, "time": time}), default=str)

    @function_tool()
    async def book_demo(self, context: RunContext, slot_id: str, name: str, email: str) -> str:
        """Book a demo. Confirm the day, time and email address out loud first.

        Args:
            slot_id: A slot_id returned by check_demo_slots.
            name: The name of the person who will attend.
            email: Where to send the invite.
        """
        return json.dumps(await sales_tools.book_demo(
            self.sales, {"slot_id": slot_id, "name": name, "email": email}), default=str)

    @function_tool()
    async def send_info_email(self, context: RunContext, email: str) -> str:
        """Email a one-page summary. Only after they agreed to receive it and you confirmed the address.

        Args:
            email: Their email address, as confirmed with them.
        """
        return json.dumps(await sales_tools.send_info_email(self.sales, {"email": email}))

    @function_tool()
    async def schedule_callback(self, context: RunContext, when: str, note: str = "") -> str:
        """Arrange to call back at a time they asked for.

        Args:
            when: Day and time in their local time, YYYY-MM-DDTHH:MM.
            note: Who to ask for or what to mention, if they said.
        """
        return json.dumps(await sales_tools.schedule_callback(self.sales, {"when": when, "note": note}))

    @function_tool()
    async def add_to_dnc(self, context: RunContext, reason: str = "requested_on_call") -> str:
        """Put this number on the do-not-call list. Use the moment they ask not to be called, say they
        are not interested, or ask to be removed. It cannot be undone.

        Args:
            reason: A few words on what they said.
        """
        await self._opt_out(reason or "requested_on_call")
        return json.dumps({"success": True,
                           "message": "They are on the do-not-call list. Apologise once, say goodbye, call end_call."})

    @function_tool()
    async def mark_wrong_number(self, context: RunContext) -> str:
        """Record that this number does not belong to the business you meant to call."""
        return json.dumps(await sales_tools.mark_wrong_number(self.sales, {}))

    @function_tool()
    async def transfer_to_founder(self, context: RunContext, reason: str) -> str:
        """Connect them to a person now. Tell them you are connecting them before calling this.

        Args:
            reason: One sentence for the person about what they want.
        """
        profile = self.state.profile
        job = get_job_context(required=False)
        sip = _sip_participant(job.room) if job else None
        if job is None or sip is None or not profile.transfer_open():
            return "Nobody can take the call right now. Offer a demo time or to have someone email them."
        await context.wait_for_playout()
        logger.info("Transferring sales call %s to %s (%s)", self.state.call_id,
                    mask_phone(profile.transfer_number), reason[:120])
        try:
            await job.api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                room_name=job.room.name, participant_identity=sip.identity,
                transfer_to=f"tel:{profile.transfer_number}", play_dialtone=False))
        except Exception:
            logger.warning("Transfer failed on sales call %s", self.state.call_id, exc_info=True)
            self.state.transfer = "failed"
            return "The transfer failed. Apologise and offer a demo time instead."
        self.state.transfer = "ok"
        self.sales.set_outcome("interested")
        self.sales.next_step = self.sales.next_step or "Transferred to a person"
        self.state.ended_reason = "transferred"
        return "Transferred. The call has left you; say nothing more."

    @function_tool()
    async def end_call(self, context: RunContext, outcome: str) -> None:
        """End the call. Use only after you have said goodbye.

        Args:
            outcome: How the call went: interested, not_interested, gatekeeper or wrong_number.
        """
        # A demo or a callback only counts when its tool succeeded; the model cannot claim one here.
        if outcome in ("interested", "not_interested", "gatekeeper", "wrong_number"):
            self.sales.set_outcome(outcome)
        elif outcome in sales_tools.END_CALL_OUTCOMES:
            self.sales.set_outcome("interested")
        self.state.ended_reason = "agent_hangup"
        await context.wait_for_playout()
        await hang_up("agent_hangup")


# ── The job ──────────────────────────────────────────────────────────────────

def final_outcome(state: SalesCallState, sales: Optional[SalesContext]) -> str:
    """What the attempt is recorded as. The dial / AMD result wins, then what the tools recorded."""
    if sales is not None and sales.opted_out:
        return "dnc"                                            # nothing outranks an opt-out
    if state.final_outcome:
        return state.final_outcome
    if sales is not None and sales.outcome:
        return sales.outcome
    return "no_conversation" if state.answered_at else "failed"


def _is_operator(participant: rtc.RemoteParticipant) -> bool:
    return (participant.attributes or {}).get("role") == OPERATOR_ROLE


def _hand_over(session: Any, state: SalesCallState) -> None:
    """A person is in charge of the conversation from here: the agent goes silent for good."""
    if state.human_in_control:
        return
    state.human_in_control = True
    try:
        session.interrupt()
        session.output.set_audio_enabled(False)
    except Exception:
        logger.debug("Silencing the agent failed", exc_info=True)
    logger.info("Call %s: a person took over", state.call_id)


async def _wait_for_operator(room: rtc.Room, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if any(_is_operator(p) for p in room.remote_participants.values()):
            return True
        await asyncio.sleep(0.25)
    return False


async def _load(meta: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    db = get_db()
    attempt = await db[m.ATTEMPTS].find_one({"_id": meta.get("attempt_id"), "status": "dispatched"})
    if attempt is None:
        return None, None, None
    lead = await db[m.LEADS].find_one({"_id": attempt["lead_id"]})
    campaign = await db[m.CAMPAIGNS].find_one({"_id": attempt["campaign_id"]})
    return attempt, lead, campaign


async def sales_caller(ctx: JobContext) -> None:
    call_id_var.set(ctx.room.name)
    set_tenant(settings.SALES_TENANT_ID or None)
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except ValueError:
        meta = {}
    await ctx.connect()
    await ensure_db()
    db = get_db()
    attempt, lead, campaign = await _load(meta)
    if attempt is None or lead is None or campaign is None:
        logger.error("Sales job %s has no open attempt; nothing dialled", ctx.room.name)
        if attempt is not None:
            await outcomes.finish_attempt(db, attempt["_id"], "failed", detail="lead or campaign missing")
        ctx.shutdown("no attempt")
        return
    attempt_id = attempt["_id"]

    # The gate, again, seconds before the phone rings. Also stops a paused campaign's queued jobs.
    now = datetime.now(timezone.utc)
    decision = await gate.run_gate(db, lead, campaign, now)
    profile = await resolve_profile(tenant_id=settings.SALES_TENANT_ID) if settings.SALES_TENANT_ID else None
    trunk = settings.sales_trunk_id
    if not decision.allowed or campaign.get("status") != "running" or profile is None or not trunk:
        if not decision.allowed:
            await dialer.apply_gate_failure(db, lead, decision, now)
            detail = ", ".join(decision.failures)
        else:
            detail = "campaign not running" if campaign.get("status") != "running" else "sales tenant or trunk missing"
        logger.warning("Attempt %s not dialled: %s", attempt_id, detail)
        await outcomes.finish_attempt(db, attempt_id, "blocked_by_gate", now=now, detail=detail)
        ctx.shutdown("blocked")
        return

    mode = str(campaign["mode"])
    human_assisted = mode == "human_assisted"
    state = SalesCallState(
        call_id=ctx.room.name, profile=profile, channel="phone", direction="outbound",
        caller_phone=lead["phone"], dialed_number=attempt["from"], sip_trunk_id=trunk,
        agent_name=settings.SALES_AGENT_NAME, campaign_id=campaign["_id"], lead_id=lead["_id"],
        attempt_id=attempt_id, summary_prompt=SUMMARY_PROMPT, mode=mode)
    sales = SalesContext(db=db, lead=lead, campaign=campaign, attempt_id=attempt_id, call_id=state.call_id,
                         tenant=profile.tenant)
    script = persona.get(attempt.get("persona_id") or persona.DEFAULT_PERSONA)

    session = build_session(ctx.proc.userdata, state)
    attach_telemetry(session, state)
    attach_provider_health(session, state)
    recorder = CallRecorder(state)
    recorder.attach(session)
    timers: list[asyncio.TimerHandle] = []

    async def _on_shutdown(reason: str) -> None:
        for timer in timers:
            timer.cancel()
        duration = (datetime.now(timezone.utc) - state.answered_at).total_seconds() if state.answered_at else None
        if state.answered_at:                    # a call record only when somebody (or something) answered
            await recorder.finish(reason)
        await outcomes.finish_attempt(
            db, attempt_id, final_outcome(state, sales), amd=state.amd, sip_status=state.sip_status,
            duration_s=duration, summary=recorder.summary, next_step=sales.next_step,
            callback_at=sales.callback_at, detail=state.detail)
    ctx.add_shutdown_callback(_on_shutdown)

    @ctx.room.on("participant_connected")
    def _on_participant(participant: rtc.RemoteParticipant) -> None:
        if _is_operator(participant):
            _hand_over(session, state)

    await session.start(
        agent=SalesAgent(state, sales, script), room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=LEAD_IDENTITY,
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony() if settings.VOICE_NOISE_CANCELLATION else None),
            delete_room_on_close=True))               # the lead hangs up → the room and the job close

    if human_assisted:
        # The person must be in the room before the phone rings, so nobody answers to silence.
        if not await _wait_for_operator(ctx.room, OPERATOR_WAIT_S):
            state.final_outcome, state.detail = "operator_absent", "nobody joined to take the call"
            await hang_up("operator_absent")
            return
        _hand_over(session, state)

    await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"status": "dialing", "started_at": now}})
    max_seconds = settings.SALES_MAX_CALL_MINUTES * 60
    amd_llm = lk_openai.LLM(model=AMD_LLM_MODEL, client=openai_client(), temperature=0)
    async with AMD(session, llm=amd_llm, stt=None, participant_identity=LEAD_IDENTITY, ivr_detection=False,
                   suppress_compatibility_warning=True) as detector:
        try:
            await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk, sip_number=attempt["from"], sip_call_to=lead["phone"],
                room_name=ctx.room.name, participant_identity=LEAD_IDENTITY,
                participant_name=(lead.get("business_name") or "Lead")[:60], wait_until_answered=True,
                krisp_enabled=settings.VOICE_NOISE_CANCELLATION,
                ringing_timeout=Duration(seconds=settings.SALES_RING_TIMEOUT_S),
                max_call_duration=Duration(seconds=max_seconds)))
        except api.SipCallError as e:
            state.sip_status = e.sip_status_code
            state.final_outcome = outcomes.sip_outcome(e.sip_status_code)
            state.detail = f"SIP {e.sip_status_code} {e.sip_status or ''}".strip()
            await hang_up(state.final_outcome)
            return
        except Exception as e:
            logger.error("Dial failed for attempt %s", attempt_id, exc_info=True)
            state.final_outcome, state.detail = "failed", type(e).__name__
            await hang_up("dial_failed")
            return

        state.answered_at = datetime.now(timezone.utc)
        state.sip_status = 200
        await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"status": "in_progress", "sip_status": 200}})
        timers.append(_later(max_seconds, "max_call_duration"))
        recorder.start()
        result = await detector.execute()

    state.amd = result.category.value
    await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"amd": state.amd}})
    machine = outcomes.amd_outcome(state.amd)
    if machine is not None and not (human_assisted and state.amd == "uncertain"):
        state.final_outcome = machine
        if (state.amd == "machine-vm" and mode == "ai_consented"
                and campaign.get("voicemail_policy") == "leave_message"):
            handle = session.say(persona.voicemail_message(), allow_interruptions=False, add_to_chat_ctx=False)
            await handle.wait_for_playout()
        await hang_up(machine)
        return
    if human_assisted:
        return                                    # the person talks; the agent listens and takes notes

    session.say(persona.opener(script, lead, mode), allow_interruptions=True)
