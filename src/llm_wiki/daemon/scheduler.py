from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from llm_wiki.issues.queue import IssueQueue

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
    health_probe_url: str | None = None


async def _probe_backend(url: str) -> bool:
    """Return True if the backend at url is reachable. False on any error."""
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{url}/models", timeout=5.0)
            return r.status_code < 500
    except Exception:
        return False


class IntervalScheduler:
    """Runs registered workers immediately on start, then on their intervals.

    Each worker runs as its own asyncio.Task. Errors raised by a worker are
    logged but do NOT stop the worker (the next interval still fires) and
    do NOT affect sibling workers. Cancellation is clean: stop() cancels
    every worker task and awaits its termination.
    """

    def __init__(
        self,
        issue_queue: IssueQueue | None = None,
        escalation_threshold: int = 3,
    ) -> None:
        self._workers: list[ScheduledWorker] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_run: dict[str, str] = {}
        self._last_attempt: dict[str, str] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._backend_reachable: dict[str, bool | None] = {}
        self._issue_queue = issue_queue
        self._escalation_threshold = escalation_threshold
        self._escalation_issue_ids: dict[str, str] = {}  # worker_name -> open issue id
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

    def last_attempt_iso(self, name: str) -> str | None:
        return self._last_attempt.get(name)

    def consecutive_failures(self, name: str) -> int:
        return self._consecutive_failures.get(name, 0)

    def backend_reachable(self, name: str) -> bool | None:
        """Last probe result for worker. None if probe has not run yet."""
        return self._backend_reachable.get(name)

    def workers_info(self) -> list[tuple[str, float, str | None]]:
        """Return (name, interval_seconds, last_run_iso) tuples for every registered worker.

        Public accessor for callers (e.g. daemon status routes) that need to
        enumerate workers without poking the private ``_workers`` list.
        """
        return [
            (w.name, w.interval_seconds, self.last_run_iso(w.name))
            for w in self._workers
        ]

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

    async def _maybe_escalate(
        self, worker: ScheduledWorker, failures: int, exc: Exception
    ) -> None:
        """File a wiki issue when consecutive_failures first crosses the threshold."""
        if self._issue_queue is None:
            return
        if failures != self._escalation_threshold:
            return  # Only act at exactly the threshold crossing

        from llm_wiki.issues.queue import Issue
        import datetime as _dt

        # Use a timestamp-based key so each threshold crossing creates a fresh issue
        key = f"{worker.name}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        issue = Issue(
            id=Issue.make_id("worker-failure", None, key),
            type="worker-failure",
            status="open",
            title=f"[{worker.name}] has failed {failures} consecutive runs",
            page=None,
            body=(
                f"Last error type: {type(exc).__name__}\n"
                f"Last error: {exc}\n\n"
                f"The worker will retry on its next interval. "
                f"Check that the configured LLM backend is reachable."
            ),
            created=Issue.now_iso(),
            detected_by="scheduler",
            severity="moderate",
        )
        _, was_new = self._issue_queue.add(issue)
        if was_new:
            self._escalation_issue_ids[worker.name] = issue.id
            logger.warning(
                "Worker %r: filed issue %s after %d consecutive failures",
                worker.name, issue.id, failures,
            )

    async def _run_once(self, worker: ScheduledWorker) -> None:
        # Health probe — skip run (not fail) if backend is unreachable
        if worker.health_probe_url is not None:
            reachable = await _probe_backend(worker.health_probe_url)
            self._backend_reachable[worker.name] = reachable
            if not reachable:
                logger.info(
                    "[%s] backend unreachable at %s, skipping run",
                    worker.name, worker.health_probe_url,
                )
                return

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_attempt[worker.name] = now
        try:
            await worker.coro_factory()
            self._last_run[worker.name] = now
            prev_failures = self._consecutive_failures.get(worker.name, 0)
            self._consecutive_failures[worker.name] = 0
            # Auto-resolve any open escalation issue for this worker
            if prev_failures >= self._escalation_threshold and self._issue_queue is not None:
                issue_id = self._escalation_issue_ids.pop(worker.name, None)
                if issue_id:
                    self._issue_queue.update_status(issue_id, "resolved")
                    logger.info(
                        "Worker %r recovered; resolved issue %s", worker.name, issue_id
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures = self._consecutive_failures.get(worker.name, 0) + 1
            self._consecutive_failures[worker.name] = failures
            logger.exception(
                "Worker %r raised (consecutive_failures=%d); will retry on next interval",
                worker.name, failures,
            )
            await self._maybe_escalate(worker, failures, exc)
