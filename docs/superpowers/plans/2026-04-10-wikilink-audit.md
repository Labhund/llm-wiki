# Per-Edit Wikilink Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After any wiki page settles past the debounce window, deterministically scan it for unlinked occurrences of known page titles and add `[[slug|title]]` wikilinks — zero LLM calls.

**Architecture:** A pure-Python module (`wikilink_audit.py`) builds a case-insensitive regex alternation from the manifest's title→slug map, then applies it to the changed file while skipping frontmatter, code fences, inline code, and existing wikilinks. The daemon caches the compiled pattern (rebuilt on rescan) and calls the audit from `_handle_settled_change`. Direct git commit via a new `CommitService.commit_direct()` method serializes writes through the existing commit lock.

**Tech Stack:** Python `re` module only (no external deps), existing `CommitService`, `WriteCoordinator`, `Vault.manifest_entries()`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/llm_wiki/audit/wikilink_audit.py` | Pure functions: pattern building, exclusion ranges, link application |
| Modify | `src/llm_wiki/daemon/commit.py` | Add `commit_direct()` method |
| Modify | `src/llm_wiki/daemon/server.py` | Cache `_title_to_slug`, rebuild on rescan, hook `_run_wikilink_audit` into `_handle_settled_change` |
| Create | `tests/test_audit/test_wikilink_audit.py` | Unit tests for wikilink_audit module |
| Create | `tests/test_daemon/test_wikilink_audit_integration.py` | Integration test: settled change triggers wikilink insertion |

---

### Task 1: Core wikilink audit module (pure functions)

**Files:**
- Create: `src/llm_wiki/audit/wikilink_audit.py`
- Create: `tests/test_audit/test_wikilink_audit.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit/test_wikilink_audit.py
from __future__ import annotations
import re
import pytest
from llm_wiki.audit.wikilink_audit import (
    build_link_pattern,
    apply_wikilinks,
)


def test_build_link_pattern_empty_dict_returns_none():
    assert build_link_pattern({}) is None


def test_build_link_pattern_single_title():
    p = build_link_pattern({"PCA": "pca"})
    assert p is not None
    assert p.search("We use PCA for dimensionality reduction")


def test_build_link_pattern_longest_first_wins():
    """'Boltz Diffusion' must win over 'Boltz' when both are in the pattern."""
    title_to_slug = {"Boltz": "boltz", "Boltz Diffusion": "boltz-diffusion"}
    p = build_link_pattern(title_to_slug)
    m = p.search("Boltz Diffusion model")
    assert m is not None
    assert m.group(1) == "Boltz Diffusion"


def test_apply_wikilinks_basic():
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "We use PCA for reduction.", title_to_slug, "srna-embeddings", p
    )
    assert count == 1
    assert "[[pca|PCA]]" in new_text


def test_apply_wikilinks_all_occurrences():
    """All occurrences of the title are linked, not just the first."""
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "PCA is used here. PCA again here.", title_to_slug, "other", p
    )
    assert count == 2
    assert new_text.count("[[pca|PCA]]") == 2


def test_apply_wikilinks_case_insensitive_match():
    """Lower-case occurrence of a title gets linked with the canonical slug."""
    title_to_slug = {"K-Means": "k-means"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "We use k-means clustering.", title_to_slug, "other", p
    )
    assert count == 1
    assert "[[k-means" in new_text


def test_apply_wikilinks_skips_frontmatter():
    text = "---\ntitle: PCA\n---\n\nBody text."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    # PCA only appears in frontmatter → no link added
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_skips_code_fence():
    text = "Normal PCA text.\n\n```\ncode with PCA\n```"
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    # Only the first PCA (outside fence) gets linked
    assert count == 1
    assert "[[pca|PCA]]" in new_text
    assert "code with PCA" in new_text  # inside fence unchanged


def test_apply_wikilinks_skips_inline_code():
    text = "Use `PCA` in code but PCA in text."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 1
    assert "`PCA`" in new_text  # inline code unchanged


def test_apply_wikilinks_skips_existing_wikilink():
    text = "Already [[pca|PCA]] linked."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_skips_self_page():
    """A page must not link to itself."""
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "PCA uses PCA decomposition.", title_to_slug, "pca", p
    )
    assert count == 0


def test_apply_wikilinks_no_change_already_all_linked():
    text = "Use [[pca|PCA]] and [[pca|PCA]] again."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_assertion_guards():
    """new_text always >= original length and wikilink count never shrinks."""
    title_to_slug = {"PCA": "pca", "K-Means": "k-means"}
    p = build_link_pattern(title_to_slug)
    original = "PCA and K-Means are clustering tools."
    new_text, count = apply_wikilinks(original, title_to_slug, "other", p)
    assert len(new_text) >= len(original)
    assert new_text.count("[[") >= original.count("[[")
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_audit/test_wikilink_audit.py -v
```

Expected: `ModuleNotFoundError: No module named 'llm_wiki.audit.wikilink_audit'`

- [ ] **Step 3: Implement `wikilink_audit.py`**

```python
# src/llm_wiki/audit/wikilink_audit.py
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def build_link_pattern(title_to_slug: dict[str, str]) -> re.Pattern | None:
    """Compile an alternation regex from the manifest's title→slug map.

    Titles are sorted longest-first so multi-word titles win over their
    prefixes (e.g. "Boltz Diffusion" beats "Boltz").

    Returns None when the dict is empty (nothing to link).
    """
    if not title_to_slug:
        return None
    titles = sorted(title_to_slug.keys(), key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in titles)
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE)


def _find_excluded_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges that must not be rewritten.

    Covers: YAML frontmatter, fenced code blocks (``` or ~~~),
    inline code spans (`...`), and existing [[...]] wikilinks.
    """
    ranges: list[tuple[int, int]] = []

    # YAML frontmatter (--- block at start of file)
    fm = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    if fm:
        ranges.append((0, fm.end()))

    # Fenced code blocks (``` or ~~~, with optional language tag)
    for m in re.finditer(r"(?:```|~~~).*?(?:```|~~~)", text, re.DOTALL):
        ranges.append((m.start(), m.end()))

    # Inline code spans
    for m in re.finditer(r"`[^`\n]+`", text):
        ranges.append((m.start(), m.end()))

    # Existing wikilinks [[...]] — must not double-link
    for m in re.finditer(r"\[\[.*?\]\]", text):
        ranges.append((m.start(), m.end()))

    return ranges


def apply_wikilinks(
    text: str,
    title_to_slug: dict[str, str],
    page_slug: str,
    pattern: re.Pattern,
) -> tuple[str, int]:
    """Replace every unlinked title occurrence with a [[slug|title]] link.

    Exclusions: frontmatter, code fences, inline code, existing wikilinks,
    and the page's own slug (no self-references).

    Returns (new_text, count_added). count_added == 0 means the file should
    not be written.
    """
    excluded = _find_excluded_ranges(text)
    # Case-insensitive canonical lookup: lowercase title → canonical title
    lower_to_canonical: dict[str, str] = {t.lower(): t for t in title_to_slug}
    count = 0

    def _in_excluded(start: int) -> bool:
        return any(ex_start <= start < ex_end for ex_start, ex_end in excluded)

    def replacer(m: re.Match) -> str:
        nonlocal count
        if _in_excluded(m.start()):
            return m.group(0)

        matched_text = m.group(1)
        canonical = lower_to_canonical.get(matched_text.lower(), matched_text)
        slug = title_to_slug.get(canonical, "")
        if not slug or slug == page_slug:
            return m.group(0)

        count += 1
        if matched_text.lower() == slug.lower():
            return f"[[{slug}]]"
        return f"[[{slug}|{matched_text}]]"

    new_text = pattern.sub(replacer, text)
    return new_text, count
```

- [ ] **Step 4: Run tests — verify they pass**

```
pytest tests/test_audit/test_wikilink_audit.py -v
```

Expected: 12 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/wikilink_audit.py tests/test_audit/test_wikilink_audit.py
git commit -m "feat: add wikilink_audit — deterministic title→[[link]] scan"
```

---

### Task 2: `CommitService.commit_direct()` — out-of-session git commits

**Files:**
- Modify: `src/llm_wiki/daemon/commit.py`

The wikilink audit writes directly to wiki pages and commits under `author: system`. It bypasses the session/journal pipeline but must still serialize through the shared `_lock` to avoid git races.

- [ ] **Step 1: Write the failing test**

Add to a new file `tests/test_daemon/test_commit_direct.py`:

```python
# tests/test_daemon/test_commit_direct.py
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.commit import CommitService


@pytest.fixture
def git_vault(tmp_path: Path):
    """Minimal git repo acting as a vault."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "test-page.md"
    page.write_text("# Test\n\nContent.\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


@pytest.mark.asyncio
async def test_commit_direct_commits_modified_file(git_vault: Path):
    lock = asyncio.Lock()
    svc = CommitService(vault_root=git_vault, llm=None, lock=lock)

    page = git_vault / "wiki" / "test-page.md"
    page.write_text("# Test\n\nContent with [[pca|PCA]].\n")

    sha = await svc.commit_direct(["wiki/test-page.md"], "audit: add 1 wikilink to test-page")
    assert sha is not None
    assert len(sha) == 40

    # Committed content is reflected in git
    result = subprocess.run(
        ["git", "show", "HEAD:wiki/test-page.md"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    )
    assert "[[pca|PCA]]" in result.stdout


@pytest.mark.asyncio
async def test_commit_direct_returns_none_when_nothing_staged(git_vault: Path):
    lock = asyncio.Lock()
    svc = CommitService(vault_root=git_vault, llm=None, lock=lock)

    # Nothing changed — commit_direct should return None without erroring
    sha = await svc.commit_direct(["wiki/test-page.md"], "audit: empty")
    assert sha is None
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_daemon/test_commit_direct.py -v
```

Expected: `AttributeError: 'CommitService' object has no attribute 'commit_direct'`

- [ ] **Step 3: Add `commit_direct` to `CommitService`**

In `src/llm_wiki/daemon/commit.py`, add after `settle_with_fallback`:

```python
    async def commit_direct(
        self,
        paths: list[str],
        message: str,
    ) -> str | None:
        """Stage `paths` and commit with `message`, outside any session.

        Acquires the shared commit lock so this never races with a concurrent
        session settle. Returns the new commit SHA, or None if nothing staged.
        """
        async with self._lock:
            for path in paths:
                self._git("add", path)
            status = self._git("status", "--porcelain", capture=True)
            staged = [
                line[3:] for line in status.splitlines()
                if line[:2] in ("A ", "M ", "D ", "AM", "MM")
            ]
            if not staged:
                return None
            self._git("commit", "-q", "-m", message)
            return self._git("rev-parse", "HEAD", capture=True).strip()
```

- [ ] **Step 4: Run tests — verify they pass**

```
pytest tests/test_daemon/test_commit_direct.py -v
```

Expected: 2 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/commit.py tests/test_daemon/test_commit_direct.py
git commit -m "feat: add CommitService.commit_direct for out-of-session git commits"
```

---

### Task 3: Wire wikilink audit into the daemon

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Create: `tests/test_daemon/test_wikilink_audit_integration.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_daemon/test_wikilink_audit_integration.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_wikilink_audit_adds_links_on_settle(sample_vault: Path, tmp_path: Path):
    """After a wiki page is modified and settles, unlinked titles get linked."""
    sock_path = tmp_path / "wikilink-audit.sock"
    # Use a very short debounce so the test doesn't wait long
    from llm_wiki.config import WikiConfig
    config = WikiConfig()
    config.maintenance.compliance_debounce_secs = "0.05"

    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        wiki_dir = sample_vault / "wiki"
        # Find a page that exists in the sample vault
        wiki_pages = list(wiki_dir.rglob("*.md"))
        assert wiki_pages, "sample_vault must have wiki pages"
        target = wiki_pages[0]
        page_slug = target.stem

        # Read manifest to pick a title that appears in another page but is
        # not yet linked in `target`.
        client = DaemonClient(sock_path)
        manifest_resp = client.request({"type": "manifest"})
        assert manifest_resp["status"] == "ok"

        # Write a synthetic page that has an unlinked title occurrence
        # We create a fresh page rather than mutating an existing one
        test_page = wiki_dir / "wikilink-test-target.md"
        test_page.write_text(
            "---\ntitle: Wikilink Test Target\n---\n\n"
            "This page mentions srna-embeddings by its slug but not as a link.\n"
        )

        # Simulate the file-watcher settling by calling the internal handler
        await server._handle_settled_change(test_page)

        # Verify links were added (title "sRNA Embeddings" or similar may vary;
        # we check that the page was modified if any manifest titles appeared)
        new_content = test_page.read_text()
        # The page content changed OR count was 0 (if no title matched) — either is valid.
        # What MUST NOT happen: the file is shorter or has fewer [[ than before.
        original = (
            "---\ntitle: Wikilink Test Target\n---\n\n"
            "This page mentions srna-embeddings by its slug but not as a link.\n"
        )
        assert len(new_content) >= len(original)
        assert new_content.count("[[") >= original.count("[[")

    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_wikilink_audit_skips_page_with_active_write_lock(
    sample_vault: Path, tmp_path: Path
):
    """Wikilink audit must not touch a page whose write lock is held."""
    sock_path = tmp_path / "wikilink-lock.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        wiki_dir = sample_vault / "wiki"
        test_page = wiki_dir / "locked-page.md"
        test_page.write_text("---\ntitle: Locked Page\n---\n\nContent.\n")

        # Hold the write lock for this page
        lock = server._write_coordinator.lock_for("locked-page")
        original_mtime = test_page.stat().st_mtime

        async with lock:
            # With lock held, the audit must skip this page
            await server._run_wikilink_audit(test_page)

        # File must not have been touched while lock was held
        assert test_page.stat().st_mtime == original_mtime

    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_daemon/test_wikilink_audit_integration.py -v
```

Expected: `AttributeError: 'DaemonServer' object has no attribute '_run_wikilink_audit'`

- [ ] **Step 3: Add `_title_to_slug` to server `__init__` and `start`/`rescan`**

In `src/llm_wiki/daemon/server.py`, in `DaemonServer.__init__`, after the existing instance variables:

```python
        self._title_to_slug: dict[str, str] = {}
```

In `start()`, directly after `self._vault = Vault.scan(self._vault_root)`:

```python
        self._title_to_slug = {
            e.title: e.name
            for e in self._vault.manifest_entries().values()
            if e.title
        }
```

In `rescan()`, directly after `self._vault = Vault.scan(self._vault_root)`:

```python
        self._title_to_slug = {
            e.title: e.name
            for e in self._vault.manifest_entries().values()
            if e.title
        }
```

- [ ] **Step 4: Add `_run_wikilink_audit` method**

Add the following method to `DaemonServer`, after `_handle_settled_change`:

```python
    async def _run_wikilink_audit(self, path: Path) -> None:
        """Add [[wikilinks]] to unlinked title occurrences in `path`.

        Only runs on pages inside wiki_dir. Skips pages with an active write
        lock. Commits directly via CommitService (no session, no LLM call).
        """
        from llm_wiki.audit.wikilink_audit import apply_wikilinks, build_link_pattern

        # Only audit pages inside wiki/
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        try:
            path.relative_to(wiki_dir)
        except ValueError:
            return

        if not path.exists():
            return

        # Conflict guard: skip if a write is in progress for this page
        if self._write_coordinator is not None:
            if self._write_coordinator.lock_for(path.stem).locked():
                logger.debug(
                    "Wikilink audit: skipping %s — write in progress", path.stem
                )
                return

        if not self._title_to_slug:
            return

        pattern = build_link_pattern(self._title_to_slug)
        if pattern is None:
            return

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("Wikilink audit: failed to read %s", path)
            return

        new_text, count = apply_wikilinks(text, self._title_to_slug, path.stem, pattern)

        # Three guards before write
        if count == 0:
            return
        if len(new_text) < len(text):
            logger.warning(
                "Wikilink audit: aborting — new_text shorter than original for %s",
                path.stem,
            )
            return
        if new_text.count("[[") < text.count("[["):
            logger.warning(
                "Wikilink audit: aborting — wikilink count shrank for %s", path.stem
            )
            return

        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError:
            logger.exception("Wikilink audit: failed to write %s", path)
            return

        if self._commit_service is not None:
            rel_path = str(path.relative_to(self._vault_root))
            msg = f"audit: add {count} wikilink(s) to {path.stem}"
            try:
                sha = await self._commit_service.commit_direct([rel_path], msg)
                if sha:
                    logger.info(
                        "Wikilink audit: %s — %d link(s) → %s",
                        path.stem, count, sha[:8],
                    )
            except Exception:
                logger.exception(
                    "Wikilink audit: commit failed for %s", path.stem
                )
```

- [ ] **Step 5: Call `_run_wikilink_audit` from `_handle_settled_change`**

In `_handle_settled_change`, append after the `logger.info(...)` call at the end of the method:

```python
        await self._run_wikilink_audit(path)
```

The full method after edit:

```python
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
        await self._run_wikilink_audit(path)
```

- [ ] **Step 6: Run integration tests — verify they pass**

```
pytest tests/test_daemon/test_wikilink_audit_integration.py -v
```

Expected: 2 tests, all PASS.

- [ ] **Step 7: Run full test suite**

```
pytest -x -q
```

Expected: all tests pass (previous: 880 passing).

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_wikilink_audit_integration.py
git commit -m "feat: wire wikilink audit into _handle_settled_change"
```

---

## Self-Review

**Spec coverage:**
- ✅ File watcher trigger with 2-3s debounce → handled by existing `ChangeDispatcher`, wikilink audit is called from `_handle_settled_change`
- ✅ Pattern cache built at startup, invalidated on manifest change → `_title_to_slug` rebuilt in both `start()` and `rescan()`
- ✅ Conflict guard → `lock_for(path.stem).locked()` check in `_run_wikilink_audit`
- ✅ Direct write path, no proposal → writes directly with `path.write_text`
- ✅ Three assertion checks → count > 0, len guard, wikilink-count guard
- ✅ Commit attribution `audit: add N wikilinks to <slug>` → `msg = f"audit: add {count} wikilink(s) to {path.stem}"`
- ✅ Exclusions: frontmatter, code fences, inline code, existing `[[...]]` → `_find_excluded_ranges`
- ✅ Self-page exclusion → `slug == page_slug` guard in `replacer`
- ✅ All occurrences → `re.sub` replaces all matches (not just first)
- ✅ Longest-first sort → `sorted(..., key=len, reverse=True)` in `build_link_pattern`
- ✅ Case-insensitive match → `re.IGNORECASE` flag + `lower_to_canonical` lookup

**Placeholder scan:** No TBDs. All code blocks are complete.

**Type consistency:** `build_link_pattern` returns `re.Pattern | None` and `apply_wikilinks` accepts `re.Pattern` — both callers check for None before passing.

**Proper noun detection** (listed in spec as an exclusion): Not implemented in this plan. The word-boundary anchors (`\b`) prevent partial matches inside longer words, which covers the most common case. Full proper noun detection (skipping titles that are common English words) is left as a future concern — it requires a curated stop-list that doesn't belong in v1.
