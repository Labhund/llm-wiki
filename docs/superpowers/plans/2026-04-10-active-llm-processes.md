# Active LLM Processes (`llm-wiki ps`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `llm-wiki ps` — a one-shot CLI command showing which background workers are running and what each in-flight LLM call is doing.

**Architecture:** Extend `LLMQueue` with a labeled active-job registry and `IntervalScheduler` with running-state tracking; thread a `label` param through `LLMClient.complete()` to all call sites; expose everything via a new `process-list` daemon route; render with `llm-wiki ps`.

**Tech Stack:** Python stdlib (`dataclasses`, `time.monotonic`), asyncio, Click (CLI), existing `LLMQueue` / `IntervalScheduler` / `DaemonServer` / `DaemonClient`.

---

## File Map

| File | Role |
|------|------|
| `src/llm_wiki/daemon/llm_queue.py` | Add `ActiveJob` dataclass; extend `submit()` with `label` + pending tracking; add `active_jobs`, `pending_count`, `slots_total` properties |
| `src/llm_wiki/daemon/scheduler.py` | Add `_running`, `_running_since`; set/clear in `_run_once()`; add `running_workers`, `running_elapsed_s()` |
| `src/llm_wiki/traverse/llm_client.py` | Add `label` param to `complete()`; pass through to `queue.submit()` |
| `src/llm_wiki/adversary/agent.py` | Tag two `complete()` calls with labels |
| `src/llm_wiki/librarian/agent.py` | Tag one `complete()` call |
| `src/llm_wiki/librarian/talk_summary.py` | Tag one `complete()` call |
| `src/llm_wiki/ingest/agent.py` | Tag five `complete()` calls |
| `src/llm_wiki/traverse/engine.py` | Thread label through `_llm_turn()`; tag synthesize call |
| `src/llm_wiki/daemon/commit.py` | Tag one `complete()` call |
| `src/llm_wiki/daemon/server.py` | Add `_handle_process_list()`; wire into `_route()` |
| `src/llm_wiki/cli/main.py` | Add `ps` subcommand |
| `tests/test_daemon/test_llm_queue.py` | Tests for job registry + pending tracking |
| `tests/test_daemon/test_scheduler.py` | Tests for running state |
| `tests/test_daemon/test_server.py` | Test for `process-list` route |
| `tests/test_cli/test_ps_cmd.py` | Tests for `llm-wiki ps` output |

---

## Task 1: LLMQueue — ActiveJob dataclass + labeled job registry

**Files:**
- Modify: `src/llm_wiki/daemon/llm_queue.py`
- Test: `tests/test_daemon/test_llm_queue.py`

- [ ] **Step 1.1: Write the failing tests**

Add to `tests/test_daemon/test_llm_queue.py`:

```python
import asyncio
import pytest
from llm_wiki.daemon.llm_queue import LLMQueue


@pytest.mark.asyncio
async def test_active_jobs_contains_label():
    """active_jobs exposes the label of an in-flight job."""
    queue = LLMQueue(max_concurrent=2)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking():
        started.set()
        await finish.wait()

    task = asyncio.create_task(
        queue.submit(blocking, priority="maintenance", label="adversary:verify:protein-dj")
    )
    await started.wait()

    jobs = queue.active_jobs
    assert len(jobs) == 1
    assert jobs[0].label == "adversary:verify:protein-dj"
    assert jobs[0].priority == "maintenance"
    assert jobs[0].elapsed_s >= 0.0

    finish.set()
    await task
    assert queue.active_jobs == []


@pytest.mark.asyncio
async def test_pending_count_while_waiting():
    """pending_count reflects tasks waiting for a semaphore slot."""
    queue = LLMQueue(max_concurrent=1)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking():
        started.set()
        await finish.wait()

    # First task takes the slot; second must wait
    t1 = asyncio.create_task(queue.submit(blocking, label="job-1"))
    await started.wait()

    t2 = asyncio.create_task(queue.submit(blocking, label="job-2"))
    await asyncio.sleep(0)  # yield so t2 registers as pending

    assert queue.pending_count == 1
    assert queue.active_jobs[0].label == "job-1"

    finish.set()
    await t1
    await t2


@pytest.mark.asyncio
async def test_slots_total():
    queue = LLMQueue(max_concurrent=3)
    assert queue.slots_total == 3


@pytest.mark.asyncio
async def test_pending_count_restored_on_cancel_before_acquire():
    """CancelledError while waiting for semaphore does not leave pending stuck."""
    queue = LLMQueue(max_concurrent=1)
    hold = asyncio.Event()

    async def holder():
        await hold.wait()

    async def waiter():
        await queue.submit(holder)

    # Fill the slot
    hold_task = asyncio.create_task(queue.submit(holder))
    await asyncio.sleep(0)

    # Start waiter (will block on semaphore)
    wait_task = asyncio.create_task(queue.submit(holder, label="pending-job"))
    await asyncio.sleep(0)
    assert queue.pending_count == 1

    # Cancel the waiter before it acquires
    wait_task.cancel()
    try:
        await wait_task
    except asyncio.CancelledError:
        pass

    assert queue.pending_count == 0

    hold.set()
    await hold_task
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
pytest tests/test_daemon/test_llm_queue.py::test_active_jobs_contains_label \
       tests/test_daemon/test_llm_queue.py::test_pending_count_while_waiting \
       tests/test_daemon/test_llm_queue.py::test_slots_total \
       tests/test_daemon/test_llm_queue.py::test_pending_count_restored_on_cancel_before_acquire \
       -v
```

Expected: all FAIL (attributes don't exist yet).

- [ ] **Step 1.3: Implement `ActiveJob` and extend `LLMQueue`**

Replace `src/llm_wiki/daemon/llm_queue.py` entirely:

```python
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


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
    the inference server. Tracks labeled active jobs and pending count
    for observability via the process-list route.
    """

    # Accepted for API compatibility; scheduling is currently FIFO via semaphore.
    PRIORITY_MAP = {"query": 0, "ingest": 1, "maintenance": 2}

    def __init__(self, max_concurrent: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._tokens_used: int = 0
        self._active: int = 0
        self._pending: int = 0
        self._active_jobs: dict[int, ActiveJob] = {}
        self._next_id: int = 0

    async def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        priority: str = "maintenance",
        label: str = "unknown",
        **kwargs: Any,
    ) -> Any:
        """Submit an async callable, waiting for a concurrency slot."""
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
                self._active += 1
                try:
                    return await fn(**kwargs)
                finally:
                    self._active -= 1
                    self._active_jobs.pop(job_id, None)
        finally:
            if not acquired:
                self._pending -= 1

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def active_count(self) -> int:
        return self._active

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

    def record_tokens(self, count: int) -> None:
        """Record tokens consumed (for accounting/limits)."""
        self._tokens_used += count
```

- [ ] **Step 1.4: Run tests to verify they pass**

```
pytest tests/test_daemon/test_llm_queue.py -v
```

Expected: all PASS (including the pre-existing tests).

- [ ] **Step 1.5: Commit**

```bash
git add src/llm_wiki/daemon/llm_queue.py tests/test_daemon/test_llm_queue.py
git commit -m "feat: add ActiveJob registry + pending tracking to LLMQueue"
```

---

## Task 2: IntervalScheduler — running state

**Files:**
- Modify: `src/llm_wiki/daemon/scheduler.py`
- Test: `tests/test_daemon/test_scheduler.py`

- [ ] **Step 2.1: Write the failing tests**

Add to `tests/test_daemon/test_scheduler.py`:

```python
import asyncio
import pytest
from llm_wiki.daemon.scheduler import IntervalScheduler, ScheduledWorker


@pytest.mark.asyncio
async def test_running_workers_set_during_execution():
    """Worker name is in running_workers while it's executing."""
    inside = asyncio.Event()
    release = asyncio.Event()

    async def slow_worker():
        inside.set()
        await release.wait()

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("slow", 60.0, slow_worker))
    await scheduler.start()

    await inside.wait()
    assert "slow" in scheduler.running_workers

    release.set()
    await scheduler.stop()


@pytest.mark.asyncio
async def test_running_workers_cleared_after_completion():
    """Worker name is removed from running_workers after its run completes."""
    done = asyncio.Event()

    async def quick():
        pass

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("quick", 60.0, quick))
    await scheduler.start()
    # Wait long enough for the first run to complete
    await asyncio.sleep(0.2)
    assert "quick" not in scheduler.running_workers
    await scheduler.stop()


@pytest.mark.asyncio
async def test_running_workers_cleared_on_error():
    """Worker name is removed even when the worker raises."""
    async def failing():
        raise RuntimeError("boom")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("failing", 60.0, failing))
    await scheduler.start()
    await asyncio.sleep(0.2)
    assert "failing" not in scheduler.running_workers
    await scheduler.stop()


@pytest.mark.asyncio
async def test_running_elapsed_s_none_when_idle():
    """running_elapsed_s returns None for a worker that isn't running."""
    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("idle-worker", 9999.0, lambda: None))
    # Don't start — worker is registered but never ran
    assert scheduler.running_elapsed_s("idle-worker") is None
    assert scheduler.running_elapsed_s("nonexistent") is None


@pytest.mark.asyncio
async def test_running_elapsed_s_positive_when_running():
    """running_elapsed_s returns a positive float while the worker is executing."""
    inside = asyncio.Event()
    release = asyncio.Event()

    async def slow():
        inside.set()
        await release.wait()

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("slow2", 60.0, slow))
    await scheduler.start()

    await inside.wait()
    elapsed = scheduler.running_elapsed_s("slow2")
    assert elapsed is not None
    assert elapsed >= 0.0

    release.set()
    await scheduler.stop()
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
pytest tests/test_daemon/test_scheduler.py::test_running_workers_set_during_execution \
       tests/test_daemon/test_scheduler.py::test_running_workers_cleared_after_completion \
       tests/test_daemon/test_scheduler.py::test_running_workers_cleared_on_error \
       tests/test_daemon/test_scheduler.py::test_running_elapsed_s_none_when_idle \
       tests/test_daemon/test_scheduler.py::test_running_elapsed_s_positive_when_running \
       -v
```

Expected: all FAIL.

- [ ] **Step 2.3: Extend `IntervalScheduler`**

Add `import time` at the top of `src/llm_wiki/daemon/scheduler.py` (it uses `datetime` already; add `time` to the same import block).

In `IntervalScheduler.__init__`, add after `self._stopping = False`:

```python
        self._running: set[str] = set()
        self._running_since: dict[str, float] = {}
```

Add two new properties after `worker_names`:

```python
    @property
    def running_workers(self) -> set[str]:
        """Names of workers currently mid-execution."""
        return set(self._running)

    def running_elapsed_s(self, name: str) -> float | None:
        """Seconds since this worker entered its current run, or None if idle."""
        since = self._running_since.get(name)
        if since is None:
            return None
        return time.monotonic() - since
```

In `_run_once`, add at the very top of the method body (before the health probe block) and in the finally that already wraps the worker execution. The current `_run_once` method has no finally — add one:

Replace the existing `_run_once` body with:

```python
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
        self._running.add(worker.name)
        self._running_since[worker.name] = time.monotonic()
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
            try:
                self._maybe_escalate(worker, failures, exc)
            except Exception:
                logger.exception("Worker %r: escalation filing failed", worker.name)
        finally:
            self._running.discard(worker.name)
            self._running_since.pop(worker.name, None)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```
pytest tests/test_daemon/test_scheduler.py -v
```

Expected: all PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py tests/test_daemon/test_scheduler.py
git commit -m "feat: add running state tracking to IntervalScheduler"
```

---

## Task 3: LLMClient — thread label parameter

**Files:**
- Modify: `src/llm_wiki/traverse/llm_client.py`
- Test: `tests/test_traverse/test_llm_client.py`

- [ ] **Step 3.1: Write the failing test**

Add to `tests/test_traverse/test_llm_client.py`:

```python
@pytest.mark.asyncio
async def test_complete_passes_label_to_queue(monkeypatch):
    """LLMClient.complete() passes the label parameter to queue.submit()."""
    from llm_wiki.daemon.llm_queue import LLMQueue
    from llm_wiki.traverse.llm_client import LLMClient

    captured_labels: list[str] = []
    original_submit = LLMQueue.submit

    async def capturing_submit(self, fn, priority="maintenance", label="unknown", **kwargs):
        captured_labels.append(label)
        return await original_submit(self, fn, priority=priority, label=label, **kwargs)

    monkeypatch.setattr(LLMQueue, "submit", capturing_submit)

    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")

    # Patch litellm to avoid real network call
    import litellm
    from unittest.mock import AsyncMock, MagicMock
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "response"
    mock_response.usage.total_tokens = 10
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=mock_response))

    await client.complete(
        [{"role": "user", "content": "hello"}],
        label="adversary:verify:test-page",
    )

    assert "adversary:verify:test-page" in captured_labels
```

- [ ] **Step 3.2: Run test to verify it fails**

```
pytest tests/test_traverse/test_llm_client.py::test_complete_passes_label_to_queue -v
```

Expected: FAIL — `complete()` doesn't accept `label` parameter yet.

- [ ] **Step 3.3: Add `label` parameter to `LLMClient.complete()`**

In `src/llm_wiki/traverse/llm_client.py`, change the `complete` signature and the `submit` call:

```python
    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        priority: str = "query",
        label: str = "unknown",
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""

        async def _call() -> LLMResponse:
            # ... (body unchanged)
            ...

        return await self._queue.submit(_call, priority=priority, label=label)
```

Only the signature line and the final `return` line change. The `_call` inner function body is untouched.

- [ ] **Step 3.4: Run tests to verify they pass**

```
pytest tests/test_traverse/test_llm_client.py -v
```

Expected: all PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/llm_wiki/traverse/llm_client.py tests/test_traverse/test_llm_client.py
git commit -m "feat: thread label parameter through LLMClient.complete()"
```

---

## Task 4: Label all call sites

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py`
- Modify: `src/llm_wiki/librarian/agent.py`
- Modify: `src/llm_wiki/librarian/talk_summary.py`
- Modify: `src/llm_wiki/ingest/agent.py`
- Modify: `src/llm_wiki/traverse/engine.py`
- Modify: `src/llm_wiki/daemon/commit.py`

No new tests needed — label values are informational strings only. Existing tests continue to pass because `label` defaults to `"unknown"`.

- [ ] **Step 4.1: Label adversary call sites**

In `src/llm_wiki/adversary/agent.py`:

Find the claim extraction call (around line 97 — search for `extract_claims`). That function is in `claim_extractor.py` and does not use `LLMClient`; the adversary's LLM calls are in `agent.py` itself.

Search for `await self._llm.complete(` — there are two:

1. **Claim extraction**: look for the call that extracts claims from raw source text. Add `label=f"adversary:extract-claims:{page.name}"` (where `page` is the page whose claims are being extracted). If the page slug isn't directly in scope, use `label="adversary:extract-claims"`.

2. **Verification**: the call at line ~144 (inside the verify loop, passing `messages` from `compose_verification_messages`). Add `label=f"adversary:verify:{claim.page_slug}"` — `claim` is a `Claim` dataclass; check its fields. If there's no `page_slug` field, use `label=f"adversary:verify:{page.name}"` from the enclosing scope.

To find the exact call and page slug, read `adversary/agent.py` around line 140 before making this edit.

```bash
# Read the file first
grep -n "await self._llm.complete" src/llm_wiki/adversary/agent.py
```

After reading, make the targeted edits. Template (adapt to actual variable names):

```python
# Extraction call (first complete() in agent.py):
response = await self._llm.complete(
    messages, temperature=0.2, priority="maintenance",
    label=f"adversary:extract-claims:{page_slug}",
)

# Verification call (second complete() in agent.py):
response = await self._llm.complete(
    messages, temperature=0.2, priority="maintenance",
    label=f"adversary:verify:{page_slug}",
)
```

- [ ] **Step 4.2: Label librarian call sites**

In `src/llm_wiki/librarian/agent.py` around line 257:

```python
response = await self._llm.complete(
    messages, temperature=0.4, priority="maintenance",
    label="librarian:refine-manifest",
)
```

In `src/llm_wiki/librarian/talk_summary.py` around line 146 — the function receives a page name in its signature. Read the file to find what variable holds it:

```bash
grep -n "await llm.complete\|def.*page\|page_name\|page_slug" src/llm_wiki/librarian/talk_summary.py | head -20
```

Then add `label=f"librarian:talk-summary:{page_name}"` (using the actual variable name).

- [ ] **Step 4.3: Label ingest call sites**

In `src/llm_wiki/ingest/agent.py` there are five `complete()` calls. Find them:

```bash
grep -n "await self._llm.complete" src/llm_wiki/ingest/agent.py
```

Add labels as follows. The source filename is available as `Path(source_path).name`:

1. **Concept extraction** (~line 161): `label=f"ingest:extract:{Path(source_path).name}"`
2. **Page writing** (~line 192): `label=f"ingest:write:{concept.name}"` (adapt to actual variable holding the concept name in scope)
3. **Overview extraction** (~line 354): `label=f"ingest:overview:{Path(source_path).name}"`
4. **Passage collection** (~line 373): `label=f"ingest:passages:{Path(source_path).name}"`
5. **Content synthesis** (~line 401): `label=f"ingest:synthesize:{concept.name}"` (adapt to actual variable)

Read the file around each line to confirm variable names before editing.

- [ ] **Step 4.4: Label traverse/engine call sites**

`src/llm_wiki/traverse/engine.py` has two `complete()` calls:

1. `_llm_turn()` (~line 192): Add `label` parameter to `_llm_turn()` signature and pass it through:

```python
async def _llm_turn(
    self,
    question: str,
    memory: WorkingMemory,
    content: str,
    system_prompt: str,
    label: str = "query:traverse",
) -> dict:
    messages = compose_traverse_messages(question, memory, content, system_prompt)
    response = await self._llm.complete(messages, label=label)
    ...
```

Then update the call site for turn 0 (search for the first `_llm_turn` call in the method body):
```python
turn_data = await self._llm_turn(
    question, memory, initial_content, traverse_prompt,
    label="query:traverse:step-0",
)
```

And the call site inside the loop (where `turn_num` is in scope):
```python
turn_data = await self._llm_turn(
    question, memory, content, traverse_prompt,
    label=f"query:traverse:step-{turn_num}",
)
```

2. `_finish()` (~line 259):
```python
response = await self._llm.complete(
    messages, temperature=0.3, label="query:synthesize"
)
```

- [ ] **Step 4.5: Label commit call site**

In `src/llm_wiki/daemon/commit.py` (~line 111):

```bash
grep -n "await self._llm.complete" src/llm_wiki/daemon/commit.py
```

The session author is in scope. Add:
```python
response = await self._llm.complete(
    messages, temperature=0.0, priority="maintenance",
    label=f"commit:summarize:{session.author}",
)
```

Adapt `session.author` to the actual variable name visible at that call site.

- [ ] **Step 4.6: Run full test suite to confirm nothing broke**

```
pytest tests/ -v --tb=short -q
```

Expected: all pre-existing tests pass.

- [ ] **Step 4.7: Commit**

```bash
git add src/llm_wiki/adversary/agent.py \
        src/llm_wiki/librarian/agent.py \
        src/llm_wiki/librarian/talk_summary.py \
        src/llm_wiki/ingest/agent.py \
        src/llm_wiki/traverse/engine.py \
        src/llm_wiki/daemon/commit.py
git commit -m "feat: label all LLM call sites for process-list observability"
```

---

## Task 5: Daemon route — `process-list`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Test: `tests/test_daemon/test_server.py`

- [ ] **Step 5.1: Write the failing test**

Add to `tests/test_daemon/test_server.py`:

```python
@pytest.mark.asyncio
async def test_process_list_route(daemon_server):
    """process-list returns workers + empty jobs when daemon is idle."""
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "process-list"})

    assert resp["status"] == "ok"
    assert isinstance(resp["jobs"], list)
    assert isinstance(resp["workers"], list)
    assert isinstance(resp["pending"], int)
    assert isinstance(resp["slots_total"], int)
    assert isinstance(resp["tokens_used"], int)

    # Queue is idle at startup
    assert resp["jobs"] == []
    assert resp["pending"] == 0

    # Workers list includes registered workers (at minimum auditor)
    worker_names = [w["name"] for w in resp["workers"]]
    assert "auditor" in worker_names

    # Each worker entry has the expected shape
    for w in resp["workers"]:
        assert "name" in w
        assert w["state"] in ("running", "idle")
        assert "consecutive_failures" in w
        # last_run may be None if worker hasn't run yet
        assert "last_run" in w
```

- [ ] **Step 5.2: Run test to verify it fails**

```
pytest tests/test_daemon/test_server.py::test_process_list_route -v
```

Expected: FAIL — route doesn't exist yet.

- [ ] **Step 5.3: Add `_handle_process_list()` to `server.py`**

Find `_handle_scheduler_status` in `server.py` (around line 1131). Add the new method immediately after it:

```python
    def _handle_process_list(self) -> dict:
        jobs = []
        pending = 0
        slots_total = 0
        tokens_used = 0

        if self._llm_queue is not None:
            for job in self._llm_queue.active_jobs:
                jobs.append({
                    "id": job.id,
                    "label": job.label,
                    "priority": job.priority,
                    "elapsed_s": round(job.elapsed_s, 1),
                })
            pending = self._llm_queue.pending_count
            slots_total = self._llm_queue.slots_total
            tokens_used = self._llm_queue.tokens_used

        workers = []
        if self._scheduler is not None:
            running = self._scheduler.running_workers
            health = self._scheduler.health_info()
            for name, _interval_s, last_run in self._scheduler.workers_info():
                workers.append({
                    "name": name,
                    "state": "running" if name in running else "idle",
                    "last_run": last_run,
                    "consecutive_failures": health.get(name, {}).get(
                        "consecutive_failures", 0
                    ),
                    "running_elapsed_s": self._scheduler.running_elapsed_s(name),
                })

        return {
            "status": "ok",
            "jobs": jobs,
            "pending": pending,
            "slots_total": slots_total,
            "tokens_used": tokens_used,
            "workers": workers,
        }
```

- [ ] **Step 5.4: Wire into `_route()`**

Find the `case "scheduler-status":` block in `_route()` (around line 581). Add after it:

```python
            case "process-list":
                return self._handle_process_list()
```

- [ ] **Step 5.5: Run tests to verify they pass**

```
pytest tests/test_daemon/test_server.py -v
```

Expected: all PASS including the new test.

- [ ] **Step 5.6: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py
git commit -m "feat: add process-list daemon route"
```

---

## Task 6: CLI — `llm-wiki ps`

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/test_ps_cmd.py`

- [ ] **Step 6.1: Write the failing tests**

Create `tests/test_cli/test_ps_cmd.py`:

```python
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_wiki.cli.main import cli
from llm_wiki.daemon.lifecycle import socket_path_for
from llm_wiki.daemon.server import DaemonServer


@pytest.fixture
def daemon_for_cli(sample_vault: Path):
    """Start a daemon in a background thread so sync CLI tests can connect."""
    sock_path = socket_path_for(sample_vault)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = DaemonServer(sample_vault, sock_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.start())
    loop.create_task(server.serve_forever())

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield sample_vault

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()


def test_ps_shows_workers(daemon_for_cli):
    """`llm-wiki ps` lists background workers."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "WORKERS" in result.output
    assert "auditor" in result.output


def test_ps_shows_queue_section(daemon_for_cli):
    """`llm-wiki ps` shows LLM QUEUE section even when idle."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "LLM QUEUE" in result.output


def test_ps_shows_processes_header(daemon_for_cli):
    """`llm-wiki ps` shows PROCESSES header with token count."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "PROCESSES" in result.output
    assert "tokens used" in result.output


def test_ps_no_daemon(tmp_path):
    """`llm-wiki ps` exits non-zero when daemon is not running."""
    runner = CliRunner()
    vault_path = tmp_path / "empty_vault"
    vault_path.mkdir()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code != 0
```

- [ ] **Step 6.2: Run tests to verify they fail**

```
pytest tests/test_cli/test_ps_cmd.py -v
```

Expected: all FAIL — `ps` command doesn't exist yet.

- [ ] **Step 6.3: Add helper `_worker_display_action()`**

Add this private function near `_relative_time` and `_format_seconds` in `src/llm_wiki/cli/main.py`:

```python
def _worker_display_action(worker_name: str, jobs: list[dict]) -> str:
    """Extract display string for a running worker from active jobs.

    Finds the first job whose label starts with the worker name, strips the
    source prefix, joins remaining parts with spaces, truncates at 30 chars.
    Returns empty string if no matching job.
    """
    for job in jobs:
        label = job.get("label", "")
        parts = label.split(":", 2)
        if parts and parts[0] == worker_name:
            action_detail = " ".join(parts[1:]) if len(parts) > 1 else label
            if len(action_detail) > 30:
                action_detail = action_detail[:29] + "…"
            return action_detail
    return ""
```

- [ ] **Step 6.4: Add `ps` command**

Add the `ps` command to `src/llm_wiki/cli/main.py` after the `status` command (around line 258):

```python
@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def ps(vault_path: Path) -> None:
    """Show active LLM processes and background worker state."""
    try:
        client = _get_client(vault_path, auto_start=False)
    except click.ClickException:
        click.echo("Daemon not running.", err=True)
        raise SystemExit(1)

    resp = client.request({"type": "process-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Failed"))

    jobs: list[dict] = resp.get("jobs", [])
    pending: int = resp.get("pending", 0)
    tokens_used: int = resp.get("tokens_used", 0)
    slots_total: int = resp.get("slots_total", 0)
    workers: list[dict] = resp.get("workers", [])
    active = len(jobs)

    # Header
    click.echo(f"PROCESSES  {active} active · {pending} pending · {tokens_used:,} tokens used")
    click.echo()

    # Workers section
    if workers:
        click.echo("WORKERS")
        for w in workers:
            name: str = w["name"]
            state: str = w.get("state", "idle")
            last_run: str | None = w.get("last_run")
            elapsed_s: float | None = w.get("running_elapsed_s")
            failures: int = w.get("consecutive_failures", 0)

            if state == "running":
                action = _worker_display_action(name, jobs)
                elapsed_str = f"{int(elapsed_s)}s" if elapsed_s is not None else "—"
                click.echo(f"  {name:<14} running   {action:<32} {elapsed_str}")
            else:
                last_str = (
                    f"last run {_relative_time(last_run)}" if last_run else "never run"
                )
                fail_str = f" [{failures} failures]" if failures > 0 else ""
                click.echo(f"  {name:<14} idle      {last_str}{fail_str}")
        click.echo()

    # LLM Queue section
    click.echo(
        f"LLM QUEUE  ({active}/{slots_total} slots, {pending} pending)"
        if active or pending
        else "LLM QUEUE"
    )
    if jobs:
        for job in jobs:
            label: str = job.get("label", "unknown")
            priority: str = job.get("priority", "")
            elapsed: int = int(job.get("elapsed_s", 0))
            click.echo(f"  [{job['id']}]  {label:<42} {priority:<14} {elapsed}s")
    else:
        click.echo("  No active LLM calls.")
```

- [ ] **Step 6.5: Run tests to verify they pass**

```
pytest tests/test_cli/test_ps_cmd.py -v
```

Expected: all PASS.

- [ ] **Step 6.6: Smoke-test manually (optional)**

With a daemon running:
```
llm-wiki ps
```

Expected output shape:
```
PROCESSES  0 active · 0 pending · 0 tokens used

WORKERS
  auditor        idle      never run
  ...

LLM QUEUE
  No active LLM calls.
```

- [ ] **Step 6.7: Run full test suite**

```
pytest tests/ -v --tb=short -q
```

Expected: all PASS.

- [ ] **Step 6.8: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_ps_cmd.py
git commit -m "feat: add llm-wiki ps command"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| `ActiveJob` dataclass with `id`, `label`, `priority`, `started_at`, `elapsed_s` | Task 1 |
| `LLMQueue`: `pending_count`, `active_jobs`, `slots_total` | Task 1 |
| `LLMQueue`: `acquired` flag for CancelledError safety | Task 1 (impl note in test 1.4 and code) |
| `IntervalScheduler`: `_running`, `_running_since` | Task 2 |
| `IntervalScheduler`: `running_workers`, `running_elapsed_s()` | Task 2 |
| `LLMClient.complete()` `label` param | Task 3 |
| Label tagging at all call sites per label-conventions table | Task 4 |
| `process-list` daemon route with full response shape | Task 5 |
| Worker `running_elapsed_s` in route response | Task 5 |
| `llm-wiki ps` CLI command | Task 6 |
| Workers section: raw label segments, no conjugation | Task 6 (`_worker_display_action`) |
| Workers elapsed from `running_elapsed_s`, not LLM job elapsed | Task 6 (uses `w["running_elapsed_s"]`) |
| Idle queue shows `No active LLM calls.` | Task 6 |
| Daemon not running → stderr + non-zero exit | Task 6 |

**Placeholder scan:** No TBDs. Every step has complete code except Task 4 call sites where variable names must be confirmed by reading the file first — the grep commands are explicit and the template code is complete.

**Type consistency:** `LLMQueue.active_jobs` returns `list[ActiveJob]` (Task 1). `_handle_process_list` reads `.active_jobs` and calls `.elapsed_s` (Task 5) — consistent. `scheduler.running_elapsed_s(name)` returns `float | None` (Task 2), surfaced as `"running_elapsed_s"` in route response (Task 5), read as `w.get("running_elapsed_s")` in CLI (Task 6) — consistent.
