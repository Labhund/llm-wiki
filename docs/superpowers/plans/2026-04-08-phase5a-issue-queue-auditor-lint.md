# Phase 5a: Issue Queue + Auditor + Lint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap reference:** See `docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md` for cross-cutting design decisions and the relationship to sub-phases 5b/5c/5d. **Read the roadmap's "Cross-cutting design decisions" and "What's already in place" sections before starting Task 1.**

**Goal:** Add a persistent issue queue (`wiki/.issues/`) and a structural-integrity auditor that finds orphans, broken wikilinks, missing section markers, and broken citations. Expose via a daemon `lint` route, an `issues` query route, and the `llm-wiki lint` and `llm-wiki issues` CLI commands. Programmatic checks only — **no LLM calls in this sub-phase**.

**Architecture:** `Issue` is a frontmatter+markdown file with a deterministic ID derived from `(type, page, key)` so re-running a check finds the same problem and produces the same ID — never duplicates. `IssueQueue` reads/writes these files. Four pure check functions take a `Vault` and return `CheckResult(issues=[...])`. `Auditor` runs all checks and routes results through the queue, distinguishing newly-filed from already-existing issues. The daemon exposes routes; the CLI prints reports grouped by check.

**Tech Stack:** Python 3.11+, PyYAML (already a dep), pytest, Click. No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  issues/
    __init__.py           # package marker
    queue.py              # Issue, IssueQueue
  audit/
    __init__.py           # package marker
    checks.py             # CheckResult, find_orphans, find_broken_wikilinks,
                          # find_missing_markers, find_broken_citations
    auditor.py            # Auditor, AuditReport
  vault.py                # MODIFIED: add public manifest_entries() accessor
  daemon/
    server.py             # MODIFIED: add "lint", "issues-list", "issues-get",
                          # "issues-update" routes
  cli/
    main.py               # MODIFIED: add lint command, issues command group

tests/
  test_issues/
    __init__.py
    test_queue.py
  test_audit/
    __init__.py
    test_checks.py
    test_auditor.py
  test_daemon/
    test_lint_route.py
  test_cli/
    test_lint_cmd.py
    test_issues_cmd.py
```

**Type flow across tasks:**
- `issues/queue.py` defines `Issue(id, type, status, title, page, body, created, detected_by, metadata)` and `IssueQueue(wiki_dir)`. `Issue.make_id(type, page, key)` is the deterministic ID helper.
- `audit/checks.py` defines `CheckResult(check, issues)` and four `find_*` functions. Each takes `Vault` (and `vault_root` for the citation check) and returns one `CheckResult`. They IMPORT `Issue` from `llm_wiki.issues.queue`.
- `audit/auditor.py` defines `AuditReport` and `Auditor(vault, queue, vault_root)`. `Auditor.audit() → AuditReport` runs all four checks, calls `queue.add()` for each issue, separates new from existing, and aggregates counts.
- `daemon/server.py` imports `Auditor` and `IssueQueue` lazily inside the route handlers. Routes: `lint`, `issues-list`, `issues-get`, `issues-update`.
- `cli/main.py` adds: `llm-wiki lint`, `llm-wiki issues list/show/resolve/wontfix`. Each calls a route on the daemon.

**Cluster naming reminder:** `Vault.scan()` assigns `cluster = rel.parts[0] if len(rel.parts) > 1 else "root"`. So top-level files (e.g. `index.md`) end up in cluster `"root"`. The orphan check skips pages by NAME (`{"index", "readme", "home"}`), not by cluster — see Task 7.

**Wiki dir resolution:** The `IssueQueue` takes a `wiki_dir: Path` (a fully resolved directory). The daemon constructs it from `vault_root / config.vault.wiki_dir.rstrip("/")`. Tests construct it as `tmp_path / "wiki"`. This matches the convention in `IngestAgent.ingest()` (`src/llm_wiki/ingest/agent.py:69`).

---

### Task 1: Package Skeleton

**Files:**
- Create: `src/llm_wiki/issues/__init__.py`
- Create: `src/llm_wiki/audit/__init__.py`
- Create: `tests/test_issues/__init__.py`
- Create: `tests/test_audit/__init__.py`

- [ ] **Step 1: Create empty package markers**

```python
# src/llm_wiki/issues/__init__.py
```

```python
# src/llm_wiki/audit/__init__.py
```

```python
# tests/test_issues/__init__.py
```

```python
# tests/test_audit/__init__.py
```

All four files are empty — package markers only.

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /home/labhund/repos/llm-wiki && pytest -q`
Expected: All existing tests pass (no regressions; we haven't added importable code yet).

- [ ] **Step 3: Commit**

```bash
git add src/llm_wiki/issues/__init__.py src/llm_wiki/audit/__init__.py \
        tests/test_issues/__init__.py tests/test_audit/__init__.py
git commit -m "feat: phase 5a skeleton — issues + audit packages"
```

---

### Task 2: `Issue` dataclass + `make_id` helper

**Files:**
- Create: `src/llm_wiki/issues/queue.py` (partial — `Issue` only)
- Create: `tests/test_issues/test_queue.py` (partial — id tests only)

- [ ] **Step 1: Write failing tests for `Issue.make_id`**

```python
# tests/test_issues/test_queue.py
from __future__ import annotations

from llm_wiki.issues.queue import Issue


def test_make_id_is_deterministic():
    """Same inputs always produce the same id."""
    id1 = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    id2 = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    assert id1 == id2


def test_make_id_format():
    """Id follows '<type>-<page-or-vault>-<6hex>' format."""
    issue_id = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    assert issue_id.startswith("broken-link-srna-tquant-")
    suffix = issue_id.rsplit("-", 1)[-1]
    assert len(suffix) == 6
    assert all(c in "0123456789abcdef" for c in suffix)


def test_make_id_uses_vault_when_page_is_none():
    """Vault-wide issues (page=None) use the literal 'vault' segment."""
    issue_id = Issue.make_id("orphan-cluster", None, "stale")
    assert issue_id.startswith("orphan-cluster-vault-")


def test_make_id_distinguishes_different_inputs():
    """Different type/page/key produce different ids."""
    a = Issue.make_id("broken-link", "page-a", "target-x")
    b = Issue.make_id("broken-link", "page-a", "target-y")
    c = Issue.make_id("broken-link", "page-b", "target-x")
    d = Issue.make_id("orphan", "page-a", "target-x")
    assert len({a, b, c, d}) == 4
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: ImportError — `llm_wiki.issues.queue` does not exist yet, OR if it exists, `AttributeError: module ... has no attribute 'Issue'`.

- [ ] **Step 3: Implement `Issue` dataclass and `make_id`**

```python
# src/llm_wiki/issues/queue.py
from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field


@dataclass
class Issue:
    """One issue in the queue, persisted as wiki/.issues/<id>.md.

    Issues are idempotent: re-running a check that finds the same problem
    produces the same id (via make_id) and the existing file is left alone.
    """

    id: str
    type: str
    status: str
    title: str
    page: str | None
    body: str
    created: str
    detected_by: str
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def make_id(type: str, page: str | None, key: str) -> str:
        """Build a deterministic id from (type, page, key).

        `key` is the discriminator that uniquely identifies this specific
        instance of the issue type — e.g. the broken-link target slug, the
        missing citation path. The hash is content-addressable so the same
        problem always maps to the same file on disk.
        """
        digest = hashlib.sha256(
            f"{type}|{page or ''}|{key}".encode("utf-8")
        ).hexdigest()[:6]
        page_part = page or "vault"
        return f"{type}-{page_part}-{digest}"

    @staticmethod
    def now_iso() -> str:
        """Current time as ISO 8601 UTC. Centralized so tests can monkeypatch."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: All four tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/issues/queue.py tests/test_issues/test_queue.py
git commit -m "feat: Issue dataclass with deterministic id helper"
```

---

### Task 3: `IssueQueue.add` + `exists` (idempotent file write)

**Files:**
- Modify: `src/llm_wiki/issues/queue.py`
- Modify: `tests/test_issues/test_queue.py`

- [ ] **Step 1: Add failing tests for `IssueQueue.add` and `exists`**

Append to `tests/test_issues/test_queue.py`:

```python
import yaml
from pathlib import Path

import pytest

from llm_wiki.issues.queue import IssueQueue


def _make_issue(
    type: str = "broken-link",
    page: str | None = "srna-tquant",
    key: str = "k-means-deep",
    title: str = "Wikilink target does not exist",
    body: str = "The page references [[k-means-deep]] but no such page exists.",
    detected_by: str = "auditor",
    metadata: dict | None = None,
) -> Issue:
    return Issue(
        id=Issue.make_id(type, page, key),
        type=type,
        status="open",
        title=title,
        page=page,
        body=body,
        created=Issue.now_iso(),
        detected_by=detected_by,
        metadata=metadata or {},
    )


def test_queue_add_creates_file(tmp_path: Path):
    """add() writes the issue to <wiki_dir>/.issues/<id>.md."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    path, was_new = queue.add(issue)

    assert was_new is True
    assert path == wiki_dir / ".issues" / f"{issue.id}.md"
    assert path.exists()


def test_queue_add_writes_frontmatter_and_body(tmp_path: Path):
    """The on-disk file has parseable YAML frontmatter and the body."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue(metadata={"target": "k-means-deep"})

    path, _ = queue.add(issue)
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    end = text.index("\n---", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["id"] == issue.id
    assert fm["type"] == "broken-link"
    assert fm["status"] == "open"
    assert fm["page"] == "srna-tquant"
    assert fm["detected_by"] == "auditor"
    assert fm["metadata"] == {"target": "k-means-deep"}

    body = text[end + 4:].strip()
    assert body == issue.body.strip()


def test_queue_add_is_idempotent(tmp_path: Path):
    """Adding the same issue twice does not overwrite — second call returns was_new=False."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    path1, was_new_1 = queue.add(issue)
    original_text = path1.read_text(encoding="utf-8")

    # Second add with the same id — even if body differs, on-disk file is preserved
    issue_again = _make_issue(body="DIFFERENT BODY THAT SHOULD NOT BE WRITTEN")
    assert issue_again.id == issue.id  # ids match
    path2, was_new_2 = queue.add(issue_again)

    assert was_new_2 is False
    assert path2 == path1
    assert path2.read_text(encoding="utf-8") == original_text


def test_queue_exists(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    assert queue.exists(issue.id) is False
    queue.add(issue)
    assert queue.exists(issue.id) is True


def test_queue_creates_issues_dir_on_demand(tmp_path: Path):
    """The .issues subdirectory does not need to exist before add()."""
    wiki_dir = tmp_path / "wiki"
    # wiki_dir itself doesn't exist
    queue = IssueQueue(wiki_dir)
    queue.add(_make_issue())

    assert (wiki_dir / ".issues").is_dir()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: AttributeError — `IssueQueue` does not exist.

- [ ] **Step 3: Implement `IssueQueue.add`, `exists`, `issues_dir`**

Append to `src/llm_wiki/issues/queue.py`:

```python
from pathlib import Path
import yaml


class IssueQueue:
    """Filesystem-backed issue queue at <wiki_dir>/.issues/.

    Issues are stored one-per-file as YAML frontmatter + markdown body.
    The id is the filename (without .md extension). Add operations are
    idempotent: if an issue with the same id already exists on disk, the
    existing file is preserved unchanged and add() returns was_new=False.

    The .issues directory is excluded from Vault.scan() because Vault
    already filters out hidden directories (those starting with '.').
    """

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir

    @property
    def issues_dir(self) -> Path:
        return self._wiki_dir / ".issues"

    def exists(self, issue_id: str) -> bool:
        return (self.issues_dir / f"{issue_id}.md").exists()

    def add(self, issue: Issue) -> tuple[Path, bool]:
        """Write the issue to disk if not already present.

        Returns:
            (path, was_new) — was_new is False if the file already existed.
        """
        path = self.issues_dir / f"{issue.id}.md"
        if path.exists():
            return path, False

        self.issues_dir.mkdir(parents=True, exist_ok=True)
        fm = {
            "id": issue.id,
            "type": issue.type,
            "status": issue.status,
            "title": issue.title,
            "page": issue.page,
            "created": issue.created,
            "detected_by": issue.detected_by,
            "metadata": issue.metadata,
        }
        frontmatter = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
        path.write_text(
            f"---\n{frontmatter}\n---\n\n{issue.body.strip()}\n",
            encoding="utf-8",
        )
        return path, True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: All tests pass (Task 2 + Task 3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/issues/queue.py tests/test_issues/test_queue.py
git commit -m "feat: IssueQueue.add — idempotent issue persistence"
```

---

### Task 4: `IssueQueue.get` + `list`

**Files:**
- Modify: `src/llm_wiki/issues/queue.py`
- Modify: `tests/test_issues/test_queue.py`

- [ ] **Step 1: Add failing tests for `get` and `list`**

Append to `tests/test_issues/test_queue.py`:

```python
def test_queue_get_round_trip(tmp_path: Path):
    """get() returns an Issue with all fields preserved."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue(metadata={"target": "k-means-deep", "section": "method"})
    queue.add(issue)

    loaded = queue.get(issue.id)

    assert loaded is not None
    assert loaded.id == issue.id
    assert loaded.type == issue.type
    assert loaded.status == issue.status
    assert loaded.title == issue.title
    assert loaded.page == issue.page
    assert loaded.body == issue.body.strip()
    assert loaded.created == issue.created
    assert loaded.detected_by == issue.detected_by
    assert loaded.metadata == issue.metadata


def test_queue_get_missing_returns_none(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.get("does-not-exist") is None


def test_queue_list_empty(tmp_path: Path):
    """list() on a queue with no .issues dir returns []."""
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.list() == []


def test_queue_list_returns_all_issues(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    a = _make_issue(type="broken-link", page="page-a", key="x")
    b = _make_issue(type="orphan", page="page-b", key="")
    c = _make_issue(type="broken-link", page="page-c", key="y")
    queue.add(a)
    queue.add(b)
    queue.add(c)

    ids = {issue.id for issue in queue.list()}
    assert ids == {a.id, b.id, c.id}


def test_queue_list_filters_by_status(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    a = _make_issue(type="orphan", page="page-a", key="")
    b = _make_issue(type="orphan", page="page-b", key="")
    queue.add(a)
    queue.add(b)

    # Manually set b to resolved by rewriting via the helper we'll add in Task 5
    # For now, fake it by rewriting the file's frontmatter
    path_b = queue.issues_dir / f"{b.id}.md"
    path_b.write_text(
        path_b.read_text(encoding="utf-8").replace("status: open", "status: resolved"),
        encoding="utf-8",
    )

    open_issues = queue.list(status="open")
    resolved = queue.list(status="resolved")
    assert {i.id for i in open_issues} == {a.id}
    assert {i.id for i in resolved} == {b.id}


def test_queue_list_filters_by_type(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    queue.add(_make_issue(type="broken-link", page="a", key="1"))
    queue.add(_make_issue(type="orphan", page="b", key=""))
    queue.add(_make_issue(type="broken-link", page="c", key="2"))

    broken = queue.list(type="broken-link")
    assert len(broken) == 2
    assert all(i.type == "broken-link" for i in broken)
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: AttributeError — `IssueQueue` has no `get` or `list`.

- [ ] **Step 3: Implement `get` and `list`**

Append to `IssueQueue` in `src/llm_wiki/issues/queue.py`:

```python
    def get(self, issue_id: str) -> Issue | None:
        path = self.issues_dir / f"{issue_id}.md"
        if not path.exists():
            return None
        return self._parse_file(path)

    def list(
        self,
        status: str | None = None,
        type: str | None = None,
    ) -> list[Issue]:
        """Return all issues, optionally filtered by status and/or type."""
        if not self.issues_dir.exists():
            return []
        results: list[Issue] = []
        for path in sorted(self.issues_dir.glob("*.md")):
            issue = self._parse_file(path)
            if issue is None:
                continue
            if status is not None and issue.status != status:
                continue
            if type is not None and issue.type != type:
                continue
            results.append(issue)
        return results

    def _parse_file(self, path: Path) -> Issue | None:
        """Parse a single issue file. Returns None if the frontmatter is malformed."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return None
        try:
            fm = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            return None
        body = text[end + 4:].strip()
        return Issue(
            id=fm.get("id", path.stem),
            type=fm.get("type", "unknown"),
            status=fm.get("status", "open"),
            title=fm.get("title", ""),
            page=fm.get("page"),
            body=body,
            created=fm.get("created", ""),
            detected_by=fm.get("detected_by", "unknown"),
            metadata=fm.get("metadata") or {},
        )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/issues/queue.py tests/test_issues/test_queue.py
git commit -m "feat: IssueQueue.get + list with status/type filters"
```

---

### Task 5: `IssueQueue.update_status`

**Files:**
- Modify: `src/llm_wiki/issues/queue.py`
- Modify: `tests/test_issues/test_queue.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_issues/test_queue.py`:

```python
def test_update_status_changes_status_only(tmp_path: Path):
    """update_status preserves all other fields."""
    queue = IssueQueue(tmp_path / "wiki")
    issue = _make_issue(metadata={"target": "k-means-deep"})
    queue.add(issue)

    ok = queue.update_status(issue.id, "resolved")
    assert ok is True

    loaded = queue.get(issue.id)
    assert loaded is not None
    assert loaded.status == "resolved"
    assert loaded.title == issue.title
    assert loaded.body == issue.body.strip()
    assert loaded.metadata == {"target": "k-means-deep"}
    assert loaded.created == issue.created


def test_update_status_missing_returns_false(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.update_status("does-not-exist", "resolved") is False


def test_update_status_validates_value(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    issue = _make_issue()
    queue.add(issue)

    with pytest.raises(ValueError):
        queue.update_status(issue.id, "invalid-status")
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_issues/test_queue.py -v -k update_status`
Expected: AttributeError — no `update_status` method.

- [ ] **Step 3: Implement `update_status`**

Append to `IssueQueue` in `src/llm_wiki/issues/queue.py`:

```python
    _VALID_STATUSES = {"open", "resolved", "wontfix"}

    def update_status(self, issue_id: str, new_status: str) -> bool:
        """Mutate the status field, preserving all other fields and the body."""
        if new_status not in self._VALID_STATUSES:
            raise ValueError(
                f"Invalid status {new_status!r}; must be one of {sorted(self._VALID_STATUSES)}"
            )
        issue = self.get(issue_id)
        if issue is None:
            return False
        issue.status = new_status
        # Re-write by deleting + re-adding (preserves the file path since id is unchanged)
        path = self.issues_dir / f"{issue_id}.md"
        path.unlink()
        self.add(issue)
        return True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/issues/queue.py tests/test_issues/test_queue.py
git commit -m "feat: IssueQueue.update_status with status validation"
```

---

### Task 6: `Vault.manifest_entries()` public accessor

**Files:**
- Modify: `src/llm_wiki/vault.py`
- Modify: `tests/test_vault.py`

The auditor needs read access to manifest entries (specifically `links_from`) to detect orphans. `Vault` currently exposes only the search/read APIs and keeps `_store` private. Add a single read-only accessor.

- [ ] **Step 1: Add failing test**

Append to `tests/test_vault.py`:

```python
def test_vault_manifest_entries_returns_dict_keyed_by_name(sample_vault):
    """manifest_entries() exposes the parsed manifest entries by page name."""
    from llm_wiki.vault import Vault
    from llm_wiki.manifest import ManifestEntry

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()

    assert isinstance(entries, dict)
    assert "srna-embeddings" in entries
    assert isinstance(entries["srna-embeddings"], ManifestEntry)
    # links_from is computed by the store; srna-embeddings is referenced by
    # both inter-rep-variant-analysis and clustering-metrics in the fixture.
    assert "inter-rep-variant-analysis" in entries["srna-embeddings"].links_from
    assert "clustering-metrics" in entries["srna-embeddings"].links_from


def test_vault_manifest_entries_is_a_copy(sample_vault):
    """Mutating the returned dict does not affect the underlying store."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()
    entries.clear()

    # Re-fetch to confirm internal state intact
    entries2 = vault.manifest_entries()
    assert "srna-embeddings" in entries2
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_vault.py -v -k manifest_entries`
Expected: AttributeError — no `manifest_entries` method.

- [ ] **Step 3: Implement the accessor**

In `src/llm_wiki/vault.py`, add a method to the `Vault` class (alongside `read_page`):

```python
    def manifest_entries(self) -> dict[str, "ManifestEntry"]:
        """Return a copy of the manifest entries dict, keyed by page name.

        The store's links_from values are already populated. Returned as a
        copy so callers (auditor, librarian) can iterate without locking.
        """
        return dict(self._store._entries)
```

If type annotations need a forward import, add at the top of `vault.py`:

```python
from llm_wiki.manifest import ManifestEntry  # already imported via build_entry — re-export
```

(`ManifestEntry` is already imported in `vault.py:9`, so the annotation works without changes.)

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_vault.py -v`
Expected: All vault tests pass, including the new ones.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py
git commit -m "feat: Vault.manifest_entries — public read accessor for auditor"
```

---

### Task 7: `find_orphans` check

**Files:**
- Create: `src/llm_wiki/audit/checks.py` (partial — `CheckResult` + `find_orphans`)
- Create: `tests/test_audit/test_checks.py` (partial — orphan tests only)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_audit/test_checks.py
from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.checks import CheckResult, find_orphans
from llm_wiki.vault import Vault


def test_find_orphans_finds_unreferenced_top_level_page(sample_vault: Path):
    """no-structure.md sits at vault root, has zero inlinks → orphan."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)

    assert isinstance(result, CheckResult)
    assert result.check == "orphans"
    orphan_pages = {issue.page for issue in result.issues}
    assert "no-structure" in orphan_pages


def test_find_orphans_does_not_flag_referenced_pages(sample_vault: Path):
    """srna-embeddings has multiple inlinks — must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "srna-embeddings" not in orphan_pages
    assert "clustering-metrics" not in orphan_pages
    assert "inter-rep-variant-analysis" not in orphan_pages


def test_find_orphans_skips_index_readme_home(tmp_path: Path):
    """Pages named index/readme/home are entry points, not orphans."""
    (tmp_path / "index.md").write_text("# Index\n\nEntry point.\n")
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / "home.md").write_text("# Home\n")

    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "index" not in orphan_pages
    assert "readme" not in orphan_pages
    assert "home" not in orphan_pages


def test_find_orphans_empty_vault(tmp_path: Path):
    """Empty vault produces no orphans without raising."""
    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    assert result.issues == []


def test_find_orphans_issue_metadata(sample_vault: Path):
    """Each orphan issue has type=orphan, status=open, detected_by=auditor."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    for issue in result.issues:
        assert issue.type == "orphan"
        assert issue.status == "open"
        assert issue.detected_by == "auditor"
        assert issue.id.startswith("orphan-")
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_checks.py -v`
Expected: ImportError — `llm_wiki.audit.checks` does not exist.

- [ ] **Step 3: Implement `CheckResult` + `find_orphans`**

```python
# src/llm_wiki/audit/checks.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm_wiki.issues.queue import Issue
from llm_wiki.vault import Vault

# Page names that should never be flagged as orphans even if nothing links to them.
_ENTRY_POINT_NAMES = {"index", "readme", "home"}


@dataclass
class CheckResult:
    """Result of one structural check."""
    check: str
    issues: list[Issue]


def find_orphans(vault: Vault) -> CheckResult:
    """Pages with zero inlinks (excluding entry-point names).

    Each orphan becomes one Issue with the page slug as the affected page
    and an empty key (since the page itself is the unique identifier).
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        if name.lower() in _ENTRY_POINT_NAMES:
            continue
        if entry.links_from:
            continue
        issues.append(
            Issue(
                id=Issue.make_id("orphan", name, ""),
                type="orphan",
                status="open",
                title=f"Page '{name}' has no inbound links",
                page=name,
                body=(
                    f"The page [[{name}]] is not referenced by any other page in the vault. "
                    f"Either link to it from a related page or remove it if obsolete."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={},
            )
        )
    return CheckResult(check="orphans", issues=issues)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_checks.py -v`
Expected: All five orphan tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: find_orphans check — pages with zero inlinks"
```

---

### Task 8: `find_broken_wikilinks` check

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `tests/test_audit/test_checks.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_checks.py`:

```python
from llm_wiki.audit.checks import find_broken_wikilinks


def test_find_broken_wikilinks_detects_missing_target(sample_vault: Path):
    """no-structure.md links to [[some-other-page]] which does not exist."""
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)

    assert result.check == "broken-wikilinks"
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "some-other-page" in targets


def test_find_broken_wikilinks_does_not_flag_existing_targets(sample_vault: Path):
    """Wikilinks to pages that exist must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "srna-embeddings" not in targets
    assert "clustering-metrics" not in targets


def test_find_broken_wikilinks_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    result = find_broken_wikilinks(vault)
    assert result.issues == []


def test_find_broken_wikilinks_issue_shape(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    assert result.issues, "expected at least one broken-wikilink issue in fixture"
    issue = next(i for i in result.issues if i.metadata.get("target") == "some-other-page")
    assert issue.type == "broken-link"
    assert issue.status == "open"
    assert issue.page == "no-structure"
    assert issue.detected_by == "auditor"
    assert "some-other-page" in issue.body
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_checks.py -v -k broken_wikilinks`
Expected: ImportError — `find_broken_wikilinks` does not exist.

- [ ] **Step 3: Implement `find_broken_wikilinks`**

Append to `src/llm_wiki/audit/checks.py`:

```python
def find_broken_wikilinks(vault: Vault) -> CheckResult:
    """For each page, every wikilink target must resolve to a known page.

    The page parser already strips wikilinks pointing at non-page files
    (PDFs, images — see llm_wiki/page.py:_NON_PAGE_EXTENSIONS), so this
    check only sees candidate page references.
    """
    entries = vault.manifest_entries()
    known_pages = set(entries)
    issues: list[Issue] = []
    for name, entry in entries.items():
        for target in entry.links_to:
            if target in known_pages:
                continue
            issues.append(
                Issue(
                    id=Issue.make_id("broken-link", name, target),
                    type="broken-link",
                    status="open",
                    title=f"Wikilink target '{target}' does not exist",
                    page=name,
                    body=(
                        f"The page [[{name}]] references [[{target}]], "
                        f"but no such page exists in the vault. "
                        f"Either create the page or remove the link."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"target": target},
                )
            )
    return CheckResult(check="broken-wikilinks", issues=issues)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_checks.py -v`
Expected: All tests so far pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: find_broken_wikilinks check"
```

---

### Task 9: `find_missing_markers` check

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `tests/test_audit/test_checks.py`

A page has missing markers when it contains `##`/`###` headings but no `%% section: ... %%` markers — i.e. the page parser is using the heading fallback rather than authoritative markers. The librarian (sub-phase 5b/5c) will retrofit markers; the auditor flags them.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_checks.py`:

```python
from llm_wiki.audit.checks import find_missing_markers


def test_find_missing_markers_flags_pages_with_headings_no_markers(sample_vault: Path):
    """clustering-metrics uses ## headings but has no %% markers."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)

    assert result.check == "missing-markers"
    affected = {issue.page for issue in result.issues}
    assert "clustering-metrics" in affected


def test_find_missing_markers_does_not_flag_pages_with_markers(sample_vault: Path):
    """srna-embeddings has %% markers — must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    affected = {issue.page for issue in result.issues}
    assert "srna-embeddings" not in affected
    assert "inter-rep-variant-analysis" not in affected


def test_find_missing_markers_does_not_flag_pages_without_headings(sample_vault: Path):
    """no-structure.md has no headings at all — also not flagged."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    affected = {issue.page for issue in result.issues}
    assert "no-structure" not in affected


def test_find_missing_markers_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    result = find_missing_markers(vault)
    assert result.issues == []
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_checks.py -v -k missing_markers`
Expected: ImportError — `find_missing_markers` does not exist.

- [ ] **Step 3: Implement `find_missing_markers`**

Append to `src/llm_wiki/audit/checks.py`:

```python
import re

# Detects ## or ### headings at line start (not inside code blocks — naive but adequate
# for v1; the librarian's retrofit pass uses the same heuristic).
_HEADING_LINE_RE = re.compile(r"^(##|###)\s+\S", re.MULTILINE)
_MARKER_LINE_RE = re.compile(r"^%%\s*section:", re.MULTILINE)


def find_missing_markers(vault: Vault) -> CheckResult:
    """Pages with ## headings but no %% section markers.

    Reads page.raw_content directly so we see what was on disk, not what
    the parser fell back to. The page is flagged exactly when:
      - it contains at least one ##/### heading at line start, AND
      - it contains zero `%% section: ... %%` markers.
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        page = vault.read_page(name)
        if page is None:
            continue
        raw = page.raw_content
        if _MARKER_LINE_RE.search(raw):
            continue
        if not _HEADING_LINE_RE.search(raw):
            continue
        issues.append(
            Issue(
                id=Issue.make_id("missing-markers", name, ""),
                type="missing-markers",
                status="open",
                title=f"Page '{name}' has headings but no %% section markers",
                page=name,
                body=(
                    f"The page [[{name}]] uses ## headings without `%% section: ... %%` "
                    f"markers. Markers are required so the daemon can slice the page by "
                    f"section. The librarian will retrofit them on its next run."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={},
            )
        )
    return CheckResult(check="missing-markers", issues=issues)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_checks.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: find_missing_markers check"
```

---

### Task 10: `find_broken_citations` check

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `tests/test_audit/test_checks.py`

A broken citation is a `[[raw/<path>]]` reference (in body or in `frontmatter.source`) where the underlying file does not exist on disk. The page parser strips these from `page.wikilinks` (extension-based filter), so the check re-scans `page.raw_content` and inspects `page.frontmatter.source`.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_audit/test_checks.py`:

```python
from llm_wiki.audit.checks import find_broken_citations


def test_find_broken_citations_detects_missing_source(sample_vault: Path):
    """srna-embeddings has frontmatter source [[raw/smith-2026-srna.pdf]] which doesn't exist."""
    vault = Vault.scan(sample_vault)
    result = find_broken_citations(vault, sample_vault)

    assert result.check == "broken-citations"
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/smith-2026-srna.pdf" in targets


def test_find_broken_citations_passes_when_source_exists(sample_vault: Path):
    """Create the missing raw file → re-running the check finds no issue for it."""
    raw_dir = sample_vault / "raw"
    raw_dir.mkdir()
    (raw_dir / "smith-2026-srna.pdf").write_bytes(b"%PDF-1.4 fake")

    vault = Vault.scan(sample_vault)
    result = find_broken_citations(vault, sample_vault)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/smith-2026-srna.pdf" not in targets


def test_find_broken_citations_detects_inline_raw_reference(tmp_path: Path):
    """A [[raw/missing.pdf]] reference in page body is also flagged."""
    page = tmp_path / "doc.md"
    page.write_text(
        "---\ntitle: Doc\n---\n\nSee [[raw/missing.pdf]] for details.\n"
    )

    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/missing.pdf" in targets


def test_find_broken_citations_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    assert result.issues == []
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_checks.py -v -k broken_citations`
Expected: ImportError — `find_broken_citations` does not exist.

- [ ] **Step 3: Implement `find_broken_citations`**

Append to `src/llm_wiki/audit/checks.py`:

```python
# Matches [[raw/<anything>]] inside page bodies. Allows | aliases.
_RAW_CITATION_RE = re.compile(r"\[\[(raw/[^\]|]+)(?:\|[^\]]+)?\]\]")
# Frontmatter source values are stored as the literal string "[[raw/...]]".
_FRONTMATTER_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def find_broken_citations(vault: Vault, vault_root: Path) -> CheckResult:
    """References to raw/ source files that don't exist on disk.

    Scans two places:
      1. page.raw_content for inline `[[raw/<path>]]` references
      2. page.frontmatter['source'] (and 'sources' as a list) for raw refs

    Each missing target produces one Issue keyed by (page, target).
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        page = vault.read_page(name)
        if page is None:
            continue
        targets: set[str] = set()

        for match in _RAW_CITATION_RE.finditer(page.raw_content):
            targets.add(match.group(1))

        source_field = page.frontmatter.get("source")
        if isinstance(source_field, str):
            for match in _FRONTMATTER_LINK_RE.finditer(source_field):
                inner = match.group(1)
                if inner.startswith("raw/"):
                    targets.add(inner)

        sources_field = page.frontmatter.get("sources")
        if isinstance(sources_field, list):
            for entry_str in sources_field:
                if not isinstance(entry_str, str):
                    continue
                for match in _FRONTMATTER_LINK_RE.finditer(entry_str):
                    inner = match.group(1)
                    if inner.startswith("raw/"):
                        targets.add(inner)

        for target in sorted(targets):
            absolute = vault_root / target
            if absolute.exists():
                continue
            issues.append(
                Issue(
                    id=Issue.make_id("broken-citation", name, target),
                    type="broken-citation",
                    status="open",
                    title=f"Citation '{target}' does not exist on disk",
                    page=name,
                    body=(
                        f"The page [[{name}]] cites `{target}`, but no such file "
                        f"exists at `{absolute}`. Either restore the source file "
                        f"or remove the citation."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"target": target},
                )
            )
    return CheckResult(check="broken-citations", issues=issues)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_checks.py -v`
Expected: All check tests pass (orphans, broken-wikilinks, missing-markers, broken-citations).

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: find_broken_citations check"
```

---

### Task 11: `Auditor` + `AuditReport`

**Files:**
- Create: `src/llm_wiki/audit/auditor.py`
- Create: `tests/test_audit/test_auditor.py`

`Auditor.audit()` runs all four checks, routes each issue through `IssueQueue.add()`, and aggregates the results into an `AuditReport` that distinguishes new issues from already-existing ones (so re-runs are quiet).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_audit/test_auditor.py
from __future__ import annotations

from pathlib import Path

from llm_wiki.audit.auditor import Auditor, AuditReport
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault


def test_audit_runs_all_checks_on_sample_vault(sample_vault: Path):
    """A first audit run finds the four expected issues from the fixture."""
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)

    report = auditor.audit()

    assert isinstance(report, AuditReport)
    # The fixture should produce: at least 1 orphan (no-structure),
    # 1 broken-wikilink (some-other-page), 1 missing-markers (clustering-metrics),
    # 1 broken-citation (raw/smith-2026-srna.pdf).
    assert report.by_check["orphans"] >= 1
    assert report.by_check["broken-wikilinks"] >= 1
    assert report.by_check["missing-markers"] >= 1
    assert report.by_check["broken-citations"] >= 1
    assert report.total_checks_run == 4
    assert len(report.new_issue_ids) == report.total_issues
    assert report.existing_issue_ids == []


def test_audit_is_idempotent(sample_vault: Path):
    """Re-running audit() produces zero new issues — all are existing."""
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)

    first = auditor.audit()
    second = auditor.audit()

    assert second.total_issues == first.total_issues
    assert second.new_issue_ids == []
    assert sorted(second.existing_issue_ids) == sorted(first.new_issue_ids)


def test_audit_writes_files_to_issues_dir(sample_vault: Path):
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)
    auditor.audit()

    files = list(queue.issues_dir.glob("*.md"))
    assert len(files) >= 4


def test_audit_empty_vault(tmp_path: Path):
    """An empty vault produces an empty report without raising."""
    queue = IssueQueue(tmp_path)
    auditor = Auditor(Vault.scan(tmp_path), queue, tmp_path)
    report = auditor.audit()
    assert report.total_issues == 0
    assert report.total_checks_run == 4
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_audit/test_auditor.py -v`
Expected: ImportError — `llm_wiki.audit.auditor` does not exist.

- [ ] **Step 3: Implement `Auditor` + `AuditReport`**

```python
# src/llm_wiki/audit/auditor.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.audit.checks import (
    find_broken_citations,
    find_broken_wikilinks,
    find_missing_markers,
    find_orphans,
)
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault


@dataclass
class AuditReport:
    """Aggregate result of one audit run."""
    total_checks_run: int
    by_check: dict[str, int] = field(default_factory=dict)
    new_issue_ids: list[str] = field(default_factory=list)
    existing_issue_ids: list[str] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(self.by_check.values())

    def to_dict(self) -> dict:
        return {
            "total_checks_run": self.total_checks_run,
            "total_issues": self.total_issues,
            "by_check": self.by_check,
            "new_issue_ids": self.new_issue_ids,
            "existing_issue_ids": self.existing_issue_ids,
        }


class Auditor:
    """Runs all structural checks and routes results through the issue queue."""

    def __init__(self, vault: Vault, queue: IssueQueue, vault_root: Path) -> None:
        self._vault = vault
        self._queue = queue
        self._vault_root = vault_root

    def audit(self) -> AuditReport:
        """Run every check and file each issue idempotently."""
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_broken_citations(self._vault, self._vault_root),
        ]

        by_check: dict[str, int] = {}
        new_ids: list[str] = []
        existing_ids: list[str] = []

        for result in results:
            by_check[result.check] = len(result.issues)
            for issue in result.issues:
                _, was_new = self._queue.add(issue)
                if was_new:
                    new_ids.append(issue.id)
                else:
                    existing_ids.append(issue.id)

        return AuditReport(
            total_checks_run=len(results),
            by_check=by_check,
            new_issue_ids=new_ids,
            existing_issue_ids=existing_ids,
        )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_audit/test_auditor.py -v`
Expected: All four auditor tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/auditor.py tests/test_audit/test_auditor.py
git commit -m "feat: Auditor — runs all checks, idempotent issue filing"
```

---

### Task 12: Daemon `lint` route

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Create: `tests/test_daemon/test_lint_route.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_daemon/test_lint_route.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_lint_route_returns_audit_report(sample_vault: Path, tmp_path: Path):
    """The lint route runs the auditor and returns a serialized AuditReport."""
    sock_path = tmp_path / "lint.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        resp = client.request({"type": "lint"})

        assert resp["status"] == "ok"
        assert resp["total_checks_run"] == 4
        assert resp["total_issues"] >= 4
        assert "orphans" in resp["by_check"]
        assert "broken-wikilinks" in resp["by_check"]
        assert "missing-markers" in resp["by_check"]
        assert "broken-citations" in resp["by_check"]
        assert isinstance(resp["new_issue_ids"], list)
        assert isinstance(resp["existing_issue_ids"], list)
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_lint_route_idempotent(sample_vault: Path, tmp_path: Path):
    """Calling lint twice does not re-create issues."""
    sock_path = tmp_path / "lint2.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        first = client.request({"type": "lint"})
        second = client.request({"type": "lint"})

        assert second["new_issue_ids"] == []
        assert sorted(second["existing_issue_ids"]) == sorted(first["new_issue_ids"])
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_lint_route.py -v`
Expected: Both tests fail with `status: error`, `message: Unknown request type: lint`.

- [ ] **Step 3: Implement the `lint` route**

In `src/llm_wiki/daemon/server.py`, add a case to `_route()`:

```python
            case "lint":
                return self._handle_lint()
```

Then add the handler method to `DaemonServer` (next to `_handle_status`):

```python
    def _handle_lint(self) -> dict:
        from llm_wiki.audit.auditor import Auditor
        from llm_wiki.issues.queue import IssueQueue

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        queue = IssueQueue(wiki_dir)
        auditor = Auditor(self._vault, queue, self._vault_root)
        report = auditor.audit()
        return {"status": "ok", **report.to_dict()}
```

The lazy imports follow the same pattern as `_handle_query` and `_handle_ingest` — keeps the daemon's startup cost low and avoids importing `audit/auditor.py` until first use.

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_lint_route.py -v`
Expected: Both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_lint_route.py
git commit -m "feat: daemon lint route — runs Auditor on demand"
```

---

### Task 13: Daemon `issues-list` / `issues-get` / `issues-update` routes

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_daemon/test_lint_route.py`

These routes are needed by the CLI in Task 15 and (later) by sub-phases 5b/5c/5d to query the queue across the IPC boundary.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_daemon/test_lint_route.py`:

```python
def _serialize_helper(issue_dict):
    return {
        k: v for k, v in issue_dict.items()
        if k in {"id", "type", "status", "title", "page", "detected_by"}
    }


@pytest.mark.asyncio
async def test_issues_list_route(sample_vault: Path, tmp_path: Path):
    """issues-list returns the issues from the queue, optionally filtered."""
    sock_path = tmp_path / "issues-list.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        # Populate the queue
        client.request({"type": "lint"})

        all_resp = client.request({"type": "issues-list"})
        assert all_resp["status"] == "ok"
        assert len(all_resp["issues"]) >= 4

        broken_resp = client.request({"type": "issues-list", "type_filter": "broken-link"})
        assert all(i["type"] == "broken-link" for i in broken_resp["issues"])

        open_resp = client.request({"type": "issues-list", "status_filter": "open"})
        assert all(i["status"] == "open" for i in open_resp["issues"])
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_issues_get_and_update(sample_vault: Path, tmp_path: Path):
    sock_path = tmp_path / "issues-get.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        client.request({"type": "lint"})

        listing = client.request({"type": "issues-list"})["issues"]
        target_id = listing[0]["id"]

        get_resp = client.request({"type": "issues-get", "id": target_id})
        assert get_resp["status"] == "ok"
        assert get_resp["issue"]["id"] == target_id
        assert "body" in get_resp["issue"]

        update_resp = client.request(
            {"type": "issues-update", "id": target_id, "status": "wontfix"}
        )
        assert update_resp["status"] == "ok"

        get_after = client.request({"type": "issues-get", "id": target_id})
        assert get_after["issue"]["status"] == "wontfix"

        bad_status = client.request(
            {"type": "issues-update", "id": target_id, "status": "bogus"}
        )
        assert bad_status["status"] == "error"

        missing = client.request({"type": "issues-get", "id": "nope-vault-aaaaaa"})
        assert missing["status"] == "error"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_lint_route.py -v -k issues_`
Expected: Failures with `Unknown request type` for issues-list / issues-get / issues-update.

- [ ] **Step 3: Implement the routes**

Add three cases to `_route()` in `src/llm_wiki/daemon/server.py`:

```python
            case "issues-list":
                return self._handle_issues_list(request)
            case "issues-get":
                return self._handle_issues_get(request)
            case "issues-update":
                return self._handle_issues_update(request)
```

Then add the handler methods (next to `_handle_lint`):

```python
    def _issue_queue(self) -> "IssueQueue":
        from llm_wiki.issues.queue import IssueQueue
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        return IssueQueue(wiki_dir)

    def _handle_issues_list(self, request: dict) -> dict:
        queue = self._issue_queue()
        issues = queue.list(
            status=request.get("status_filter"),
            type=request.get("type_filter"),
        )
        return {
            "status": "ok",
            "issues": [_serialize_issue(i) for i in issues],
        }

    def _handle_issues_get(self, request: dict) -> dict:
        if "id" not in request:
            return {"status": "error", "message": "Missing required field: id"}
        queue = self._issue_queue()
        issue = queue.get(request["id"])
        if issue is None:
            return {"status": "error", "message": f"Issue not found: {request['id']}"}
        return {"status": "ok", "issue": _serialize_issue(issue, include_body=True)}

    def _handle_issues_update(self, request: dict) -> dict:
        if "id" not in request or "status" not in request:
            return {"status": "error", "message": "Missing required fields: id, status"}
        queue = self._issue_queue()
        try:
            ok = queue.update_status(request["id"], request["status"])
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if not ok:
            return {"status": "error", "message": f"Issue not found: {request['id']}"}
        return {"status": "ok"}
```

Add the serializer helper at the bottom of `server.py` (alongside `_serialize_result`):

```python
def _serialize_issue(issue: "Issue", include_body: bool = False) -> dict:
    data = {
        "id": issue.id,
        "type": issue.type,
        "status": issue.status,
        "title": issue.title,
        "page": issue.page,
        "created": issue.created,
        "detected_by": issue.detected_by,
        "metadata": issue.metadata,
    }
    if include_body:
        data["body"] = issue.body
    return data
```

The forward `"Issue"` annotation is fine because `Issue` is imported lazily inside `_issue_queue()`.

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_lint_route.py -v`
Expected: All five tests in this file pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_lint_route.py
git commit -m "feat: daemon issues-list/get/update routes"
```

---

### Task 14: CLI `llm-wiki lint` command

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/test_lint_cmd.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli/test_lint_cmd.py
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from llm_wiki.cli.main import cli


def test_lint_cmd_prints_grouped_report(sample_vault: Path, monkeypatch):
    """`llm-wiki lint` runs the daemon's lint route and prints a grouped report."""
    runner = CliRunner()
    result = runner.invoke(cli, ["lint", "--vault", str(sample_vault)])

    assert result.exit_code == 0, result.output
    # Output should mention each check name and a count
    assert "orphans" in result.output
    assert "broken-wikilinks" in result.output
    assert "missing-markers" in result.output
    assert "broken-citations" in result.output


def test_lint_cmd_idempotent_quiet_on_rerun(sample_vault: Path):
    """Second invocation reports zero new issues."""
    runner = CliRunner()
    runner.invoke(cli, ["lint", "--vault", str(sample_vault)])
    result = runner.invoke(cli, ["lint", "--vault", str(sample_vault)])

    assert result.exit_code == 0, result.output
    assert "0 new" in result.output or "no new" in result.output.lower()
```

Note: these tests rely on the daemon auto-start path that other CLI tests already use. If the daemon was previously started for `sample_vault`, the test fixture's cleanup hook (`shutil.rmtree(state_dir)` in `conftest.py`) clears the state between tests.

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_cli/test_lint_cmd.py -v`
Expected: `Error: No such command 'lint'`.

- [ ] **Step 3: Implement the `lint` command**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def lint(vault_path: Path) -> None:
    """Run structural integrity checks on the vault and file issues."""
    client = _get_client(vault_path)
    resp = client.request({"type": "lint"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Lint failed"))

    total = resp["total_issues"]
    new_count = len(resp["new_issue_ids"])
    existing_count = len(resp["existing_issue_ids"])

    click.echo(f"Ran {resp['total_checks_run']} checks — {total} issue(s) total")
    click.echo(f"  {new_count} new, {existing_count} already in queue")
    click.echo()

    for check, count in resp["by_check"].items():
        marker = "✓" if count == 0 else "!"
        click.echo(f"  {marker} {check}: {count}")

    if new_count > 0:
        click.echo()
        click.echo("New issue ids:")
        for issue_id in resp["new_issue_ids"]:
            click.echo(f"  - {issue_id}")
```

(The `✓` and `!` characters are ASCII-safe alternatives that don't violate the "no emoji" rule — they're standard symbols. If the user prefers no symbols, replace with `OK`/`FAIL`.)

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_cli/test_lint_cmd.py -v`
Expected: Both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_lint_cmd.py
git commit -m "feat: llm-wiki lint CLI command"
```

---

### Task 15: CLI `llm-wiki issues` command group

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/test_issues_cmd.py`

`llm-wiki issues` is a Click group with four subcommands: `list`, `show`, `resolve`, `wontfix`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli/test_issues_cmd.py
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from llm_wiki.cli.main import cli


def _populate(vault_path: Path) -> None:
    """Run lint to seed the issue queue."""
    runner = CliRunner()
    runner.invoke(cli, ["lint", "--vault", str(vault_path)])


def test_issues_list(sample_vault: Path):
    _populate(sample_vault)
    runner = CliRunner()
    result = runner.invoke(cli, ["issues", "list", "--vault", str(sample_vault)])
    assert result.exit_code == 0, result.output
    assert "broken-link" in result.output or "broken-wikilinks" in result.output


def test_issues_list_filter_by_type(sample_vault: Path):
    _populate(sample_vault)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["issues", "list", "--type", "orphan", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0, result.output
    # Should NOT contain a broken-citation issue id
    assert "broken-citation-" not in result.output


def test_issues_show_and_resolve(sample_vault: Path):
    _populate(sample_vault)
    runner = CliRunner()

    list_result = runner.invoke(cli, ["issues", "list", "--vault", str(sample_vault)])
    # Pull the first id from the output (lines look like "  <id> — <title>")
    first_id = next(
        line.strip().split(" ")[0]
        for line in list_result.output.splitlines()
        if line.strip() and not line.strip().startswith(("Found", "id"))
    )

    show_result = runner.invoke(
        cli, ["issues", "show", first_id, "--vault", str(sample_vault)]
    )
    assert show_result.exit_code == 0, show_result.output
    assert first_id in show_result.output

    resolve_result = runner.invoke(
        cli, ["issues", "resolve", first_id, "--vault", str(sample_vault)]
    )
    assert resolve_result.exit_code == 0, resolve_result.output

    show_after = runner.invoke(
        cli, ["issues", "show", first_id, "--vault", str(sample_vault)]
    )
    assert "resolved" in show_after.output


def test_issues_wontfix(sample_vault: Path):
    _populate(sample_vault)
    runner = CliRunner()

    list_result = runner.invoke(cli, ["issues", "list", "--vault", str(sample_vault)])
    first_id = next(
        line.strip().split(" ")[0]
        for line in list_result.output.splitlines()
        if line.strip() and not line.strip().startswith(("Found", "id"))
    )

    wf_result = runner.invoke(
        cli, ["issues", "wontfix", first_id, "--vault", str(sample_vault)]
    )
    assert wf_result.exit_code == 0, wf_result.output

    show = runner.invoke(cli, ["issues", "show", first_id, "--vault", str(sample_vault)])
    assert "wontfix" in show.output
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_cli/test_issues_cmd.py -v`
Expected: `Error: No such command 'issues'`.

- [ ] **Step 3: Implement the `issues` command group**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.group()
def issues() -> None:
    """Query and manage the issue queue."""
    pass


@issues.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--status", default=None, help="Filter by status (open|resolved|wontfix)")
@click.option("--type", "type_filter", default=None, help="Filter by issue type")
def issues_list(vault_path: Path, status: str | None, type_filter: str | None) -> None:
    """List issues in the queue."""
    client = _get_client(vault_path)
    req: dict = {"type": "issues-list"}
    if status:
        req["status_filter"] = status
    if type_filter:
        req["type_filter"] = type_filter
    resp = client.request(req)
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Issues list failed"))

    items = resp["issues"]
    if not items:
        click.echo("No issues found.")
        return

    click.echo(f"Found {len(items)} issue(s):\n")
    for item in items:
        click.echo(f"  {item['id']} — {item['title']}")
        click.echo(f"    type: {item['type']} | status: {item['status']} | page: {item['page']}")


@issues.command("show")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def issues_show(issue_id: str, vault_path: Path) -> None:
    """Show full details of a single issue."""
    client = _get_client(vault_path)
    resp = client.request({"type": "issues-get", "id": issue_id})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Issue not found"))

    issue = resp["issue"]
    click.echo(f"id:          {issue['id']}")
    click.echo(f"type:        {issue['type']}")
    click.echo(f"status:      {issue['status']}")
    click.echo(f"page:        {issue['page']}")
    click.echo(f"detected_by: {issue['detected_by']}")
    click.echo(f"created:     {issue['created']}")
    click.echo()
    click.echo(issue['title'])
    click.echo()
    click.echo(issue['body'])


def _set_status(issue_id: str, vault_path: Path, status: str) -> None:
    client = _get_client(vault_path)
    resp = client.request({"type": "issues-update", "id": issue_id, "status": status})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Update failed"))
    click.echo(f"{issue_id} → {status}")


@issues.command("resolve")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def issues_resolve(issue_id: str, vault_path: Path) -> None:
    """Mark an issue as resolved."""
    _set_status(issue_id, vault_path, "resolved")


@issues.command("wontfix")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def issues_wontfix(issue_id: str, vault_path: Path) -> None:
    """Mark an issue as wontfix."""
    _set_status(issue_id, vault_path, "wontfix")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_cli/test_issues_cmd.py -v`
Expected: All four tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_issues_cmd.py
git commit -m "feat: llm-wiki issues command group (list/show/resolve/wontfix)"
```

---

### Task 16: End-to-end integration test

**Files:**
- Create: `tests/test_audit/test_audit_integration.py`

This is a fresh end-to-end check that exercises the full path: vault → daemon → lint → issues stored → issues queried → resolved → re-lint stays quiet about the resolved one.

- [ ] **Step 1: Write failing test**

```python
# tests/test_audit/test_audit_integration.py
"""End-to-end: vault → daemon → lint → query → resolve → re-lint."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_full_lint_lifecycle(sample_vault: Path, tmp_path: Path):
    sock_path = tmp_path / "audit-int.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)

        # 1. First lint — populates the queue
        first = client.request({"type": "lint"})
        assert first["status"] == "ok"
        assert first["total_issues"] >= 4
        assert len(first["new_issue_ids"]) == first["total_issues"]

        # 2. List the issues
        listing = client.request({"type": "issues-list"})
        assert listing["status"] == "ok"
        assert len(listing["issues"]) >= 4

        # 3. Pick an issue and resolve it
        target_id = listing["issues"][0]["id"]
        update = client.request(
            {"type": "issues-update", "id": target_id, "status": "resolved"}
        )
        assert update["status"] == "ok"

        # 4. Verify status changed via get
        got = client.request({"type": "issues-get", "id": target_id})
        assert got["issue"]["status"] == "resolved"

        # 5. Filter open issues — resolved one should not appear
        open_only = client.request({"type": "issues-list", "status_filter": "open"})
        open_ids = {i["id"] for i in open_only["issues"]}
        assert target_id not in open_ids

        # 6. Re-lint — should produce zero new issues; resolved one is preserved
        second = client.request({"type": "lint"})
        assert second["new_issue_ids"] == []

        # 7. The resolved one is still resolved (not re-opened by the auditor)
        got2 = client.request({"type": "issues-get", "id": target_id})
        assert got2["issue"]["status"] == "resolved"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run test, expect PASS**

Run: `pytest tests/test_audit/test_audit_integration.py -v`
Expected: PASS — all the underlying machinery is in place from earlier tasks. The auditor's idempotency (Task 11) ensures step 6 produces zero new issues, and `IssueQueue.add` preserves resolved files (Task 3) so the resolved status survives re-runs.

If this test reveals a bug — e.g. the auditor accidentally re-opens resolved issues — fix it in `Auditor.audit()` or `IssueQueue.add()` and add a regression test in `test_auditor.py`. Do NOT special-case in the integration test; the integration test is the canary.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass — no regressions in Phase 1-4 tests, all new Phase 5a tests green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_audit/test_audit_integration.py
git commit -m "test: phase 5a end-to-end lint lifecycle integration test"
```

---

### Task 17: README + roadmap update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Quick Start section**

Add the lint and issues commands to the Quick Start in `README.md` (next to the existing `llm-wiki ingest` example):

```markdown
# Run structural checks (orphans, broken links, missing markers, broken citations)
llm-wiki lint --vault /path/to/your/vault

# Manage the resulting issue queue
llm-wiki issues list --vault /path/to/your/vault
llm-wiki issues show <issue-id> --vault /path/to/your/vault
llm-wiki issues resolve <issue-id> --vault /path/to/your/vault
```

- [ ] **Step 2: Update the Project Structure section**

Add to the package layout under `src/llm_wiki/`:

```
  issues/
    queue.py             # Issue + IssueQueue (filesystem persistence)
  audit/
    checks.py            # Structural checks (orphans, broken links, markers, citations)
    auditor.py           # Auditor + AuditReport
```

- [ ] **Step 3: Update the Roadmap**

Replace the Phase 5 line with sub-phase entries (only 5a is checked):

```markdown
- [x] **Phase 5a: Issue Queue + Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [ ] **Phase 5b: Background Workers + Compliance Review** — Async scheduler, debounced compliance pipeline
- [ ] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [ ] **Phase 5d: Adversary + Talk Pages** — Claim verification, async discussion sidecars
- [ ] **Phase 6: MCP Server** — High-level + low-level tools for agent integration
```

Replace the existing Phase 5 line with these four lines (and remove the now-stale "Phase 5: Maintenance Agents" entry).

- [ ] **Step 4: Add a Documentation reference**

Add to the Documentation list:

```markdown
- **[Phase 5 Roadmap](docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md)** — Master plan for maintenance agents (sub-phases 5a-5d)
- **[Phase 5a Plan](docs/superpowers/plans/2026-04-08-phase5a-issue-queue-auditor-lint.md)** — Implementation plan for issue queue + auditor + lint
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README updates for phase 5a — lint command, roadmap split"
```

---

## Self-review checklist

Before declaring this plan complete, verify:

- [ ] Every check function has at least one happy-path test, one false-positive avoidance test, and one empty-vault test
- [ ] Every daemon route has both a happy-path and an error-path test
- [ ] No task uses placeholder code or "TBD" markers
- [ ] Every code block in tests/impl steps is complete and runnable
- [ ] Type names are consistent across tasks: `Issue`, `IssueQueue`, `CheckResult`, `Auditor`, `AuditReport` — used identically in every task that mentions them
- [ ] No LLM calls anywhere in this sub-phase (verify by grepping for `LLMClient` or `litellm` in the new code)
- [ ] The auditor's idempotency property is exercised by both `test_auditor.py` and `test_audit_integration.py`
- [ ] The conftest's `sample_vault` fixture provides natural test cases for all four checks (orphan: `no-structure`; broken-link: `some-other-page`; missing-markers: `clustering-metrics`; broken-citation: `raw/smith-2026-srna.pdf`)
- [ ] The README roadmap is updated to show the sub-phase split

## Spec sections satisfied by 5a

- §5 Auditor row (programmatic checks; LLM spot-check of citations is deferred to 5d)
- §5 Lint row (CLI command, on-demand subset of auditor checks — in 5a, lint == full audit)
- §5 Issue queue paragraph (`wiki/.issues/` directory of markdown files with frontmatter, retained after resolution)

## Dependencies on other sub-phases

**None.** Phase 5a is the foundation. Sub-phases 5b/5c/5d all consume `IssueQueue` and `Auditor` from this sub-phase.

## What's deferred from this sub-phase

Explicitly out of scope (handled by later sub-phases):

- LLM-based citation spot-checks → **5d** (adversary)
- Scheduled audit runs → **5b** (background worker scheduler)
- Compliance review of edits → **5b**
- New-idea detection from human edits → **5b**
- Tag/summary refinement → **5c**
- Authority scoring → **5c**
- Claim verification against raw sources → **5d**
- Talk pages → **5d**

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-phase5a-issue-queue-auditor-lint.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Either option uses this plan as the input.
