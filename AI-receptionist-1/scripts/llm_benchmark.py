"""
Compare LLMs for the voice agent (plan 2.1 / A1.5): time to first token and whether they do the
right thing on dental calls, with the real dental prompt and tools. A report only: nothing switches.

    uv run python -m scripts.llm_benchmark                       # gpt-4.1-mini vs gpt-4.1-nano vs gpt-4o-mini
    uv run python -m scripts.llm_benchmark gpt-4.1-mini gpt-5-mini --runs 5

Calls the OpenAI API directly (store=False, no database). A few cents per run. Each case runs
`--runs` times per model; the first run of a model warms the prompt cache, so "cached" shows what a
real call (same prompt prefix every turn) gets.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.prompts.builder import BusinessSnapshot, build_voice_prompt
from core.tenancy.models import HoursEntry, TenantConfig
from core.tools.registry import schemas_for

DEFAULT_MODELS = ("gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini")

# (name, caller says, check(text, tools) -> ok)
CASES: List[Tuple[str, str, Any]] = [
    ("booking", "Hi, I'd like a check-up and clean next Tuesday afternoon please.",
     lambda text, tools: "check_availability" in tools or "date of birth" in text.lower()
     or "new or existing" in text.lower() or "existing patient" in text.lower()),
    ("red flag", "My face has swollen up on one side and it's getting hard to swallow.",
     lambda text, tools: "000" in text and "book_appointment" not in tools),
    ("implant price", "How much does a dental implant cost?",
     lambda text, tools: "from" in text.lower() and "book_appointment" not in tools),
    ("cancel identity", "I need to cancel my appointment this week.",
     lambda text, tools: "cancel_appointment" not in tools and ("date of birth" in text.lower() or "name" in text.lower())),
]


def _tenant() -> Tuple[TenantConfig, BusinessSnapshot]:
    t = TenantConfig.model_validate({
        "_id": "ten_bench", "name": "Harbour Smiles Dental", "slug": "bench", "vertical": "dental", "country": "AU",
        "timezone": "Australia/Sydney", "currency": "AUD", "phone": "+61290000000",
        "hours": [HoursEntry(dow=d, open="08:00", close="18:00").model_dump() for d in range(6)]})
    snap = BusinessSnapshot(
        services=[{"_id": "s1", "name": "Check-up and clean", "duration_min": 45, "category": "routine",
                   "price": {"display": "$220"}},
                  {"_id": "s2", "name": "Implant consultation", "duration_min": 45, "category": "implant",
                   "price": {"display": "$150"}},
                  {"_id": "s3", "name": "Single implant", "duration_min": 90, "category": "implant",
                   "price": {"display": "$4,500"}}],
        resources=[{"_id": "r1", "name": "Dr Lee", "kind": "staff"}])
    return t, snap


async def _one(client: Any, model: str, system: str, tools: List[Dict[str, Any]], user: str) -> Dict[str, Any]:
    started = time.perf_counter()
    first: Optional[float] = None
    text, called = "", set()
    cached = prompt = 0
    # Reasoning models (gpt-5*, o*) take no temperature; give them the least thinking, as a voice turn would.
    tuning: Dict[str, Any] = ({"reasoning_effort": "minimal"} if model.startswith(("gpt-5", "o"))
                              else {"temperature": 0.3})
    stream = await client.chat.completions.create(
        model=model, stream=True, store=False, tools=tools, **tuning,
        stream_options={"include_usage": True},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    async for chunk in stream:
        if chunk.usage:
            prompt = chunk.usage.prompt_tokens or 0
            details = getattr(chunk.usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        for choice in chunk.choices or []:
            delta = choice.delta
            if first is None and (delta.content or delta.tool_calls):
                first = time.perf_counter() - started
            if delta.content:
                text += delta.content
            for tc in delta.tool_calls or []:
                if tc.function and tc.function.name:
                    called.add(tc.function.name)
    return {"ttft": first, "total": time.perf_counter() - started, "text": text, "tools": called,
            "cached": cached, "prompt": prompt}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("models", nargs="*", default=list(DEFAULT_MODELS))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is empty")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    t, snap = _tenant()
    system = build_voice_prompt(t, snap, caller_phone="+61412345678")
    tools = schemas_for(t)
    print(f"Prompt: {len(system)} chars, {len(tools)} tools, {args.runs} runs per case\n")
    print(f"{'model':<16}{'TTFT p50':>10}{'TTFT p95':>10}{'correct':>10}{'cached':>9}")
    for model in args.models:
        ttfts: List[float] = []
        ok = total = 0
        cached = prompt = 0
        misses: List[str] = []
        try:
            for name, user, check in CASES:
                for _ in range(args.runs):
                    r = await _one(client, model, system, tools, user)
                    total += 1
                    if r["ttft"] is not None:
                        ttfts.append(r["ttft"])
                    if check(r["text"], r["tools"]):
                        ok += 1
                    else:
                        misses.append(f"{name}: {r['text'][:90]!r} tools={sorted(r['tools'])}")
                    cached += r["cached"]
                    prompt += r["prompt"]
        except Exception as e:                  # e.g. the model isn't available on this account
            print(f"{model:<16}  unavailable: {type(e).__name__}: {str(e)[:80]}")
            continue
        ttfts.sort()
        p50 = statistics.median(ttfts) if ttfts else float("nan")
        p95 = ttfts[min(len(ttfts) - 1, int(round(0.95 * (len(ttfts) - 1))))] if ttfts else float("nan")
        print(f"{model:<16}{p50:>9.2f}s{p95:>9.2f}s{ok:>6}/{total:<3}{(cached / prompt if prompt else 0):>8.0%}")
        for m in misses[:4]:
            print(f"    miss  {m}")
    print("\nTTFT here is model + network from this machine; on a call add STT end-of-turn and TTS first byte.")


if __name__ == "__main__":
    asyncio.run(main())
