# Inbox In-Progress Lint Check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `find_inbox_staleness` auditor check that surfaces any `inbox/` plan file with `status: in-progress` as a minor issue so active ingests never get silently forgotten.

**Prerequisites:** Both `source-reading-status` and `attended-ingest-foundation` must be merged before executing this plan.
- `source-reading-status` modifies `audit/checks.py`, `audit/auditor.py`, and `tests/test_audit/test_auditor.py` — this plan touches the same three files.
- `attended-ingest-foundation` provides `ingest/plan.py` and `read_plan_frontmatter` which this plan imports.

Merge order: source-reading-status → attended-ingest-foundation → this plan.

**Architecture:** One new function in `audit/checks.py`, one new call in `Auditor.audit()`, tests. No LLM, no daemon routes, no MCP tools — purely a lint check.

**Tech Stack:** Python stdlib (`pathlib`, `datetime`), PyYAML (already a dep), pytest.

---

## Explicit Assumptions (verify these after source-reading-status merges)

**`audit/checks.py`**

```python
@dataclass
class CheckResult:
    check: str
    issues: list[Issue]
```

**`issues/queue.py` — `Issue` constructor**

```python
Issue(
    id=Issue.make_id(type: str, page: str | None, key: str) -> str,
    type=str,
    status="open",
    severity="minor",          # Severity = Literal["critical","moderate","minor"]
    title=str,
    page=str | None,
    body=str,
    created=Issue.now_iso(),   # -> str (ISO 8601 UTC)
    detected_by="auditor",
    metadata=dict,
)
```

`Issue.make_id(type, page, key)` — all three args are strings. `page` may be `None` (produces `vault` in the id). `key` is the discriminator; empty string `""` is valid when the page itself is the unique identifier.

**`audit/auditor.py` — state after source-reading-status merges**

```python
class Auditor:
    def __init__(
        self,
        vault: Vault,
        queue: IssueQueue,
        vault_root: Path,
        config: WikiConfig | None = None,   # ← added by source-reading-status
    ) -> None: ...

    def audit(self) -> AuditReport:
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_broken_citations(self._vault, self._vault_root),
            find_source_gaps(self._vault_root, self._config),  # ← added by source-reading-status
        ]
        # total_checks_run == 5 after source-reading-status
```

**`ingest/plan.py` — provided by the attended-ingest-foundation plan**

```python
def read_plan_frontmatter(path: Path) -> dict:
    """Returns {} on any error.""" ...
```

**`audit/checks.py` — `_file_slug` helper (provided by source-reading-status)**

```python
def _file_slug(path: Path) -> str:
    """Convert a filename to a valid issue-ID slug.

    Replaces '/', '.', and other characters not in [a-z0-9-] with '-'.
    Used to build Issue IDs from file paths where '/' and '.' would
    fail _ISSUE_ID_RE = r'^[a-z][a-z0-9-]{0,127}$'.
    """
    ...
```

Usage in `find_inbox_staleness`: `_file_slug(file)` not `f"inbox/{file.name}"` — the latter contains `/` and `.` which crash `Issue._validate_id`.

**`config.py` — after both prior plans merge**

```python
@dataclass
class VaultConfig:
    inbox_dir: str = "inbox/"   # added by attended-ingest-foundation

@dataclass
class MaintenanceConfig:
    # no new fields needed for this check
```

**Test count to update**

`tests/test_audit/test_auditor.py` will contain after source-reading-status merges:
```python
assert report.total_checks_run == 5
```
This plan bumps it to `6`.

---

## File Structure

| File | Change |
|---|---|
| `src/llm_wiki/audit/checks.py` | Add `find_inbox_staleness` |
| `src/llm_wiki/audit/auditor.py` | Import + call `find_inbox_staleness`; update `_config` usage |
| `tests/test_audit/test_checks.py` | Add `find_inbox_staleness` tests |
| `tests/test_audit/test_auditor.py` | Bump `total_checks_run` 5 → 6 |

---

### Task 1: `find_inbox_staleness` + tests

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `tests/test_audit/test_checks.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_audit/test_checks.py`:

```python
# ---------------------------------------------------------------------------
# find_inbox_staleness
# ---------------------------------------------------------------------------

from llm_wiki.audit.checks import find_inbox_staleness


def _write_plan(path: Path, status: str, started: str = "2026-04-10") -> None:
    path.write_text(
        f"---\nsource: raw/paper.pdf\nstarted: {started}\nstatus: {status}\nsessions: 1\n---\n\n"
        "## Claims / Ideas\n- [ ] Alpha\n"
    )


def test_find_inbox_staleness_flags_in_progress(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_plan(inbox_dir / "2026-04-10-paper-plan.md", "in-progress")
    result = find_inbox_staleness(tmp_path)
    assert result.check == "inbox-staleness"
    types = {i.type for i in result.issues}
    assert "inbox-in-progress" in types


def test_find_inbox_staleness_includes_started_date(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_plan(inbox_dir / "2026-04-10-paper-plan.md", "in-progress", started="2026-04-01")
    result = find_inbox_staleness(tmp_path)
    assert any("2026-04-01" in i.body for i in result.issues)


def test_find_inbox_staleness_ignores_completed(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_plan(inbox_dir / "2026-04-10-paper-plan.md", "completed")
    result = find_inbox_staleness(tmp_path)
    assert result.issues == []


def test_find_inbox_staleness_no_inbox_dir(tmp_path: Path):
    result = find_inbox_staleness(tmp_path)
    assert result.check == "inbox-staleness"
    assert result.issues == []


def test_find_inbox_staleness_empty_inbox(tmp_path: Path):
    (tmp_path / "inbox").mkdir()
    result = find_inbox_staleness(tmp_path)
    assert result.issues == []


def test_find_inbox_staleness_severity_is_minor(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_plan(inbox_dir / "plan.md", "in-progress")
    result = find_inbox_staleness(tmp_path)
    assert all(i.severity == "minor" for i in result.issues)


def test_find_inbox_staleness_skips_non_md_files(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (inbox_dir / "notes.txt").write_text("status: in-progress\n")
    result = find_inbox_staleness(tmp_path)
    assert result.issues == []


def test_find_inbox_staleness_skips_missing_status_frontmatter(tmp_path: Path):
    """A plan file with no frontmatter is not flagged — only explicit in-progress is."""
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (inbox_dir / "plan.md").write_text("# Plan\n\nNo frontmatter.\n")
    result = find_inbox_staleness(tmp_path)
    assert result.issues == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_audit/test_checks.py -k "inbox_staleness" -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'find_inbox_staleness'`

- [ ] **Step 3: Add `find_inbox_staleness` to `src/llm_wiki/audit/checks.py`**

Add at the end of the file, after `find_source_gaps`:

```python
from llm_wiki.ingest.plan import read_plan_frontmatter


def find_inbox_staleness(vault_root: Path) -> CheckResult:
    """Surface any inbox/ plan file with status: in-progress as a minor issue.

    Skips gracefully if inbox/ does not exist. Ignores files with no
    frontmatter or any status other than 'in-progress'.
    """
    inbox_dir = vault_root / "inbox"
    if not inbox_dir.is_dir():
        return CheckResult(check="inbox-staleness", issues=[])

    issues: list[Issue] = []
    for file in sorted(inbox_dir.iterdir()):
        if not file.is_file() or file.suffix.lower() not in (".md", ".markdown"):
            continue
        fm = read_plan_frontmatter(file)
        if fm.get("status") != "in-progress":
            continue
        started = fm.get("started", "unknown")
        source = fm.get("source", "unknown source")
        issues.append(Issue(
            id=Issue.make_id("inbox-in-progress", _file_slug(file), ""),
            type="inbox-in-progress",
            status="open",
            severity="minor",
            title=f"Active ingest plan: inbox/{file.name}",
            page=f"inbox/{file.name}",
            body=(
                f"`inbox/{file.name}` is `status: in-progress` (started {started}, "
                f"source: {source}). Complete the ingest or mark the plan as completed."
            ),
            created=Issue.now_iso(),
            detected_by="auditor",
            metadata={"path": f"inbox/{file.name}", "started": started, "source": source},
        ))
    return CheckResult(check="inbox-staleness", issues=issues)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_audit/test_checks.py -k "inbox_staleness" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: find_inbox_staleness — auditor check for in-progress inbox plans"
```

---

### Task 2: Wire into `Auditor` + update test count

**Files:**
- Modify: `src/llm_wiki/audit/auditor.py`
- Modify: `tests/test_audit/test_auditor.py`

- [ ] **Step 1: Add `find_inbox_staleness` to `Auditor.audit()`**

In `src/llm_wiki/audit/auditor.py`, update the import and the `results` list:

```python
from llm_wiki.audit.checks import (
    find_broken_citations,
    find_broken_wikilinks,
    find_inbox_staleness,   # ← new
    find_missing_markers,
    find_orphans,
    find_source_gaps,
)
```

In `audit()`, append to `results`:

```python
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_broken_citations(self._vault, self._vault_root),
            find_source_gaps(self._vault_root, self._config),
            find_inbox_staleness(self._vault_root),   # ← new
        ]
```

- [ ] **Step 2: Update `total_checks_run` assertions in all test files**

Find every `total_checks_run == 5` assertion across the test suite and change to `6`:

```bash
grep -rn "total_checks_run" tests/
```

Files that assert this count (after source-reading-status merges):
- `tests/test_audit/test_auditor.py` — two occurrences: `test_audit_empty_vault` and `test_audit_runs_all_checks_on_sample_vault`
- `tests/test_daemon/test_lint_route.py` — one occurrence in the lint route smoke test

Each occurrence of `== 5` becomes `== 6`.

- [ ] **Step 3: Run the full audit test suite**

```bash
pytest tests/test_audit/ -q
```

Expected: all PASS

- [ ] **Step 4: Run the full test suite — no regressions**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/auditor.py tests/test_audit/test_auditor.py
git commit -m "feat: wire find_inbox_staleness into Auditor (6 checks total)"
```
