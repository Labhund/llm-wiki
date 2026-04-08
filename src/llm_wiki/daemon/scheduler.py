from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(spec: str) -> float:
    """Parse an interval spec ('30s', '15m', '6h', '2d') to seconds.

    Returns:
        The interval in seconds as a float.

    Raises:
        ValueError: if the spec is malformed (empty, missing unit, unknown
        unit, fractional, negative, or contains long-form unit names).
    """
    if not isinstance(spec, str):
        raise ValueError(f"Interval spec must be a string, got {type(spec).__name__}")
    stripped = spec.strip()
    match = _INTERVAL_RE.match(stripped)
    if match is None:
        raise ValueError(f"Invalid interval spec: {spec!r}")
    value = int(match.group(1))
    unit = match.group(2)
    return float(value * _UNIT_SECONDS[unit])


@dataclass
class ScheduledWorker:
    """One named worker the scheduler runs on an interval."""
    name: str
    interval_seconds: float
    coro_factory: Callable[[], Awaitable[None]]


class IntervalScheduler:
    """Runs registered workers immediately on start, then on their intervals.

    Each worker runs as its own asyncio.Task. Errors raised by a worker are
    logged but do NOT stop the worker (the next interval still fires) and
    do NOT affect sibling workers. Cancellation is clean: stop() cancels
    every worker task and awaits its termination.
    """

    def __init__(self) -> None:
        self._workers: list[ScheduledWorker] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_run: dict[str, str] = {}
        self._stopping = False

    def register(self, worker: ScheduledWorker) -> None:
        if any(w.name == worker.name for w in self._workers):
            raise ValueError(f"Worker already registered: {worker.name}")
        self._workers.append(worker)

    @property
    def worker_names(self) -> list[str]:
        return [w.name for w in self._workers]

    def last_run_iso(self, name: str) -> str | None:
        return self._last_run.get(name)

    async def start(self) -> None:
        self._stopping = False
        for worker in self._workers:
            self._tasks[worker.name] = asyncio.create_task(self._run_loop(worker))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error while stopping scheduler task")
        self._tasks.clear()

    async def _run_loop(self, worker: ScheduledWorker) -> None:
        """Run-now-then-loop. Errors are isolated per iteration."""
        try:
            while not self._stopping:
                await self._run_once(worker)
                if self._stopping:
                    return
                try:
                    await asyncio.sleep(worker.interval_seconds)
                except asyncio.CancelledError:
                    return
        except asyncio.CancelledError:
            return

    async def _run_once(self, worker: ScheduledWorker) -> None:
        try:
            await worker.coro_factory()
            self._last_run[worker.name] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker %r raised; will retry on next interval", worker.name)
