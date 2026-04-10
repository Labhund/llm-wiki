# Active LLM Processes Design (`llm-wiki ps`)

**Date:** 2026-04-10
**Status:** Approved

---

## Problem

There is no way to see what the daemon is currently doing. The LLM queue tracks only a bare `active_count` integer. The scheduler records `last_run` timestamps but not whether a worker is mid-execution. When the daemon is slow or busy, the operator has no way to know why.

---

## Goal

A one-shot `llm-wiki ps` CLI command that prints a snapshot of:

1. **Background workers** — which are currently running vs. idle, when each last completed, consecutive failures
2. **LLM queue** — each in-flight call with what it is doing, elapsed time, and priority; pending count; slot ceiling

`--watch` (live refresh) is deferred as a todo.

---

## Approach: Labeled jobs in `LLMQueue` + running-state in `IntervalScheduler`

No new abstractions. Both objects already own the relevant state; we extend them minimally.

---

## Data Model

### `ActiveJob` (new dataclass in `llm_queue.py`)

```python
@dataclass
class ActiveJob:
    id: int
    label: str        # e.g. "adversary:verify:protein-dj"
    priority: str     # "query" | "ingest" | "maintenance"
    started_at: float # time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at
```

### `LLMQueue` changes

- `submit()` gains `label: str = "unknown"` parameter.
- New instance fields:
  - `_pending: int` — incremented before semaphore acquire, decremented after
  - `_active_jobs: dict[int, ActiveJob]` — jobs currently occupying a slot
  - `_next_id: int` — monotonic counter for job IDs
  - `_max_concurrent: int` — stored at init (already passed in, just not retained)
- `active_jobs` property returns a snapshot list of `ActiveJob`
- `pending_count` property returns `_pending`
- `slots_total` property returns `_max_concurrent`

Pending/active lifecycle:

```
submit() called
  → _pending += 1
  → await semaphore acquire
  → _pending -= 1
  → register ActiveJob in _active_jobs
  → run fn()
  → deregister ActiveJob (finally)

If cancelled while waiting for semaphore (CancelledError before acquire):
  → _pending decremented in outer finally block
```

### `IntervalScheduler` changes

- New field: `_running: set[str]` — worker names currently executing.
- Set at top of `_run_once()`, cleared in `finally` (so errors don't leave workers stuck as "running").
- New property: `running_workers` returns a copy of the set.

---

## Label Conventions

Format: `"<source>:<action>[:<detail>]"` — colon-separated, lowercase. Detail is included only when it's a meaningful discriminator (page slug, chunk position, author). Labels are purely informational — no code parses them.

| Call site | Label |
|-----------|-------|
| Adversary claim extraction | `adversary:extract-claims:<page>` |
| Adversary verification | `adversary:verify:<page>` |
| Librarian manifest refinement | `librarian:refine-manifest` |
| Librarian authority recalc | `librarian:authority-recalc` |
| Librarian talk summary | `librarian:talk-summary:<page>` |
| Compliance review | `compliance:review:<page>` |
| Ingest concept extraction | `ingest:extract:<source> chunk <n>/<total>` |
| Ingest grounding | `ingest:ground:<page>` |
| Query traversal | `query:traverse:step-<n>` |
| Query synthesis | `query:synthesize` |
| Commit summariser | `commit:summarize:<author>` |

Call sites that don't pass a label fall back to `"unknown"` without breaking anything. Labels can be filled in incrementally.

---

## Daemon Route

**New route type:** `process-list`
**Handler:** `_handle_process_list()` in `server.py`

Response:

```json
{
  "status": "ok",
  "jobs": [
    {"id": 1, "label": "adversary:verify:protein-dj", "priority": "maintenance", "elapsed_s": 12.3},
    {"id": 2, "label": "query:traverse:step-2",       "priority": "query",       "elapsed_s": 4.1}
  ],
  "pending": 1,
  "slots_total": 2,
  "tokens_used": 84230,
  "workers": [
    {"name": "adversary",  "state": "running", "last_run": "2026-04-10T14:02:11Z", "consecutive_failures": 0},
    {"name": "auditor",    "state": "idle",    "last_run": "2026-04-10T13:57:44Z", "consecutive_failures": 0},
    {"name": "librarian",  "state": "idle",    "last_run": "2026-04-10T13:01:09Z", "consecutive_failures": 0},
    {"name": "compliance", "state": "idle",    "last_run": "2026-04-10T14:04:58Z", "consecutive_failures": 0}
  ]
}
```

Worker `state` is `"running"` if the worker name is in `scheduler.running_workers`, otherwise `"idle"`.

`last_run` is the ISO timestamp from `scheduler.last_run_iso()`. May be `null` if the worker has not yet completed a run since daemon start.

Handler is synchronous (no async work). Reads directly from `_llm_queue` and `_scheduler`. Added to `_route()` alongside existing cases.

---

## CLI Command

**`llm-wiki ps`** — added to `cli/main.py`. Calls the daemon's `process-list` route via the existing client. No new flags for now (`--watch` is deferred).

### Output format

```
PROCESSES  2 active · 1 pending · 84,230 tokens used

WORKERS
  adversary    running   verifying protein-dj                     12s
  auditor      idle      last run 6m ago
  librarian    idle      last run 1h ago
  compliance   idle      last run 1m ago

LLM QUEUE  (2/2 slots, 1 pending)
  [1]  adversary · verify · protein-dj     maintenance   12s
  [2]  query · traverse · step-2           query          4s
```

**Idle queue:** Workers section renders; jobs section shows `No active LLM calls.`

**Daemon not running:** Print `Daemon not running.` to stderr, exit non-zero. Same pattern as other CLI commands that require the daemon.

### Display logic

- `last run N ago` — human-readable relative time from `last_run` ISO string. Buckets: seconds, minutes, hours.
- Elapsed time column in the jobs section — rendered as `<n>s` (always seconds; jobs running for minutes indicate a stall worth noticing).
- Workers section: label split at `:`, source prefix dropped (redundant with name column), action + detail shown, detail truncated with `…` if > 30 chars.
- Jobs section: full label rendered, source kept (provides context without the name column).
- Tokens formatted with comma separator (`84,230`).

---

## Files Changed

| File | Change |
|------|--------|
| `daemon/llm_queue.py` | Add `ActiveJob`, extend `submit()`, add `active_jobs`/`pending_count`/`slots_total` properties |
| `daemon/scheduler.py` | Add `_running: set[str]`, set/clear in `_run_once()`, add `running_workers` property |
| `daemon/server.py` | Add `_handle_process_list()`, wire into `_route()` |
| `daemon/protocol.py` | No change needed (route type is just a string) |
| `cli/main.py` | Add `ps` subcommand |
| `daemon/client.py` | Verify `arequest` handles `process-list` type (should already work) |

All `queue.submit()` call sites in: `adversary/`, `librarian/`, `audit/compliance.py`, `ingest/`, `traverse/`, `daemon/commit.py` — add `label=` argument.

---

## Deferred

- **`--watch` flag** — live refresh (poll `process-list` every N seconds, rerender in-place). Left as todo; the snapshot command is the foundation.
- **MCP tool** — `wiki_processes`. Left as todo; agents can invoke the CLI for now.
- **Queue depth histogram** — how long jobs typically wait. Not needed for v1.
