# Phase 5: Maintenance Agents — Master Roadmap

> **Status:** Roadmap. Each sub-phase below gets its own full TDD implementation plan in a separate file. This document is the shared reference those plans build from.

**Goal:** Stand up the maintenance side of the wiki — agents that keep the knowledge base honest, current, and well-organized over time. These agents run as background workers in the daemon and write to a shared issue queue and (for some) talk pages.

**Architecture:** Six independent capabilities sharing two pieces of common substrate (issue queue + interval scheduler). Built incrementally so each sub-phase produces working, testable software on its own. Background workers route LLM calls through the existing `LLMQueue` at `priority="maintenance"` so user-facing queries always preempt them.

**Tech Stack:** Python 3.11+, asyncio, pytest-asyncio, existing `LLMClient`/`LLMQueue`/`Vault`/`ManifestStore`/`Page` infrastructure. No new third-party dependencies.

---

## Why split Phase 5 into sub-phases

The Phase 5 spec (Section 5 of `docs/superpowers/specs/2026-04-07-llm-wiki-tool-design.md`) covers six independent subsystems:

1. Issue queue (storage substrate)
2. Auditor (structural integrity checks)
3. Background worker scheduler (runtime substrate)
4. Compliance review queue (file-watcher → auditor)
5. Librarian (usage-driven manifest refinement)
6. Adversary + talk pages (claim verification + async discussion)

Each is independently shippable and independently testable. A single Phase 5 plan would balloon past 100 tasks and create false dependencies. The sub-phase split below maximizes early value (5a is mostly programmatic, ships `llm-wiki lint` immediately) and lets later sub-phases build cleanly on substrate the earlier ones already proved out.

---

## Dependency graph

```
                ┌──────────────────────────┐
                │ 5a: Issue Queue +        │
                │     Auditor + Lint       │
                │ (programmatic, no LLM)   │
                └─────────────┬────────────┘
                              │ provides IssueQueue + Auditor
                              ▼
                ┌──────────────────────────┐
                │ 5b: Scheduler +          │
                │     Compliance Review    │
                │ (runtime substrate)      │
                └────────┬─────────┬───────┘
                         │         │ provides scheduler
              ┌──────────┘         └──────────┐
              ▼                               ▼
   ┌──────────────────────┐       ┌──────────────────────┐
   │ 5c: Librarian        │       │ 5d: Adversary +      │
   │ (usage → manifest)   │       │     Talk Pages       │
   │ (LLM-heavy)          │       │ (LLM-heavy)          │
   └──────────────────────┘       └──────────────────────┘
```

**5c and 5d are independent of each other** and can be sequenced in either order, or implemented in parallel by different sessions if desired.

---

## What's already in place (relevant to Phase 5)

This section exists so the per-sub-phase sessions don't need to re-discover the supporting infrastructure.

**Config (`src/llm_wiki/config.py`):**
- `MaintenanceConfig` already has `librarian_interval`, `adversary_interval`, `adversary_claims_per_run`, `auditor_interval`, `authority_recalc`, `compliance_debounce_secs`, `talk_pages_enabled`. No config changes required.
- `LLMQueueConfig.priority_order = ["query", "ingest", "maintenance"]` is already defined.

**LLM queue (`src/llm_wiki/daemon/llm_queue.py`):**
- `LLMQueue.submit(fn, priority=...)` already accepts a `priority` argument. Currently FIFO via semaphore — `priority` is recorded but not yet enforced. Maintenance workers should pass `priority="maintenance"` for forward compatibility.
- `LLMQueue.PRIORITY_MAP = {"query": 0, "ingest": 1, "maintenance": 2}` is defined.

**LLM client (`src/llm_wiki/traverse/llm_client.py`):**
- `LLMClient.complete(messages, temperature, priority)` already exists. Maintenance agents reuse this directly.

**Manifest entries (`src/llm_wiki/manifest.py`):**
- `ManifestEntry` already has placeholder fields waiting to be populated by 5c: `read_count: int = 0`, `usefulness: float = 0.0`, `authority: float = 0.0`, `last_corroborated: str | None = None`. Comment in code: "Usage stats — initialized to defaults, updated by librarian (Phase 5)".
- `build_entry()` initializes `tags=[]` with comment "Tags added by librarian in Phase 5".

**Traversal logs (`src/llm_wiki/traverse/log.py`):**
- `TraversalLog.save(log_dir)` writes one JSON line per query to `<state_dir>/traversal_logs/traversal_logs.jsonl`. This is the librarian's input.
- Each `TurnLog` carries `pages_read` with `salient_points` and `relevance` — exactly what the librarian needs to compute usefulness.

**Vault (`src/llm_wiki/vault.py`):**
- `Vault.scan(root)` already excludes hidden directories: `if not any(p.startswith(".") for p in f.relative_to(root).parts)`. So `wiki/.issues/` is auto-excluded from page indexing — but **`*.talk.md` files are NOT** excluded by extension. Sub-phase 5d needs to filter these out.
- `_state_dir_for(vault_root)` returns `~/.llm-wiki/vaults/<slug>-<hash>/` — sub-phases write sidecar files (manifest overrides, scheduler state) here.

**Daemon (`src/llm_wiki/daemon/server.py`):**
- `DaemonServer._route()` uses `match` on `req_type`. Sub-phases add new cases.
- `DaemonServer.start()` is the place to register and start the scheduler (sub-phase 5b).
- `DaemonServer.stop()` must stop the scheduler symmetrically.
- `_handle_query` and `_handle_ingest` show the LLMClient construction pattern that maintenance agents will reuse.

**File watcher (`src/llm_wiki/daemon/watcher.py`):**
- `FileWatcher` calls `on_change(changed: list[Path], removed: list[Path])` after each poll. Sub-phase 5b wraps this with a debouncer.
- Currently the only on_change handler in `__main__.py` calls `server.rescan()`. 5b extends this.

**Page parser (`src/llm_wiki/page.py`):**
- `Page.parse(path)` already extracts `wikilinks`, `frontmatter`, `sections` (with `%%` markers or heading fallback). Sub-phase 5a uses `wikilinks` for orphan/broken-link detection.
- `_NON_PAGE_EXTENSIONS = {".pdf", ".png", ...}` is the existing set used to skip wikilinks pointing at non-markdown files.

---

## Cross-cutting design decisions

These apply to every sub-phase. The per-sub-phase plans should not re-litigate them.

**1. Issue ID format.** Issues are stored as `wiki/.issues/<id>.md`. The `id` is `<type>-<page-or-target>-<hash6>` where `hash6` is the first 6 hex chars of `sha256(type + page + body)`. This makes issues idempotent: re-running a check that finds the same problem produces the same ID and the existing file is left alone. Examples: `orphan-stale-notes-a3f9e1`, `broken-link-srna-tquant-7b2c40`, `claim-failed-srna-embeddings-9d11f2`.

**2. Issue file format.** YAML frontmatter + markdown body. All datetime values are ISO 8601 UTC.

```markdown
---
id: broken-link-srna-tquant-7b2c40
type: broken-link
status: open
title: "Wikilink target 'k-means-deep' does not exist"
page: srna-tquant
created: 2026-04-08T12:34:56+00:00
detected_by: auditor
metadata:
  target: k-means-deep
  source_section: method
---

The page [[srna-tquant]] in section `method` references [[k-means-deep]],
but no such page exists in the vault. Either create the page or remove the link.
```

**3. Status transitions.** `open → resolved | wontfix`. Resolved/wontfix issues are retained for audit trail (per spec). The auditor never re-opens a `wontfix` issue.

**4. Maintenance agents never overwrite human prose.** This is the spec's "human prose is sacred" rule. Agents may:
- File issues
- Append to talk pages
- Add `%%` markers (5b's compliance reviewer can do this — invisible to humans)
- Update sidecar metadata in `~/.llm-wiki/vaults/.../`

Agents may NOT:
- Edit any markdown body content authored by a human
- Modify page frontmatter that wasn't written by them
- Overwrite an existing `%% section: ... %%` marker block boundary

**5. Manifest persistence.** Sub-phase 5c needs librarian-refined `tags`/`summary`/`authority`/`last_corroborated`/`read_count`/`usefulness` to survive `Vault.scan()`. The chosen approach: a sidecar JSON file at `<state_dir>/manifest_overrides.json`, keyed by page name. `Vault.scan()` loads it and applies overrides on top of programmatically-built entries. Pages that have been deleted are pruned from the override file on next scan. Frontmatter is NEVER mutated for this purpose.

**6. Background workers use the LLM queue.** All LLM calls from maintenance agents go through `LLMClient.complete(..., priority="maintenance")`. No direct litellm calls. This is a non-negotiable structural contract from the spec.

**7. Scheduler cancellation discipline.** Workers must be cancellable mid-iteration. Long-running LLM calls will be allowed to complete (the semaphore makes interruption messy), but the scheduler stops dispatching new work as soon as `stop()` is called.

**8. Empty vault is valid.** Every check, agent, and worker must handle a vault with zero markdown files without raising. Tests should cover this.

---

# Sub-phase 5a: Issue Queue + Auditor + Lint

**Plan file (to be written):** `docs/superpowers/plans/2026-04-08-phase5a-issue-queue-auditor-lint.md`

**Goal:** Add a persistent issue queue and a structural-integrity auditor, exposed via `llm-wiki lint` and a daemon `lint` route. Programmatic checks only — no LLM calls in this sub-phase.

**Why first:** Smallest scope, no LLM dependencies (so no flaky tests), establishes the issue queue contract that 5b/5c/5d all consume, ships immediate user-facing value via `llm-wiki lint`. Builds on existing `Page.wikilinks` and `Page.sections` infrastructure with no modifications to `Vault`.

## Scope (in)

- `Issue` dataclass + `IssueQueue` (filesystem persistence under `wiki/.issues/`)
- Four structural checks:
  1. **Orphans** — pages with zero `links_from` AND not in the configured root cluster
  2. **Broken wikilinks** — `[[target]]` references where `target` is not a page in the vault and not a known non-page extension
  3. **Missing markers** — pages with `##` headings but no `%% section: ... %%` markers (these are pages the librarian/compliance reviewer should retrofit)
  4. **Broken citations** — `[[raw/...]]` or `source: [[...]]` references pointing at files that don't exist on disk
- `Auditor` class wiring checks → queue with idempotent ID generation
- Daemon `lint` route returning an `AuditReport`
- `llm-wiki lint` CLI command
- Daemon `issues` route (list / get / update_status) — needed by 5b/5c/5d, cheaper to add now
- `llm-wiki issues` CLI command (list, show, resolve)

## Scope (out — deferred to later sub-phases)

- LLM-driven checks (deferred to 5d adversary)
- Citation spot-check by LLM (deferred to 5d)
- Scheduled audit runs (deferred to 5b)
- Compliance review of edits (deferred to 5b)

## File structure

```
src/llm_wiki/
  issues/
    __init__.py
    queue.py            # Issue, IssueQueue
  audit/
    __init__.py
    checks.py           # find_orphans, find_broken_wikilinks, find_missing_markers, find_broken_citations
    auditor.py          # Auditor, AuditReport
  daemon/
    server.py           # MODIFIED: add "lint" + "issues" + "issue-update" routes
  cli/
    main.py             # MODIFIED: add lint, issues commands

tests/
  test_issues/
    __init__.py
    test_queue.py
  test_audit/
    __init__.py
    test_checks.py      # one test per check function, plus empty-vault edge cases
    test_auditor.py     # idempotency, status preservation across runs
  test_daemon/
    test_lint_route.py
  test_cli/
    test_lint_cmd.py
    test_issues_cmd.py
```

## Key types

```python
# src/llm_wiki/issues/queue.py
from __future__ import annotations
import datetime
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Issue:
    id: str
    type: str            # "orphan" | "broken-link" | "missing-marker" | "broken-citation" | "compliance" | "claim-failed" | "new-idea"
    status: str          # "open" | "resolved" | "wontfix"
    title: str
    page: str | None     # affected page slug, or None for vault-wide issues
    body: str            # markdown body (the full description)
    created: str         # ISO 8601 UTC
    detected_by: str     # "auditor" | "compliance" | "librarian" | "adversary"
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def make_id(type: str, page: str | None, body: str) -> str:
        digest = hashlib.sha256(
            f"{type}|{page or ''}|{body}".encode("utf-8")
        ).hexdigest()[:6]
        page_part = page or "vault"
        return f"{type}-{page_part}-{digest}"

class IssueQueue:
    def __init__(self, vault_root: Path) -> None: ...
    @property
    def issues_dir(self) -> Path: ...                 # vault_root / "wiki" / ".issues" — actually <wiki_dir from config> / .issues
    def add(self, issue: Issue) -> tuple[Path, bool]: # (path, was_new)
        ...
    def get(self, issue_id: str) -> Issue | None: ...
    def list(self, status: str | None = None, type: str | None = None) -> list[Issue]: ...
    def update_status(self, issue_id: str, new_status: str) -> bool: ...
    def exists(self, issue_id: str) -> bool: ...
```

```python
# src/llm_wiki/audit/checks.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from llm_wiki.issues.queue import Issue
from llm_wiki.vault import Vault

@dataclass
class CheckResult:
    check: str
    issues: list[Issue]

def find_orphans(vault: Vault) -> CheckResult: ...
def find_broken_wikilinks(vault: Vault) -> CheckResult: ...
def find_missing_markers(vault: Vault) -> CheckResult: ...
def find_broken_citations(vault: Vault, vault_root: Path) -> CheckResult: ...
```

```python
# src/llm_wiki/audit/auditor.py
from __future__ import annotations
from dataclasses import dataclass, field
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.audit.checks import CheckResult
from llm_wiki.vault import Vault

@dataclass
class AuditReport:
    total_checks_run: int
    by_check: dict[str, int]              # check_name → issues_found
    new_issue_ids: list[str] = field(default_factory=list)
    existing_issue_ids: list[str] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(self.by_check.values())

class Auditor:
    def __init__(self, vault: Vault, queue: IssueQueue, vault_root: Path) -> None: ...
    def audit(self) -> AuditReport: ...
```

## Task outline (to be expanded into TDD steps in the per-sub-phase plan)

1. Package skeleton: `src/llm_wiki/issues/__init__.py`, `src/llm_wiki/audit/__init__.py`, test packages
2. `Issue` dataclass + `Issue.make_id()` (pure function, easy to TDD)
3. `IssueQueue.add()` + `exists()` — write frontmatter+body, idempotent on existing ID
4. `IssueQueue.get()` + `list()` — parse files back into `Issue`
5. `IssueQueue.update_status()` — preserve existing fields, mutate only `status`
6. `find_orphans()` — uses `vault._store._entries[name].links_from` (need a public accessor; add `Vault.manifest_entries() → dict[str, ManifestEntry]`)
7. `find_broken_wikilinks()` — iterate page wikilinks, check membership in vault
8. `find_missing_markers()` — re-parse raw page content, look for `##` without preceding `%%`
9. `find_broken_citations()` — extract `[[raw/...]]` and `source:` frontmatter wikilinks, resolve against `vault_root`
10. `Auditor.audit()` — run all four checks, aggregate, pass to queue
11. `DaemonServer._handle_lint()` returning serialized `AuditReport`
12. `DaemonServer._handle_issues_list()` / `_handle_issues_get()` / `_handle_issues_update()`
13. `llm-wiki lint` CLI — calls daemon, prints report grouped by check
14. `llm-wiki issues` CLI — `list`, `show <id>`, `resolve <id>`, `wontfix <id>` subcommands
15. Empty-vault edge case test for each check
16. Idempotency integration test: run `audit()` twice in a row, second run produces zero new issues

## Tests

| File | Covers |
|---|---|
| `test_issues/test_queue.py` | `Issue.make_id` determinism, add+get round-trip, idempotent add returns `was_new=False`, list with status/type filters, update_status preserves other fields, missing issue returns None |
| `test_audit/test_checks.py` | One test per check (happy path), empty vault, vault with no issues, false-positive avoidance (e.g. wikilinks to PDFs are not "broken") |
| `test_audit/test_auditor.py` | Audit aggregates check results, idempotent across runs, respects existing wontfix status |
| `test_daemon/test_lint_route.py` | Lint route returns serialized report, issues route round-trip |
| `test_cli/test_lint_cmd.py` | Click runner test for `llm-wiki lint` and `llm-wiki issues` |

## Spec sections satisfied by 5a

- §5 Auditor row
- §5 Lint row
- §5 Issue queue paragraph (`wiki/.issues/` directory of markdown files with frontmatter)

## Dependencies

- None on other sub-phases (5a is the foundation)
- Requires no changes to existing tests beyond a possible new public accessor on `Vault` for manifest entries

---

# Sub-phase 5b: Background Worker Scheduler + Compliance Review

**Plan file (to be written):** `docs/superpowers/plans/2026-04-08-phase5b-scheduler-compliance.md`

**Goal:** Add an async interval scheduler that runs maintenance workers in the daemon's event loop, plus a compliance-review pipeline that audits human edits arriving via the file watcher (debounced).

**Why second:** Provides the runtime substrate every other agent needs. Compliance review is a natural extension of 5a's auditor — it runs the same kinds of structural checks but scoped to a single edit. After 5b, the auditor from 5a runs automatically on a schedule.

## Scope (in)

- `IntervalScheduler` async coroutine manager with `register` / `start` / `stop`
- `parse_interval("6h" | "30m" | "12h" | "2d") → float` seconds parser
- Wire scheduler into `DaemonServer.start()` and `DaemonServer.stop()`
- Register an auditor worker on `config.maintenance.auditor_interval`
- File watcher debouncer: collect changes, wait `compliance_debounce_secs` after the last change, then dispatch
- `ComplianceReviewer.review_change(page_path, old_content, new_content)` returning `ComplianceResult`
  - Minor-edit heuristic (under 50 chars diff, no new wikilinks, no new headings → auto-approve)
  - Missing-citation check (new sentences without `[[...]]` → file `compliance` issue)
  - Structural drift (new `##` heading without `%% section: ... %%` → can auto-fix by inserting marker, since markers are invisible to humans)
  - New-idea detection (new paragraph in a previously stable section → file `new-idea` issue)
- Snapshot ring buffer: keep the last-seen content for each watched page so the reviewer can diff
- Wire compliance reviewer into the debounced watcher callback

## Scope (out)

- LLM-based compliance review (heuristic only — LLM compliance is a future enhancement)
- Three-way merge for write conflicts (deferred indefinitely; spec calls this librarian work)
- Talk-page autoposting from compliance reviewer (deferred to 5d)

## File structure

```
src/llm_wiki/
  daemon/
    scheduler.py            # IntervalScheduler, ScheduledWorker, parse_interval
    server.py               # MODIFIED: own scheduler, register auditor, register compliance worker
    watcher.py              # MODIFIED: optional debounce wrapper OR keep watcher pure and add debouncer in server.py
    snapshot.py             # PageSnapshotStore — last-known content per page
  audit/
    compliance.py           # ComplianceReviewer, ComplianceResult

tests/
  test_daemon/
    test_scheduler.py       # interval parser, registration, stop semantics, error isolation
    test_compliance_integration.py  # edit file → wait debounce → issue appears
  test_audit/
    test_compliance.py      # minor edit, missing citation, structural drift, new idea
```

## Key types

```python
# src/llm_wiki/daemon/scheduler.py
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

@dataclass
class ScheduledWorker:
    name: str
    interval_seconds: float
    coro_factory: Callable[[], Awaitable[None]]

_INTERVAL_RE = re.compile(r"^(\d+)\s*([smhd])$")

def parse_interval(spec: str) -> float:
    """'30s' | '15m' | '6h' | '2d' → seconds. Raises ValueError on bad input."""
    ...

class IntervalScheduler:
    def __init__(self) -> None: ...
    def register(self, worker: ScheduledWorker) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    @property
    def worker_names(self) -> list[str]: ...
```

```python
# src/llm_wiki/daemon/snapshot.py
from __future__ import annotations
from pathlib import Path

class PageSnapshotStore:
    """Holds the last-seen content of each page so the compliance reviewer
    can diff. Stored in <state_dir>/snapshots/<slug>.md. Updated AFTER
    a successful compliance review (so the diff for the next edit is the
    delta from the last-reviewed state, not from whatever the agent wrote)."""
    def __init__(self, state_dir: Path) -> None: ...
    def get(self, page_slug: str) -> str | None: ...
    def set(self, page_slug: str, content: str) -> None: ...
    def remove(self, page_slug: str) -> None: ...
```

```python
# src/llm_wiki/audit/compliance.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault

@dataclass
class ComplianceResult:
    page: str
    auto_approved: bool
    auto_fixed: list[str]                # ["inserted-marker:method", ...]
    issues_filed: list[str]              # issue IDs
    reasons: list[str]                   # ["minor-edit"] or ["missing-citation", "structural-drift"]

class ComplianceReviewer:
    def __init__(
        self,
        vault_root: Path,
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None: ...

    def review_change(
        self,
        page_path: Path,
        old_content: str | None,         # None for new file
        new_content: str,
    ) -> ComplianceResult: ...
```

## Debouncer design

The existing `FileWatcher.on_change` callback is currently called immediately on detection. Sub-phase 5b wraps the callback in the daemon to:
1. Append (path, mtime) tuples to a per-path "pending" map
2. (Re)start a per-path asyncio.Task that sleeps `compliance_debounce_secs`
3. After the sleep, if no further change has arrived for that path, fire the compliance reviewer
4. If a new change arrives during the sleep, cancel and restart the timer

Implementation lives in `DaemonServer` (or a new `ChangeDispatcher` helper). The watcher itself stays unchanged — pure mtime polling.

## Task outline

1. `parse_interval()` — pure function, exhaustive parsing tests first
2. `ScheduledWorker` dataclass + `IntervalScheduler` skeleton
3. `IntervalScheduler.register/start/stop` with cancellation discipline
4. Error isolation test: a worker raising should NOT crash the scheduler or other workers
5. `PageSnapshotStore` (read/write under state_dir)
6. `ComplianceReviewer.review_change`: minor-edit heuristic
7. ComplianceReviewer: missing-citation detection (look for new sentences without `[[...]]` suffix in the diff)
8. ComplianceReviewer: structural drift — new `##` heading without preceding `%% section: ... %%` marker
9. ComplianceReviewer auto-fix: insert `%% section: <slug> %%` line above the orphaned heading; mark `auto_fixed`
10. ComplianceReviewer new-idea detection: new paragraph (>=200 chars) inside a previously stable section
11. Wire `IntervalScheduler` into `DaemonServer.start()` / `stop()`
12. Register auditor worker (calls 5a's `Auditor.audit()` on schedule)
13. `ChangeDispatcher` debouncer in daemon
14. Wire dispatcher into the daemon's existing `on_file_change` (currently in `__main__.py` — move to server)
15. Compliance integration test: write file, modify, wait debounce, assert compliance issue exists
16. Daemon `scheduler-status` route showing registered workers + last-run timestamps
17. CLI `llm-wiki maintenance status` command showing the same

## Tests

| File | Covers |
|---|---|
| `test_daemon/test_scheduler.py` | `parse_interval` exhaustive cases, register before/after start, stop is idempotent, worker raise doesn't crash siblings, stop while worker is mid-iteration |
| `test_audit/test_compliance.py` | Minor edit auto-approves; missing citation files issue; new heading without marker is auto-fixed AND records `auto_fixed`; new-idea heuristic; first-time-seen-page treats as creation, not edit |
| `test_daemon/test_compliance_integration.py` | End-to-end: serve daemon, edit a tracked page, wait `compliance_debounce_secs + slack`, assert issue appears in queue |

## Spec sections satisfied by 5b

- §5 Compliance Review Queue (full)
- §2 Background Workers paragraph
- §2 LLM Request Queue priority paragraph (workers route at `priority="maintenance"`)

## Dependencies

- **Requires 5a** for `IssueQueue` and `Auditor`
- Does not require 5c or 5d

---

# Sub-phase 5c: Librarian

**Plan file (to be written):** `docs/superpowers/plans/2026-04-08-phase5c-librarian.md`

**Goal:** A scheduled librarian that consumes traversal logs to refine `ManifestEntry` tags/summary via LLM and recomputes authority scores from the link graph + usage data. Librarian state survives `Vault.scan()` via a sidecar override file.

**Why third:** Builds on 5b's scheduler. Touches manifest store, which already has placeholder fields ready to receive librarian output (`tags`, `read_count`, `usefulness`, `authority`). All the input it needs (`traversal_logs.jsonl`, `pages_read.salient_points`, `pages_read.relevance`) is already produced by Phase 3's traversal engine.

## Scope (in)

- `aggregate_logs(log_path)` → `dict[str, PageUsage]` reading `traversal_logs.jsonl`
- `compute_authority(entries, usage)` per spec formula:
  ```
  authority = (inlink_count × 0.3) + (traversal_usefulness × 0.4)
            + (freshness × 0.2) + (outlink_quality × 0.1)
  ```
  Each input is normalized to `[0, 1]` before weighting. `freshness` uses `last_corroborated` (None → 0.5 neutral per spec).
- `ManifestOverrides` sidecar at `<state_dir>/manifest_overrides.json` storing per-page `{tags, summary_override, authority, last_corroborated, read_count, usefulness}`
- `Vault.scan()` modification: load overrides, apply on top of programmatically-built entries
- LLM-driven tag/summary refinement: librarian batches pages whose `traversals_since_refresh >= manifest_refresh_after_traversals` and asks the LLM to propose tags + a one-line summary using the page content + a sample of recent salient_points
- `LibrarianAgent.run()` orchestration
- Librarian-filed issues: stale page (no reads in N runs), low-authority but high-traffic mismatch, etc. Defer most issue types to a future enhancement — file at most one type in this sub-phase: `stale-page`
- Wire librarian to scheduler at `config.maintenance.librarian_interval`
- Authority recalc as a sub-step (or its own scheduled worker on `authority_recalc`) — implement as a method `LibrarianAgent.recalc_authority()` and schedule both intervals separately

## Scope (out)

- Cross-reference suggestions (defer to future enhancement)
- Cluster summary refinement (defer)
- Talk-page reading (deferred to 5d, since 5d ships talk pages)
- Honcho integration (out of phase 5 entirely)

## File structure

```
src/llm_wiki/
  librarian/
    __init__.py
    log_reader.py        # PageUsage, aggregate_logs
    authority.py         # compute_authority, normalization helpers
    overrides.py         # ManifestOverrides JSON sidecar
    prompts.py           # tag + summary refinement prompt
    agent.py             # LibrarianAgent, LibrarianResult
  vault.py               # MODIFIED: load overrides on scan, apply to entries
  daemon/
    server.py            # MODIFIED: register librarian worker, register authority recalc worker

tests/
  test_librarian/
    __init__.py
    test_log_reader.py
    test_authority.py
    test_overrides.py
    test_prompts.py
    test_agent.py
  test_vault.py          # MODIFIED: assert overrides applied on scan
```

## Key types

```python
# src/llm_wiki/librarian/log_reader.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class PageUsage:
    name: str
    read_count: int = 0
    total_relevance: float = 0.0
    salient_samples: list[str] = field(default_factory=list)   # recent non-empty salient_points
    queries: list[str] = field(default_factory=list)           # recent query strings that read this page

    @property
    def avg_relevance(self) -> float:
        return self.total_relevance / self.read_count if self.read_count else 0.0

def aggregate_logs(
    log_path: Path,
    since_iso: str | None = None,
    sample_cap: int = 5,
) -> dict[str, PageUsage]: ...
```

```python
# src/llm_wiki/librarian/authority.py
from __future__ import annotations
from llm_wiki.manifest import ManifestEntry
from llm_wiki.librarian.log_reader import PageUsage

def compute_authority(
    entries: dict[str, ManifestEntry],
    usage: dict[str, PageUsage],
) -> dict[str, float]: ...
```

```python
# src/llm_wiki/librarian/overrides.py
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

@dataclass
class PageOverride:
    tags: list[str] = field(default_factory=list)
    summary_override: str | None = None
    authority: float = 0.0
    last_corroborated: str | None = None
    read_count: int = 0
    usefulness: float = 0.0

class ManifestOverrides:
    def __init__(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> "ManifestOverrides": ...
    def get(self, page_name: str) -> PageOverride | None: ...
    def set(self, page_name: str, override: PageOverride) -> None: ...
    def prune(self, valid_names: set[str]) -> None: ...
    def save(self) -> None: ...
```

```python
# src/llm_wiki/librarian/agent.py
from __future__ import annotations
from dataclasses import dataclass, field
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.traverse.llm_client import LLMClient
from llm_wiki.vault import Vault

@dataclass
class LibrarianResult:
    pages_refined: list[str] = field(default_factory=list)
    authorities_updated: int = 0
    issues_filed: list[str] = field(default_factory=list)

class LibrarianAgent:
    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: LLMClient,
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None: ...

    async def run(self) -> LibrarianResult: ...
    async def recalc_authority(self) -> int: ...     # returns number of authority values updated
```

## Vault.scan modification

```python
# In vault.py, after building entries and BEFORE constructing ManifestStore:
overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
for entry in entries:
    override = overrides.get(entry.name)
    if override is not None:
        entry.tags = override.tags or entry.tags
        entry.authority = override.authority
        entry.read_count = override.read_count
        entry.usefulness = override.usefulness
        entry.last_corroborated = override.last_corroborated
        if override.summary_override:
            entry.summary = override.summary_override
overrides.prune({e.name for e in entries})
overrides.save()
```

This is the cleanest hook point. Tests that call `Vault.scan()` won't break because `ManifestOverrides.load()` returns an empty store when the file doesn't exist.

## Task outline

1. `PageUsage` + `aggregate_logs()` reading `traversal_logs.jsonl` line by line
2. `compute_authority()` with normalization (test edge cases: no inlinks, no usage, all-zero)
3. `PageOverride` + `ManifestOverrides` JSON round-trip
4. `Vault.scan()` modification + regression test that existing scan tests still pass
5. Librarian prompts: tag refinement (system + user template), summary refinement (single LLM call may produce both)
6. Prompt parser (JSON response → list of tags + summary string)
7. `LibrarianAgent.recalc_authority()` — pure programmatic, no LLM
8. `LibrarianAgent.run()` — read logs, pick refresh candidates, LLM call per candidate, write override, save
9. Refresh trigger: page has `read_count - last_refreshed_at_count >= manifest_refresh_after_traversals`
10. Stale-page detection (no reads in last N runs) → file `stale-page` issue
11. Wire to scheduler (separate workers for `librarian_interval` and `authority_recalc`)
12. Integration test: synthesize traversal log, run librarian, assert overrides applied to entries on next scan

## Tests

| File | Covers |
|---|---|
| `test_librarian/test_log_reader.py` | Empty file → empty result; multi-turn log aggregation; salient_samples cap; since_iso filter |
| `test_librarian/test_authority.py` | Spec formula values, normalization, neutral freshness for None last_corroborated, empty vault |
| `test_librarian/test_overrides.py` | Save/load round-trip, prune removes deleted pages, missing file → empty store |
| `test_librarian/test_prompts.py` | Prompt parser handles fenced JSON, missing fields, invalid types (mirror Phase 4 prompt parser tests) |
| `test_librarian/test_agent.py` | Run with stub LLM, refresh trigger correctness, stale-page issue filed, authority recalc updates entries |
| `test_vault.py` (modified) | Overrides applied on scan, prune removes obsolete entries |

## Spec sections satisfied by 5c

- §5 Librarian row
- §4 Manifest lifecycle paragraph (refresh trigger, override storage)
- §5 Authority Scoring section (full)

## Dependencies

- **Requires 5a** for `IssueQueue` (to file `stale-page` issues)
- **Requires 5b** for the scheduler (the librarian needs to be registered as a worker)
- Does NOT require 5d

---

# Sub-phase 5d: Adversary + Talk Pages

**Plan file (to be written):** `docs/superpowers/plans/2026-04-08-phase5d-adversary-talk-pages.md`

**Goal:** A scheduled adversary that samples wiki claims, fetches the cited raw source, and verifies the claim against the source via LLM. Findings post to talk pages (for nuanced discussion) or the issue queue (for clear failures). Talk pages are added in the same sub-phase because they are the adversary's primary output channel.

**Why fourth:** Most LLM-intensive sub-phase, builds on the scheduler from 5b and benefits from the override sidecar from 5c (to update `last_corroborated`). Talk pages are tightly coupled — they are the adversary's main output channel for nuanced findings that aren't clear-cut failures.

## Scope (in)

- `Claim` extraction from page sections (sentences ending in `[[citation]]`)
- Weighted sampling: `weight = age_factor × (1 - authority) × random_jitter`, where `age_factor` increases with time since `last_corroborated`
- Adversary verification prompt: gets the raw source text + the wiki claim text, returns `{verdict, confidence, explanation}` where verdict ∈ `{validated, contradicted, unsupported, ambiguous}`
- `AdversaryAgent.run()` orchestration: sample → load raw → LLM verify → record
- Validated claims update `last_corroborated` on the page (via 5c's override store)
- Contradicted/unsupported claims file a `claim-failed` issue with the LLM explanation
- Ambiguous verdicts post to the page's talk page asking for human review
- `TalkPage` parser/writer for `<page>.talk.md` sidecar files
- Talk-page discovery: when a talk page exists, ensure the parent page contains a `%% talk: [[<page>.talk]] %%` marker (auto-inserted; markers are invisible)
- `*.talk.md` exclusion from `Vault.scan()` page indexing (modify the existing scan filter)
- Daemon `talk` route: read, append, list-with-talk
- CLI `llm-wiki talk <page>` (read), `llm-wiki talk <page> --post "..."` (append as `@human`)
- Wire adversary to scheduler at `config.maintenance.adversary_interval`

## Scope (out)

- Threading on talk pages (v1: chronological flat log per spec)
- Auto-archive of old talk entries (deferred per spec)
- LLM-driven librarian reading of talk pages (out of phase 5 entirely)
- Multi-source claim verification (one source per claim is fine)

## File structure

```
src/llm_wiki/
  adversary/
    __init__.py
    claim_extractor.py    # Claim, extract_claims
    sampling.py           # sample_claims with weighting
    prompts.py            # verification prompt + parser
    agent.py              # AdversaryAgent, AdversaryResult
  talk/
    __init__.py
    page.py               # TalkPage, TalkEntry
    discovery.py          # ensure_talk_marker(page_path)
  vault.py                # MODIFIED: exclude *.talk.md from page indexing
  librarian/
    overrides.py          # MODIFIED: ensure last_corroborated update path is exposed
  daemon/
    server.py             # MODIFIED: register adversary worker, add "talk" route
  cli/
    main.py               # MODIFIED: add talk command group

tests/
  test_adversary/
    __init__.py
    test_claim_extractor.py
    test_sampling.py
    test_prompts.py
    test_agent.py
  test_talk/
    __init__.py
    test_page.py
    test_discovery.py
  test_daemon/
    test_talk_route.py
  test_cli/
    test_talk_cmd.py
  test_vault.py            # MODIFIED: assert *.talk.md excluded from pages
```

## Key types

```python
# src/llm_wiki/adversary/claim_extractor.py
from __future__ import annotations
from dataclasses import dataclass
from llm_wiki.page import Page

@dataclass
class Claim:
    page: str            # page slug
    section: str         # section slug
    text: str            # the claim sentence (with citation suffix removed)
    citation: str        # raw target as written: "raw/smith-2026-srna.pdf"

    @property
    def id(self) -> str:
        """Stable id derived from page+section+text hash."""
        ...

def extract_claims(page: Page) -> list[Claim]: ...
```

```python
# src/llm_wiki/adversary/sampling.py
from __future__ import annotations
import datetime
from random import Random
from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.manifest import ManifestEntry

def sample_claims(
    claims: list[Claim],
    entries: dict[str, ManifestEntry],
    n: int,
    rng: Random,
    now: datetime.datetime,
) -> list[Claim]: ...
```

```python
# src/llm_wiki/adversary/agent.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides
from llm_wiki.traverse.llm_client import LLMClient
from llm_wiki.vault import Vault

@dataclass
class AdversaryResult:
    claims_checked: int = 0
    validated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    issues_filed: list[str] = field(default_factory=list)
    talk_posts: list[str] = field(default_factory=list)

class AdversaryAgent:
    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: LLMClient,
        queue: IssueQueue,
        overrides: ManifestOverrides,
        config: WikiConfig,
    ) -> None: ...

    async def run(self) -> AdversaryResult: ...
```

```python
# src/llm_wiki/talk/page.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TalkEntry:
    timestamp: str       # ISO 8601 UTC
    author: str          # "@human" | "@adversary" | "@librarian" | "@auditor"
    body: str            # markdown body

class TalkPage:
    def __init__(self, path: Path) -> None: ...
    @classmethod
    def for_page(cls, page_path: Path) -> "TalkPage": ...
    @property
    def exists(self) -> bool: ...
    def load(self) -> list[TalkEntry]: ...
    def append(self, entry: TalkEntry) -> None: ...   # creates file if missing
    @property
    def parent_page_slug(self) -> str: ...
```

## Talk page format

```markdown
---
page: srna-embeddings
---

**2026-04-08T15:01:00+00:00 — @adversary**
Verified `silhouette > 0.5` claim against [[raw/smith-2026-srna.pdf]].
Source uses 0.5 as a threshold but cites Rousseeuw 1987 for the scale.
Suggest adding a [[clustering-metrics]] cross-reference.

**2026-04-08T16:22:00+00:00 — @human**
Good catch — added the cross-reference.
```

## Claim extraction strategy

Walk each page section's content. Split into sentences (split on `. `, `.\n`, `! `, `? ` — naive but adequate for v1; do NOT pull in nltk). For each sentence ending in `[[target]]`, extract the citation. If the target starts with `raw/` (or matches a configured `raw_dir`), it's a verifiable claim.

Edge cases the per-sub-phase plan must handle:
- Sentence with multiple citations: `Claim X [[a]] [[b]].` → record one Claim with the first citation; the per-sub-phase plan can decide whether to also enqueue the second
- Code blocks should be skipped (don't extract claims from inside ```)
- Wikilinks inside `%%` markers should be skipped
- Frontmatter `source:` references are not claims

## Task outline

1. `Claim` dataclass + `Claim.id` (deterministic hash)
2. `extract_claims(page)` — sentence splitter, citation extractor, code-block skip
3. `sample_claims()` weighted sampling (test with seeded RNG for determinism)
4. Adversary verification prompt (system + user template; structural contract: JSON response)
5. Verification response parser (mirrors Phase 4 prompt parsers — handle fenced JSON, missing fields)
6. `AdversaryAgent.run()` — sample, fetch raw via existing extractor (`extract_text` from Phase 4), LLM verify, dispatch by verdict
7. Validated → update overrides `last_corroborated` to `now`
8. Contradicted/unsupported → file `claim-failed` issue with explanation
9. Ambiguous → post to talk page
10. `TalkPage` parser (read existing entries, preserving order)
11. `TalkPage.append()` — create file with frontmatter if missing, append entry block
12. `TalkPage` discovery: `ensure_talk_marker(page_path)` adds `%% talk: [[<slug>.talk]] %%` to bottom of parent page if not present
13. `Vault.scan()` modification: skip `*.talk.md` files when building pages dict
14. Daemon `talk` route: `read` (return entries), `append` (write entry as specified author), `list` (which pages have talk pages)
15. CLI `talk` command group: `read`, `post`, `list`
16. Wire `AdversaryAgent` to scheduler at `config.maintenance.adversary_interval`
17. Integration test: synthesize a wiki page with a claim citing a fake raw file, run adversary with stub LLM, assert verdict pathway works (validated → override updated, contradicted → issue filed, ambiguous → talk entry written)

## Tests

| File | Covers |
|---|---|
| `test_adversary/test_claim_extractor.py` | Sentence with citation, multiple citations, code-block skip, marker skip, frontmatter source skip, empty page |
| `test_adversary/test_sampling.py` | Sampling honors n cap, weights favor stale claims, seeded RNG produces deterministic order, empty list returns empty |
| `test_adversary/test_prompts.py` | JSON parser variants (mirror Phase 4) |
| `test_adversary/test_agent.py` | Stub LLM with each verdict; validated path updates overrides; contradicted path files issue; ambiguous path posts talk; raw extraction failure files separate issue |
| `test_talk/test_page.py` | Append creates file with frontmatter; round-trip load preserves order; for_page derives correct path |
| `test_talk/test_discovery.py` | Marker inserted only when missing; idempotent; doesn't disturb existing content |
| `test_daemon/test_talk_route.py` | Read/append/list round-trip via the daemon |
| `test_cli/test_talk_cmd.py` | Click runner for `llm-wiki talk read|post|list` |
| `test_vault.py` (modified) | `*.talk.md` files not in page index, but parent page still parsed normally |

## Spec sections satisfied by 5d

- §5 Adversary row (full, including the spec's claim selection weighting)
- §5 Talk Pages section (v1 — flat chronological, talk-page discovery marker, daemon append path)
- §4 Manifest entry `last_corroborated` field semantics

## Dependencies

- **Requires 5a** for `IssueQueue`
- **Requires 5b** for the scheduler
- **Requires 5c** for `ManifestOverrides` (to update `last_corroborated`)
- Reuses Phase 4's `extract_text()` for loading raw source content

---

## Spec coverage matrix

| Spec section | Sub-phase | Notes |
|---|---|---|
| §5 Ingest row | done in Phase 4 | — |
| §5 Query row | done in Phase 3 | — |
| §5 Librarian row | **5c** | manifest refinement, authority |
| §5 Adversary row | **5d** | claim verification, sampling |
| §5 Auditor row | **5a** + **5b** | structural checks (5a) + scheduled run (5b) |
| §5 Lint row | **5a** | CLI command |
| §5 Issue queue paragraph | **5a** | substrate |
| §5 Compliance Review Queue | **5b** | full subsection |
| §5 Talk Pages | **5d** | full subsection |
| §5 Authority Scoring | **5c** | full subsection |
| §4 Manifest lifecycle | **5c** | refresh trigger, override store |
| §2 Background Workers | **5b** | scheduler infra |

---

## Recommended execution order

1. **5a** — small, programmatic, ships immediate value (`llm-wiki lint`)
2. **5b** — adds runtime substrate (scheduler) + auto-compliance
3. **5c** OR **5d** — independent of each other; pick whichever feels higher-value at the time
4. The other of 5c/5d

After all four sub-phases land, Phase 5 is complete and the roadmap moves on to Phase 6 (MCP Server).

## Per-sub-phase planning workflow

Each sub-phase plan should be written in a fresh session via `/superpowers:writing-plans` so the planner has full focused context on that one sub-phase. The planner reads:

1. This roadmap (skim Cross-cutting, then deep-read the relevant sub-phase section)
2. The relevant existing source files listed in "What's already in place"
3. The Phase 4 plan file as a style reference for TDD task structure

The output is a `2026-04-08-phase5<X>-<name>.md` plan with the same level of detail as the Phase 4 plan: every task has explicit failing test → run-fail → implement → run-pass → commit steps with full code blocks.
