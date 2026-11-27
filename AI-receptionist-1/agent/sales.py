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

A browser test call of an offer (`test_call`, SALES_DIALER_PLAN.md Phase 1C) runs the same agent in a
dry run: no phone, no gate, no attempt, and its tools write nothing.

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
from typing import Any, AsyncIterable, Dict, Mapping, Optional

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
from agent.profile import load_profile, resolve_profile
from agent.receptionist import _sip_participant, hang_up
from agent.session_factory import build_session, openai_client
from agent.speech import clean_stream
from agent.state import CallState
from agent.telemetry import attach_telemetry
from config import settings
from core.log import call_id_var, set_tenant
from core.outbound import (coach, dialer, gate, learning_stats, memory, offers, optout, outcomes, persona, playbook,
                           qualify, sales_tools)
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
    "Summarise this outbound sales call for the founder of the company that placed it, in two plain "
    "sentences (at most 55 words). Say who was reached and their role (owner, manager, gatekeeper, nobody), "
    "the problem they described and what they use today, any budget or timing signal, their main objection, "
    "and the agreed next step with its date and time. No preamble, no markdown."
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
    ended_by: Optional[str] = None           # agent | prospect | system: who ended the call first


def _mark_end(state: SalesCallState, who: str) -> None:
    """Who ended the call (SALES_LEARNING_PLAN.md Phase 0). The first one to act wins: the room
    closing after our own hang-up must not count as the prospect hanging up."""
    if state.ended_by is None:
        state.ended_by = who


def _later(seconds: float, reason: str, state: Optional[SalesCallState] = None) -> asyncio.TimerHandle:
    def _fire() -> None:
        if state is not None:
            _mark_end(state, "system")
        asyncio.ensure_future(hang_up(reason))
    return asyncio.get_running_loop().call_later(seconds, _fire)


class SalesAgent(Agent):
    def __init__(self, state: SalesCallState, sales: SalesContext) -> None:
        super().__init__(instructions=persona.build_prompt(
            sales.offer, sales.variant, sales.lead, sales.campaign, research=sales.research, now=sales.clock(),
            can_transfer=bool(state.profile.transfer_number), memory=sales.memory,
            playbook=(sales.playbook or {}).get("lines")))
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
            _later(DNC_HANG_UP_S, "opt_out_timeout", self.state)     # the call ends even if nothing else works

    async def _goodbye(self) -> None:
        try:
            self.session.interrupt()
            handle = self.session.say(optout.GOODBYE, allow_interruptions=False)
            await handle.wait_for_playout()
        except Exception:
            logger.warning("Opt-out goodbye failed on call %s", self.state.call_id, exc_info=True)
        _mark_end(self.state, "agent")
        await hang_up("opt_out")

    # ── Tools ───────────────────────────────────────────────────────────────

    @function_tool()
    async def check_meeting_slots(self, context: RunContext, date: str = "", weekday: str = "", time: str = "") -> str:
        """Find open meeting times in the next week. Call before offering any time.

        Args:
            date: Day they asked about, YYYY-MM-DD in their local time. Leave empty for the next available.
            weekday: The weekday name of that date (e.g. Tuesday), so the date can be checked.
            time: Time they asked about, HH:MM 24-hour, if any.
        """
        return json.dumps(await sales_tools.check_meeting_slots(
            self.sales, {"date": date, "weekday": weekday, "time": time}), default=str)

    @function_tool()
    async def book_meeting(self, context: RunContext, slot_id: str, name: str, email: str) -> str:
        """Book the meeting. Confirm the day, time and email address out loud first.

        Args:
            slot_id: A slot_id returned by check_meeting_slots.
            name: The name of the person who will attend.
            email: Where to send the invite.
        """
        return json.dumps(await sales_tools.book_meeting(
            self.sales, {"slot_id": slot_id, "name": name, "email": email}), default=str)

    @function_tool()
    async def send_info_email(self, context: RunContext, email: str) -> str:
        """Email a one-page summary. Only after they agreed to receive it and you confirmed the address.

        Args:
            email: Their email address, as confirmed with them.
        """
        return json.dumps(await sales_tools.send_info_email(self.sales, {"email": email}))

    @function_tool()
    async def send_whatsapp_info(self, context: RunContext, number: str = "") -> str:
        """Send the details and the booking link on WhatsApp. Only after they agreed to get them on WhatsApp.
        Ask whether it's this number; if they give another, read it back first.

        Args:
            number: The WhatsApp number they gave, digit by digit, or empty for the number you called.
        """
        return json.dumps(await sales_tools.send_whatsapp_info(self.sales, {"number": number}))

    @function_tool()
    async def estimate_roi(self, context: RunContext, answers: list[float]) -> str:
        """Work out roughly what the offer could be worth to them, from the numbers they gave you.
        Never work a figure out yourself; say only what this returns.

        Args:
            answers: Their answers to the ROI questions in the offer, as numbers, in the same order.
        """
        return json.dumps(await sales_tools.estimate_roi(self.sales, {"answers": answers}), default=str)

    @function_tool()
    async def schedule_callback(self, context: RunContext, when: str, weekday: str = "", note: str = "") -> str:
        """Arrange to call back at a time they asked for.

        Args:
            when: Day and time in their local time, YYYY-MM-DDTHH:MM.
            weekday: The weekday name of that day (e.g. Tuesday), so the date can be checked.
            note: Who to ask for or what to mention, if they said.
        """
        return json.dumps(await sales_tools.schedule_callback(
            self.sales, {"when": when, "weekday": weekday, "note": note}))

    @function_tool()
    async def add_to_dnc(self, context: RunContext, reason: str = "requested_on_call") -> str:
        """Put this number on the do-not-call list. Use the moment they ask not to be called again, to
        stop calling, or to be removed from the list. It cannot be undone. A plain "not interested" is
        not this: use mark_not_interested.

        Args:
            reason: A few words on what they said.
        """
        await self._opt_out(reason or "requested_on_call")
        return json.dumps({"success": True,
                           "message": "They are on the do-not-call list. Apologise once, say goodbye, call end_call."})

    @function_tool()
    async def mark_not_interested(self, context: RunContext, reason: str = "") -> str:
        """Record that they are not interested in this offer (they did not ask to stop being called).

        Args:
            reason: Their reason in a few words, if they gave one.
        """
        return json.dumps(await sales_tools.mark_not_interested(self.sales, {"reason": reason}))

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
            return "Nobody can take the call right now. Offer a meeting time or to have someone email them."
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
            return "The transfer failed. Apologise and offer a meeting time instead."
        self.state.transfer = "ok"
        _mark_end(self.state, "agent")             # the line leaving us next is the transfer, not a hang-up
        self.sales.add_event("transfer")
        self.sales.set_outcome("interested")
        self.sales.next_step = self.sales.next_step or "Transferred to a person"
        self.state.ended_reason = "transferred"
        return "Transferred. The call has left you; say nothing more."

    @function_tool()
    async def end_call(self, context: RunContext, outcome: str) -> None:
        """End the call. Use only after you have said goodbye.

        Args:
            outcome: How the call went: interested or gatekeeper. (For "not interested" or a wrong
                number, call mark_not_interested or mark_wrong_number first.)
        """
        recorded = sales_tools.end_call_outcome(outcome)    # code decides what a claim may record
        if recorded:
            self.sales.set_outcome(recorded)
        self.state.ended_reason = "agent_hangup"
        _mark_end(self.state, "agent")
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


async def load_offer(db: Any, attempt: Mapping[str, Any]) -> Dict[str, Any]:
    """The exact offer version this attempt was dialled with (the built-in one for old campaigns)."""
    return await offers.offer_for(db, attempt.get("offer_id"), attempt.get("offer_version"))


def usable_research(lead: Mapping[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """Prospect research, when it finished and hasn't gone stale. The dial never waits for it."""
    research = lead.get("research") or {}
    expires = research.get("expires_at")
    if research.get("status") != "done" or not research.get("data"):
        return None
    if isinstance(expires, datetime) and (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) < now:
        return None
    return dict(research)


TEST_WRAP_UP_LINE = "That's the end of the test call. Thanks!"


async def test_call(ctx: JobContext, test_id: str) -> None:
    """A browser test of an offer: the tester is the "lead". Everything the agent does is a dry run."""
    db = get_db()
    doc = await db[offers.TEST_CALLS].find_one({"_id": test_id})
    if doc is None:
        logger.error("Test call %s not found; leaving room %s", test_id, ctx.room.name)
        ctx.shutdown("no test call")
        return
    offer = {**offers.OfferSettings.model_validate(doc["settings"]).model_dump(), "id": doc["offer_id"], "version": 0}
    lead = {"_id": "test_lead", "phone": "+10000000000", "status": "in_progress", "country": "", **doc["lead"]}
    campaign = {"_id": "test", "mode": doc.get("mode") or "ai_cold_b2b_us", "notes": ""}
    profile = (await resolve_profile(tenant_id=settings.SALES_TENANT_ID) if settings.SALES_TENANT_ID else None) \
        or load_profile()
    state = SalesCallState(call_id=ctx.room.name, profile=profile, channel="browser", direction="outbound",
                           caller_phone=lead["phone"], agent_name=settings.SALES_AGENT_NAME, attempt_id="test",
                           summary_prompt=SUMMARY_PROMPT, mode=campaign["mode"])
    sales = SalesContext(db=db, lead=lead, campaign=campaign, attempt_id="test", call_id=state.call_id,
                         tenant=profile.tenant, offer=offer, variant_id=doc.get("variant_id"), dry_run=True,
                         playbook={"lines": list(doc["playbook_lines"])} if doc.get("playbook_lines") else None)
    session = build_session(ctx.proc.userdata, state)
    loop = asyncio.get_running_loop()
    timers = [
        loop.call_later(offers.TEST_CALL_MAX_S - 10, lambda: session.say(TEST_WRAP_UP_LINE, allow_interruptions=False)),
        loop.call_later(offers.TEST_CALL_MAX_S, lambda: asyncio.ensure_future(hang_up("test_time_limit"))),
    ]

    async def _done(reason: str) -> None:
        for timer in timers:
            timer.cancel()
    ctx.add_shutdown_callback(_done)
    await session.start(agent=SalesAgent(state, sales), room=ctx.room,
                        room_options=room_io.RoomOptions(participant_identity=LEAD_IDENTITY, delete_room_on_close=True))
    logger.info("Test call %s of offer %s started", test_id, doc["offer_id"])
    session.say(persona.opener(offer, sales.variant, lead, campaign["mode"]), allow_interruptions=True)


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
    if meta.get("test_call_id"):
        await test_call(ctx, str(meta["test_call_id"]))
        return
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
    reviewed = m.legal_review_valid(campaign)
    if not decision.allowed or campaign.get("status") != "running" or not reviewed or profile is None or not trunk:
        if not decision.allowed:
            await dialer.apply_gate_failure(db, lead, decision, now)
            detail = ", ".join(decision.failures)
        elif campaign.get("status") != "running":
            detail = "campaign not running"
        elif not reviewed:
            detail = "legal review missing"
        else:
            detail = "sales tenant or trunk missing"
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
                         tenant=profile.tenant, offer=await load_offer(db, attempt),
                         variant_id=attempt.get("variant_id") or attempt.get("persona_id"),
                         research=usable_research(lead, now))
    # What we noted on earlier calls with this business (SALES_LEARNING_PLAN.md Phase 1). Read before
    # the phone rings, so it costs no time once they answer; it never blocks the call.
    sales.memory = await memory.lead_brief(db, lead, sales.offer, now=now, exclude_attempt_id=attempt_id)
    callback = attempt.get("dialled_as") == "callback"
    # The playbook of learned coaching the dialer assigned (SALES_LEARNING_PLAN.md Phase 3), checked
    # against this offer version once more. Not loaded = the reviewed baseline runs, and the call is
    # left out of the playbook's test.
    sales.playbook, playbook_applied = await playbook.for_call(db, attempt, sales.offer)
    playbook_applied = playbook_applied and attempt.get("playbook_applied") is not False

    session = build_session(ctx.proc.userdata, state)
    attach_telemetry(session, state)
    attach_provider_health(session, state)
    recorder = CallRecorder(state)
    recorder.attach(session)
    timers: list[asyncio.TimerHandle] = []

    async def _on_shutdown(reason: str) -> None:
        for timer in timers:
            timer.cancel()
        outcome = final_outcome(state, sales)
        state.outcome = outcome                 # the call record says what happened, not just "completed"
        duration = (datetime.now(timezone.utc) - state.answered_at).total_seconds() if state.answered_at else None
        extra: Dict[str, Any] = {"research_used": (sales.research or {}).get("fetched_at"),
                                 "email_consent_at": sales.email_consent_at, "roi_estimate": sales.roi_estimate,
                                 "memory_lines": list(sales.memory), "playbook_applied": playbook_applied}
        try:
            extra.update(learning_stats.shutdown_fields(
                transcript=state.transcript, ended_by=state.ended_by, events=sales.events, info_status=sales.info_status,
                callback_at=sales.callback_at, whatsapp_consent_at=sales.whatsapp_consent_at,
                transferred=state.transfer == "ok", taken_over=state.human_in_control and not human_assisted))
            if state.answered_at and not human_assisted:
                # Fixed-rule checks on what the agent said (no model, no cost); a severe one ends a playbook test.
                extra["checks"] = coach.scan(state.transcript, sales.offer)
            if state.answered_at:                # a call record only when somebody (or something) answered
                await recorder.finish(reason)
                notes = await qualify.extract_qualification(state.transcript, sales.offer)
                if notes:
                    extra["qualification"] = notes
                    await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"qualification": notes}})
        except Exception:
            # The outcome matters more than the extras: without it the reaper would mark the call "failed".
            logger.error("Wrapping up attempt %s failed; recording its outcome anyway", attempt_id, exc_info=True)
        await outcomes.finish_attempt(
            db, attempt_id, outcome, amd=state.amd, sip_status=state.sip_status,
            duration_s=duration, summary=recorder.summary, next_step=sales.next_step,
            callback_at=sales.callback_at, detail=state.detail, reason=sales.not_interested_reason, extra=extra)
    ctx.add_shutdown_callback(_on_shutdown)

    @ctx.room.on("participant_connected")
    def _on_participant(participant: rtc.RemoteParticipant) -> None:
        if _is_operator(participant):
            _hand_over(session, state)

    max_seconds = settings.SALES_MAX_CALL_MINUTES * 60

    @ctx.room.on("participant_disconnected")
    def _on_left(participant: rtc.RemoteParticipant) -> None:
        if participant.identity != LEAD_IDENTITY:
            return
        answered = state.answered_at
        at_cap = answered is not None and (datetime.now(timezone.utc) - answered).total_seconds() >= max_seconds - 2
        _mark_end(state, "system" if at_cap else "prospect")   # the SIP layer's own length cap isn't them

    await session.start(
        agent=SalesAgent(state, sales), room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=LEAD_IDENTITY,
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony() if settings.VOICE_NOISE_CANCELLATION else None),
            delete_room_on_close=True))               # the lead hangs up → the room and the job close

    if human_assisted:
        # The person must be in the room before the phone rings, so nobody answers to silence.
        if not await _wait_for_operator(ctx.room, OPERATOR_WAIT_S):
            state.final_outcome, state.detail = "operator_absent", "nobody joined to take the call"
            _mark_end(state, "system")
            await hang_up("operator_absent")
            return
        _hand_over(session, state)

    claimed = await db[m.ATTEMPTS].update_one({"_id": attempt_id, "status": "dispatched"},
                                              {"$set": {"status": "dialing", "started_at": now}})
    if not claimed.matched_count:                # closed meanwhile (reaped, or a person ended it): never ring
        state.final_outcome, state.detail = "failed", "the attempt was closed before dialling"
        _mark_end(state, "system")
        await hang_up("attempt_closed")
        return
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
            _mark_end(state, "system")
            await hang_up(state.final_outcome)
            return
        except Exception as e:
            logger.error("Dial failed for attempt %s", attempt_id, exc_info=True)
            state.final_outcome, state.detail = "failed", type(e).__name__
            _mark_end(state, "system")
            await hang_up("dial_failed")
            return

        state.answered_at = datetime.now(timezone.utc)
        state.sip_status = 200
        await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"status": "in_progress", "sip_status": 200,
                                                                         "answered_at": state.answered_at}})
        timers.append(_later(max_seconds, "max_call_duration", state))
        recorder.start()
        result = await detector.execute()

    state.amd = result.category.value
    await db[m.ATTEMPTS].update_one({"_id": attempt_id}, {"$set": {"amd": state.amd}})
    machine = outcomes.hang_up_on_amd(state.amd, human_assisted=human_assisted, consented=mode == "ai_consented")
    if machine is not None:
        state.final_outcome = machine
        if (state.amd == "machine-vm" and mode == "ai_consented"
                and campaign.get("voicemail_policy") == "leave_message"):
            handle = session.say(persona.voicemail_message(sales.offer), allow_interruptions=False, add_to_chat_ctx=False)
            await handle.wait_for_playout()
        _mark_end(state, "system")
        await hang_up(machine)
        return
    if human_assisted:
        return                                    # the person talks; the agent listens and takes notes

    session.say(persona.opener(sales.offer, sales.variant, lead, mode, callback=callback), allow_interruptions=True)
