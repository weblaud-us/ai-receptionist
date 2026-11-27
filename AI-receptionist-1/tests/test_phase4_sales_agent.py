"""
The sales agent's wiring (agent/sales.py): tools, prompt, the opt-out hook, take-over and how the
attempt's outcome is decided. Needs the LiveKit runtime, so it is skipped where livekit-agents
can't be imported (some Windows builds) and always runs in CI on Linux.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.conftest import livekit_agents_importable

if not livekit_agents_importable():
    pytest.skip("livekit-agents can't be imported on this machine", allow_module_level=True)

from livekit.agents import StopResponse  # noqa: E402

from agent import sales as sales_mod  # noqa: E402
from agent.profile import load_profile  # noqa: E402
from agent.sales import SalesAgent, SalesCallState, final_outcome  # noqa: E402
from core.outbound import models as m  # noqa: E402
from core.outbound import optout  # noqa: E402
from core.outbound import sales_tools  # noqa: E402
from core.outbound.sales_tools import SalesContext  # noqa: E402

PHONE = "+15125550199"


def _lead():
    return {"_id": "lead_1", "phone": PHONE, "business_name": "Joe's Plumbing", "status": "in_progress",
            "timezones": ["America/Chicago"], "timezone": "America/Chicago", "country": "US"}


def _agent(db, **state_kw):
    state = SalesCallState(call_id="out-att_1", profile=load_profile(), direction="outbound", caller_phone=PHONE,
                           attempt_id="att_1", **state_kw)
    ctx = SalesContext(db=db, lead=_lead(), campaign={"_id": "cmp_1", "mode": "ai_cold_b2b_us"},
                       attempt_id="att_1", call_id="out-att_1")
    return SalesAgent(state, ctx), state, ctx


class FakeSession:
    def __init__(self):
        self.said, self.interrupted = [], 0

    def interrupt(self):
        self.interrupted += 1

    def say(self, text, **_):
        self.said.append(text)
        return SimpleNamespace(wait_for_playout=lambda: asyncio.sleep(0))


def test_sales_agent_has_its_own_tools_and_prompt(db):
    agent, _, _ = _agent(db)
    names = {t.info.name for t in agent.tools}
    assert names == {"check_meeting_slots", "book_meeting", "send_info_email", "send_whatsapp_info", "estimate_roi",
                     "schedule_callback", "add_to_dnc", "mark_not_interested", "mark_wrong_number",
                     "transfer_to_founder", "end_call"}
    assert "create_booking" not in names and "take_message" not in names        # none of the receptionist's tools
    assert "You are an AI" in agent.instructions and "Joe's Plumbing" in agent.instructions


async def test_opt_out_is_caught_in_code_before_the_model_answers(db, monkeypatch):
    await db[m.LEADS].insert_one(_lead())
    agent, state, ctx = _agent(db)
    session = FakeSession()
    monkeypatch.setattr(SalesAgent, "session", property(lambda self: session))
    monkeypatch.setattr(sales_mod, "DNC_HANG_UP_S", 0.01)
    hung_up = []

    async def fake_hang_up(reason):
        hung_up.append(reason)
    monkeypatch.setattr(sales_mod, "hang_up", fake_hang_up)

    with pytest.raises(StopResponse):                      # the model never gets to argue
        await agent.on_user_turn_completed(None, SimpleNamespace(text_content="Take me off your list."))
    await asyncio.sleep(0.05)
    assert (await db[m.DNC].find_one({"phone": PHONE}))["reason"] == "keyword_opt_out"
    assert (await db[m.LEADS].find_one({}))["status"] == "dnc"
    assert session.said == [optout.GOODBYE] and session.interrupted == 1
    assert "opt_out" in hung_up and "opt_out_timeout" in hung_up          # the timer fires whatever else happens
    assert final_outcome(state, ctx) == "dnc"

    with pytest.raises(StopResponse):                      # and stays silent for the rest of the call
        await agent.on_user_turn_completed(None, SimpleNamespace(text_content="Hello?"))


async def test_ordinary_replies_go_to_the_model(db):
    agent, _, ctx = _agent(db)
    await agent.on_user_turn_completed(None, SimpleNamespace(text_content="Yes, this is the owner."))
    assert not ctx.opted_out and await db[m.DNC].count_documents({}) == 0


async def test_agent_is_silent_while_a_person_has_the_call_but_still_honours_opt_outs(db, monkeypatch):
    await db[m.LEADS].insert_one(_lead())
    agent, state, ctx = _agent(db, human_in_control=True)
    session = FakeSession()
    monkeypatch.setattr(SalesAgent, "session", property(lambda self: session))
    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(None, SimpleNamespace(text_content="How much does it cost?"))
    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(None, SimpleNamespace(text_content="Stop calling us."))
    assert await db[m.DNC].count_documents({"phone": PHONE}) == 1
    assert session.said == []                              # the person on the call says goodbye, not the agent


def test_hand_over_silences_the_agent_once():
    calls = []
    session = SimpleNamespace(interrupt=lambda: calls.append("interrupt"),
                              output=SimpleNamespace(set_audio_enabled=lambda on: calls.append(on)))
    state = SalesCallState(call_id="out-1", profile=load_profile())
    sales_mod._hand_over(session, state)
    sales_mod._hand_over(session, state)
    assert state.human_in_control and calls == ["interrupt", False]


def test_how_the_attempt_outcome_is_decided(db):
    _, state, ctx = _agent(db)
    assert final_outcome(state, ctx) == "failed"                           # never answered
    state.answered_at = datetime.now(timezone.utc)
    assert final_outcome(state, ctx) == "no_conversation"                  # answered, nothing came of it
    ctx.set_outcome("interested")
    assert final_outcome(state, ctx) == "interested"
    state.final_outcome = "voicemail"                                      # the AMD verdict wins over the tools
    assert final_outcome(state, ctx) == "voicemail"
    ctx.opted_out = True
    assert final_outcome(state, ctx) == "dnc"


def test_shutdown_records_who_ended_the_call_first():
    # SALES_LEARNING_PLAN.md Phase 0: the room closing after our own hang-up is not the prospect hanging up.
    state = SalesCallState(call_id="out-1", profile=load_profile())
    sales_mod._mark_end(state, "agent")
    sales_mod._mark_end(state, "prospect")
    assert state.ended_by == "agent"
    other = SalesCallState(call_id="out-2", profile=load_profile())
    sales_mod._mark_end(other, "prospect")
    assert other.ended_by == "prospect"


def test_only_a_take_over_token_counts_as_an_operator():
    assert sales_mod._is_operator(SimpleNamespace(attributes={"role": "takeover"}))
    assert not sales_mod._is_operator(SimpleNamespace(attributes={}))
    assert not sales_mod._is_operator(SimpleNamespace(attributes=None))


def test_sales_worker_registers_under_its_own_agent_name():
    from agent import sales_worker, worker
    from config import settings

    assert sales_worker.server._agent_name == settings.SALES_AGENT_NAME != worker.server._agent_name


async def test_plain_not_interested_is_not_an_opt_out(db):
    await db[m.LEADS].insert_one(_lead())
    agent, _, ctx = _agent(db)
    await sales_tools.mark_not_interested(ctx, {"reason": "no budget"})
    assert "mark_not_interested" in {t.info.name for t in agent.tools}
    assert ctx.outcome == "not_interested" and not ctx.opted_out
    assert not await db[m.DNC].count_documents({}) and final_outcome(SalesCallState(call_id="x", profile=load_profile()), ctx) == "not_interested"


async def test_the_agent_speaks_for_the_offer_version_the_attempt_was_dialled_with(db):
    from core.outbound.offer_seeds import WEBLAUD_ID
    from migrations import m0006_sales_offers

    await m0006_sales_offers.run(db, apply=True)
    offer = await sales_mod.load_offer(db, {"offer_id": WEBLAUD_ID, "offer_version": 1})
    state = SalesCallState(call_id="out-att_2", profile=load_profile(), direction="outbound", caller_phone=PHONE)
    ctx = SalesContext(db=db, lead=_lead(), campaign={"_id": "cmp_2", "mode": "ai_cold_b2b_us"}, attempt_id="att_2",
                       call_id="out-att_2", offer=offer, variant_id="custom_software")
    agent = SalesAgent(state, ctx)
    assert "on behalf of WebLaud" in agent.instructions and "Which tools or spreadsheets" in agent.instructions
    legacy = await sales_mod.load_offer(db, {"persona_id": "sales_v1"})
    assert legacy["id"] == "off_laudline" and legacy["version"] == 0
