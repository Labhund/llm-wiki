from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import litellm

from llm_wiki.daemon.llm_queue import LLMQueue

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    tokens_used: int


class LLMClient:
    """Routes LLM completion requests through the daemon's concurrency queue.

    Wraps litellm.acompletion. Supports the litellm proxy via api_base/api_key.
    For local-instruct served by the litellm proxy on port 4000, use:
        model="openai/local-instruct", api_base="http://localhost:4000".
    """

    def __init__(
        self,
        queue: LLMQueue,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._queue = queue
        self._model = model
        self._api_base = api_base
        self._api_key = api_key

    async def complete(
        self, messages: list[dict[str, str]], temperature: float = 0.7
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""

        async def _call() -> LLMResponse:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": temperature,
            }
            if self._api_base is not None:
                kwargs["api_base"] = self._api_base
            if self._api_key is not None:
                kwargs["api_key"] = self._api_key

            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0
            self._queue.record_tokens(tokens)
            return LLMResponse(content=content, tokens_used=tokens)

        return await self._queue.submit(_call, priority="query")
