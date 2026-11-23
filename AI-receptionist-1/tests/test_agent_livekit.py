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


async def test_slow_tools_run_inside_a_filler_and_fast_ones_dont(db):
    from contextlib import asynccontextmanager

    opened: list = []

    class Ctx:
        userdata = _state(caller_phone="+8801711111111")

        @asynccontextmanager
        async def with_filler(self, source, *, delay=0, interval=None, max_steps=None):
            opened.append((source(0), delay, max_steps))
            yield

    tools = {_name(t): t for t in shared_tools(load_profile())}
    await tools["take_message"](raw_arguments={"caller_name": "Rahim", "callback_number": "+8801711111111",
                                               "message": "Late check-out?"}, context=Ctx())
    assert opened == []                                   # nothing to wait for: no filler
    slow = next(n for n in ("check_availability", "check_room_availability") if n in tools)
    await tools[slow](raw_arguments={"date": "2026-12-01"}, context=Ctx())
    assert len(opened) == 1 and opened[0][1] == 0.7 and opened[0][2] == 1 and opened[0][0]


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


def _voices(monkeypatch, **kw):
    """Every voice off, then only the ones given on (keys / Kokoro URL / order)."""
    for name, val in {"ELEVENLABS_API_KEY": "", "CARTESIA_API_KEY": "", "DEEPGRAM_API_KEY": "",
                      "KOKORO_BASE_URL": "", "OPENAI_API_KEY": "", **kw}.items():
        monkeypatch.setattr(settings, name, val)


async def test_deepgram_aura_first_kokoro_then_openai(monkeypatch):
    from livekit.agents import tts as lk_tts
    from livekit.plugins import deepgram

    _voices(monkeypatch, VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="kokoro,openai",
            DEEPGRAM_API_KEY="dg-test", KOKORO_BASE_URL="http://kokoro:8880/v1", OPENAI_API_KEY="sk-test",
            DEEPGRAM_TTS_MODEL="aura-2-luna-en")
    tts = session_factory.build_tts(load_profile())
    assert isinstance(tts, lk_tts.FallbackAdapter)
    engines = tts._tts_instances
    assert isinstance(engines[0], deepgram.TTS) and engines[0]._opts.model == "aura-2-luna-en"
    assert "kokoro" in str(engines[1]._client.base_url) and len(engines) == 3


async def test_a_business_aura_voice_is_used_and_other_ids_never_reach_deepgram(monkeypatch):
    from types import SimpleNamespace

    from livekit.plugins import deepgram

    _voices(monkeypatch, VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="", DEEPGRAM_API_KEY="dg-test",
            DEEPGRAM_TTS_MODEL="aura-2-andromeda-en")
    profile = load_profile()

    def as_business(provider, voice_id):
        agent = profile.tenant.agent.model_copy(update={"voice_provider": provider, "voice_id": voice_id})
        tenant = profile.tenant.model_copy(update={"agent": agent})
        return SimpleNamespace(tenant=tenant, voice_provider=provider, voice_id=voice_id,
                               tts_language="en")

    assert session_factory.aura_model(as_business("deepgram", "aura-2-theia-en")) == "aura-2-theia-en"
    # An ElevenLabs id on a business that switched to Deepgram's fallback: the platform Aura default
    assert session_factory.aura_model(as_business("elevenlabs", "EST9Ui6982FZPSi7gCHi")) == "aura-2-andromeda-en"
    assert session_factory.aura_model(as_business("deepgram", "")) == "aura-2-andromeda-en"
    tts = session_factory._tts_for("deepgram", as_business("deepgram", "aura-2-draco-en"))
    assert isinstance(tts, deepgram.TTS) and tts._opts.model == "aura-2-draco-en"


async def test_fallback_voices_keep_the_practice_accent_and_their_own_ids(monkeypatch):
    """An AU practice on Aura: if Aura fails, ElevenLabs and Cartesia speak in their AU voices —
    the Aura model name is never sent to them."""
    from types import SimpleNamespace

    from livekit.agents import tts as lk_tts
    from livekit.plugins import cartesia, deepgram, elevenlabs

    _voices(monkeypatch, VOICE_TTS_PROVIDER="elevenlabs", VOICE_TTS_FALLBACKS="cartesia",
            DEEPGRAM_API_KEY="dg-test", ELEVENLABS_API_KEY="el-test", CARTESIA_API_KEY="ca-test",
            ELEVENLABS_VOICE_ID_AU="el-au-voice", CARTESIA_VOICE_ID_AU="ca-au-voice",
            ELEVENLABS_TTS_MODEL="eleven_v4_turbo", ELEVENLABS_ENCODING="pcm_24000",
            DEEPGRAM_TTS_MODEL="flux-hannah-en", DEEPGRAM_TTS_MODEL_AU="")
    base = load_profile()
    agent = base.tenant.agent.model_copy(update={"voice_provider": "deepgram", "voice_id": "aura-2-theia-en"})
    tenant = base.tenant.model_copy(update={"agent": agent, "country": "AU"})
    p = SimpleNamespace(tenant=tenant, voice_provider="deepgram", voice_id="aura-2-theia-en", tts_language="en")

    el = session_factory._tts_for("elevenlabs", p)
    assert isinstance(el, elevenlabs.TTS) and el._opts.voice_id == "el-au-voice"
    assert el._opts.encoding == "pcm_24000" and el.sample_rate == 24000
    ca = session_factory._tts_for("cartesia", p)
    assert isinstance(ca, cartesia.TTS) and ca._opts.voice == "ca-au-voice"
    dg = session_factory._tts_for("deepgram", p)
    assert isinstance(dg, deepgram.TTS) and dg._opts.model == "aura-2-theia-en"
    assert isinstance(session_factory.build_tts(p), lk_tts.FallbackAdapter)


async def test_a_flux_voice_uses_deepgrams_v2_endpoint_and_aura_stays_on_v1(monkeypatch):
    from types import SimpleNamespace

    from livekit.plugins import deepgram

    _voices(monkeypatch, VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="", DEEPGRAM_API_KEY="dg-test",
            DEEPGRAM_TTS_MODEL="flux-hannah-en", DEEPGRAM_TTS_MODEL_AU="")
    base = load_profile()

    def biz(voice_id):
        agent = base.tenant.agent.model_copy(update={"voice_provider": "deepgram", "voice_id": voice_id})
        return SimpleNamespace(tenant=base.tenant.model_copy(update={"agent": agent, "country": "AU"}),
                               voice_provider="deepgram", voice_id=voice_id, tts_language="en")

    flux = session_factory._tts_for("deepgram", biz(""))
    assert isinstance(flux, deepgram.TTSv2) and flux._opts.model == "flux-hannah-en"
    aura = session_factory._tts_for("deepgram", biz("aura-2-theia-en"))
    assert isinstance(aura, deepgram.TTS) and not isinstance(aura, deepgram.TTSv2)


async def test_greeting_cache_follows_the_voice_that_speaks(monkeypatch):
    from types import SimpleNamespace

    _voices(monkeypatch, VOICE_TTS_PROVIDER="elevenlabs", VOICE_TTS_FALLBACKS="deepgram",
            ELEVENLABS_API_KEY="el-test", DEEPGRAM_API_KEY="dg-test", ELEVENLABS_VOICE_ID_GB="el-gb-1",
            DEEPGRAM_TTS_MODEL="aura-2-andromeda-en", DEEPGRAM_TTS_MODEL_GB="aura-2-pandora-en")
    base = load_profile()
    agent = base.tenant.agent.model_copy(update={"voice_provider": "elevenlabs", "voice_id": ""})
    tenant = base.tenant.model_copy(update={"agent": agent, "country": "GB"})
    p = SimpleNamespace(tenant=tenant, voice_provider="elevenlabs", voice_id="", tts_language="en")
    assert session_factory.spoken_voice(p) == "elevenlabs:el-gb-1"
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID_GB", "el-gb-2")     # a new platform default…
    assert session_factory.spoken_voice(p) == "elevenlabs:el-gb-2"          # …is a new greeting cache key
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "")
    assert session_factory.spoken_voice(p) == "deepgram:aura-2-pandora-en"


async def test_kokoro_alone_points_at_the_local_server(monkeypatch):
    from livekit.plugins import openai as lk_openai

    _voices(monkeypatch, VOICE_TTS_PROVIDER="kokoro", KOKORO_BASE_URL="http://localhost:8880/v1",
            KOKORO_VOICE="af_bella")
    tts = session_factory.build_tts(load_profile())
    assert isinstance(tts, lk_openai.TTS)
    assert "localhost:8880" in str(tts._client.base_url) and tts._opts.voice == "af_bella"


async def test_voice_without_key_or_url_is_skipped(monkeypatch):
    from livekit.plugins import openai as lk_openai

    # Deepgram first by .env, but no key and no Kokoro URL: only OpenAI is usable
    _voices(monkeypatch, VOICE_TTS_PROVIDER="deepgram", VOICE_TTS_FALLBACKS="kokoro,openai", OPENAI_API_KEY="sk-test")
    assert isinstance(session_factory.build_tts(load_profile()), lk_openai.TTS)


async def test_no_voice_configured_is_a_clear_error(monkeypatch):
    _voices(monkeypatch, VOICE_TTS_PROVIDER="deepgram")
    with pytest.raises(RuntimeError, match="No voice configured"):
        session_factory.build_tts(load_profile())


async def test_session_builds_with_turn_detection(monkeypatch):
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "el-test")
    session = session_factory.build_session({"vad": None}, _state())
    assert session.userdata.call_id == "call-lk"


def test_turn_taking_is_explicit_about_the_cloud_models(monkeypatch):
    # Unset, `start` on our own hosts silently picks the local turn model and no adaptive barge-in.
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "APIkey")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "secret")
    th = session_factory.turn_handling(load_profile())
    assert th["interruption"]["mode"] == settings.VOICE_INTERRUPT_MODE == "adaptive"
    assert th["interruption"]["min_duration"] == 0.25 and th["interruption"]["resume_false_interruption"]
    assert th["preemptive_generation"] == {"enabled": True, "preemptive_tts": True}
    assert th["endpointing"]["mode"] == "dynamic"
    assert th["turn_detection"].model == "turn-detector-v1"
    monkeypatch.setattr(settings, "VOICE_PREEMPTIVE_TTS", False)
    monkeypatch.setattr(settings, "VOICE_INTERRUPT_MODE", "vad")
    th = session_factory.turn_handling(load_profile())
    assert th["preemptive_generation"]["preemptive_tts"] is False and th["interruption"]["mode"] == "vad"
    # No LiveKit keys (tests, a laptop): the local model, never a crash.
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "")
    assert session_factory.turn_detector_version() == "v1-mini"
    assert session_factory.turn_handling(load_profile())["turn_detection"].model == "turn-detector-v1-mini"


async def test_turn_tuning_changes_a_real_sessions_endpointing(monkeypatch):
    from agent.turn_tuning import PATIENT_ENDPOINTING, TurnTuner

    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "el-test")
    state = _state()
    session = session_factory.build_session({"vad": None}, state)
    normal = session_factory.endpointing(state.profile)
    tuner = TurnTuner(lambda o: session.update_options(endpointing_opts=o), normal)
    tuner.on_assistant_message("What's the best number to reach you on?")
    assert session._opts.endpointing["max_delay"] == PATIENT_ENDPOINTING["max_delay"]
    tuner.on_user_message()
    assert session._opts.endpointing["max_delay"] == normal["max_delay"]
    assert session._opts.endpointing["min_delay"] == normal["min_delay"]


# ── Phase 3: fallbacks, provider health, busy lines, per-business call limit ──


async def test_llm_falls_back_to_anthropic_when_configured(monkeypatch):
    from livekit.agents import llm as lk_llm
    from livekit.plugins import anthropic
    from livekit.plugins import openai as lk_openai

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    assert isinstance(session_factory.build_llm(load_profile()), lk_openai.LLM)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    llm = session_factory.build_llm(load_profile())
    assert isinstance(llm, lk_llm.FallbackAdapter)
    primary, fallback = llm._llm_instances
    assert isinstance(primary, lk_openai.LLM) and isinstance(fallback, anthropic.LLM)
    assert fallback.model == settings.VOICE_LLM_FALLBACK_MODEL


async def test_stt_falls_back_to_openai_when_deepgram_fails(monkeypatch):
    from livekit.agents import stt as lk_stt
    from livekit.plugins import deepgram, silero

    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "VOICE_STT_FALLBACK", True)
    stt = session_factory.build_stt(load_profile(), silero.VAD.load())
    assert isinstance(stt, lk_stt.FallbackAdapter)
    assert isinstance(stt._stt_instances[0], deepgram.STT)
    monkeypatch.setattr(settings, "VOICE_STT_FALLBACK", False)
    assert isinstance(session_factory.build_stt(load_profile(), silero.VAD.load()), deepgram.STT)


async def test_provider_health_is_recorded_from_a_real_session(monkeypatch):
    from types import SimpleNamespace

    from livekit.agents import AgentSession

    from agent.health import attach_provider_health

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    state = _state()
    session = AgentSession(llm=session_factory.build_llm(state.profile), userdata=state)
    attach_provider_health(session, state)
    session.emit("error", SimpleNamespace(error=SimpleNamespace(type="llm_error", label="openai", recoverable=True)))
    session.llm.emit("llm_availability_changed", SimpleNamespace(llm=object(), available=False))
    assert state.provider_errors == {"llm": 1}
    assert state.fallbacks == {"llm": 1}


async def test_busy_line_agent_apologises_and_ends(monkeypatch):
    from agent.receptionist import BUSY_MESSAGE, BusyLineAgent

    said = []

    class Handle:
        async def wait_for_playout(self):
            return None

    class FakeSession:
        def say(self, text, **_):
            said.append(text)
            return Handle()

        def shutdown(self):
            said.append("<shutdown>")

    state = _state()
    agent = BusyLineAgent(state)
    monkeypatch.setattr(BusyLineAgent, "session", property(lambda self: FakeSession()))
    await agent.on_enter()
    assert said == [BUSY_MESSAGE.format(name=state.profile.tenant.name), "<shutdown>"]
    assert state.outcome == "busy" and state.ended_reason == "concurrency_limit"


async def test_worker_enforces_the_per_business_call_limit(redis):
    from agent import worker
    from agent.profile import VoiceProfile

    tenant = load_profile().tenant.model_copy(deep=True)
    tenant.id = "ten_busy"
    tenant.limits.max_concurrent_calls = 1
    one = CallState(call_id="room-1", profile=VoiceProfile(tenant=tenant))
    two = CallState(call_id="room-2", profile=VoiceProfile(tenant=tenant))
    assert await worker._line_free(one)
    assert not await worker._line_free(two)
    assert await worker._line_free(_state())          # the legacy .env hotel has no plan limit


# ── Landing-page live demo ─────────────────────────────────────────────────

def _participant(kind, attrs):
    from types import SimpleNamespace

    return SimpleNamespace(kind=kind, attributes=attrs)


async def test_demo_lead_comes_only_from_a_browser_token_and_only_on_a_demo_practice(monkeypatch):
    from livekit import rtc

    from agent import worker
    from agent.profile import VoiceProfile

    browser = worker._caller_attributes(_participant(rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
                                                     {"tenant_id": "ten_demo_au", "web_demo_lead": "dml_1"}))
    sip = worker._caller_attributes(_participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
                                                 {"web_demo_lead": "dml_1", "sip.trunkPhoneNumber": "+61255500123"}))
    assert browser["web_demo_lead"] == "dml_1" and sip["web_demo_lead"] is None

    monkeypatch.setattr(settings, "WEB_DEMO_TENANT_AU", "ten_demo_au")
    demo = load_profile().tenant.model_copy(update={"id": "ten_demo_au"})
    real = load_profile().tenant.model_copy(update={"id": "ten_real"})
    ctx = type("Ctx", (), {"room": type("Room", (), {"name": "demo-1"})()})()
    assert worker._call_state(ctx, browser, VoiceProfile(tenant=demo)).web_demo_lead_id == "dml_1"
    assert worker._call_state(ctx, browser, VoiceProfile(tenant=real)).web_demo_lead_id is None


async def test_browser_calls_get_full_band_noise_cancellation(monkeypatch):
    from agent import worker

    monkeypatch.setattr(settings, "VOICE_NOISE_CANCELLATION", True)
    phone, browser = worker._noise_filter("phone"), worker._noise_filter("browser")
    assert phone is not None and browser is not None
    assert phone.options["modelPath"] != browser.options["modelPath"]     # telephony model vs full-band BVC
    monkeypatch.setattr(settings, "VOICE_NOISE_CANCELLATION", False)
    assert worker._noise_filter("browser") is None


async def test_demo_cap_says_the_wrap_up_then_ends_the_call():
    import asyncio

    from agent import web_demo as demo_calls

    said, ended = [], []

    class Session:
        def say(self, text, **kw):
            said.append((text, kw.get("allow_interruptions")))

    async def end(reason):
        ended.append(reason)

    wrap_up, cap = demo_calls.start_timers(Session(), 180, on_end=end)
    assert round(cap.when() - wrap_up.when()) == demo_calls.WRAP_UP_BEFORE_S
    for handle in (wrap_up, cap):          # run them now rather than wait three minutes
        handle._run()
        handle.cancel()                    # (cancel clears the callback, so only after running it)
    await asyncio.sleep(0)
    assert said == [(demo_calls.WRAP_UP_LINE, False)] and ended == ["demo_time_limit"]


# ── Phase 2.2: warm transfer ───────────────────────────────────────────────

class _IO:
    def __init__(self):
        self.enabled = True

    def set_audio_enabled(self, on):
        self.enabled = on


class _TransferCtx:
    def __init__(self):
        class Session:
            input = _IO()
            output = _IO()
        self.session = Session()


class _Sip:
    attributes = {"sip.trunkPhoneNumber": "+61290000000"}


def _fake_task(monkeypatch, *, fail: bool):
    import livekit.agents.beta.workflows as wf
    from livekit.agents.llm.tool_context import ToolError

    seen: dict = {}

    class FakeWarmTransfer:
        def __init__(self, sip_call_to=None, **kw):
            seen.update(kw, sip_call_to=sip_call_to)

        def __await__(self):
            async def run():
                if fail:
                    raise ToolError("could not dial human agent")
                return object()
            return run().__await__()

    monkeypatch.setattr(wf, "WarmTransferTask", FakeWarmTransfer)
    return seen


async def test_a_warm_transfer_briefs_staff_then_the_assistant_goes_quiet(monkeypatch):
    from agent import transfer

    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_abc")
    seen = _fake_task(monkeypatch, fail=False)
    state = _state()
    agent = ReceptionistAgent(state)
    ctx = _TransferCtx()
    out = await agent._warm_transfer(ctx, _Sip(), "Wants to discuss an invoice")
    assert out == transfer.CONNECTED and state.transfer == "ok" and state.outcome == "transferred"
    assert seen["sip_trunk_id"] == "ST_abc" and seen["sip_number"] == "+61290000000"
    assert seen["ringing_timeout"] == transfer.RINGING_TIMEOUT_S
    assert ctx.session.input.enabled is False and ctx.session.output.enabled is False


async def test_nobody_answering_a_warm_transfer_brings_the_caller_back(monkeypatch):
    from agent import transfer

    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_abc")
    _fake_task(monkeypatch, fail=True)
    state = _state()
    ctx = _TransferCtx()
    out = await ReceptionistAgent(state)._warm_transfer(ctx, _Sip(), "Billing question")
    assert out == transfer.NO_ANSWER and state.transfer == "failed" and state.outcome != "transferred"
    assert ctx.session.input.enabled is True                     # the assistant keeps the caller


# ── Phase 2.5: confirmation calls ──────────────────────────────────────────

def test_the_confirmation_agent_only_gets_the_tools_it_needs():
    from agent.confirm import ConfirmAgent, ConfirmCallState

    state = ConfirmCallState(call_id="confirm-apt_1-1", profile=load_profile(), direction="outbound",
                             caller_phone="+61412345678", appointment_id="apt_1")
    agent = ConfirmAgent(state, "tomorrow at 10 30 a.m.")
    names = {_name(t) for t in agent.tools}
    assert {"confirm_appointment", "end_call"} <= names
    assert names - {"confirm_appointment", "end_call"} <= {"check_availability", "reschedule_appointment",
                                                           "cancel_appointment", "take_message"}
    assert "book_appointment" not in names and "transfer_to_human" not in names
    assert "they did not call you" in agent.instructions


async def test_confirming_records_the_outcome():
    from agent.confirm import ConfirmAgent, ConfirmCallState, status_for

    state = ConfirmCallState(call_id="confirm-apt_1-1", profile=load_profile(), direction="outbound",
                             caller_phone="+61412345678", appointment_id="apt_1", answered=True, amd="human")
    agent = ConfirmAgent(state, "tomorrow at 10 30 a.m.")
    assert status_for(state) == "no_decision"
    await agent.confirm_appointment(None)
    assert state.confirmed and status_for(state) == "confirmed"
