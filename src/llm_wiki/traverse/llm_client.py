from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

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
    cached_tokens: int = 0   # tokens served from KV cache (0 if provider doesn't report)

    @property
    def tokens_used(self) -> int:
        """Total tokens (input + output, unweighted). Used for context budget tracking."""
        return self.input_tokens + self.output_tokens


class LLMClient:
    """Routes LLM completion requests through the daemon's concurrency queue.

    Wraps litellm.acompletion. Supports the litellm proxy via api_base/api_key.
    For local-instruct served by the litellm proxy on port 4000, use:
        model="openai/local-instruct", api_base="http://localhost:4000".

    When ``trace_fn`` is provided, it is awaited after every successful completion
    with a dict containing the label, messages, response, token counts, and latency.
    Use this to capture the full LLM call trace for debugging.
    """

    def __init__(
        self,
        queue: LLMQueue,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        trace_fn: "Callable[[dict], Awaitable[None]] | None" = None,
    ) -> None:
        self._queue = queue
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._timeout = timeout
        self._trace_fn = trace_fn

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        priority: str = "query",
        label: str = "unknown",
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""
        t0 = time.monotonic()

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
                    if self._timeout is not None:
                        kwargs["timeout"] = self._timeout

                    response = await litellm.acompletion(**kwargs)
                    content = response.choices[0].message.content
                    usage = response.usage
                    input_tokens = usage.prompt_tokens if usage else 0
                    output_tokens = usage.completion_tokens if usage else 0
                    # cached_tokens: reported by OpenAI-compatible providers in
                    # prompt_tokens_details.cached_tokens; Anthropic uses a
                    # different field name but litellm normalises it.
                    cached_tokens = 0
                    if usage:
                        details = getattr(usage, "prompt_tokens_details", None)
                        if details:
                            cached_tokens = getattr(details, "cached_tokens", 0) or 0
                    self._queue.record_tokens(input_tokens, output_tokens)
                    return LLMResponse(
                        content=content,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_tokens=cached_tokens,
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

        resp = await self._queue.submit(_call, priority=priority, label=label)

        if self._trace_fn is not None:
            try:
                await self._trace_fn({
                    "label": label,
                    "model": self._model,
                    "temperature": temperature,
                    "priority": priority,
                    "messages": messages,
                    "response": resp.content,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cached_tokens": resp.cached_tokens,
                    "latency_s": round(time.monotonic() - t0, 3),
                })
            except Exception:
                logger.warning("trace_fn raised — tracing suppressed for this call", exc_info=True)

        return resp
