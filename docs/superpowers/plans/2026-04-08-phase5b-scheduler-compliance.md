# Phase 5b: Background Worker Scheduler + Compliance Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap reference:** See `docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md` for cross-cutting design decisions and the relationship to sub-phases 5a/5c/5d. **Read the roadmap's "Cross-cutting design decisions" and "What's already in place" sections before starting Task 1.**
>
> **Prerequisite:** Sub-phase 5a must be merged. This plan imports `IssueQueue`, `Issue`, and `Auditor` from 5a directly.

**Goal:** Add an async interval scheduler that runs maintenance workers in the daemon's event loop, plus a compliance-review pipeline that audits human edits arriving via the file watcher (debounced). After this sub-phase, the auditor from 5a runs automatically on a schedule, and unstructured edits to wiki pages are reviewed for missing citations, structural drift, and substantive new ideas.

**Architecture:** Three new pieces of substrate plus a debouncer:

1. `IntervalScheduler` — registers `ScheduledWorker(name, interval, coro_factory)` instances; runs each at start and on its interval forever; isolates errors so one crashing worker can't take down siblings; cancellable via `stop()`.
2. `PageSnapshotStore` — persists last-reviewed page content under `<state_dir>/snapshots/<slug>.md` so the compliance reviewer always diffs against the version it last approved (not against whatever the next watcher tick happens to see).
3. `ComplianceReviewer` — pure heuristic checks (no LLM): minor-edit shortcut, missing-citation detection, structural drift with auto-fix, new-idea detection. Returns a `ComplianceResult` and may write structural-drift fixes back to the file.
4. `ChangeDispatcher` — wraps the existing `FileWatcher` callback with a per-path debouncer so rapid edits collapse into one review.

The `DaemonServer` owns all four. `start()` constructs and registers them; `stop()` cancels them in the right order.

**Tech Stack:** Python 3.11+, asyncio, pytest-asyncio, existing `IssueQueue`/`Auditor`/`Vault`/`FileWatcher` from 5a + Phase 1-4. **No LLM calls in this sub-phase.** No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  daemon/
    scheduler.py          # ScheduledWorker, IntervalScheduler, parse_interval
    dispatcher.py         # ChangeDispatcher (debouncer)
    snapshot.py           # PageSnapshotStore
    server.py             # MODIFIED: own scheduler/dispatcher/reviewer; wire on_file_change;
                          # add scheduler-status route
    __main__.py           # MODIFIED: delegate file-change callback to server
  audit/
    compliance.py         # ComplianceReviewer, ComplianceResult
  cli/
    main.py               # MODIFIED: add `maintenance status` command group

tests/
  test_daemon/
    test_scheduler.py
    test_dispatcher.py
    test_snapshot.py
    test_compliance_integration.py
  test_audit/
    test_compliance.py
  test_cli/
    test_maintenance_cmd.py
```

**Type flow across tasks:**
- `daemon/scheduler.py` defines `ScheduledWorker(name, interval_seconds, coro_factory)` and `IntervalScheduler` with `register/start/stop` plus `last_run_iso(name)`. `parse_interval(spec) → float` is a module-level helper.
- `daemon/snapshot.py` defines `PageSnapshotStore(state_dir)` with `get/set/remove`.
- `audit/compliance.py` defines `ComplianceResult(page, auto_approved, auto_fixed, issues_filed, reasons)` and `ComplianceReviewer(vault_root, queue, config)` with `review_change(page_path, old_content, new_content) → ComplianceResult`.
- `daemon/dispatcher.py` defines `ChangeDispatcher(debounce_secs, on_settled)` with `submit(path)` and `stop()`.
- `daemon/server.py` constructs `IntervalScheduler`, `PageSnapshotStore`, `ComplianceReviewer`, and `ChangeDispatcher` in `start()`. New methods: `handle_file_changes(changed, removed)`, `_handle_settled_change(path)`, `_handle_scheduler_status()`, `_make_auditor_worker()`.
- `daemon/__main__.py` replaces its inline `on_file_change` with `server.handle_file_changes`.
- `cli/main.py` adds the `maintenance` Click group with a `status` subcommand calling the `scheduler-status` route.

**Cross-cutting reminders from the roadmap:**
- All maintenance LLM calls must go through `LLMClient.complete(..., priority="maintenance")`. **5b has no LLM calls**, but the auditor worker is the template that 5c/5d will follow — keep the registration helper general so adding the librarian/adversary in later sub-phases is purely additive.
- Workers must be cancellable mid-iteration. Long-running iterations may complete (the cancellation point is between iterations), but no new work is dispatched after `stop()`.
- Empty vault is valid: every check, worker, and dispatcher must handle a zero-page vault without raising.
- "Human prose is sacred" applies: the compliance reviewer may insert `%% section: ... %%` markers (invisible to humans) but never edit body content.

---

### Task 1: `parse_interval` pure function

**Files:**
- Create: `src/llm_wiki/daemon/scheduler.py` (partial — `parse_interval` only)
- Create: `tests/test_daemon/test_scheduler.py` (partial — interval parser tests only)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_scheduler.py
from __future__ import annotations

import pytest

from llm_wiki.daemon.scheduler import parse_interval


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("30s", 30.0),
        ("1s", 1.0),
        ("15m", 900.0),
        ("6h", 21600.0),
        ("12h", 43200.0),
        ("2d", 172800.0),
        ("0s", 0.0),
    ],
)
def test_parse_interval_valid(spec: str, expected: float):
    assert parse_interval(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "abc",
        "5",          # missing unit
        "5x",         # unknown unit
        "h6",         # number after unit
        "-5m",        # negative
        "5.5h",       # fractional not supported in v1
        "5 hours",    # long-form units not supported
    ],
)
def test_parse_interval_invalid_raises(spec: str):
    with pytest.raises(ValueError):
        parse_interval(spec)


def test_parse_interval_strips_whitespace():
    assert parse_interval("  6h  ") == 21600.0
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: ImportError — `llm_wiki.daemon.scheduler` does not exist.

- [ ] **Step 3: Implement `parse_interval`**

```python
# src/llm_wiki/daemon/scheduler.py
from __future__ import annotations

import re

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
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All parse_interval tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py tests/test_daemon/test_scheduler.py
git commit -m "feat: parse_interval helper for scheduler intervals"
```

---

### Task 2: `ScheduledWorker` + `IntervalScheduler` core

**Files:**
- Modify: `src/llm_wiki/daemon/scheduler.py`
- Modify: `tests/test_daemon/test_scheduler.py`

The scheduler runs each registered worker immediately on start, then on its interval forever. Workers run as `asyncio.Task`s in the daemon's event loop.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_daemon/test_scheduler.py`:

```python
import asyncio

from llm_wiki.daemon.scheduler import IntervalScheduler, ScheduledWorker


@pytest.mark.asyncio
async def test_scheduler_runs_worker_on_interval():
    """Worker runs once at start, then on each interval."""
    counter = {"n": 0}

    async def increment() -> None:
        counter["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(
        ScheduledWorker(name="incrementer", interval_seconds=0.1, coro_factory=increment)
    )

    await scheduler.start()
    await asyncio.sleep(0.35)
    await scheduler.stop()

    # 1 immediate run + ~3 interval runs (with slack for scheduling jitter)
    assert counter["n"] >= 3, f"expected ≥3 runs, got {counter['n']}"


@pytest.mark.asyncio
async def test_scheduler_stop_halts_dispatch():
    """No more worker invocations after stop()."""
    counter = {"n": 0}

    async def increment() -> None:
        counter["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(
        ScheduledWorker(name="incrementer", interval_seconds=0.05, coro_factory=increment)
    )
    await scheduler.start()
    await asyncio.sleep(0.12)
    await scheduler.stop()

    snapshot = counter["n"]
    await asyncio.sleep(0.2)
    assert counter["n"] == snapshot, "worker fired after stop()"


@pytest.mark.asyncio
async def test_scheduler_register_multiple_workers():
    """Multiple workers run independently."""
    a_count = {"n": 0}
    b_count = {"n": 0}

    async def a() -> None:
        a_count["n"] += 1

    async def b() -> None:
        b_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("a", 0.1, a))
    scheduler.register(ScheduledWorker("b", 0.1, b))
    await scheduler.start()
    await asyncio.sleep(0.25)
    await scheduler.stop()

    assert a_count["n"] >= 2
    assert b_count["n"] >= 2


@pytest.mark.asyncio
async def test_scheduler_records_last_run_iso():
    """last_run_iso(name) returns an ISO 8601 timestamp after the worker has run."""
    async def noop() -> None:
        pass

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("noop", 1.0, noop))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    last = scheduler.last_run_iso("noop")
    assert last is not None
    assert "T" in last  # ISO 8601 has a T separator
    assert "+00:00" in last or last.endswith("Z")


def test_scheduler_worker_names():
    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("a", 1.0, lambda: None))   # type: ignore[arg-type]
    scheduler.register(ScheduledWorker("b", 1.0, lambda: None))   # type: ignore[arg-type]
    assert scheduler.worker_names == ["a", "b"]
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: ImportError — `IntervalScheduler` and `ScheduledWorker` do not exist.

- [ ] **Step 3: Implement `ScheduledWorker` + `IntervalScheduler`**

Append to `src/llm_wiki/daemon/scheduler.py`:

```python
import asyncio
import datetime
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


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
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All scheduler tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py tests/test_daemon/test_scheduler.py
git commit -m "feat: IntervalScheduler with ScheduledWorker + last-run tracking"
```

---

### Task 3: Scheduler error isolation

**Files:**
- Modify: `tests/test_daemon/test_scheduler.py`

Tests only — the implementation in Task 2 already isolates errors. This task locks that behavior down so no future change accidentally regresses it.

- [ ] **Step 1: Add failing tests for error isolation**

Append to `tests/test_daemon/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_scheduler_isolates_worker_errors():
    """A worker that raises does not crash sibling workers or its own loop."""
    good_count = {"n": 0}
    bad_count = {"n": 0}

    async def good() -> None:
        good_count["n"] += 1

    async def bad() -> None:
        bad_count["n"] += 1
        raise RuntimeError("simulated worker failure")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("bad", 0.1, bad))
    scheduler.register(ScheduledWorker("good", 0.1, good))
    await scheduler.start()
    await asyncio.sleep(0.35)
    await scheduler.stop()

    # Both workers should have continued running across the failure
    assert good_count["n"] >= 3
    assert bad_count["n"] >= 3, "bad worker should retry on next interval"


@pytest.mark.asyncio
async def test_scheduler_register_duplicate_name_raises():
    scheduler = IntervalScheduler()
    async def noop() -> None:
        pass
    scheduler.register(ScheduledWorker("dup", 1.0, noop))
    with pytest.raises(ValueError):
        scheduler.register(ScheduledWorker("dup", 2.0, noop))
```

- [ ] **Step 2: Run tests, expect PASS** (these are regression locks for behavior already implemented in Task 2)

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All tests pass, including the two new ones. If `test_scheduler_isolates_worker_errors` fails, it means the error path in `_run_once` is not catching the exception — fix `_run_once` before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon/test_scheduler.py
git commit -m "test: lock down scheduler error isolation + duplicate-name guard"
```

---

### Task 4: `PageSnapshotStore`

**Files:**
- Create: `src/llm_wiki/daemon/snapshot.py`
- Create: `tests/test_daemon/test_snapshot.py`

Persists the last-reviewed content of each page so the compliance reviewer can diff against the version it last approved (not against whatever the next watcher tick produces). Stored under `<state_dir>/snapshots/<slug>.md`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_snapshot.py
from __future__ import annotations

from pathlib import Path

from llm_wiki.daemon.snapshot import PageSnapshotStore


def test_snapshot_set_and_get(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("srna-embeddings", "Original content.\n")
    assert store.get("srna-embeddings") == "Original content.\n"


def test_snapshot_get_missing_returns_none(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    assert store.get("nope") is None


def test_snapshot_overwrite(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("foo", "v1")
    store.set("foo", "v2")
    assert store.get("foo") == "v2"


def test_snapshot_remove(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("foo", "bar")
    store.remove("foo")
    assert store.get("foo") is None


def test_snapshot_remove_missing_is_noop(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.remove("nope")  # must not raise


def test_snapshot_creates_dir_on_demand(tmp_path: Path):
    state_dir = tmp_path / "fresh-state"
    store = PageSnapshotStore(state_dir)
    store.set("page", "content")
    assert (state_dir / "snapshots").is_dir()


def test_snapshot_unicode_content_round_trip(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("page", "α β γ — café résumé\n")
    assert store.get("page") == "α β γ — café résumé\n"
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_snapshot.py -v`
Expected: ImportError — `llm_wiki.daemon.snapshot` does not exist.

- [ ] **Step 3: Implement `PageSnapshotStore`**

```python
# src/llm_wiki/daemon/snapshot.py
from __future__ import annotations

from pathlib import Path


class PageSnapshotStore:
    """Last-known content of each page, used by the compliance reviewer.

    Stored at <state_dir>/snapshots/<slug>.md. The store is updated AFTER a
    successful compliance review so the diff for the next edit is the delta
    from the last-reviewed state, not from whatever the daemon happens to
    see on the next watcher tick.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "snapshots"

    def get(self, page_slug: str) -> str | None:
        path = self._path_for(page_slug)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def set(self, page_slug: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path_for(page_slug).write_text(content, encoding="utf-8")

    def remove(self, page_slug: str) -> None:
        path = self._path_for(page_slug)
        if path.exists():
            path.unlink()

    def _path_for(self, page_slug: str) -> Path:
        return self._dir / f"{page_slug}.md"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_snapshot.py -v`
Expected: All snapshot tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/snapshot.py tests/test_daemon/test_snapshot.py
git commit -m "feat: PageSnapshotStore for compliance-review diffing"
```

---

### Task 5: `ComplianceReviewer` minor-edit shortcut + `ComplianceResult`

**Files:**
- Create: `src/llm_wiki/audit/compliance.py` (partial — types + minor-edit only)
- Create: `tests/test_audit/test_compliance.py` (partial — minor-edit tests only)

The minor-edit shortcut auto-approves edits that are smaller than `compliance_minor_edit_chars` (default 50) AND introduce no new wikilinks AND no new headings. This handles the "typo fix" case without firing other checks.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_audit/test_compliance.py
from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.compliance import ComplianceResult, ComplianceReviewer
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue


def _setup(tmp_path: Path) -> tuple[Path, IssueQueue, ComplianceReviewer, Path]:
    """Create a wiki dir + queue + reviewer rooted at tmp_path."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    queue = IssueQueue(wiki_dir)
    config = WikiConfig()
    reviewer = ComplianceReviewer(tmp_path, queue, config)
    page_path = wiki_dir / "test.md"
    return wiki_dir, queue, reviewer, page_path


def test_minor_edit_auto_approves(tmp_path: Path):
    """A small edit with no new wikilinks/headings is auto-approved."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nOriginal text. [[raw/source.pdf]]\n"
    new = old.replace("Original text", "Origina1 text")  # typo fix style
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert isinstance(result, ComplianceResult)
    assert result.page == "test"
    assert result.auto_approved is True
    assert "minor-edit" in result.reasons
    assert result.issues_filed == []
    assert result.auto_fixed == []


def test_minor_edit_threshold_is_50_chars(tmp_path: Path):
    """Edits ≥ 50 chars are NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText. [[raw/source.pdf]]\n"
    addition = "x" * 60  # 60 chars > 50 threshold
    new = old.replace("Text.", f"Text. {addition}")
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_minor_edit_disqualified_by_new_wikilink(tmp_path: Path):
    """A small edit that introduces a new wikilink is NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText. [[raw/source.pdf]]\n"
    new = old.replace("Text.", "Text. See [[other-page]].")
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_minor_edit_disqualified_by_new_heading(tmp_path: Path):
    """A small edit that introduces a new ## heading is NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText.\n"
    new = old + "\n## New\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_first_time_seen_page_skips_minor_edit(tmp_path: Path):
    """When old_content is None (new file), minor-edit shortcut does not apply."""
    _, _, reviewer, page = _setup(tmp_path)
    new = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nNew page.\n"
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "minor-edit" not in result.reasons
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_compliance.py -v`
Expected: ImportError — `llm_wiki.audit.compliance` does not exist.

- [ ] **Step 3: Implement `ComplianceResult` + `ComplianceReviewer` minor-edit logic**

```python
# src/llm_wiki/audit/compliance.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue

# Threshold for the minor-edit shortcut. Spec §5 Compliance Review uses 50 chars.
_MINOR_EDIT_CHARS = 50

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(##|###)\s+\S", re.MULTILINE)


@dataclass
class ComplianceResult:
    """Outcome of one compliance review pass over a single page edit."""
    page: str
    auto_approved: bool = False
    auto_fixed: list[str] = field(default_factory=list)
    issues_filed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class ComplianceReviewer:
    """Heuristic compliance checks for human edits to wiki pages.

    No LLM calls. Each check is independent and may fire multiple reasons
    on the same edit. The reviewer may modify the file on disk to insert
    missing %% section: ... %% markers (markers are invisible in Obsidian's
    preview, so this respects "human prose is sacred").

    Construction:
        ComplianceReviewer(vault_root, queue, config)
    """

    def __init__(
        self,
        vault_root: Path,
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None:
        self._vault_root = vault_root
        self._queue = queue
        self._config = config

    def review_change(
        self,
        page_path: Path,
        old_content: str | None,
        new_content: str,
    ) -> ComplianceResult:
        result = ComplianceResult(page=page_path.stem)

        # Minor-edit shortcut only applies when we have a prior snapshot.
        if old_content is not None and self._is_minor_edit(old_content, new_content):
            result.auto_approved = True
            result.reasons.append("minor-edit")
            return result

        # Other heuristics fire here in subsequent tasks (Task 6, 7, 8).
        return result

    @staticmethod
    def _is_minor_edit(old: str, new: str) -> bool:
        """True iff diff size < threshold AND no new wikilinks AND no new headings."""
        if abs(len(new) - len(old)) >= _MINOR_EDIT_CHARS:
            return False

        old_links = set(_WIKILINK_RE.findall(old))
        new_links = set(_WIKILINK_RE.findall(new))
        if new_links - old_links:
            return False

        old_headings = set(_HEADING_RE.findall(old))
        new_headings = set(_HEADING_RE.findall(new))
        if new_headings - old_headings:
            return False

        return True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_compliance.py -v`
Expected: All five minor-edit tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance.py
git commit -m "feat: ComplianceReviewer minor-edit shortcut"
```

---

### Task 6: `ComplianceReviewer` missing-citation check

**Files:**
- Modify: `src/llm_wiki/audit/compliance.py`
- Modify: `tests/test_audit/test_compliance.py`

A new sentence introduced by the edit must end with a `[[...]]` citation. Sentences without citations file a `compliance` issue.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_compliance.py`:

```python
def test_missing_citation_files_issue(tmp_path: Path):
    """A new sentence without a citation produces a compliance issue."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nWe used PCA [[raw/paper.pdf]].\n"
    new = old + "\nWe also computed silhouette scores using k=10.\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "missing-citation" in result.reasons
    assert len(result.issues_filed) >= 1
    issue = queue.get(result.issues_filed[0])
    assert issue is not None
    assert issue.type == "compliance"
    assert issue.detected_by == "compliance"


def test_new_sentences_with_citations_pass(tmp_path: Path):
    """A new sentence ending in [[...]] does NOT file a missing-citation issue."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\nMore text [[raw/b.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "missing-citation" not in result.reasons


def test_missing_citation_first_time_seen_page(tmp_path: Path):
    """A new file with uncited sentences is also flagged."""
    _, _, reviewer, page = _setup(tmp_path)
    new = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nThis is an uncited claim.\n"
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "missing-citation" in result.reasons
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_compliance.py -v -k missing_citation or new_sentences`
Expected: Missing-citation tests fail (reviewer doesn't fire that reason yet).

- [ ] **Step 3: Implement missing-citation detection**

Add a sentence-extraction helper and the check method to `ComplianceReviewer` in `src/llm_wiki/audit/compliance.py`. First add the imports and constants at the module top:

```python
import datetime
from llm_wiki.issues.queue import Issue

# Sentence splitter — naive but adequate for v1. Splits on sentence-final
# punctuation followed by whitespace or end-of-string.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# A line that opens or closes a fenced code block.
_CODE_FENCE_RE = re.compile(r"^```")
# A line that is a %% marker (not body content).
_MARKER_LINE_RE = re.compile(r"^%%\s*section:")
```

Then add this method to `ComplianceReviewer`:

```python
    def _check_missing_citation(
        self,
        result: ComplianceResult,
        old_content: str | None,
        new_content: str,
    ) -> None:
        """File a compliance issue for any uncited sentence introduced by the edit.

        For first-time-seen pages (old_content is None), every body sentence is
        considered "new" and checked. For edits, only sentences present in the
        new body but not in the old body are checked.
        """
        new_body = self._strip_frontmatter(new_content)
        new_sentences = set(self._extract_body_sentences(new_body))
        if old_content is None:
            uncited_new = [s for s in new_sentences if not self._has_citation(s)]
        else:
            old_body = self._strip_frontmatter(old_content)
            old_sentences = set(self._extract_body_sentences(old_body))
            added = new_sentences - old_sentences
            uncited_new = [s for s in added if not self._has_citation(s)]

        if not uncited_new:
            return

        result.reasons.append("missing-citation")
        for sentence in uncited_new:
            preview = sentence.strip()[:80]
            issue = Issue(
                id=Issue.make_id("compliance", result.page, f"missing-citation:{preview}"),
                type="compliance",
                status="open",
                title=f"Uncited sentence on '{result.page}'",
                page=result.page,
                body=(
                    f"The page [[{result.page}]] received a new sentence without a "
                    f"`[[...]]` citation:\n\n> {preview}\n\n"
                    f"Either add a citation or revise the sentence."
                ),
                created=Issue.now_iso(),
                detected_by="compliance",
                metadata={"sentence_preview": preview, "subtype": "missing-citation"},
            )
            _, was_new = self._queue.add(issue)
            if was_new:
                result.issues_filed.append(issue.id)

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return text
        return text[end + 4:].lstrip()

    @staticmethod
    def _extract_body_sentences(body: str) -> list[str]:
        """Sentences from non-code, non-marker, non-heading lines."""
        keep_lines: list[str] = []
        in_code = False
        for line in body.splitlines():
            stripped = line.strip()
            if _CODE_FENCE_RE.match(stripped):
                in_code = not in_code
                continue
            if in_code:
                continue
            if _MARKER_LINE_RE.match(stripped):
                continue
            if stripped.startswith("#"):
                continue
            keep_lines.append(line)
        joined = "\n".join(keep_lines)
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(joined) if s.strip()]
        return sentences

    @staticmethod
    def _has_citation(sentence: str) -> bool:
        return bool(_WIKILINK_RE.search(sentence))
```

Then call the new check from `review_change()`. Replace the existing body of `review_change` with:

```python
    def review_change(
        self,
        page_path: Path,
        old_content: str | None,
        new_content: str,
    ) -> ComplianceResult:
        result = ComplianceResult(page=page_path.stem)

        if old_content is not None and self._is_minor_edit(old_content, new_content):
            result.auto_approved = True
            result.reasons.append("minor-edit")
            return result

        self._check_missing_citation(result, old_content, new_content)
        # Tasks 7 + 8 add structural-drift auto-fix and new-idea detection here.

        return result
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_compliance.py -v`
Expected: All compliance tests so far pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance.py
git commit -m "feat: ComplianceReviewer missing-citation check"
```

---

### Task 7: `ComplianceReviewer` structural drift + auto-fix

**Files:**
- Modify: `src/llm_wiki/audit/compliance.py`
- Modify: `tests/test_audit/test_compliance.py`

A new `##`/`###` heading without a preceding `%% section: ... %%` marker is structural drift. The reviewer auto-inserts a marker (markers are invisible to humans) and records the fix in `result.auto_fixed`. The file on disk is updated.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_compliance.py`:

```python
def test_structural_drift_auto_inserts_marker(tmp_path: Path):
    """A new ## heading without a preceding marker is auto-fixed in place."""
    _, _, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    )
    new = old + "\n## New Section\n\nMore text [[raw/b.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "structural-drift" in result.reasons
    assert "inserted-marker:new-section" in result.auto_fixed

    updated = page.read_text(encoding="utf-8")
    assert "%% section: new-section %%" in updated
    # Original heading still present
    assert "## New Section" in updated
    # Marker appears immediately before the heading
    marker_pos = updated.index("%% section: new-section %%")
    heading_pos = updated.index("## New Section")
    assert marker_pos < heading_pos
    # Nothing between marker and heading except whitespace
    between = updated[marker_pos + len("%% section: new-section %%"):heading_pos]
    assert between.strip() == ""


def test_structural_drift_skipped_when_marker_present(tmp_path: Path):
    """A new heading WITH its marker is not flagged."""
    _, _, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    )
    new = (
        old
        + "\n%% section: method %%\n## Method\n\nDetails [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "structural-drift" not in result.reasons
    assert result.auto_fixed == []


def test_structural_drift_handles_h3(tmp_path: Path):
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\n### Sub Heading\n\nDetail [[raw/a.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "structural-drift" in result.reasons
    assert "inserted-marker:sub-heading" in result.auto_fixed
    assert "%% section: sub-heading %%" in page.read_text(encoding="utf-8")


def test_structural_drift_first_time_seen_page(tmp_path: Path):
    """A brand-new file with headings but no markers is auto-fixed."""
    _, _, reviewer, page = _setup(tmp_path)
    new = (
        "---\ntitle: Test\n---\n\n"
        "## Overview\n\nText [[raw/a.pdf]].\n"
        "## Method\n\nMore [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "structural-drift" in result.reasons
    assert "inserted-marker:overview" in result.auto_fixed
    assert "inserted-marker:method" in result.auto_fixed

    updated = page.read_text(encoding="utf-8")
    assert "%% section: overview %%" in updated
    assert "%% section: method %%" in updated
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_compliance.py -v -k structural_drift`
Expected: Failures — `structural-drift` reason and `auto_fixed` entries are missing.

- [ ] **Step 3: Implement structural drift detection + auto-fix**

Add the slugify helper at the top of `src/llm_wiki/audit/compliance.py` (next to the other constants):

```python
_HEADING_LINE_RE = re.compile(r"^(?P<level>##|###)\s+(?P<text>.+?)\s*$", re.MULTILINE)
_MARKER_BEFORE_HEADING_RE = re.compile(
    r"%%\s*section:\s*[^%]*?%%\s*\n(##|###)\s+(?P<text>.+?)\s*$",
    re.MULTILINE,
)


def _slugify(text: str) -> str:
    """Heading text → slug. 'Sub Heading' → 'sub-heading'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
```

Add the check method to `ComplianceReviewer`:

```python
    def _check_structural_drift(
        self,
        result: ComplianceResult,
        page_path: Path,
        new_content: str,
    ) -> str:
        """Insert %% section markers above any heading that lacks one.

        Returns the (possibly mutated) page content. Updates result.reasons
        and result.auto_fixed in place. Writes the updated content back to
        the file if any markers were inserted.
        """
        headings_with_markers = {
            m.group("text").strip().lower()
            for m in _MARKER_BEFORE_HEADING_RE.finditer(new_content)
        }
        all_headings = list(_HEADING_LINE_RE.finditer(new_content))
        orphans: list[tuple[int, str, str]] = []  # (start, heading_line, slug)
        for match in all_headings:
            heading_text = match.group("text").strip()
            if heading_text.lower() in headings_with_markers:
                continue
            slug = _slugify(heading_text)
            if not slug:
                continue
            orphans.append((match.start(), match.group(0), slug))

        if not orphans:
            return new_content

        # Insert markers in reverse order so earlier offsets remain valid
        updated = new_content
        inserted_slugs: list[str] = []
        for start, heading_line, slug in reversed(orphans):
            marker_line = f"%% section: {slug} %%\n"
            updated = updated[:start] + marker_line + updated[start:]
            inserted_slugs.append(slug)

        # Write back to disk
        page_path.write_text(updated, encoding="utf-8")

        result.reasons.append("structural-drift")
        for slug in reversed(inserted_slugs):  # restore original order for stability
            result.auto_fixed.append(f"inserted-marker:{slug}")

        return updated
```

Update `review_change()` to call `_check_structural_drift` and use the (possibly fixed) content for downstream checks:

```python
    def review_change(
        self,
        page_path: Path,
        old_content: str | None,
        new_content: str,
    ) -> ComplianceResult:
        result = ComplianceResult(page=page_path.stem)

        if old_content is not None and self._is_minor_edit(old_content, new_content):
            result.auto_approved = True
            result.reasons.append("minor-edit")
            return result

        # Auto-fix structural drift first; downstream checks see the fixed content.
        new_content = self._check_structural_drift(result, page_path, new_content)
        self._check_missing_citation(result, old_content, new_content)
        # Task 8 adds new-idea detection here.

        return result
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_compliance.py -v`
Expected: All compliance tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance.py
git commit -m "feat: ComplianceReviewer structural drift auto-fix (insert markers)"
```

---

### Task 8: `ComplianceReviewer` new-idea detection

**Files:**
- Modify: `src/llm_wiki/audit/compliance.py`
- Modify: `tests/test_audit/test_compliance.py`

A new paragraph ≥ 200 characters introduced by the edit is flagged as a `new-idea` issue (separate type from `compliance`). This is a heuristic — a substantive new paragraph from a human likely warrants librarian/human review.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_compliance.py`:

```python
def test_new_idea_files_issue_for_large_addition(tmp_path: Path):
    """A new paragraph ≥ 200 chars is flagged as new-idea."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOriginal text [[raw/a.pdf]].\n"
    )
    big = (
        "This is a substantial new paragraph that introduces a fresh idea about "
        "the topic. It has enough content to clear the 200-character threshold "
        "and trip the new-idea heuristic so the librarian can take a look later "
        "and decide what to do with it [[raw/a.pdf]]."
    )
    new = old + "\n" + big + "\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "new-idea" in result.reasons
    new_idea_issues = [
        i for i in result.issues_filed
        if (issue := queue.get(i)) is not None and issue.type == "new-idea"
    ]
    assert len(new_idea_issues) >= 1


def test_small_addition_does_not_trigger_new_idea(tmp_path: Path):
    """A short addition (< 200 chars) does not trigger new-idea."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\nA brief addition with citation [[raw/a.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "new-idea" not in result.reasons


def test_new_idea_skipped_for_first_time_seen_page(tmp_path: Path):
    """A brand-new file is not flagged as new-idea (the whole file is 'new' by definition)."""
    _, _, reviewer, page = _setup(tmp_path)
    big = "x" * 300
    new = (
        "---\ntitle: Test\n---\n\n"
        f"%% section: overview %%\n## Overview\n\n{big} [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "new-idea" not in result.reasons
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_compliance.py -v -k new_idea`
Expected: Failures.

- [ ] **Step 3: Implement new-idea detection**

Add the threshold constant near the others in `src/llm_wiki/audit/compliance.py`:

```python
_NEW_IDEA_PARAGRAPH_CHARS = 200
```

Add the check method to `ComplianceReviewer`:

```python
    def _check_new_idea(
        self,
        result: ComplianceResult,
        old_content: str | None,
        new_content: str,
    ) -> None:
        """A paragraph ≥ 200 chars added by the edit is flagged as new-idea.

        Skipped for first-time-seen pages (old_content is None) — those are
        creations, not edits, and the entire file is "new" trivially.
        """
        if old_content is None:
            return

        old_paragraphs = self._extract_paragraphs(self._strip_frontmatter(old_content))
        new_paragraphs = self._extract_paragraphs(self._strip_frontmatter(new_content))
        added = [p for p in new_paragraphs if p not in old_paragraphs]
        large_new = [p for p in added if len(p) >= _NEW_IDEA_PARAGRAPH_CHARS]
        if not large_new:
            return

        result.reasons.append("new-idea")
        for paragraph in large_new:
            preview = paragraph.strip()[:80]
            issue = Issue(
                id=Issue.make_id("new-idea", result.page, preview),
                type="new-idea",
                status="open",
                title=f"New paragraph added to '{result.page}'",
                page=result.page,
                body=(
                    f"A substantive new paragraph was added to [[{result.page}]]:\n\n"
                    f"> {preview}{'...' if len(paragraph) > 80 else ''}\n\n"
                    f"Librarian: review whether this should be integrated, sourced, "
                    f"or moved to the talk page."
                ),
                created=Issue.now_iso(),
                detected_by="compliance",
                metadata={"preview": preview, "length": len(paragraph)},
            )
            _, was_new = self._queue.add(issue)
            if was_new:
                result.issues_filed.append(issue.id)

    @staticmethod
    def _extract_paragraphs(body: str) -> list[str]:
        """Split body into paragraphs (separated by blank lines).

        Skips lines that are headings, %% markers, or fenced code blocks.
        """
        paragraphs: list[str] = []
        current: list[str] = []
        in_code = False
        for line in body.splitlines():
            if _CODE_FENCE_RE.match(line.strip()):
                in_code = not in_code
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            if in_code:
                continue
            stripped = line.strip()
            if not stripped:
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            if stripped.startswith("#") or _MARKER_LINE_RE.match(stripped):
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            current.append(stripped)
        if current:
            paragraphs.append(" ".join(current).strip())
        return [p for p in paragraphs if p]
```

Wire it into `review_change()`:

```python
        new_content = self._check_structural_drift(result, page_path, new_content)
        self._check_missing_citation(result, old_content, new_content)
        self._check_new_idea(result, old_content, new_content)

        return result
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_compliance.py -v`
Expected: All compliance tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance.py
git commit -m "feat: ComplianceReviewer new-idea heuristic"
```

---

### Task 9: `ChangeDispatcher` (debouncer)

**Files:**
- Create: `src/llm_wiki/daemon/dispatcher.py`
- Create: `tests/test_daemon/test_dispatcher.py`

The dispatcher wraps the existing `FileWatcher.on_change` callback. When a change arrives for a path, it (re)starts a per-path debounce timer; only after the timer elapses without a new change does it call `on_settled(path)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_dispatcher.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.dispatcher import ChangeDispatcher


@pytest.mark.asyncio
async def test_dispatcher_debounces_rapid_submissions(tmp_path: Path):
    """Three rapid submits within the debounce window collapse into one dispatch."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.1, on_settled=on_settled)
    p = tmp_path / "x.md"

    dispatcher.submit(p)
    await asyncio.sleep(0.04)
    dispatcher.submit(p)
    await asyncio.sleep(0.04)
    dispatcher.submit(p)

    await asyncio.sleep(0.2)
    assert fired == [p]

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_independent_paths_dispatch_in_parallel(tmp_path: Path):
    """Different paths debounce independently."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.1, on_settled=on_settled)
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"

    dispatcher.submit(a)
    dispatcher.submit(b)
    await asyncio.sleep(0.2)

    assert set(fired) == {a, b}

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_stop_cancels_pending(tmp_path: Path):
    """stop() cancels any pending dispatches before they fire."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.5, on_settled=on_settled)
    dispatcher.submit(tmp_path / "x.md")
    await dispatcher.stop()

    await asyncio.sleep(0.6)
    assert fired == []


@pytest.mark.asyncio
async def test_dispatcher_isolates_callback_errors(tmp_path: Path):
    """A callback that raises does not break subsequent dispatches."""
    call_count = {"n": 0}

    async def flaky_callback(path: Path) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated handler failure")

    dispatcher = ChangeDispatcher(debounce_secs=0.05, on_settled=flaky_callback)
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"

    dispatcher.submit(a)
    await asyncio.sleep(0.15)
    dispatcher.submit(b)
    await asyncio.sleep(0.15)

    assert call_count["n"] == 2  # both dispatched even though the first raised

    await dispatcher.stop()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_dispatcher.py -v`
Expected: ImportError — `llm_wiki.daemon.dispatcher` does not exist.

- [ ] **Step 3: Implement `ChangeDispatcher`**

```python
# src/llm_wiki/daemon/dispatcher.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

OnSettledCallback = Callable[[Path], Awaitable[None]]


class ChangeDispatcher:
    """Per-path debouncer for file change events.

    submit(path) (re)starts a debounce timer for the path. After the timer
    elapses without further submissions for that path, on_settled(path) is
    called. Errors raised by on_settled are logged but do not affect future
    dispatches.
    """

    def __init__(
        self,
        debounce_secs: float,
        on_settled: OnSettledCallback,
    ) -> None:
        self._debounce = debounce_secs
        self._on_settled = on_settled
        self._pending: dict[Path, asyncio.Task] = {}

    def submit(self, path: Path) -> None:
        existing = self._pending.get(path)
        if existing is not None and not existing.done():
            existing.cancel()
        self._pending[path] = asyncio.create_task(self._wait_and_dispatch(path))

    async def _wait_and_dispatch(self, path: Path) -> None:
        try:
            await asyncio.sleep(self._debounce)
            try:
                await self._on_settled(path)
            except Exception:
                logger.exception("Error in dispatch callback for %s", path)
        except asyncio.CancelledError:
            return
        finally:
            self._pending.pop(path, None)

    async def stop(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        for task in list(self._pending.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error during dispatcher shutdown")
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._pending.values() if not t.done())
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_dispatcher.py -v`
Expected: All four dispatcher tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/dispatcher.py tests/test_daemon/test_dispatcher.py
git commit -m "feat: ChangeDispatcher — per-path debouncer for file events"
```

---

### Task 10: Wire scheduler + dispatcher + reviewer into `DaemonServer`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `src/llm_wiki/daemon/__main__.py`

The daemon server now owns all four pieces of substrate (scheduler, snapshot store, reviewer, dispatcher). `start()` constructs them; `stop()` shuts them down in the right order. The watcher callback is moved out of `__main__.py` into a server method.

- [ ] **Step 1: Write failing test**

Append to `tests/test_daemon/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_daemon_server_registers_auditor_worker(sample_vault: Path, tmp_path: Path):
    """Starting DaemonServer registers and runs the auditor worker."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "test.sock"
    config = WikiConfig(
        maintenance=MaintenanceConfig(auditor_interval="1s"),
    )
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        # Auditor should run immediately on start, before the first interval
        await asyncio.sleep(0.2)
        assert "auditor" in server._scheduler.worker_names
        assert server._scheduler.last_run_iso("auditor") is not None
    finally:
        await server.stop()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v -k registers_auditor`
Expected: Failure — `DaemonServer` has no `_scheduler` attribute.

- [ ] **Step 3: Modify `DaemonServer`**

In `src/llm_wiki/daemon/server.py`, update imports at the top:

```python
from llm_wiki.daemon.dispatcher import ChangeDispatcher
from llm_wiki.daemon.scheduler import IntervalScheduler, ScheduledWorker, parse_interval
from llm_wiki.daemon.snapshot import PageSnapshotStore
```

Add to `DaemonServer.__init__`:

```python
        self._scheduler: IntervalScheduler | None = None
        self._snapshot_store: PageSnapshotStore | None = None
        self._compliance_reviewer = None  # type: ignore[assignment]  # set in start()
        self._dispatcher: ChangeDispatcher | None = None
```

Update `DaemonServer.start()`:

```python
    async def start(self) -> None:
        """Scan vault, construct maintenance substrate, start listening."""
        self._vault = Vault.scan(self._vault_root)

        # Phase 5b substrate
        from llm_wiki.audit.compliance import ComplianceReviewer
        from llm_wiki.issues.queue import IssueQueue
        state_dir = _state_dir_for(self._vault_root)
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        self._snapshot_store = PageSnapshotStore(state_dir)
        self._compliance_reviewer = ComplianceReviewer(
            self._vault_root, IssueQueue(wiki_dir), self._config
        )
        self._dispatcher = ChangeDispatcher(
            debounce_secs=self._config.maintenance.compliance_debounce_secs,
            on_settled=self._handle_settled_change,
        )

        self._scheduler = IntervalScheduler()
        self._register_maintenance_workers()
        await self._scheduler.start()

        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        logger.info(
            "Daemon started: %d pages, socket %s, workers=%s",
            self._vault.page_count, self._socket_path,
            self._scheduler.worker_names,
        )
```

Update `DaemonServer.stop()`:

```python
    async def stop(self) -> None:
        """Shut down the maintenance substrate, then the server."""
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._dispatcher is not None:
            await self._dispatcher.stop()
            self._dispatcher = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path.exists():
            self._socket_path.unlink()
        logger.info("Daemon stopped")
```

Add the worker registration helper and the file-change handlers:

```python
    def _register_maintenance_workers(self) -> None:
        """Register all maintenance workers with the scheduler.

        Sub-phase 5b registers only the auditor. Sub-phases 5c (librarian)
        and 5d (adversary) extend this method to register additional workers.
        """
        assert self._scheduler is not None

        async def run_auditor() -> None:
            from llm_wiki.audit.auditor import Auditor
            from llm_wiki.issues.queue import IssueQueue
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            auditor = Auditor(self._vault, queue, self._vault_root)
            report = auditor.audit()
            logger.info(
                "Auditor: %d new issues, %d existing",
                len(report.new_issue_ids), len(report.existing_issue_ids),
            )

        self._scheduler.register(
            ScheduledWorker(
                name="auditor",
                interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
                coro_factory=run_auditor,
            )
        )

    async def handle_file_changes(
        self, changed: list[Path], removed: list[Path]
    ) -> None:
        """File-watcher callback. Replaces __main__.on_file_change.

        Rescans the vault, then queues each changed page for compliance review
        via the debouncer. Removed pages purge their snapshot.
        """
        await self.rescan()
        for path in changed:
            try:
                rel = path.relative_to(self._vault_root)
            except ValueError:
                continue
            if any(p.startswith(".") for p in rel.parts):
                continue  # skip hidden dirs (e.g. .issues)
            if self._dispatcher is not None:
                self._dispatcher.submit(path)
        for path in removed:
            if self._snapshot_store is not None:
                self._snapshot_store.remove(path.stem)

    async def _handle_settled_change(self, path: Path) -> None:
        """Called by ChangeDispatcher after a path has settled past the debounce window."""
        if self._compliance_reviewer is None or self._snapshot_store is None:
            return
        if not path.exists():
            self._snapshot_store.remove(path.stem)
            return
        try:
            new_content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("Failed to read %s for compliance review", path)
            return
        old_content = self._snapshot_store.get(path.stem)
        result = self._compliance_reviewer.review_change(path, old_content, new_content)
        # Re-read in case the reviewer auto-fixed the file
        try:
            self._snapshot_store.set(path.stem, path.read_text(encoding="utf-8"))
        except OSError:
            logger.exception("Failed to update snapshot for %s", path)
        logger.info(
            "Compliance review %s: auto_approved=%s reasons=%s issues=%d",
            path.stem, result.auto_approved, result.reasons, len(result.issues_filed),
        )
```

- [ ] **Step 4: Update `__main__.py` to use `server.handle_file_changes`**

In `src/llm_wiki/daemon/__main__.py`, replace the `on_file_change` definition with:

```python
    watcher = FileWatcher(
        vault_root, server.handle_file_changes, poll_interval=2.0
    )
```

And remove the inline `async def on_file_change` block (and its surrounding `await server.rescan()` call) since `handle_file_changes` already does both.

- [ ] **Step 5: Run tests, expect PASS**

Run: `pytest tests/test_daemon/ -v`
Expected: All daemon tests pass — including the existing `test_full_daemon_lifecycle` and `test_watcher_triggers_rescan`, plus the new auditor-registration test. If any existing tests fail, the most likely cause is that they construct `DaemonServer` without a config that has a parseable `auditor_interval`. Default `WikiConfig().maintenance.auditor_interval` is `"6h"` which `parse_interval` accepts — so the default path should be fine.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py src/llm_wiki/daemon/__main__.py \
        tests/test_daemon/test_scheduler.py
git commit -m "feat: wire scheduler + dispatcher + compliance reviewer into DaemonServer"
```

---

### Task 11: Daemon `scheduler-status` route

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_daemon/test_scheduler.py`

A read-only route returning the registered workers and their last-run timestamps. Used by the `llm-wiki maintenance status` CLI command in Task 12.

- [ ] **Step 1: Write failing test**

Append to `tests/test_daemon/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_scheduler_status_route(sample_vault: Path, tmp_path: Path):
    """The scheduler-status route returns workers + last-run timestamps."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.client import DaemonClient
    from llm_wiki.daemon.server import DaemonServer

    sock_path = tmp_path / "sched-status.sock"
    config = WikiConfig(maintenance=MaintenanceConfig(auditor_interval="1s"))
    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Wait for the auditor to run at least once
        await asyncio.sleep(0.2)

        client = DaemonClient(sock_path)
        resp = client.request({"type": "scheduler-status"})

        assert resp["status"] == "ok"
        workers = resp["workers"]
        assert isinstance(workers, list)
        names = {w["name"] for w in workers}
        assert "auditor" in names

        auditor = next(w for w in workers if w["name"] == "auditor")
        assert "interval_seconds" in auditor
        assert auditor["interval_seconds"] == 1.0
        assert auditor["last_run"] is not None
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v -k status_route`
Expected: Failure — `Unknown request type: scheduler-status`.

- [ ] **Step 3: Implement the route**

In `src/llm_wiki/daemon/server.py`, add a case to `_route()`:

```python
            case "scheduler-status":
                return self._handle_scheduler_status()
```

Add the handler method:

```python
    def _handle_scheduler_status(self) -> dict:
        if self._scheduler is None:
            return {"status": "ok", "workers": []}
        workers = []
        for worker in self._scheduler._workers:  # noqa: SLF001 — internal access is fine, same module family
            workers.append({
                "name": worker.name,
                "interval_seconds": worker.interval_seconds,
                "last_run": self._scheduler.last_run_iso(worker.name),
            })
        return {"status": "ok", "workers": workers}
```

- [ ] **Step 4: Run test, expect PASS**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All scheduler tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_scheduler.py
git commit -m "feat: daemon scheduler-status route"
```

---

### Task 12: CLI `llm-wiki maintenance status`

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/test_maintenance_cmd.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli/test_maintenance_cmd.py
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from llm_wiki.cli.main import cli


def test_maintenance_status_lists_auditor(sample_vault: Path):
    """`llm-wiki maintenance status` lists the auditor worker."""
    runner = CliRunner()
    result = runner.invoke(cli, ["maintenance", "status", "--vault", str(sample_vault)])
    assert result.exit_code == 0, result.output
    assert "auditor" in result.output
    # Header line
    assert "interval" in result.output.lower()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_cli/test_maintenance_cmd.py -v`
Expected: `Error: No such command 'maintenance'`.

- [ ] **Step 3: Implement the command group**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.group()
def maintenance() -> None:
    """Inspect and manage maintenance workers."""
    pass


@maintenance.command("status")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def maintenance_status(vault_path: Path) -> None:
    """Show registered maintenance workers and their last-run times."""
    client = _get_client(vault_path)
    resp = client.request({"type": "scheduler-status"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Status query failed"))

    workers = resp["workers"]
    if not workers:
        click.echo("No maintenance workers registered.")
        return

    click.echo(f"{'name':<14} {'interval':<12} last_run")
    click.echo("-" * 60)
    for worker in workers:
        interval = f"{worker['interval_seconds']:.0f}s"
        last = worker["last_run"] or "never"
        click.echo(f"{worker['name']:<14} {interval:<12} {last}")
```

- [ ] **Step 4: Run test, expect PASS**

Run: `pytest tests/test_cli/test_maintenance_cmd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_maintenance_cmd.py
git commit -m "feat: llm-wiki maintenance status CLI command"
```

---

### Task 13: Compliance integration test

**Files:**
- Create: `tests/test_daemon/test_compliance_integration.py`

End-to-end: start the daemon, modify a page, wait the debounce window, assert a compliance issue exists.

- [ ] **Step 1: Write failing test**

```python
# tests/test_daemon/test_compliance_integration.py
"""End-to-end: edit page → debounced compliance review → issue filed."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_compliance_review_fires_after_edit(tmp_path: Path):
    """Edit a page → wait for debounce → assert compliance issue exists."""
    # Build a tiny vault from scratch (the sample_vault fixture sample doesn't
    # have a wiki_dir layout we control, and we want to set wiki_dir cleanly).
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page = wiki_dir / "test-page.md"
    page.write_text(
        "---\ntitle: Test Page\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOriginal text [[raw/source.pdf]].\n"
    )

    config = WikiConfig(
        maintenance=MaintenanceConfig(
            compliance_debounce_secs=0.3,
            auditor_interval="1h",
        ),
        vault=VaultConfig(wiki_dir="wiki/"),
    )

    sock_path = tmp_path / "compliance-int.sock"
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    watcher = FileWatcher(tmp_path, server.handle_file_changes, poll_interval=0.1)
    await watcher.start()

    try:
        client = DaemonClient(sock_path)

        # Seed the snapshot store by triggering one settled change with the
        # original content. We do this by writing the file again with identical
        # content (mtime changes), waiting the debounce, and assuming the
        # reviewer treats it as a "creation" review.
        page.write_text(page.read_text(encoding="utf-8"))
        await asyncio.sleep(0.6)

        # Now make an uncited edit
        page.write_text(
            "---\ntitle: Test Page\n---\n\n"
            "%% section: overview %%\n## Overview\n\nOriginal text [[raw/source.pdf]].\n"
            "We added a brand new uncited claim that the reviewer should flag.\n"
        )

        # Wait debounce + slack
        await asyncio.sleep(0.8)

        # Query the issue queue — there should be a compliance issue
        listing = client.request({"type": "issues-list", "type_filter": "compliance"})
        assert listing["status"] == "ok"
        compliance_titles = [i["title"] for i in listing["issues"]]
        assert any("test-page" in t.lower() for t in compliance_titles), (
            f"expected a compliance issue for test-page, got: {compliance_titles}"
        )
    finally:
        await watcher.stop()
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run test, expect PASS**

Run: `pytest tests/test_daemon/test_compliance_integration.py -v`
Expected: PASS — all the underlying machinery is in place from earlier tasks.

If the test is flaky (e.g., the debounce window is too tight on a slow CI machine), bump `compliance_debounce_secs` to `0.5` and the slack sleeps to `1.0`. Do not eliminate the sleeps — the test is exercising real timing behavior.

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon/test_compliance_integration.py
git commit -m "test: compliance review end-to-end integration test"
```

---

### Task 14: Run full suite + README + roadmap update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass — Phase 1-4, Phase 5a, and all new Phase 5b tests.

If any Phase 5a or earlier test regressed, the most likely cause is `DaemonServer.stop()` ordering: the dispatcher must be stopped before the asyncio loop is torn down. Verify the stop sequence in `DaemonServer.stop()` matches the implementation in Task 10.

- [ ] **Step 2: Update README Quick Start**

Add to the daemon management section in `README.md`:

```markdown
# Inspect maintenance workers
llm-wiki maintenance status --vault /path/to/your/vault
```

- [ ] **Step 3: Update Project Structure**

Add to the package layout in `README.md` under `src/llm_wiki/`:

```
  daemon/
    scheduler.py        # IntervalScheduler + ScheduledWorker
    dispatcher.py       # ChangeDispatcher (per-path debouncer)
    snapshot.py         # PageSnapshotStore
  audit/
    compliance.py       # ComplianceReviewer (heuristic edit review)
```

- [ ] **Step 4: Update Roadmap**

Mark 5b as complete:

```markdown
- [x] **Phase 5a: Issue Queue + Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [x] **Phase 5b: Background Workers + Compliance Review** — Async scheduler, debounced compliance pipeline
- [ ] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [ ] **Phase 5d: Adversary + Talk Pages** — Claim verification, async discussion sidecars
- [ ] **Phase 6: MCP Server** — High-level + low-level tools for agent integration
```

- [ ] **Step 5: Update Documentation references**

Add to the Documentation list:

```markdown
- **[Phase 5b Plan](docs/superpowers/plans/2026-04-08-phase5b-scheduler-compliance.md)** — Implementation plan for scheduler + compliance review
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README updates for phase 5b — scheduler, compliance review"
```

---

## Self-review checklist

Before declaring this plan complete, verify:

- [ ] `parse_interval` rejects every malformed input listed in the test parametrize block
- [ ] `IntervalScheduler` runs each worker immediately on start AND on its interval (not just on the interval)
- [ ] Scheduler error isolation is exercised by `test_scheduler_isolates_worker_errors` AND no exception escapes `_run_once` other than `CancelledError`
- [ ] `ChangeDispatcher` cancels prior pending tasks for the same path on `submit`, not all pending tasks
- [ ] `ChangeDispatcher.stop()` cancels in-flight tasks and awaits their cancellation cleanly
- [ ] `ComplianceReviewer.review_change` handles `old_content=None` (first-time-seen page) for every check: minor-edit is skipped, missing-citation runs, structural-drift runs, new-idea is skipped
- [ ] Structural drift auto-fix updates the file on disk AND records `inserted-marker:<slug>` in `auto_fixed`
- [ ] Auto-fixed markers appear immediately above their headings (no blank line between)
- [ ] No LLM calls anywhere in this sub-phase (grep for `LLMClient`, `litellm`, `complete(` in new code)
- [ ] `DaemonServer.stop()` shuts down scheduler → dispatcher → server → socket in that order
- [ ] `__main__.py` no longer defines `on_file_change` — it passes `server.handle_file_changes` directly to `FileWatcher`
- [ ] Every new test handles cleanup (worktree fixtures, server stop, watcher stop) in a `try/finally` block
- [ ] The compliance integration test sleeps long enough to cover `compliance_debounce_secs + watcher poll_interval + handler latency`

## Spec sections satisfied by 5b

- §5 Compliance Review Queue (full subsection — debounce, diff, auto-approve / auto-fix / flag pathways)
- §2 Background Workers paragraph (async coroutines on intervals, configurable schedules)
- §2 Write Coordination — partial: the snapshot store provides the "previously seen content" foundation that the spec's three-way merge will eventually need
- §5 Auditor row (now runs on schedule, not just on demand)

## What's deferred from this sub-phase

Explicitly out of scope (handled by later sub-phases):

- LLM-driven compliance review (heuristic only — LLM compliance review is a future enhancement)
- Three-way merge for write conflicts (deferred indefinitely)
- Talk-page auto-posting from compliance reviewer (deferred to 5d)
- Honoring the LLM queue's `priority="maintenance"` semantics in scheduling (current FIFO is fine; 5c/5d will validate)

## Dependencies

- **Requires 5a** for `IssueQueue`, `Issue`, `Auditor` (the auditor worker calls `Auditor.audit()`)
- Does not require 5c or 5d
- Sub-phases 5c and 5d will register additional workers via the same `_register_maintenance_workers` extension point added in Task 10

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-phase5b-scheduler-compliance.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Either option uses this plan as the input. The most fragile tasks are 10 (server wiring) and 13 (timing-sensitive integration test) — review those carefully.
