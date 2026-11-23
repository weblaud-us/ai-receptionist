"""
The assistant doesn't hang up on a caller who isn't done, and the caller's history reaches the very
first reply when it loads in time (agent/receptionist.py).
"""

import asyncio
import time

import pytest

from tests.conftest import livekit_agents_importable

if not livekit_agents_importable():
    pytest.skip("livekit-agents can't be imported on this machine", allow_module_level=True)

from livekit.agents import llm as lk_llm  # noqa: E402

from agent import receptionist as rec  # noqa: E402
from agent.profile import load_profile  # noqa: E402
from agent.receptionist import END_CALL_TOO_SOON, ReceptionistAgent, caller_ready_to_end, end_call_check  # noqa: E402
from agent.state import CallState  # noqa: E402
from core.prompts.builder import BASE_VOICE_RULES  # noqa: E402


def _state(**kw) -> CallState:
    return CallState(call_id="call-end", profile=load_profile(), **kw)


def _chat(*turns: tuple) -> lk_llm.ChatContext:
    ctx = lk_llm.ChatContext()
    for role, text in turns:
        ctx.add_message(role=role, content=text)
    return ctx


class _Session:
    def __init__(self):
        self.closed = False

    def shutdown(self):
        self.closed = True


class _RunCtx:
    def __init__(self):
        self.session = _Session()

    async def wait_for_playout(self):
        return None


# ── When may the call end? ─────────────────────────────────────────────────

def test_nothing_from_the_caller_yet_is_not_the_end():
    assert not caller_ready_to_end(_chat(("assistant", "Hi, thanks for calling. How can I help?")))


@pytest.mark.parametrize("said", ["Thanks, bye!", "No, that's all.", "That’s it, thanks", "nothing else, cheers"])
def test_a_caller_saying_goodbye_can_be_let_go(said):
    assert caller_ready_to_end(_chat(("assistant", "You're booked for Tuesday."), ("user", said)))


def test_answering_anything_else_ends_the_call():
    assert caller_ready_to_end(_chat(("assistant", "Is there anything else I can help with?"), ("user", "No.")))


def test_thanks_after_a_booking_is_not_the_end():
    assert not caller_ready_to_end(_chat(("assistant", "You're booked for Tuesday at ten with Dr Lee."),
                                         ("user", "Great, thanks")))


def test_only_messages_up_to_the_callers_latest_count():
    """This turn's own goodbye may already be in the chat; it must not count as a question we asked."""
    ctx = _chat(("assistant", "You're booked for Tuesday."), ("user", "Great, thanks"),
                ("assistant", "Anything else? Goodbye!"))
    assert not caller_ready_to_end(ctx)


def test_we_push_back_once_then_let_the_call_end():
    state = _state()
    ctx = _chat(("assistant", "You're booked for Tuesday."), ("user", "Great, thanks"))
    assert end_call_check(state, ctx) == END_CALL_TOO_SOON
    assert end_call_check(state, ctx) == END_CALL_TOO_SOON          # they haven't spoken since: still ask
    ctx.add_message(role="assistant", content="Is there anything else you need.")   # no question mark
    ctx.add_message(role="user", content="Okay")
    assert end_call_check(state, ctx) is None                         # they answered: let them go


async def test_end_call_too_soon_keeps_the_call_open():
    agent = ReceptionistAgent(_state())
    await agent.update_chat_ctx(_chat(("assistant", "You're booked for Tuesday."), ("user", "Perfect, thanks")))
    run = _RunCtx()
    assert await agent.end_call(run) == END_CALL_TOO_SOON
    assert not run.session.closed and agent.state.ended_reason is None


async def test_end_call_after_anything_else_hangs_up():
    agent = ReceptionistAgent(_state())
    await agent.update_chat_ctx(_chat(("assistant", "Anything else I can help with?"), ("user", "No thanks, bye")))
    run = _RunCtx()
    assert await agent.end_call(run) is None
    assert run.session.closed and agent.state.ended_reason == "agent_hangup"


async def test_the_confirmation_call_asks_before_hanging_up_too():
    from agent.confirm import ConfirmAgent, ConfirmCallState

    state = ConfirmCallState(call_id="confirm-apt_1-1", profile=load_profile(), direction="outbound",
                             caller_phone="+61412345678", appointment_id="apt_1", answered=True, amd="human")
    agent = ConfirmAgent(state, "tomorrow at 10 30 a.m.")
    await agent.update_chat_ctx(_chat(("assistant", "Lovely, you're confirmed."), ("user", "Okay great")))
    assert await agent.end_call(_RunCtx()) == END_CALL_TOO_SOON


def test_the_prompt_asks_before_ending_and_says_how_to_help():
    assert "anything else" in BASE_VOICE_RULES and "# How to help" in BASE_VOICE_RULES
    assert "Never leave a caller with nothing" in BASE_VOICE_RULES


# ── Caller history on the first reply ──────────────────────────────────────

CONTACT = {"name": "Sarah Jones", "upcoming": ["Check-up with Dr Lee, Tuesday at 10am"]}


def _with_lookup(delay: float, contact=CONTACT):
    state = _state(caller_phone="+61412345678")
    agent = ReceptionistAgent(state)

    async def lookup():
        await asyncio.sleep(delay)
        state.contact = contact
        if contact:
            await agent.refresh_instructions()

    agent.history_task = asyncio.ensure_future(lookup())
    return agent


def _first_turn() -> tuple:
    return lk_llm.ChatContext(), lk_llm.ChatMessage(role="user", content=["Hi, I need to move my appointment"])


def _instructions(turn_ctx: lk_llm.ChatContext) -> str:
    return "\n".join(m.text_content or "" for m in turn_ctx.items if getattr(m, "role", "") == "system")


async def test_history_that_loads_quickly_reaches_the_first_reply():
    agent = _with_lookup(0.2)
    turn_ctx, msg = _first_turn()
    await agent.on_user_turn_completed(turn_ctx, msg)
    text = _instructions(turn_ctx)
    assert "Returning caller" in text and "Sarah" in text


async def test_a_slow_lookup_never_holds_the_first_reply_long(monkeypatch):
    monkeypatch.setattr(rec, "HISTORY_WAIT_S", 0.3)
    agent = _with_lookup(1.5)
    turn_ctx, msg = _first_turn()
    started = time.monotonic()
    await agent.on_user_turn_completed(turn_ctx, msg)
    assert time.monotonic() - started < 1.0
    assert "Returning caller" not in _instructions(turn_ctx)
    await agent.history_task                                   # not cancelled: it still lands for later turns
    assert "Returning caller" in agent.instructions


async def test_an_unknown_number_changes_nothing():
    agent = _with_lookup(0.0, contact=None)
    turn_ctx, msg = _first_turn()
    await agent.on_user_turn_completed(turn_ctx, msg)
    assert _instructions(turn_ctx) == "" and "Returning caller" not in agent.instructions


async def test_only_the_first_turn_waits(monkeypatch):
    monkeypatch.setattr(rec, "HISTORY_WAIT_S", 0.3)
    agent = _with_lookup(5.0)
    await agent.on_user_turn_completed(*_first_turn())
    started = time.monotonic()
    await agent.on_user_turn_completed(*_first_turn())
    assert time.monotonic() - started < 0.1
    agent.history_task.cancel()
