"""
LLM Provider — OpenAI (primary) + Gemini (alternative).
LM Studio / local models removed for clean production code.
Switch via LLM_PROVIDER=openai|gemini in .env
"""
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, AsyncIterator, Optional
from config import settings, LLMProvider

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    @abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.6,
        max_tokens: int = 1024,
        model: Optional[str] = None,
    ) -> Dict:
        """Returns {content, tool_calls, usage}"""
        pass

    @abstractmethod
    async def stream_completion(
        self,
        messages: List[Dict],
        temperature: float = 0.6,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        pass


# ─────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────

class OpenAIProvider(BaseLLMProvider):

    def __init__(self):
        from openai import AsyncOpenAI
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model  = settings.OPENAI_MODEL
        logger.info("🤖 LLM: OpenAI (%s)", self.model)

    async def chat_completion(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.6,
        max_tokens: int = 1024,
        model: Optional[str] = None,
    ) -> Dict:
        # Always use the standard chat-completions model.
        # OPENAI_VOICE_MODEL (realtime) is only for the LiveKit agent.
        use_model = self.model
        kwargs = dict(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = tool_choice

        response = await self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                    "type": "function",
                }
                for tc in msg.tool_calls
            ]

        return {
            "content":    msg.content or "",
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens":     response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
        }

    async def stream_completion(
        self,
        messages: List[Dict],
        temperature: float = 0.5,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


# ─────────────────────────────────────────────
# Gemini (alternative)
# ─────────────────────────────────────────────

class GeminiProvider(BaseLLMProvider):

    def __init__(self):
        import google.generativeai as genai
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.genai      = genai
        self.model_name = settings.GEMINI_MODEL
        logger.info("🤖 LLM: Google Gemini (%s)", self.model_name)

    def _to_gemini(self, messages):
        system = ""
        history = []
        last_user = ""
        for m in messages:
            r, c = m["role"], m.get("content") or ""
            if r == "system":
                system = c
            elif r == "user":
                last_user = c
                if history:
                    history.append({"role": "user", "parts": [c]})
            elif r == "assistant":
                history.append({"role": "model", "parts": [c]})
        return system, history, last_user

    def _tools_to_gemini(self, tools):
        from google.generativeai.types import FunctionDeclaration, Tool
        decls = [
            FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"].get("description", ""),
                parameters=t["function"].get("parameters", {}),
            )
            for t in tools if t.get("type") == "function"
        ]
        return [Tool(function_declarations=decls)]

    async def chat_completion(self, messages, tools=None, tool_choice="auto",
                               temperature=0.6, max_tokens=1024, model=None) -> Dict:
        import asyncio, json as _json
        system, history, last_msg = self._to_gemini(messages)
        kw = {
            "model_name": (model or self.model_name),
            "generation_config": {"temperature": temperature, "max_output_tokens": max_tokens},
        }
        if system:
            kw["system_instruction"] = system
        if tools:
            kw["tools"] = self._tools_to_gemini(tools)

        chat     = self.genai.GenerativeModel(**kw).start_chat(history=history)
        response = await asyncio.to_thread(chat.send_message, last_msg)

        content    = ""
        tool_calls = None
        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                fc = part.function_call
                tool_calls = [{
                    "id":       f"gemini_{fc.name}",
                    "function": {"name": fc.name, "arguments": _json.dumps(dict(fc.args))},
                    "type":     "function",
                }]
            elif hasattr(part, "text"):
                content += part.text

        return {"content": content, "tool_calls": tool_calls, "usage": {}}

    async def stream_completion(self, messages, temperature=0.6, max_tokens=512) -> AsyncIterator[str]:
        import asyncio
        system, history, last_msg = self._to_gemini(messages)
        model = self.genai.GenerativeModel(
            model_name=self.model_name,
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
            system_instruction=system or None,
        )
        resp = await asyncio.to_thread(model.start_chat(history=history).send_message, last_msg, stream=True)
        for chunk in resp:
            if chunk.text:
                yield chunk.text


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

_provider: Optional[BaseLLMProvider] = None


def get_llm_provider() -> BaseLLMProvider:
    global _provider
    if _provider is None:
        if settings.LLM_PROVIDER == LLMProvider.GEMINI:
            _provider = GeminiProvider()
        else:
            # Default to OpenAI (also covers any unknown value)
            _provider = OpenAIProvider()
    return _provider
