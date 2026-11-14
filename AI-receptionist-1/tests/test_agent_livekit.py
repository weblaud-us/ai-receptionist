"""
Voice agent wiring that needs the LiveKit runtime: shared tools, agent construction, provider
selection. Skipped where livekit-agents can't be imported (some Windows builds); always runs in
CI on Linux and in `docker compose -f docker-compose.dev.yml run --rm tests`.
"""

import json

import pytest

from tests.conftest import livekit_agents_importable

if not livekit_agents_importable():
    pytest.skip("livekit-agents can't be imported on this machine", allow_module_level=True)

from agent import session_factory  # noqa: E402
from agent.profile import load_profile  # noqa: E402
from agent.receptionist import ReceptionistAgent  # noqa: E402
from agent.state import CallState  # noqa: E402
from agent.tools import shared_tools  # noqa: E402
from config import settings  # noqa: E402
from core.tools.registry import AI_TOOL_SCHEMAS  # noqa: E402


def _state(**kw) -> CallState:
    return CallState(call_id="call-lk", profile=load_profile(), **kw)


def _name(tool) -> str:
    return tool.info.name


def test_voice_uses_the_shared_schemas_unchanged():
    tools = {_name(t): t for t in shared_tools(load_profile())}
    assert set(tools) == {s["function"]["name"] for s in AI_TOOL_SCHEMAS}
    for schema in AI_TOOL_SCHEMAS:
        assert tools[schema["function"]["name"]].info.raw_schema["parameters"] == schema["function"]["parameters"]


async def test_voice_tool_runs_the_shared_implementation(db):
    class Ctx:
        userdata = _state(caller_phone="+8801711111111")

    take_message = next(t for t in shared_tools(load_profile()) if _name(t) == "take_message")
    result = json.loads(await take_message(raw_arguments={
        "caller_name": "Rahim", "callback_number": "+8801711111111", "message": "Late check-out?"}, context=Ctx()))
    assert result["success"]
    assert Ctx.userdata.outcome == "message"
    assert (await db.messages.find_one({"message_id": result["message_id"]}))["channel"] == "voice"


def test_agent_has_voice_only_tools_and_prompt():
    agent = ReceptionistAgent(_state())
    names = {_name(t) for t in agent.tools}
    assert {"end_call", "transfer_to_human", "create_booking", "take_message"} <= names
    assert "You are an AI assistant" in agent.instructions


async def test_stt_falls_back_to_openai_without_deepgram(monkeypatch):
    from livekit.plugins import deepgram
    from livekit.plugins import openai as lk_openai

    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "")
    assert isinstance(session_factory.build_stt(load_profile()), lk_openai.STT)
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-test")
    assert isinstance(session_factory.build_stt(load_profile()), deepgram.STT)


async def test_tts_primary_with_fallback(monkeypatch):
    from livekit.agents import tts as lk_tts
    from livekit.plugins import elevenlabs

    monkeypatch.setattr(settings, "VOICE_TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert isinstance(session_factory.build_tts(load_profile()), elevenlabs.TTS)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    assert isinstance(session_factory.build_tts(load_profile()), lk_tts.FallbackAdapter)


async def test_session_builds_with_turn_detection(monkeypatch):
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "el-test")
    session = session_factory.build_session({"vad": None}, _state())
    assert session.userdata.call_id == "call-lk"
