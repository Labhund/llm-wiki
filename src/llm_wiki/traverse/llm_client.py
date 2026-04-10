from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm

from llm_wiki.daemon.llm_queue import LLMQueue

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}
_RETRY_DELAYS = [5.0, 15.0, 45.0]

# These exception types indicate permanent failures — retrying won't help.
# Check type first because litellm may report unexpected status codes
# (e.g. AuthenticationError with status_code=500 in some environments).
_PERMANENT_EXC_TYPES = (
    litellm.AuthenticationError,
    litellm.BadRequestError,
    litellm.NotFoundError,
    litellm.PermissionDeniedError,
)


def _should_retry(exc: Exception) -> bool:
    """Return True for transient errors that should be retried."""
    if isinstance(exc, _PERMANENT_EXC_TYPES):
        return False
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # Connection/timeout errors have no status_code
    return status in _TRANSIENT_HTTP_CODES


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int

    @property
    def tokens_used(self) -> int:
        """Total tokens (input + output, unweighted). Used for context budget tracking."""
        return self.input_tokens + self.output_tokens


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
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        priority: str = "query",
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""

        async def _call() -> LLMResponse:
            last_exc: Exception | None = None
            for attempt, delay in enumerate([*_RETRY_DELAYS, None]):
                try:
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
                    usage = response.usage
                    input_tokens = usage.prompt_tokens if usage else 0
                    output_tokens = usage.completion_tokens if usage else 0
                    self._queue.record_tokens(input_tokens, output_tokens)
                    return LLMResponse(
                        content=content,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not _should_retry(exc) or delay is None:
                        raise
                    last_exc = exc
                    logger.warning(
                        "LLM call failed (attempt %d/%d, retrying in %.0fs): %s",
                        attempt + 1, len(_RETRY_DELAYS) + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]  # unreachable

        return await self._queue.submit(_call, priority=priority)
