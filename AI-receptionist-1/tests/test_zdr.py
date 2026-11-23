"""Audit P0-06: no call to OpenAI asks it to keep the conversation (store=False everywhere)."""

import ast
from pathlib import Path
from types import SimpleNamespace

from config import settings

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [p for d in ("agent", "ai", "core", "routers", "services", "jobs") for p in (ROOT / d).rglob("*.py")]


def test_every_chat_completion_passes_store():
    missing = []
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "create" and "completions" in ast.unparse(node.func)):
                    continue
                direct = any(k.arg == "store" for k in node.keywords)
                # create(**kwargs): the function must put "store" into the dict it passes
                via_kwargs = any(k.arg is None for k in node.keywords) and "store=" in ast.unparse(fn)
                if not (direct or via_kwargs):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert missing == [], missing


async def test_the_call_summary_is_not_stored(monkeypatch):
    seen: dict = {}

    class FakeCompletions:
        async def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Booked a check-up."))])

    class FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    import openai

    from agent import summary

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    text = await summary.llm_summary([{"role": "user", "text": "I have a toothache"}])
    assert text == "Booked a check-up." and seen["store"] is False
