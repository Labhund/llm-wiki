from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class LLMQueue:
    """Concurrency-limited queue for LLM requests.

    Gates all LLM calls through a semaphore to prevent overloading
    the inference server. Phase 3+ will route traversal/ingest LLM
    calls through this queue.
    """

    # Accepted for API compatibility; scheduling is currently FIFO via semaphore.
    PRIORITY_MAP = {"query": 0, "ingest": 1, "maintenance": 2}

    def __init__(self, max_concurrent: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tokens_used: int = 0
        self._active: int = 0

    async def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        priority: str = "maintenance",
        **kwargs: Any,
    ) -> Any:
        """Submit an async callable, waiting for a concurrency slot."""
        async with self._semaphore:
            self._active += 1
            try:
                return await fn(**kwargs)
            finally:
                self._active -= 1

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def active_count(self) -> int:
        return self._active

    def record_tokens(self, count: int) -> None:
        """Record tokens consumed (for accounting/limits)."""
        self._tokens_used += count
