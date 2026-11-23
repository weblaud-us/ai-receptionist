"""
LLM Provider — OpenAI only.
Switch model via OPENAI_MODEL in .env
"""
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, AsyncIterator, Optional
from config import settings

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
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            store=settings.OPENAI_STORE,      # never kept by OpenAI (audit P0-06)
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
            store=settings.OPENAI_STORE,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


_provider: Optional[BaseLLMProvider] = None


def get_llm_provider() -> BaseLLMProvider:
    global _provider
    if _provider is None:
        _provider = OpenAIProvider()
    return _provider
