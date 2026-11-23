"""
The appointment confirmation call (plan Phase 2.5): one job per call, run by the receptionist worker
when its dispatch metadata says kind=appointment_confirmation (core/services/confirmation_calls.py).

    load tenant + appointment → still worth calling? → session → dial from the practice's number
    → machine: practice name and number only, hang up → person: fixed opener (AI disclosed), then
    confirm / move / cancel with the shared scheduling tools → on shutdown: call record + outcome

The opener and the voicemail are templates, never model output: the AI is always disclosed first,
and a voicemail never mentions a time or a treatment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from google.protobuf.duration_pb2 import Duration
from livekit import api
from livekit.agents import AMD, Agent, JobContext, RunContext, function_tool, get_job_context, room_io
from livekit.plugins import openai as lk_openai

from agent.persistence import CallRecorder
from agent.profile import resolve_profile
from agent.prompt import build_voice_prompt
from agent.receptionist import end_call_check, hang_up
from agent.session_factory import build_session, openai_client
from agent.speech import clean_stream
from agent.state import CallState
from agent.telemetry import attach_telemetry
from agent.tools import shared_tools
from config import settings
from core.log import set_tenant
from core.repositories import Repos
from core.services import availability as av
from core.services import confirmation_calls as cc
from core.timeutils import business_now
from database.mongodb import get_db

logger = logging.getLogger("agent.confirm")

PATIENT_IDENTITY = "patient"
RING_TIMEOUT_S = 30
MAX_CALL_S = 5 * 60
AMD_LLM_MODEL = "gpt-4.1-mini"
TOOLS = ("check_availability", "reschedule_appointment", "cancel_appointment", "take_message")


@dataclass
class ConfirmCallState(CallState):
    appointment_id: str = ""
    confirmed: bool = False
    answered: bool = False
    amd: Optional[str] = None


class ConfirmAgent(Agent):
    def __init__(self, state: ConfirmCallState, when: str) -> None:
        profile = state.profile
        tools = [t for t in shared_tools(profile) if getattr(getattr(t, "info", None), "name", "") in TOOLS]
        prompt = build_voice_prompt(profile, caller_phone=state.caller_phone)
        super().__init__(instructions=prompt + "\n\n" + cc.instructions(business=profile.business_name, when=when),
                         tools=tools)
        self.state = state

    def tts_node(self, text: Any, model_settings: Any) -> Any:
        return Agent.default.tts_node(self, clean_stream(text), model_settings)

    @function_tool()
    async def confirm_appointment(self, context: RunContext) -> str:
        """The patient says they can make the appointment."""
        self.state.confirmed = True
        self.state.set_outcome("completed")
        return "Confirmed. Thank them, ask if there's anything else, and call end_call once they're done."

    @function_tool()
    async def end_call(self, context: RunContext) -> Optional[str]:
        """End the call. Use only after the patient has said they need nothing else and you've said goodbye."""
        too_soon = end_call_check(self.state, self.chat_ctx)
        if too_soon:
            return too_soon
        self.state.ended_reason = "agent_hangup"
        await context.wait_for_playout()
        if get_job_context(required=False) is None:
            context.session.shutdown()
            return None
        await hang_up("agent_hangup")
        return None


def status_for(state: ConfirmCallState) -> str:
    return cc.outcome_of(state.confirmed, state.outcome, state.amd, state.answered)


async def run_confirmation(ctx: JobContext, meta: Dict[str, Any]) -> None:
    tenant_id, appointment_id = str(meta.get("tenant_id") or ""), str(meta.get("appointment_id") or "")
    profile = await resolve_profile(tenant_id=tenant_id) if tenant_id else None
    appt = (await Repos(get_db(), tenant_id).appointments.find_one({"appointment_id": appointment_id})
            if profile else None)
    trunk = settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID
    if profile is None or appt is None or appt.get("status") != "confirmed" or not trunk or not appt.get("phone"):
        logger.warning("Confirmation call %s not placed (appointment gone, cancelled or no trunk)", ctx.room.name)
        if profile is not None and appt is not None:
            await cc.record_outcome(tenant_id, appointment_id, "skipped", call_id=ctx.room.name, detail="not callable")
        ctx.shutdown("not callable")
        return
    set_tenant(tenant_id)
    tenant = profile.tenant
    when = av.spoken(appt["start"], tenant.timezone, today=business_now(tenant.timezone).date())
    state = ConfirmCallState(call_id=ctx.room.name, profile=profile, channel="phone", direction="outbound",
                             caller_phone=appt["phone"], dialed_number=str(meta.get("from") or ""),
                             sip_trunk_id=trunk, appointment_id=appointment_id)
    session = build_session(ctx.proc.userdata, state)
    attach_telemetry(session, state)
    recorder = CallRecorder(state)
    recorder.attach(session)

    async def _on_shutdown(reason: str) -> None:
        if state.answered:
            await recorder.finish(reason)
        await cc.record_outcome(tenant_id, appointment_id, status_for(state), call_id=state.call_id)
    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(agent=ConfirmAgent(state, when), room=ctx.room,
                        room_options=room_io.RoomOptions(participant_identity=PATIENT_IDENTITY,
                                                         delete_room_on_close=True))
    amd_llm = lk_openai.LLM(model=AMD_LLM_MODEL, client=openai_client(), temperature=0)
    async with AMD(session, llm=amd_llm, stt=None, participant_identity=PATIENT_IDENTITY, ivr_detection=False,
                   suppress_compatibility_warning=True) as detector:
        try:
            await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk, sip_number=state.dialed_number or None, sip_call_to=appt["phone"],
                room_name=ctx.room.name, participant_identity=PATIENT_IDENTITY,
                participant_name=tenant.name[:60], wait_until_answered=True,
                ringing_timeout=Duration(seconds=RING_TIMEOUT_S), max_call_duration=Duration(seconds=MAX_CALL_S)))
        except Exception as e:              # busy, no answer, rejected
            logger.info("Confirmation call %s not answered: %s", state.call_id, e)
            await hang_up("no_answer")
            return
        state.answered = True
        recorder.start()
        result = await detector.execute()

    state.amd = result.category.value
    if state.amd != "human":
        if state.amd in ("machine-vm", "machine-unavailable"):
            handle = session.say(cc.voicemail(business=tenant.name, phone=tenant.phone), allow_interruptions=False,
                                 add_to_chat_ctx=False)
            await handle.wait_for_playout()
        await hang_up("machine")
        return
    session.say(cc.opener(agent_name=profile.agent_name, business=tenant.name,
                          patient_first_name=cc.first_name(appt.get("name") or ""), when=when),
                allow_interruptions=True)
    asyncio.get_running_loop().call_later(MAX_CALL_S, lambda: asyncio.ensure_future(hang_up("max_call_duration")))

