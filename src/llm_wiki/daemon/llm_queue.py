from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_HOUR = 3600.0
_DAY = 86400.0

# Output tokens cost ~5x input tokens on most APIs.
_OUTPUT_WEIGHT = 5


def _weighted(input_tokens: int, output_tokens: int) -> int:
    return input_tokens + output_tokens * _OUTPUT_WEIGHT


class LimitExceededError(Exception):
    """Raised when a token-spend limit would be breached."""


@dataclass
class ActiveJob:
    id: int
    label: str        # e.g. "adversary:verify:protein-dj"
    priority: str     # "query" | "ingest" | "maintenance"
    started_at: float # time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at


class LLMQueue:
    """Concurrency-limited queue for LLM requests.

    Gates all LLM calls through a semaphore to prevent overloading
    the inference server.

    Optionally enforces hourly/daily weighted token limits.  The
    weighted cost of a call is ``input_tokens + output_tokens * 5``,
    approximating relative API pricing.  Input and output totals are
    tracked separately so callers can compute exact costs later once
    they know the per-token rates for their model.

    Tracks labeled active jobs and pending count for observability via
    the process-list route.

    Enforcement:
    - ``maintenance`` priority calls are rejected with ``LimitExceededError``
      when the running spend is at or above a limit.
    - ``query`` / ``ingest`` calls (user-initiated) log a warning but proceed.
    """

    PRIORITY_MAP = {"query": 0, "ingest": 1, "maintenance": 2}

    def __init__(
        self,
        max_concurrent: int = 2,
        hourly_limit: int | None = None,
        daily_limit: int | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._pending: int = 0
        self._active_jobs: dict[int, ActiveJob] = {}
        self._next_id: int = 0

        # Separate totals for cost calculation.
        self._input_tokens: int = 0
        self._output_tokens: int = 0

        # Rolling windows: deque of (timestamp, weighted_tokens) pairs.
        self._hourly: collections.deque[tuple[float, int]] = collections.deque()
        self._daily: collections.deque[tuple[float, int]] = collections.deque()

        self._hourly_limit: int | None = hourly_limit
        self._daily_limit: int | None = daily_limit

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        priority: str = "maintenance",
        label: str = "unknown",
        **kwargs: Any,
    ) -> Any:
        """Submit an async callable, waiting for a concurrency slot.

        Raises ``LimitExceededError`` for ``maintenance`` priority calls
        when the hourly or daily weighted token limit is exceeded.  For
        ``query`` and ``ingest`` priority calls the limit is advisory only
        (a warning is logged).
        """
        self._check_limits(priority)
        self._pending += 1
        acquired = False
        try:
            async with self._semaphore:
                acquired = True
                self._pending -= 1
                job_id = self._next_id
                self._next_id += 1
                self._active_jobs[job_id] = ActiveJob(
                    id=job_id,
                    label=label,
                    priority=priority,
                    started_at=time.monotonic(),
                )
                try:
                    return await fn(**kwargs)
                finally:
                    self._active_jobs.pop(job_id, None)
        finally:
            if not acquired:
                self._pending -= 1

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record tokens consumed after a completed LLM call.

        Updates cumulative totals and the rolling spend windows used for
        limit enforcement.
        """
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

        w = _weighted(input_tokens, output_tokens)
        now = time.monotonic()
        self._hourly.append((now, w))
        self._daily.append((now, w))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def input_tokens_total(self) -> int:
        return self._input_tokens

    @property
    def output_tokens_total(self) -> int:
        return self._output_tokens

    @property
    def tokens_used(self) -> int:
        """Total tokens consumed (input + output, unweighted)."""
        return self._input_tokens + self._output_tokens

    @property
    def hourly_weighted(self) -> int:
        """Weighted tokens consumed in the rolling last-hour window."""
        self._sweep(self._hourly, _HOUR)
        return sum(w for _, w in self._hourly)

    @property
    def daily_weighted(self) -> int:
        """Weighted tokens consumed in the rolling last-24h window."""
        self._sweep(self._daily, _DAY)
        return sum(w for _, w in self._daily)

    @property
    def active_count(self) -> int:
        return len(self._active_jobs)

    @property
    def active_jobs(self) -> list[ActiveJob]:
        """Snapshot of currently running jobs."""
        return list(self._active_jobs.values())

    @property
    def pending_count(self) -> int:
        """Number of submit() callers waiting for a semaphore slot."""
        return self._pending

    @property
    def slots_total(self) -> int:
        """Maximum concurrent jobs (semaphore ceiling)."""
        return self._max_concurrent

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sweep(dq: collections.deque[tuple[float, int]], window: float) -> None:
        """Remove entries older than ``window`` seconds from the left."""
        cutoff = time.monotonic() - window
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _check_limits(self, priority: str) -> None:
        """Enforce configured token limits.

        Maintenance calls are hard-blocked; user-facing calls are warned.
        """
        is_maintenance = priority == "maintenance"

        if self._hourly_limit is not None:
            current = self.hourly_weighted  # also sweeps stale entries
            if current >= self._hourly_limit:
                msg = (
                    f"Hourly weighted token limit reached "
                    f"({current}/{self._hourly_limit})"
                )
                if is_maintenance:
                    raise LimitExceededError(msg)
                logger.warning("%s — proceeding (priority=%s)", msg, priority)

        if self._daily_limit is not None:
            current = self.daily_weighted
            if current >= self._daily_limit:
                msg = (
                    f"Daily weighted token limit reached "
                    f"({current}/{self._daily_limit})"
                )
                if is_maintenance:
                    raise LimitExceededError(msg)
                logger.warning("%s — proceeding (priority=%s)", msg, priority)
