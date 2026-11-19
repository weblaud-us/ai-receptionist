"""
Eval cases built from real calls (Roadmap Task 7.1) — one JSON file per case in tests/evals/cases/.

Each case is a business (its facts, services and FAQs), what the caller said turn by turn, and what a
good assistant does: tools it must / must not call and an intent the judge LLM checks the final
reply against. Cases come from reviewed pilot calls (platform admin → Transcript review → "Make eval
case" → approve → `python -m scripts.export_eval_cases`), or are written by hand.

Real LLM, so only on request — same switches as test_voice_evals.py:

    RUN_EVALS=1 EVAL_OPENAI_API_KEY=sk-... pytest tests/evals/test_case_evals.py
"""

import json
import os
from pathlib import Path

import pytest

from tests.conftest import livekit_agents_importable

CASES_DIR = Path(__file__).resolve().parent / "cases"
CASES = sorted(CASES_DIR.glob("*.json"))


def _real_openai_key() -> str:
    key = os.environ.get("EVAL_OPENAI_API_KEY", "")
    if not key:
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            from dotenv import dotenv_values

            key = dotenv_values(env_file).get("OPENAI_API_KEY") or ""
    return key


def load_case(path: Path) -> dict:
    case = json.loads(path.read_text(encoding="utf-8"))
    for key in ("id", "title", "tenant", "caller_turns", "expect"):
        assert key in case, f"{path.name}: missing '{key}'"
    assert case["caller_turns"], f"{path.name}: no caller turns"
    return case


def test_case_files_are_valid():
    """Runs without RUN_EVALS: a malformed case file fails CI before anyone pays for an eval run."""
    from core.tenancy.models import TenantConfig

    for path in CASES:
        case = load_case(path)
        t = case["tenant"]
        TenantConfig.from_doc({"_id": "ten_eval", "name": t["name"], "slug": "eval", "vertical": t.get("vertical", "generic"),
                               "timezone": t.get("timezone", "UTC"), "hours": t.get("hours", [])})


RUN = os.environ.get("RUN_EVALS") == "1"
skip_reason = ("evals call the real LLM; set RUN_EVALS=1 to run them" if not RUN
               else "livekit-agents can't be imported on this machine" if not livekit_agents_importable()
               else "no real OpenAI key (EVAL_OPENAI_API_KEY or OPENAI_API_KEY in .env)"
               if not _real_openai_key().startswith("sk-") or _real_openai_key() == "sk-test" else "")


async def _profile_for(case: dict):
    from agent.profile import resolve_profile
    from core.repositories import Repos
    from core.services.tenant_admin import create_tenant
    from core.tenancy.ids import new_id
    from database.mongodb import get_db

    t = case["tenant"]
    tenant = await create_tenant(
        name=t["name"], vertical=t.get("vertical", "generic"), timezone=t.get("timezone", "UTC"),
        country=t.get("country", "US"), currency=t.get("currency", "USD"), tenant_id="ten_eval", status="active",
        extra={"description": t.get("description", ""), "agent": t.get("agent") or {},
               **({"hours": t["hours"]} if "hours" in t else {})},     # [] = closed all week
        seed_template=False)
    repos = Repos(get_db(), tenant.id)
    for i, svc in enumerate(t.get("services") or []):
        await repos.services.insert_one({"_id": new_id("svc"), "active": True, "ai_bookable": True, "resource_ids": [],
                                         "buffer_min": 0, "sort": i, "duration_min": 30, **svc})
    for kb in t.get("knowledge") or []:
        await repos.knowledge.insert_one({"_id": new_id("kb"), "active": True, "tags": [], **kb})
    return await resolve_profile(tenant_id=tenant.id)


@pytest.mark.skipif(bool(skip_reason), reason=skip_reason or "ok")
@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
async def test_real_call_case(path, db, monkeypatch):
    from livekit.agents import AgentSession
    from livekit.plugins import openai as lk_openai

    from agent import session_factory
    from agent.receptionist import ReceptionistAgent
    from agent.state import CallState
    from config import settings
    from core.tenancy import invalidate

    case = load_case(path)
    key = _real_openai_key()
    monkeypatch.setattr(settings, "OPENAI_API_KEY", key)
    monkeypatch.setattr(session_factory, "_openai_client", None)
    invalidate()
    profile = await _profile_for(case)
    assert profile is not None
    state = CallState(call_id=f"eval-{case['id']}", profile=profile, caller_phone="+15555550100")
    judge = lk_openai.LLM(model="gpt-4.1-mini", api_key=key)
    tools: list[str] = []
    result = None
    async with AgentSession(llm=session_factory.build_llm(profile), userdata=state) as session:
        await session.start(ReceptionistAgent(state))
        for turn in case["caller_turns"]:
            result = await session.run(user_input=turn)
            tools += [e.item.name for e in result.events if e.type == "function_call"]
    expect = case["expect"]
    for name in expect.get("must_call") or []:
        assert name in tools, f"{case['title']}: expected a call to {name}, got {tools}"
    for name in expect.get("must_not_call") or []:
        assert name not in tools, f"{case['title']}: {name} must not be called"
    if expect.get("intent"):
        await result.expect.contains_message(role="assistant").judge(judge, intent=expect["intent"])
