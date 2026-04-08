# Phase 6a: Visibility & Severity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Spec reference:** `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md`. Read §"Talk entries gain `severity` and append-only closure", §"Issues gain `severity`", §"New librarian responsibility — talk page summaries", and the §"`read` (enriched)" / §"`search` (enriched)" / §"`lint` (enriched)" subsections of "Three modified routes" before starting Task 1. Note especially the **2026-04-08 changelog entry** explaining why talk pages use markdown-with-HTML-comment metadata rather than YAML.
>
> **Prerequisites:** Phases 1–5d must be merged. This plan modifies code that 5a (`IssueQueue`), 5b (`compliance`, `IntervalScheduler`), 5c (`LibrarianAgent`), and 5d (`AdversaryAgent`, `TalkPage`) put in place. No new packages — every change is to existing modules. Phase 6a is independently shippable: when this plan is done, the maintenance loop becomes visible to CLI users (severity-aware lint output, talk-page summaries) without depending on the write surface or the MCP server that come in 6b/6c.

**Goal:** Make the wiki's maintenance backlog visible to active agents (and CLI users) by adding severity to issues and talk entries, making talk-page closure work via append-only `resolves` references, teaching the librarian to summarize stale talk pages, and enriching the daemon's `read` / `search` / `lint` responses to surface issues, talk digests, search snippets, and a vault-wide attention map.

**Architecture:** Eleven existing files modified, two new modules. The `TalkEntry` dataclass gains `index`, `severity`, and `resolves` fields; `TalkPage` parser/writer learns to round-trip an `<!-- key:value -->` metadata comment on the entry header line and to compute the open set in pure Python. The `Issue` dataclass gains a `severity` field; the auditor sets it per check type. The librarian gains a `refresh_talk_summaries()` method backed by a small new `TalkSummaryStore` (JSON sidecar in the state dir) and registers a new `talk_summary` worker through the existing scheduler. Daemon routes `read`, `search`, and `lint` are enriched in place — `read` folds in issue + talk digests for the page, `search` adds snippet matches with line numbers and the nearest preceding heading, `lint` returns a vault-wide `attention_map`. The `talk-append` route gains `severity` and `resolves` request fields.

**Tech Stack:** Python 3.11+, pytest-asyncio, existing `IssueQueue`/`TalkPage`/`LibrarianAgent`/`LLMClient`/`LLMQueue`. **All new LLM calls use `priority="maintenance"`.** No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  config.py                       # MODIFIED: extend MaintenanceConfig with talk_summary fields
  issues/
    queue.py                      # MODIFIED: add severity field; serialize/parse it; default "minor"
  audit/
    checks.py                     # MODIFIED: each check passes severity= when constructing Issue
  talk/
    page.py                       # MODIFIED: TalkEntry gains index/severity/resolves; parser/writer
                                  #           round-trips HTML-comment metadata; compute_open_set()
  adversary/
    agent.py                      # MODIFIED: pass severity="critical" on talk posts for ambiguous verdicts
  librarian/
    talk_summary.py               # NEW: TalkSummaryStore (sidecar JSON) + summarize_open_entries() helper
    agent.py                      # MODIFIED: refresh_talk_summaries() method
    prompts.py                    # MODIFIED: compose_talk_summary_messages() + parse_talk_summary()
  search/
    tantivy_backend.py            # MODIFIED: stored=True on body field; new search_with_snippets() returning matches
  daemon/
    server.py                     # MODIFIED: enriched read/search/lint routes; talk-append accepts
                                  #           severity+resolves; register talk_summary worker

tests/
  test_issues/
    test_queue.py                 # MODIFIED: severity round-trip + legacy default tests
  test_audit/
    test_checks.py                # MODIFIED: assert severity on each issue type
  test_talk/
    test_page.py                  # MODIFIED: HTML-comment round-trip, compute_open_set, legacy parsing
  test_adversary/
    test_agent.py                 # MODIFIED: assert severity on adversary's talk posts
  test_librarian/
    test_talk_summary.py          # NEW: TalkSummaryStore + summarize helper tests
    test_agent.py                 # MODIFIED: refresh_talk_summaries threshold + rate-limit tests
  test_search/
    test_tantivy.py               # MODIFIED: snippet extraction tests
  test_daemon/
    test_server.py                # MODIFIED: enriched read/search/lint response shapes
    test_talk_route.py            # MODIFIED: talk-append accepts severity + resolves
```

**Type flow across tasks:**

- `config.MaintenanceConfig` gains `talk_summary_min_new_entries: int = 5` and `talk_summary_min_interval_seconds: int = 3600`. Used by Task 9 (librarian method) and Task 10 (scheduler registration).
- `issues.queue.Issue` gains `severity: str = "minor"`. Persisted to YAML frontmatter alongside the existing fields. The full vocabulary is `critical | moderate | minor | suggestion | new_connection`, but auditor checks only use `critical | moderate | minor`.
- `audit.checks.find_*` functions take no new parameters. Each one constructs `Issue(..., severity=<per-check default>, ...)` directly. Mapping: `find_orphans → "minor"`, `find_broken_wikilinks → "moderate"`, `find_missing_markers → "minor"`, `find_broken_citations → "critical"`.
- `talk.page.TalkEntry` becomes:
  ```python
  @dataclass
  class TalkEntry:
      index: int                      # 1-based, positional, computed at load time
      timestamp: str
      author: str
      body: str
      severity: str = "suggestion"    # critical|moderate|minor|suggestion|new_connection
      resolves: list[int] = field(default_factory=list)
  ```
  `index` is **not** stored in the file — `TalkPage.load()` assigns it from chronological position. Callers passing a `TalkEntry` to `TalkPage.append()` may set `index=0` (or any sentinel); the writer ignores it because it never serializes the index.
- `talk.page.TalkPage` gains:
  - `compute_open_set(entries: list[TalkEntry]) -> list[TalkEntry]` — pure-Python resolver. Walks entries forward; an entry is "open" iff no later entry's `resolves` list contains its `index`. Returns the open subset in chronological order.
  - `append(entry: TalkEntry)` — writer extended to emit the optional `<!-- severity:..., resolves:[...] -->` comment when fields are non-default.
- `librarian.talk_summary.TalkSummaryStore`: JSON sidecar at `<state_dir>/talk_summaries.json` keyed by page name. Each entry: `{"summary": str, "last_max_index": int, "last_summary_ts": str}`. The `last_max_index` field is a **high-water mark** — the maximum entry `index` present in the talk file at the moment of the last summary. The librarian uses it to count new arrivals (`entry.index > last_max_index`) so closures between runs don't mask new entries by lowering the open count. Atomic-replace writes (same pattern as `ManifestOverrides`).
- `librarian.talk_summary.summarize_open_entries(open_entries, llm) -> str`: async helper. Calls the cheap maintenance LLM with a 2-sentence-summary prompt; returns the summary string. Falls back to a deterministic 1-line string built from entry counts if the LLM fails.
- `librarian.agent.LibrarianAgent.refresh_talk_summaries() -> int`: scans every `*.talk.md` in the wiki, loads entries, computes the open set, decides whether to summarize based on `count(open entries with index > last_max_index) >= threshold` AND `(now - last_summary_ts) >= min_interval`. The threshold counts new arrivals (by index, against the high-water mark) so closures of older entries between runs do not mask new entries by lowering the open count. After summarizing, sets `last_max_index` to `max(entry.index for entry in entries)`. Returns the number of pages summarized.
- `search.tantivy_backend.TantivyBackend` gains `search_with_snippets(query, limit, vault_root) -> list[SnippetSearchResult]`. The result type adds a `matches: list[SnippetMatch]` field where each `SnippetMatch` has `line: int`, `before: str`, `match: str`, `after: str`. Implementation reads the page file from `vault_root` and runs an in-Python case-insensitive search for each query token, computing the nearest preceding `^##` heading for the `before` field.
- `daemon.server.DaemonServer._handle_read` enriches its existing `{"status": "ok", "content": ...}` response with `"issues": {...}` and `"talk": {...}` blocks. The shape is the one in the spec's §`read` (enriched).
- `daemon.server.DaemonServer._handle_search` calls `search_with_snippets` instead of `search`, attaches the `matches` array per result.
- `daemon.server.DaemonServer._handle_lint` extends its existing structural-checks output with an `attention_map` block aggregating issue + talk severity counts vault-wide.
- `daemon.server.DaemonServer._handle_talk_append` accepts optional `severity` (default `"suggestion"`) and `resolves` (default `[]`) request fields and passes them through to the new `TalkEntry`.
- `daemon.server.DaemonServer._register_maintenance_workers` registers a new `talk_summary` worker that calls `LibrarianAgent.refresh_talk_summaries()`.

**Cross-cutting reminders:**
- Talk entries are append-only. Never mutate prior entries. `resolves` is a closure mechanism, not an edit mechanism (PHILOSOPHY.md Principle 4).
- The librarian's talk-summary call uses `priority="maintenance"` and falls back deterministically if the LLM is unreachable.
- Daemon routes that already exist must stay backward-compatible: enriched fields are added to existing responses, never replace them. CLI commands that read `wiki_search`/`wiki_read`/`wiki_lint` responses today must keep working without changes.
- Empty vaults, empty talk pages, and pages with no issues must all return well-shaped responses (empty arrays, zero counts) — never raise.
- `wiki/` is the canonical wiki directory under `vault_root`. Always derive it via `self._vault_root / self._config.vault.wiki_dir.rstrip("/")`, matching existing code in `daemon/server.py`.
- **Snippet `before` field semantics (Task 12, intentional spec deviation).** The spec's §"`search` (enriched)" example shows `before` as ±1 line of context around the match. The implementation puts the **nearest preceding heading text** there instead — better than literal previous-line context because it gives the agent structural framing for filtering ("the match is in the wrong section, skip"). This is an intentional improvement on the spec.
- **Known debt: `tests/test_daemon/test_server.py::daemon_server` fixture wiki_dir mismatch.** The original fixture uses default config (`wiki_dir="wiki/"`) against `sample_vault` (which places pages directly under `tmp_path` in cluster subdirectories — no `wiki/` prefix). The mismatch is latent because no existing test round-trips through the issue queue. Phase 6a adds a `phase6a_daemon_server` fixture (Task 11) with `wiki_dir=""` to align the two for the new tests; fixing the original fixture is **out of scope for Phase 6a** because the existing tests have not been audited for dependence on it.

---

### Task 1: Configuration extensions

**Files:**
- Modify: `src/llm_wiki/config.py:60-69` (extend `MaintenanceConfig`)
- Modify: `tests/test_config.py` (round-trip the new fields)

This is the smallest task and unblocks several others — the librarian (Task 9) and the scheduler (Task 10) both need these knobs to exist.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_maintenance_config_has_talk_summary_defaults():
    """Phase 6a adds talk-page summary refresh fields with safe defaults."""
    from llm_wiki.config import WikiConfig

    cfg = WikiConfig()
    assert cfg.maintenance.talk_summary_min_new_entries == 5
    assert cfg.maintenance.talk_summary_min_interval_seconds == 3600


def test_maintenance_config_loads_talk_summary_overrides(tmp_path):
    """A config file can override the talk-summary defaults."""
    import yaml
    from llm_wiki.config import WikiConfig

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "maintenance": {
            "talk_summary_min_new_entries": 3,
            "talk_summary_min_interval_seconds": 1800,
        }
    }))
    cfg = WikiConfig.load(cfg_file)
    assert cfg.maintenance.talk_summary_min_new_entries == 3
    assert cfg.maintenance.talk_summary_min_interval_seconds == 1800
```

- [ ] **Step 2: Run the test to verify failure**

Run: `pytest tests/test_config.py::test_maintenance_config_has_talk_summary_defaults tests/test_config.py::test_maintenance_config_loads_talk_summary_overrides -v`
Expected: FAIL with `AttributeError: 'MaintenanceConfig' object has no attribute 'talk_summary_min_new_entries'`.

- [ ] **Step 3: Add the fields to `MaintenanceConfig`**

Edit `src/llm_wiki/config.py:60-69`:

```python
@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    auditor_interval: str = "24h"
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_config.py::test_maintenance_config_has_talk_summary_defaults tests/test_config.py::test_maintenance_config_loads_talk_summary_overrides -v`
Expected: PASS.

- [ ] **Step 5: Run the full config test module to confirm no regressions**

Run: `pytest tests/test_config.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: phase 6a config — talk_summary maintenance fields"
```

---

### Task 2: `Issue.severity` round-trip

**Files:**
- Modify: `src/llm_wiki/issues/queue.py` (add field, serialize, parse, default-on-load)
- Modify: `tests/test_issues/test_queue.py` (round-trip + legacy file)

The default is `"minor"` so that any issue file written by 5a code (which doesn't set severity) loads with a sensible value. New writers (Task 3) set severity explicitly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_issues/test_queue.py`:

```python
def test_issue_default_severity_is_minor():
    """Issues without an explicit severity default to 'minor'."""
    issue = _make_issue()
    assert issue.severity == "minor"


def test_queue_round_trips_severity(tmp_path):
    """add() then get() preserves a non-default severity."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()
    issue.severity = "critical"

    queue.add(issue)
    loaded = queue.get(issue.id)
    assert loaded is not None
    assert loaded.severity == "critical"


def test_queue_writes_severity_to_frontmatter(tmp_path):
    """The on-disk YAML carries the severity field."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()
    issue.severity = "moderate"

    path, _ = queue.add(issue)
    text = path.read_text(encoding="utf-8")
    end = text.index("\n---", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["severity"] == "moderate"


def test_queue_legacy_file_without_severity_defaults_to_minor(tmp_path):
    """A 5a-era issue file with no severity field reads as 'minor'."""
    wiki_dir = tmp_path / "wiki"
    issues_dir = wiki_dir / ".issues"
    issues_dir.mkdir(parents=True)
    legacy = issues_dir / "broken-link-foo-abc123.md"
    legacy.write_text(
        "---\n"
        "id: broken-link-foo-abc123\n"
        "type: broken-link\n"
        "status: open\n"
        "title: Wikilink target does not exist\n"
        "page: foo\n"
        "created: 2026-04-01T10:00:00+00:00\n"
        "detected_by: auditor\n"
        "metadata: {}\n"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )

    queue = IssueQueue(wiki_dir)
    loaded = queue.get("broken-link-foo-abc123")
    assert loaded is not None
    assert loaded.severity == "minor"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_issues/test_queue.py -k "severity" -v`
Expected: FAIL with `AttributeError: 'Issue' object has no attribute 'severity'`.

- [ ] **Step 3: Add `severity` to the `Issue` dataclass**

Edit `src/llm_wiki/issues/queue.py:25-41` to insert `severity: str = "minor"` after `metadata`:

```python
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
    severity: str = "minor"
```

- [ ] **Step 4: Persist `severity` in the YAML frontmatter**

Edit the `fm = {...}` block in `IssueQueue.add()` (`src/llm_wiki/issues/queue.py:104-114`) to include severity:

```python
        self.issues_dir.mkdir(parents=True, exist_ok=True)
        fm = {
            "id": issue.id,
            "type": issue.type,
            "status": issue.status,
            "severity": issue.severity,
            "title": issue.title,
            "page": issue.page,
            "created": issue.created,
            "detected_by": issue.detected_by,
            "metadata": issue.metadata,
        }
```

- [ ] **Step 5: Parse `severity` back in `_parse_file`**

Edit `IssueQueue._parse_file()` (`src/llm_wiki/issues/queue.py:182-192`) to read the severity field, defaulting to `"minor"` for legacy files:

```python
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
            severity=fm.get("severity", "minor"),
        )
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_issues/test_queue.py -k "severity" -v`
Expected: PASS for all four severity tests.

- [ ] **Step 7: Run the full queue test module to confirm no regressions**

Run: `pytest tests/test_issues/test_queue.py -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/issues/queue.py tests/test_issues/test_queue.py
git commit -m "feat: phase 6a — Issue.severity field with legacy default"
```

---

### Task 3: Auditor sets severity per check type

**Files:**
- Modify: `src/llm_wiki/audit/checks.py` (each `find_*` function)
- Modify: `tests/test_audit/test_checks.py` (assert severity on each check's output)

Mapping (per the spec's §"Issues gain `severity`"):

| Check | Severity |
|---|---|
| `find_orphans` | `minor` |
| `find_broken_wikilinks` | `moderate` |
| `find_missing_markers` | `minor` |
| `find_broken_citations` | `critical` |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit/test_checks.py`:

```python
def test_find_orphans_severity_is_minor(sample_vault):
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    for issue in result.issues:
        assert issue.severity == "minor"


def test_find_broken_wikilinks_severity_is_moderate(sample_vault):
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    assert result.issues, "expected at least one broken-wikilink in fixture"
    for issue in result.issues:
        assert issue.severity == "moderate"


def test_find_missing_markers_severity_is_minor(sample_vault):
    from llm_wiki.audit.checks import find_missing_markers
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    for issue in result.issues:
        assert issue.severity == "minor"


def test_find_broken_citations_severity_is_critical(tmp_path):
    """Construct a vault with a broken raw citation; severity should be critical."""
    from llm_wiki.audit.checks import find_broken_citations
    (tmp_path / "p.md").write_text(
        "---\ntitle: P\nsource: \"[[raw/missing.pdf]]\"\n---\n\n"
        "## Body\n\nHas a citation [[raw/missing.pdf]].\n"
    )
    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    assert result.issues, "expected a broken-citation issue"
    for issue in result.issues:
        assert issue.severity == "critical"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_audit/test_checks.py -k "severity" -v`
Expected: FAIL — every issue currently defaults to `severity="minor"` from Task 2, so the broken-wikilinks/broken-citations tests fail.

- [ ] **Step 3: Set severity on `find_orphans` issues**

In `src/llm_wiki/audit/checks.py`, edit the `Issue(...)` constructor inside `find_orphans` (~line 33) to add `severity="minor"` (explicit, even though it's the default — the audit table reads more clearly when every check is annotated):

```python
        issues.append(
            Issue(
                id=Issue.make_id("orphan", name, ""),
                type="orphan",
                status="open",
                severity="minor",
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
```

- [ ] **Step 4: Set severity on `find_broken_wikilinks` issues**

In `src/llm_wiki/audit/checks.py`, edit the `Issue(...)` constructor inside `find_broken_wikilinks` (~line 66):

```python
            issues.append(
                Issue(
                    id=Issue.make_id("broken-link", name, target),
                    type="broken-link",
                    status="open",
                    severity="moderate",
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
```

- [ ] **Step 5: Set severity on `find_missing_markers` issues**

In `src/llm_wiki/audit/checks.py`, edit the `Issue(...)` constructor inside `find_missing_markers` (~line 110):

```python
        issues.append(
            Issue(
                id=Issue.make_id("missing-markers", name, ""),
                type="missing-markers",
                status="open",
                severity="minor",
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
```

- [ ] **Step 6: Set severity on `find_broken_citations` issues**

In `src/llm_wiki/audit/checks.py`, edit the `Issue(...)` constructor inside `find_broken_citations` (~line 176):

```python
            issues.append(
                Issue(
                    id=Issue.make_id("broken-citation", name, target),
                    type="broken-citation",
                    status="open",
                    severity="critical",
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
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `pytest tests/test_audit/test_checks.py -k "severity" -v`
Expected: PASS for all four severity tests.

- [ ] **Step 8: Run the full audit test module + integration to confirm no regressions**

Run: `pytest tests/test_audit -v`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "feat: phase 6a — auditor sets severity per check type"
```

---

### Task 3b: Compliance reviewer sets severity on its issues

**Files:**
- Modify: `src/llm_wiki/audit/compliance.py` (two `Issue(...)` sites)
- Modify: `tests/test_audit/test_compliance.py` (assert severity)

The compliance reviewer files two issue types: `new-idea` (a substantive new paragraph that may need integration) and `compliance` with subtype `missing-citation` (an uncited sentence). Both default to `"minor"` after Task 2; per the spec, compliance findings are `"moderate"`. The two existing call sites are at `src/llm_wiki/audit/compliance.py:111` (new-idea) and `src/llm_wiki/audit/compliance.py:300` (missing-citation).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit/test_compliance.py`:

```python
def test_compliance_new_idea_issue_is_moderate(tmp_path):
    """A new-idea issue filed by the compliance reviewer has severity='moderate'."""
    from llm_wiki.audit.compliance import ComplianceReviewer
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue

    page_path = tmp_path / "p.md"
    old = "---\ntitle: P\n---\n\n## Body\n\nShort intro.\n"
    new_paragraph = "x" * 250  # > 200 chars triggers new-idea
    new = (
        "---\ntitle: P\n---\n\n## Body\n\nShort intro.\n\n" + new_paragraph + "\n"
    )
    page_path.write_text(old)

    queue = IssueQueue(tmp_path)
    reviewer = ComplianceReviewer(tmp_path, queue, WikiConfig())
    reviewer.review_change(page_path, old, new)

    new_idea = [i for i in queue.list(status="open") if i.type == "new-idea"]
    assert new_idea, "expected a new-idea issue"
    for issue in new_idea:
        assert issue.severity == "moderate"


def test_compliance_missing_citation_issue_is_moderate(tmp_path):
    """A missing-citation issue filed by the compliance reviewer has severity='moderate'."""
    from llm_wiki.audit.compliance import ComplianceReviewer
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue

    page_path = tmp_path / "p.md"
    old = "---\ntitle: P\n---\n\n## Body\n\nFirst sentence [[raw/a.pdf]].\n"
    new = (
        "---\ntitle: P\n---\n\n## Body\n\nFirst sentence [[raw/a.pdf]].\n\n"
        "An uncited sentence with no citation at all.\n"
    )
    page_path.write_text(old)

    queue = IssueQueue(tmp_path)
    reviewer = ComplianceReviewer(tmp_path, queue, WikiConfig())
    reviewer.review_change(page_path, old, new)

    compliance_issues = [
        i for i in queue.list(status="open")
        if i.type == "compliance" and i.metadata.get("subtype") == "missing-citation"
    ]
    assert compliance_issues, "expected a missing-citation issue"
    for issue in compliance_issues:
        assert issue.severity == "moderate"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_audit/test_compliance.py -k "moderate" -v`
Expected: FAIL — current Issue construction omits `severity`, so the issues default to `"minor"`.

- [ ] **Step 3: Set severity on the `new-idea` issue**

Edit `src/llm_wiki/audit/compliance.py:111-126` (the `Issue(...)` constructor inside `_check_new_idea`):

```python
            issue = Issue(
                id=Issue.make_id("new-idea", result.page, preview),
                type="new-idea",
                status="open",
                severity="moderate",
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
```

- [ ] **Step 4: Set severity on the `missing-citation` issue**

Edit `src/llm_wiki/audit/compliance.py:300-314` (the `Issue(...)` constructor inside `_check_missing_citation`):

```python
            issue = Issue(
                id=Issue.make_id("compliance", result.page, f"missing-citation:{preview}"),
                type="compliance",
                status="open",
                severity="moderate",
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
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `pytest tests/test_audit/test_compliance.py -k "moderate" -v`
Expected: PASS for both severity tests.

- [ ] **Step 6: Run the full audit test module to confirm no regressions**

Run: `pytest tests/test_audit -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance.py
git commit -m "feat: phase 6a — compliance reviewer files moderate-severity issues"
```

---

### Task 4: `TalkEntry` gains `index` / `severity` / `resolves`; HTML-comment round-trip

**Files:**
- Modify: `src/llm_wiki/talk/page.py` (dataclass + parser/writer)
- Modify: `tests/test_talk/test_page.py` (existing tests + new ones)

This is the largest task in Plan A. The dataclass gains three fields. The parser learns to read the optional HTML-comment metadata on the entry header line and to assign positional indices on load. The writer learns to emit the comment when fields are non-default. **No format migration**: legacy files (no comments) keep working — they parse with `severity="suggestion"` and `resolves=[]`, and the index is computed from position.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_talk/test_page.py`:

```python
def test_load_assigns_positional_indices(tmp_path):
    """Loaded entries have 1-based positional indices."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-01T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-02T10:00:00+00:00", "@b", "second"))
    talk.append(TalkEntry(0, "2026-04-03T10:00:00+00:00", "@c", "third"))

    entries = talk.load()
    assert [e.index for e in entries] == [1, 2, 3]


def test_load_legacy_entries_default_severity_suggestion(tmp_path):
    """Pre-Phase-6a entries (no HTML-comment) parse with severity='suggestion'."""
    path = tmp_path / "legacy.talk.md"
    path.write_text(
        "---\npage: legacy\n---\n\n"
        "**2026-04-01T10:00:00+00:00 — @adversary**\n"
        "Body of the legacy entry.\n",
        encoding="utf-8",
    )
    entries = TalkPage(path).load()
    assert len(entries) == 1
    assert entries[0].severity == "suggestion"
    assert entries[0].resolves == []
    assert entries[0].index == 1


def test_append_writes_severity_comment(tmp_path):
    """A non-default severity is emitted as <!-- severity:critical -->."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@adversary",
        body="A serious finding.",
        severity="critical",
    ))
    text = talk.path.read_text(encoding="utf-8")
    assert "<!-- severity:critical -->" in text


def test_append_writes_resolves_comment(tmp_path):
    """A `resolves` list is emitted as resolves:[1,3] in the comment."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-08T10:01:00+00:00", "@b", "second"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:02:00+00:00",
        author="@c",
        body="closes 1 and 2",
        resolves=[1, 2],
    ))
    text = talk.path.read_text(encoding="utf-8")
    assert "resolves:[1,2]" in text


def test_append_combines_severity_and_resolves_in_one_comment(tmp_path):
    """Both fields ride in a single <!-- ... --> comment, comma-separated."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "open"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:01:00+00:00",
        author="@b",
        body="closes 1",
        severity="minor",
        resolves=[1],
    ))
    text = talk.path.read_text(encoding="utf-8")
    # Look for a single comment with both fields, in either order
    assert "<!-- severity:minor, resolves:[1] -->" in text


def test_append_omits_comment_for_default_suggestion_no_resolves(tmp_path):
    """The common case (suggestion + no resolves) writes no comment — zero churn."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@a",
        body="just a thought",
    ))
    text = talk.path.read_text(encoding="utf-8")
    # No HTML comment on the header line
    assert "<!--" not in text


def test_round_trip_severity_critical(tmp_path):
    """Write a critical entry, read it back, severity is preserved."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@adversary",
        body="critical finding",
        severity="critical",
    ))
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].severity == "critical"
    assert entries[0].body == "critical finding"


def test_round_trip_resolves_list(tmp_path):
    """Write an entry with resolves=[1,3], read it back, list is preserved."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-08T10:01:00+00:00", "@b", "second"))
    talk.append(TalkEntry(0, "2026-04-08T10:02:00+00:00", "@c", "third"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:03:00+00:00",
        author="@d",
        body="closer",
        resolves=[1, 3],
    ))
    entries = talk.load()
    assert entries[3].resolves == [1, 3]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_talk/test_page.py -v`
Expected: Existing tests fail with `TypeError: __init__() missing 1 required positional argument: 'index'` (because the dataclass signature is changing). New tests fail similarly. Both kinds of failure are expected — Step 3 fixes them.

- [ ] **Step 3: Update the existing tests to pass an `index` argument**

Edit each existing `TalkEntry(...)` call in `tests/test_talk/test_page.py` to pass `index=0` as the first positional argument (the writer ignores it; load() reassigns it positionally). For example, `tests/test_talk/test_page.py:30-34`:

```python
def test_append_creates_file_with_frontmatter(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "srna-embeddings.talk.md")
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-08T15:01:00+00:00",
        author="@adversary",
        body="First entry body.",
    )
    talk.append(entry)
    ...
```

Apply the same edit (`index=0` as first arg) to every other existing `TalkEntry(...)` constructor call in the file. Use keyword arguments to keep the call sites readable.

- [ ] **Step 4: Update the `TalkEntry` dataclass**

Edit `src/llm_wiki/talk/page.py:17-22`:

```python
from dataclasses import dataclass, field

@dataclass
class TalkEntry:
    """One chronological entry in a talk page.

    `index` is 1-based and positional — assigned by `TalkPage.load()` from the
    entry's chronological position in the file. It is not stored in the file
    and may be left as 0 by callers constructing entries to pass to `append()`.
    """
    index: int
    timestamp: str
    author: str
    body: str
    severity: str = "suggestion"
    resolves: list[int] = field(default_factory=list)
```

(Keep the existing `import re`, `from pathlib import Path`, and `import yaml` at the top of the file.)

- [ ] **Step 5: Extend the entry-header regex to capture optional metadata**

Edit `src/llm_wiki/talk/page.py:11-14` (replace the `_ENTRY_HEADER_RE` definition):

```python
# Matches an entry header line: **<iso-timestamp> — @<author>**
# with optional HTML-comment metadata: <!-- severity:critical, resolves:[1,2] -->
_ENTRY_HEADER_RE = re.compile(
    r"^\*\*(?P<ts>\S+)\s*[—-]\s*(?P<author>@\S+)\*\*"
    r"(?:\s*<!--\s*(?P<meta>[^>]*?)\s*-->)?\s*$",
    re.MULTILINE,
)
```

- [ ] **Step 6: Add a small helper for parsing the metadata blob**

Add this helper to `src/llm_wiki/talk/page.py` (after the regex, before the `TalkEntry` class):

```python
def _parse_meta(meta_str: str | None) -> tuple[str, list[int]]:
    """Parse a `severity:foo, resolves:[1,2]` metadata blob.

    Returns (severity, resolves). Missing keys default to ("suggestion", []).
    Whitespace and key order are tolerant. Invalid blobs return defaults.
    """
    if not meta_str:
        return "suggestion", []

    severity = "suggestion"
    resolves: list[int] = []

    # Split top-level by comma — but not inside [...] which holds the resolves list.
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in meta_str:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())

    for part in parts:
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "severity":
            severity = value
        elif key == "resolves":
            inner = value.strip("[]")
            if inner:
                try:
                    resolves = [int(x.strip()) for x in inner.split(",") if x.strip()]
                except ValueError:
                    resolves = []
    return severity, resolves
```

- [ ] **Step 7: Update `TalkPage.load()` to assign indices and parse metadata**

Edit the `TalkPage.load()` method in `src/llm_wiki/talk/page.py` (~lines 66-81):

```python
    def load(self) -> list[TalkEntry]:
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        body = self._strip_frontmatter(text)

        headers = list(_ENTRY_HEADER_RE.finditer(body))
        entries: list[TalkEntry] = []
        for i, match in enumerate(headers):
            ts = match.group("ts")
            author = match.group("author")
            meta = match.group("meta")
            severity, resolves = _parse_meta(meta)
            content_start = match.end()
            content_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
            entry_body = body[content_start:content_end].strip()
            entries.append(TalkEntry(
                index=i + 1,                  # 1-based, positional
                timestamp=ts,
                author=author,
                body=entry_body,
                severity=severity,
                resolves=resolves,
            ))
        return entries
```

- [ ] **Step 8: Add a writer helper that builds the optional comment**

Add this helper to `src/llm_wiki/talk/page.py` (alongside `_parse_meta`):

```python
def _format_meta(severity: str, resolves: list[int]) -> str:
    """Build the optional `<!-- ... -->` suffix for an entry header line.

    Returns an empty string for the default case (severity='suggestion',
    no resolves) so the writer emits the same shape as pre-Phase-6a files.
    """
    parts: list[str] = []
    if severity != "suggestion":
        parts.append(f"severity:{severity}")
    if resolves:
        joined = ",".join(str(i) for i in resolves)
        parts.append(f"resolves:[{joined}]")
    if not parts:
        return ""
    return f" <!-- {', '.join(parts)} -->"
```

- [ ] **Step 9: Update `TalkPage.append()` to emit the comment**

Edit the `TalkPage.append()` method in `src/llm_wiki/talk/page.py` (~lines 83-100):

```python
    def append(self, entry: TalkEntry) -> None:
        """Append a new entry, creating the file with frontmatter if missing.

        The caller's `entry.index` is ignored — indices are positional and
        get assigned by `load()`. The optional severity/resolves fields ride
        in an HTML comment on the header line; the default case writes the
        same shape as pre-Phase-6a files.
        """
        meta_suffix = _format_meta(entry.severity, entry.resolves)
        block = (
            f"\n**{entry.timestamp} — {entry.author}**{meta_suffix}\n"
            f"{entry.body.strip()}\n"
        )
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            frontmatter = yaml.dump(
                {"page": self.parent_page_slug},
                default_flow_style=False,
            ).strip()
            self._path.write_text(
                f"---\n{frontmatter}\n---\n{block}", encoding="utf-8"
            )
        else:
            existing = self._path.read_text(encoding="utf-8").rstrip()
            self._path.write_text(existing + "\n" + block, encoding="utf-8")
```

- [ ] **Step 10: Run the talk test module to verify everything passes**

Run: `pytest tests/test_talk/test_page.py -v`
Expected: All tests pass — both the updated existing ones and the new ones.

- [ ] **Step 11: Update the two known external `TalkEntry` constructor sites**

Adding the required `index` parameter to `TalkEntry` is a signature change, so any code that constructs `TalkEntry` outside `talk/page.py` will fail with `TypeError: __init__() missing 1 required positional argument: 'index'`. There are exactly two such sites in the codebase (verified by `grep -rn "TalkEntry(" src/llm_wiki/`):

1. `src/llm_wiki/adversary/agent.py:202` — adversary's ambiguous-verdict talk post. Task 6 will revise this same constructor to add `severity="critical"`; for now, just add `index=0` so the tree compiles.

2. `src/llm_wiki/daemon/server.py:_handle_talk_append` (~line 421) — the daemon's talk-append route. Task 7 will revise this same constructor to read severity/resolves from the request; for now, just add `index=0`.

Edit `src/llm_wiki/adversary/agent.py:202` (the existing `TalkEntry(...)` call inside the ambiguous-verdict handler):

```python
        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=now.isoformat(),
            author="@adversary",
            body=(
                f"Checked claim against [[{claim.citation}]] — verdict is ambiguous.\n\n"
                f"> {claim.text}\n\n"
                f"{explanation}"
            ),
        )
        talk.append(entry)
```

Edit `src/llm_wiki/daemon/server.py:_handle_talk_append` (the existing `TalkEntry(...)` call ~line 421):

```python
        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            author=request["author"],
            body=request["body"],
        )
        talk.append(entry)
```

- [ ] **Step 12: Run the dependent test modules to verify the regressions are fixed**

Run: `pytest tests/test_talk tests/test_adversary tests/test_daemon/test_talk_route.py -v`
Expected: All tests pass. The constructor regressions from Step 9 are now fixed by Step 11's `index=0` additions.

- [ ] **Step 13: Run the full test suite to confirm no other regressions**

Run: `pytest -q`
Expected: All tests pass. The signature change is now fully propagated through every call site in the codebase.

- [ ] **Step 14: Commit**

```bash
git add src/llm_wiki/talk/page.py tests/test_talk/test_page.py \
        src/llm_wiki/adversary/agent.py src/llm_wiki/daemon/server.py
git commit -m "feat: phase 6a — TalkEntry gains index/severity/resolves with HTML-comment round-trip"
```

---

### Task 5: `TalkPage.compute_open_set` resolver

**Files:**
- Modify: `src/llm_wiki/talk/page.py` (add classmethod or module function)
- Modify: `tests/test_talk/test_page.py` (closure semantics)

This is the pure-Python resolver that powers everything downstream: the `wiki_read` digest excludes resolved entries, the librarian's summary input excludes them, and `wiki_lint`'s attention map counts only the open ones.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_talk/test_page.py`:

```python
def test_compute_open_set_no_resolves_returns_all():
    """With no resolves, every entry is open."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "third"),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 2, 3]


def test_compute_open_set_single_closure():
    """An entry with resolves=[1] removes entry 1 from the open set."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 1", resolves=[1]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [2]


def test_compute_open_set_multi_closure():
    """resolves=[1,3] closes entries 1 and 3 in one shot."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "third"),
        TalkEntry(4, "t4", "@d", "closer", resolves=[1, 3]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [2, 4]


def test_compute_open_set_resolver_itself_remains_open():
    """The resolving entry is itself open until something else closes it."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 1", resolves=[1]),
    ]
    open_set = compute_open_set(entries)
    assert 2 in [e.index for e in open_set]


def test_compute_open_set_chained_closures():
    """Entry 3 closes entry 2, entry 4 closes entry 3 — only 1 and 4 are open."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "closes 2", resolves=[2]),
        TalkEntry(4, "t4", "@d", "closes 3", resolves=[3]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 4]


def test_compute_open_set_resolves_pointing_at_unknown_index_is_ignored():
    """A resolves reference to a non-existent index is harmless."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 99", resolves=[99]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 2]


def test_compute_open_set_empty():
    from llm_wiki.talk.page import compute_open_set
    assert compute_open_set([]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_talk/test_page.py -k "compute_open_set" -v`
Expected: FAIL with `ImportError: cannot import name 'compute_open_set'`.

- [ ] **Step 3: Implement `compute_open_set`**

Add this top-level function to `src/llm_wiki/talk/page.py` (after the helpers, before or after the `TalkPage` class — placement is style):

```python
def compute_open_set(entries: list[TalkEntry]) -> list[TalkEntry]:
    """Return the subset of `entries` that are not closed by any later entry.

    A `TalkEntry` is closed iff some entry with a strictly greater `index`
    references it via its `resolves` list. Walks entries forward in pure
    Python — no LLM calls, no I/O. Order of the returned list is the same
    as the input (chronological).
    """
    closed: set[int] = set()
    for entry in entries:
        for target in entry.resolves:
            closed.add(target)
    return [e for e in entries if e.index not in closed]
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_talk/test_page.py -k "compute_open_set" -v`
Expected: PASS for all seven `compute_open_set` tests.

- [ ] **Step 5: Run the full talk module to confirm no regressions**

Run: `pytest tests/test_talk -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/talk/page.py tests/test_talk/test_page.py
git commit -m "feat: phase 6a — compute_open_set resolver for talk-page closure"
```

---

### Task 6: Adversary sets severity on its talk posts

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py:201-211` (the only `TalkEntry(...)` constructor)
- Modify: `tests/test_adversary/test_agent.py` (assert severity)

The adversary posts to talk pages on the `ambiguous` verdict (per the verdict-dispatch table in `src/llm_wiki/adversary/agent.py:40-49`). Per the spec, the severity for an adversary's "ambiguous" verdict in a talk post is `critical` — the user/agent on the read side needs to know an adversary couldn't decide. The current call site is in `_post_ambiguous_to_talk_page` (or similarly named) at `src/llm_wiki/adversary/agent.py:201-211`:

```python
talk = TalkPage.for_page(page_path)
entry = TalkEntry(
    timestamp=now.isoformat(),
    author="@adversary",
    body=(
        f"Checked claim against [[{claim.citation}]] — verdict is ambiguous.\n\n"
        f"> {claim.text}\n\n"
        f"{explanation}"
    ),
)
talk.append(entry)
```

After Task 4 this constructor signature gained the `index` parameter; Task 4 Step 12 already inserted `index=0` here as a regression fix. Task 6 just adds `severity="critical"` alongside it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adversary/test_agent.py` (use the existing test setup pattern in the file as a reference):

```python
@pytest.mark.asyncio
async def test_adversary_talk_post_carries_critical_severity(tmp_path, monkeypatch):
    """When the adversary posts an ambiguous verdict to a talk page, the entry's
    severity is 'critical' — surfaced inline in wiki_read so the agent sees it."""
    from llm_wiki.adversary.agent import AdversaryAgent
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.talk.page import TalkPage
    from llm_wiki.vault import Vault

    # Build a minimal vault with one page that has a verifiable claim.
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "src.txt").write_text("ambiguous source content")
    (tmp_path / "p.md").write_text(
        "---\ntitle: P\nsource: \"[[raw/src.txt]]\"\n---\n\n"
        "## Body\n\nA testable claim [[raw/src.txt]].\n"
    )
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)

    # Mock the LLM to return an `ambiguous` verdict for the one claim.
    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            from llm_wiki.traverse.llm_client import LLMResponse
            return LLMResponse(
                content='{"verdict": "ambiguous", "confidence": 0.5, "explanation": "unclear"}',
                tokens_used=10,
            )

    cfg = WikiConfig()
    agent = AdversaryAgent(vault, tmp_path, MockLLM(), queue, cfg)
    await agent.run()

    # The adversary should have posted to p.talk.md with severity=critical.
    talk = TalkPage.for_page(tmp_path / "p.md")
    entries = talk.load()
    assert len(entries) >= 1
    assert any(e.severity == "critical" for e in entries)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_adversary/test_agent.py::test_adversary_talk_post_carries_critical_severity -v`
Expected: FAIL — currently the adversary constructs `TalkEntry` without `severity`, so it defaults to `"suggestion"`.

- [ ] **Step 3: Add `severity="critical"` to the adversary's `TalkEntry` construction**

Edit `src/llm_wiki/adversary/agent.py:201-211`. The `TalkEntry(...)` constructor inside the ambiguous-verdict handler already has `index=0` from Task 4 Step 12; add `severity="critical"`:

```python
        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=now.isoformat(),
            author="@adversary",
            body=(
                f"Checked claim against [[{claim.citation}]] — verdict is ambiguous.\n\n"
                f"> {claim.text}\n\n"
                f"{explanation}"
            ),
            severity="critical",
        )
        talk.append(entry)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_adversary/test_agent.py::test_adversary_talk_post_carries_critical_severity -v`
Expected: PASS.

- [ ] **Step 5: Run the full adversary test module + integration to confirm no regressions**

Run: `pytest tests/test_adversary -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_agent.py
git commit -m "feat: phase 6a — adversary posts critical-severity talk entries"
```

---

### Task 7: Daemon `talk-append` route accepts `severity` and `resolves`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:_handle_talk_append` (~lines 406-428)
- Modify: `tests/test_daemon/test_talk_route.py` (round-trip via the daemon)

This is the daemon-side surface that Phase 6c's `wiki_talk_post` MCP tool will eventually wrap. Phase 6a just makes the route accept the fields.

**Layout note.** `_handle_talk_append` resolves pages via `wiki_dir / f"{request['page']}.md"` — a flat layout. The existing tests in `tests/test_daemon/test_talk_route.py` already work around this with a per-test fresh-server pattern (`_vault_with_page_and_talk(tmp_path)` builds a flat vault under `tmp_path/wiki/`). New tests follow the same pattern instead of the `daemon_server` fixture from `test_server.py`, which uses the nested `sample_vault`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_talk_route.py`:

```python
@pytest.mark.asyncio
async def test_talk_append_accepts_severity_field(tmp_path: Path):
    """The talk-append route persists a non-default severity."""
    from llm_wiki.talk.page import TalkPage

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@adversary",
            "body": "A critical contradiction.",
            "severity": "critical",
        })
        assert resp["status"] == "ok"

        talk = TalkPage.for_page(wiki / "p.md")
        entries = talk.load()
        assert len(entries) == 1
        assert entries[0].severity == "critical"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_accepts_resolves_field(tmp_path: Path):
    """The talk-append route persists a `resolves` list; compute_open_set
    excludes the closed entries."""
    from llm_wiki.talk.page import TalkPage, compute_open_set

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        for body in ("first", "second"):
            resp = client.request({
                "type": "talk-append",
                "page": "p",
                "author": "@a",
                "body": body,
            })
            assert resp["status"] == "ok"

        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@b",
            "body": "closes 1",
            "resolves": [1],
        })
        assert resp["status"] == "ok"

        talk = TalkPage.for_page(wiki / "p.md")
        entries = talk.load()
        open_set = compute_open_set(entries)
        open_indices = [e.index for e in open_set]
        assert 1 not in open_indices
        assert 2 in open_indices
        assert 3 in open_indices  # the closing entry itself stays open
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_rejects_non_integer_resolves(tmp_path: Path):
    """A `resolves` value that isn't a list of ints returns a clean error."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@a",
            "body": "bad",
            "resolves": ["not-an-int"],
        })
        assert resp["status"] == "error"
        assert "resolves" in resp["message"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_talk_route.py -k "severity or resolves" -v`
Expected: FAIL — the route currently ignores the `severity` and `resolves` fields, so they don't round-trip.

- [ ] **Step 3: Update `_handle_talk_append` to read the new fields**

Edit `src/llm_wiki/daemon/server.py:_handle_talk_append` (~lines 406-428):

```python
    def _handle_talk_append(self, request: dict) -> dict:
        import datetime as _dt
        from llm_wiki.talk.discovery import ensure_talk_marker
        from llm_wiki.talk.page import TalkEntry, TalkPage

        for field_name in ("page", "author", "body"):
            if field_name not in request:
                return {"status": "error", "message": f"Missing required field: {field_name}"}

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{request['page']}.md"
        if not page_path.exists():
            return {"status": "error", "message": f"Page not found: {request['page']}"}

        severity = request.get("severity", "suggestion")
        resolves_raw = request.get("resolves", [])
        # Defensive: coerce to list[int] in case JSON delivers strings.
        try:
            resolves = [int(x) for x in resolves_raw]
        except (TypeError, ValueError):
            return {"status": "error", "message": "resolves must be a list of integers"}

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            author=request["author"],
            body=request["body"],
            severity=severity,
            resolves=resolves,
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        return {"status": "ok"}
```

The route's flat-layout assumption (`wiki_dir / f"{page}.md"`) is preserved from the existing implementation — the new tests in Step 1 use the same flat-layout pattern as `test_talk_route.py`'s existing tests, so this is not a regression. Nested-layout support is a pre-existing limitation, out of scope for Phase 6a.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_talk_route.py -k "severity or resolves" -v`
Expected: PASS.

- [ ] **Step 5: Run the full talk-route test module to confirm no regressions**

Run: `pytest tests/test_daemon/test_talk_route.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_talk_route.py
git commit -m "feat: phase 6a — talk-append route accepts severity + resolves"
```

---

### Task 8: `TalkSummaryStore` and `summarize_open_entries` helper

**Files:**
- Create: `src/llm_wiki/librarian/talk_summary.py` (new module)
- Create: `tests/test_librarian/test_talk_summary.py` (new test file)
- Modify: `src/llm_wiki/librarian/prompts.py` (add `compose_talk_summary_messages` + `parse_talk_summary`)

The store is a JSON sidecar at `<state_dir>/talk_summaries.json` keyed by page name. The summarize helper calls the cheap maintenance LLM with the open entries and returns a 2-sentence summary, with a deterministic fallback.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_librarian/test_talk_summary.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.librarian.talk_summary import (
    TalkSummaryStore,
    summarize_open_entries,
)
from llm_wiki.talk.page import TalkEntry


def test_store_load_missing_file_returns_empty(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "missing.json")
    assert store.get("any-page") is None


def test_store_set_and_get_round_trip(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "store.json")
    store.set("p1", summary="Two unresolved findings.", last_max_index=5,
              last_summary_ts="2026-04-08T10:00:00+00:00")
    store.save()

    reloaded = TalkSummaryStore.load(tmp_path / "store.json")
    record = reloaded.get("p1")
    assert record is not None
    assert record.summary == "Two unresolved findings."
    assert record.last_max_index == 5
    assert record.last_summary_ts == "2026-04-08T10:00:00+00:00"


def test_store_save_writes_atomically(tmp_path):
    """The store uses temp-file-rename so a partial write can't corrupt it."""
    store = TalkSummaryStore.load(tmp_path / "s.json")
    store.set("a", summary="x", last_max_index=1, last_summary_ts="t")
    store.save()
    assert (tmp_path / "s.json").exists()
    payload = json.loads((tmp_path / "s.json").read_text())
    assert "a" in payload


def test_store_delete_removes_entry(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "s.json")
    store.set("a", summary="x", last_max_index=1, last_summary_ts="t")
    store.delete("a")
    assert store.get("a") is None


@pytest.mark.asyncio
async def test_summarize_open_entries_calls_llm(tmp_path):
    """summarize_open_entries() formats entries and asks the LLM for 2 sentences."""
    entries = [
        TalkEntry(1, "2026-04-08T10:00:00+00:00", "@adversary", "First.", severity="critical"),
        TalkEntry(2, "2026-04-08T10:01:00+00:00", "@compliance", "Second.", severity="moderate"),
    ]

    captured: dict = {}

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            from llm_wiki.traverse.llm_client import LLMResponse
            captured["messages"] = messages
            captured["priority"] = priority
            return LLMResponse(
                content="Two unresolved entries: an adversary contradiction and a compliance flag.",
                tokens_used=20,
            )

    summary = await summarize_open_entries(entries, MockLLM())
    assert "unresolved" in summary.lower()
    assert captured["priority"] == "maintenance"
    # The prompt should mention each entry's body
    flat = " ".join(m["content"] for m in captured["messages"])
    assert "First." in flat
    assert "Second." in flat


@pytest.mark.asyncio
async def test_summarize_open_entries_falls_back_on_llm_error():
    """If the LLM raises, return a deterministic summary based on counts."""
    entries = [
        TalkEntry(1, "t", "@a", "first", severity="critical"),
        TalkEntry(2, "t", "@b", "second", severity="moderate"),
    ]

    class FailingLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            raise RuntimeError("model unreachable")

    summary = await summarize_open_entries(entries, FailingLLM())
    assert "2" in summary or "two" in summary.lower()
    assert "critical" in summary.lower() or "moderate" in summary.lower()


@pytest.mark.asyncio
async def test_summarize_open_entries_empty_returns_empty_string():
    summary = await summarize_open_entries([], llm=None)
    assert summary == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_librarian/test_talk_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.librarian.talk_summary'`.

- [ ] **Step 3: Create the `TalkSummaryStore` module**

Create `src/llm_wiki/librarian/talk_summary.py`:

```python
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.talk.page import TalkEntry

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class TalkSummaryRecord:
    """One entry in the talk-summary sidecar.

    `last_max_index` is a high-water mark: the maximum entry index in the
    talk file at the moment of the last summary. The librarian uses it to
    count entries that arrived after the last summary by checking
    `entry.index > last_max_index`. This is robust to closures: if entries
    get resolved between runs, the open count drops but new arrivals are
    still counted, so the threshold is computed against arrivals not net
    state.
    """
    summary: str
    last_max_index: int
    last_summary_ts: str


class TalkSummaryStore:
    """JSON-backed sidecar of librarian-managed talk-page summaries.

    Atomic writes via temp-file-and-rename so concurrent workers cannot
    corrupt the file. Stored at `<state_dir>/talk_summaries.json` and
    rebuildable from the talk pages on rescan (the wiki itself is the
    source of truth — this is just cached LLM output).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, TalkSummaryRecord] = {}

    @classmethod
    def load(cls, path: Path) -> "TalkSummaryStore":
        store = cls(path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return store
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            store._entries[name] = TalkSummaryRecord(
                summary=str(raw.get("summary", "")),
                last_max_index=int(raw.get("last_max_index", 0) or 0),
                last_summary_ts=str(raw.get("last_summary_ts", "")),
            )
        return store

    def get(self, page_name: str) -> TalkSummaryRecord | None:
        return self._entries.get(page_name)

    def set(
        self,
        page_name: str,
        summary: str,
        last_max_index: int,
        last_summary_ts: str,
    ) -> None:
        self._entries[page_name] = TalkSummaryRecord(
            summary=summary,
            last_max_index=last_max_index,
            last_summary_ts=last_summary_ts,
        )

    def delete(self, page_name: str) -> None:
        self._entries.pop(page_name, None)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(rec) for name, rec in self._entries.items()}
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise


async def summarize_open_entries(
    entries: list[TalkEntry],
    llm: "LLMClient | None",
) -> str:
    """Summarize a talk page's open (unresolved) entries in 2 sentences.

    Calls the cheap maintenance LLM via `priority="maintenance"`. Falls back
    to a deterministic count-based summary if the LLM is unreachable or
    raises. Returns "" for an empty input list.
    """
    if not entries:
        return ""

    if llm is None:
        return _deterministic_summary(entries)

    from llm_wiki.librarian.prompts import (
        compose_talk_summary_messages,
        parse_talk_summary,
    )

    try:
        messages = compose_talk_summary_messages(entries)
        response = await llm.complete(
            messages, temperature=0.0, priority="maintenance",
        )
        summary = parse_talk_summary(response.content)
        if summary:
            return summary
    except Exception:
        logger.warning("talk_summary LLM call failed; using deterministic fallback", exc_info=True)

    return _deterministic_summary(entries)


def _deterministic_summary(entries: list[TalkEntry]) -> str:
    """Build a one-line count-based summary as a fallback for LLM failures."""
    by_severity: dict[str, int] = {}
    for e in entries:
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
    parts = [f"{count} {sev}" for sev, count in sorted(by_severity.items())]
    return f"{len(entries)} unresolved talk entries: " + ", ".join(parts) + "."
```

- [ ] **Step 4: Add the prompt helpers to `prompts.py`**

Append to `src/llm_wiki/librarian/prompts.py`:

```python
def compose_talk_summary_messages(entries: "list[TalkEntry]") -> list[dict[str, str]]:
    """Build a 2-message prompt asking for a 2-sentence digest of open talk entries.

    The librarian uses this when refreshing a talk-page summary. Entries are
    formatted compactly so the cheap maintenance model can read them all in
    a single small prompt.
    """
    from llm_wiki.talk.page import TalkEntry  # local import to avoid cycles

    body_lines = []
    for e in entries:
        body_lines.append(
            f"[#{e.index} {e.severity} by {e.author}] {e.body.strip()}"
        )
    body_text = "\n".join(body_lines)

    return [
        {
            "role": "system",
            "content": (
                "You are summarizing the unresolved discussion on a wiki talk page. "
                "Produce a single 2-sentence digest that an active reader can use "
                "to decide whether to investigate further. Do not list individual "
                "entries — synthesize."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Unresolved entries on this talk page:\n\n{body_text}\n\n"
                f"Write a 2-sentence summary."
            ),
        },
    ]


def parse_talk_summary(text: str) -> str:
    """Extract a clean 2-sentence summary from the LLM response.

    The cheap model often wraps its output in quotes or prefixes. Strip
    common decoration. Returns an empty string if the response is empty.
    """
    if not text:
        return ""
    cleaned = text.strip()
    # Strip surrounding quotes
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    # Strip a leading "Summary:" prefix
    for prefix in ("Summary:", "summary:", "SUMMARY:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `pytest tests/test_librarian/test_talk_summary.py -v`
Expected: PASS for all six tests.

- [ ] **Step 6: Run the librarian test module to confirm no regressions**

Run: `pytest tests/test_librarian -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/librarian/talk_summary.py \
        src/llm_wiki/librarian/prompts.py \
        tests/test_librarian/test_talk_summary.py
git commit -m "feat: phase 6a — TalkSummaryStore and summarize_open_entries helper"
```

---

### Task 9: `LibrarianAgent.refresh_talk_summaries()`

**Files:**
- Modify: `src/llm_wiki/librarian/agent.py` (add the method)
- Modify: `tests/test_librarian/test_agent.py` (threshold + rate-limit tests)

The librarian's new responsibility: walk every `*.talk.md`, decide whether to summarize based on the threshold (`talk_summary_min_new_entries`) and rate limit (`talk_summary_min_interval_seconds`), and write the summary to the `TalkSummaryStore`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_librarian/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_refresh_talk_summaries_below_threshold_does_nothing(tmp_path):
    """A talk page with fewer than min_new_entries open entries is skipped."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault, _state_dir_for

    (tmp_path / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(tmp_path / "p.talk.md")
    # Two entries — below the default threshold of 5
    talk.append(TalkEntry(0, "t1", "@a", "first"))
    talk.append(TalkEntry(0, "t2", "@b", "second"))

    cfg = WikiConfig()
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("LLM should not be called below threshold")

    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0

    store = TalkSummaryStore.load(_state_dir_for(tmp_path) / "talk_summaries.json")
    assert store.get("p") is None


@pytest.mark.asyncio
async def test_refresh_talk_summaries_above_threshold_summarizes(tmp_path):
    """When open entries cross the threshold, the LLM is called and the
    summary is persisted to the store. The high-water mark is set to the
    max entry index in the file."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.traverse.llm_client import LLMResponse
    from llm_wiki.vault import Vault, _state_dir_for

    (tmp_path / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(tmp_path / "p.talk.md")
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    cfg = WikiConfig()  # threshold default = 5

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            return LLMResponse(content="Five open entries about validation.", tokens_used=10)

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, MockLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 1

    store = TalkSummaryStore.load(_state_dir_for(tmp_path) / "talk_summaries.json")
    record = store.get("p")
    assert record is not None
    assert "validation" in record.summary
    # high-water mark is the max entry index = 5
    assert record.last_max_index == 5


@pytest.mark.asyncio
async def test_refresh_talk_summaries_robust_to_intervening_closures(tmp_path):
    """Closures between runs lower the open count but should not mask new
    arrivals. The threshold counts entries with index > last_max_index that
    are still open — measuring arrivals, not net open state."""
    import datetime as _dt
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.traverse.llm_client import LLMResponse
    from llm_wiki.vault import Vault, _state_dir_for

    (tmp_path / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(tmp_path / "p.talk.md")

    # First run: 5 entries, all open → summarize, high-water = 5
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    cfg = WikiConfig()
    call_count = {"n": 0}

    class CountingLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            call_count["n"] += 1
            return LLMResponse(content="Summary text.", tokens_used=10)

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, CountingLLM(), queue, cfg)
    assert await agent.refresh_talk_summaries() == 1
    assert call_count["n"] == 1

    # Backdate the rate-limit timestamp so the next run isn't blocked by it
    state_dir = _state_dir_for(tmp_path)
    store = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    rec = store.get("p")
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    store.set("p", summary=rec.summary, last_max_index=rec.last_max_index, last_summary_ts=old_ts)
    store.save()

    # Second run: append 5 NEW entries (indices 6-10), then a closer that resolves 1-4
    for i in range(5, 10):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))
    talk.append(TalkEntry(
        0, "t-closer", "@closer", "closes 1-4", resolves=[1, 2, 3, 4],
    ))
    # Open count is now: entry 5 + entries 6-10 + closer = 7
    # Last summary high-water = 5
    # New entries with index > 5 that are open = entries 6-10 + closer = 6 → above threshold

    assert await agent.refresh_talk_summaries() == 1
    assert call_count["n"] == 2  # LLM called again because new arrivals exceeded threshold

    rec2 = store.load(state_dir / "talk_summaries.json").get("p")
    assert rec2 is not None
    assert rec2.last_max_index == 11  # max index in the file is now 11


@pytest.mark.asyncio
async def test_refresh_talk_summaries_excludes_resolved_entries(tmp_path):
    """Resolved entries are not counted toward the threshold."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault

    (tmp_path / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(tmp_path / "p.talk.md")
    # Five entries, but four of them get resolved → only one open + the resolver
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))
    talk.append(TalkEntry(0, "t-close", "@closer", "closes 1-4", resolves=[1, 2, 3, 4]))

    # Threshold is 5; open entries = 2 (entry 5 + the closer) → below threshold
    cfg = WikiConfig()

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("LLM should not be called — open count is 2, below 5")

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0


@pytest.mark.asyncio
async def test_refresh_talk_summaries_rate_limit_blocks_resummary(tmp_path):
    """A page summarized within `talk_summary_min_interval_seconds` is skipped."""
    import datetime as _dt
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault, _state_dir_for

    (tmp_path / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(tmp_path / "p.talk.md")
    for i in range(6):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    # Pre-populate the store with a recent summary covering only entry 1.
    # This leaves entries 2-6 (= 5 new arrivals) above the threshold, so the
    # threshold check would pass on its own. The recent timestamp must be
    # what blocks the resummary — that's the contract under test.
    state_dir = _state_dir_for(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    store = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    store.set("p", summary="recent", last_max_index=1, last_summary_ts=now_iso)
    store.save()

    cfg = WikiConfig()  # min_interval default = 3600s

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("rate limit should block this call")

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_librarian/test_agent.py -k "refresh_talk_summaries" -v`
Expected: FAIL with `AttributeError: 'LibrarianAgent' object has no attribute 'refresh_talk_summaries'`.

- [ ] **Step 3: Add the method to `LibrarianAgent`**

Append the method to `src/llm_wiki/librarian/agent.py` (inside the `LibrarianAgent` class):

```python
    async def refresh_talk_summaries(self) -> int:
        """Refresh stale talk-page summaries.

        For each `*.talk.md` in the wiki, load entries and compute the open
        set. Summarize via the cheap maintenance LLM iff:
          - the number of OPEN entries with `index > last_max_index` (the
            high-water mark from the last summary) is at least
            `config.maintenance.talk_summary_min_new_entries`. This counts
            new arrivals that are still unresolved, so closures of older
            entries between runs do not mask new arrivals.
          - at least `config.maintenance.talk_summary_min_interval_seconds`
            have passed since the last summary.

        After summarizing, the store's `last_max_index` is set to the
        highest entry index in the file (open or resolved) — that becomes
        the high-water mark for the next run.

        Returns the number of pages whose summary was refreshed.
        """
        import datetime as _dt
        from llm_wiki.librarian.talk_summary import (
            TalkSummaryStore,
            summarize_open_entries,
        )
        from llm_wiki.talk.page import TalkPage, compute_open_set

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        if not wiki_dir.exists():
            return 0

        store = TalkSummaryStore.load(self._state_dir / "talk_summaries.json")
        threshold = self._config.maintenance.talk_summary_min_new_entries
        min_interval = self._config.maintenance.talk_summary_min_interval_seconds
        now = _dt.datetime.now(_dt.timezone.utc)
        refreshed = 0

        for talk_path in sorted(wiki_dir.rglob("*.talk.md")):
            # Skip files inside hidden directories (e.g. .issues)
            rel = talk_path.relative_to(wiki_dir)
            if any(p.startswith(".") for p in rel.parts):
                continue

            talk = TalkPage(talk_path)
            entries = talk.load()
            if not entries:
                continue
            open_entries = compute_open_set(entries)

            # Page slug derives from the talk file's stem
            stem = talk_path.stem
            page_name = stem[: -len(".talk")] if stem.endswith(".talk") else stem
            current_max_index = max(e.index for e in entries)

            existing = store.get(page_name)
            high_water = existing.last_max_index if existing else 0

            # Count NEW unresolved entries: open AND index > high_water.
            # Resilient to closures: a closure between runs only removes
            # entries from open_entries; new arrivals are still counted.
            new_unresolved = sum(1 for e in open_entries if e.index > high_water)
            if new_unresolved < threshold:
                continue

            # Rate limit: don't re-summarize a page within min_interval seconds
            if existing is not None:
                try:
                    last_ts = _dt.datetime.fromisoformat(existing.last_summary_ts)
                except ValueError:
                    last_ts = None
                if last_ts is not None:
                    elapsed = (now - last_ts).total_seconds()
                    if elapsed < min_interval:
                        continue

            try:
                summary = await summarize_open_entries(open_entries, self._llm)
            except Exception:
                logger.exception("Failed to summarize talk page %s", page_name)
                continue
            if not summary:
                continue

            store.set(
                page_name,
                summary=summary,
                last_max_index=current_max_index,
                last_summary_ts=now.isoformat(),
            )
            refreshed += 1

        if refreshed > 0:
            store.save()
        return refreshed
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_librarian/test_agent.py -k "refresh_talk_summaries" -v`
Expected: PASS for all four refresh-talk-summaries tests.

- [ ] **Step 5: Run the full librarian test module to confirm no regressions**

Run: `pytest tests/test_librarian -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/librarian/agent.py tests/test_librarian/test_agent.py
git commit -m "feat: phase 6a — librarian.refresh_talk_summaries() with threshold + rate limit"
```

---

### Task 10: Wire `talk_summary` worker into the scheduler

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:_register_maintenance_workers` (add a registration block)
- Modify: `tests/test_daemon/test_server.py` (assert the worker is registered)

The new worker calls `LibrarianAgent.refresh_talk_summaries()`. It runs on the librarian's interval (`maintenance.librarian_interval`, default 6h) — no separate interval knob, since the rate limit inside `refresh_talk_summaries` already prevents wasted work on hot pages.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon/test_server.py`:

```python
@pytest.mark.asyncio
async def test_daemon_registers_talk_summary_worker(daemon_server):
    """The daemon's scheduler includes a talk_summary worker after Phase 6a."""
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "scheduler-status"})
    assert resp["status"] == "ok"
    worker_names = [w["name"] for w in resp["workers"]]
    assert "talk_summary" in worker_names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_daemon/test_server.py::test_daemon_registers_talk_summary_worker -v`
Expected: FAIL — `talk_summary` is not in the registered worker list.

- [ ] **Step 3: Register the new worker**

Edit `src/llm_wiki/daemon/server.py:_register_maintenance_workers` (~lines 98-201). Add a new `run_talk_summary` coroutine factory and register it:

```python
        async def run_talk_summary() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            count = await agent.refresh_talk_summaries()
            logger.info("Talk summary: refreshed=%d", count)
```

Then add the registration block alongside the existing librarian/auditor/adversary registrations:

```python
        self._scheduler.register(
            ScheduledWorker(
                name="talk_summary",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_talk_summary,
            )
        )
```

(Keep the existing four `self._scheduler.register(...)` blocks. The new one slots in after them.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_daemon/test_server.py::test_daemon_registers_talk_summary_worker -v`
Expected: PASS.

- [ ] **Step 5: Run the full daemon test module to confirm no regressions**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py
git commit -m "feat: phase 6a — register talk_summary worker in scheduler"
```

---

### Task 11: Daemon `read` route enriched with issues + talk digest

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:_handle_read` (~lines 312-322)
- Modify: `tests/test_daemon/test_server.py` (new fixture + assert the new response shape)

**Fixture note for Tasks 11–13.** The existing `daemon_server` fixture starts the server with default config (`vault.wiki_dir = "wiki/"`), but `sample_vault` is a flat tmp_path with pages in cluster subdirectories — there is no `tmp_path/wiki/` directory. As a result, the daemon's `_issue_queue()` and `_read_talk_block()` look at `tmp_path/wiki/.issues/` and `tmp_path/wiki/<page>.talk.md` while these tests need to write to `tmp_path/.issues/` and `tmp_path/bioinformatics/<page>.talk.md`. None of the existing test_server.py tests round-trip through the issue queue, so this latent mismatch hasn't bitten anyone yet. Tasks 11–13 do round-trip, so we add a new fixture that aligns the daemon's wiki_dir with the actual vault root via `WikiConfig(vault=VaultConfig(wiki_dir=""))`.

The enriched response shape (per the spec's §"`read` (enriched)"):

```json
{
  "status": "ok",
  "content": "...",
  "issues": {
    "open_count": 2,
    "by_severity": {"critical": 0, "moderate": 1, "minor": 1},
    "items": [{"id": "...", "severity": "moderate", "title": "...", "body": "..."}]
  },
  "talk": {
    "entry_count": 14,
    "open_count": 5,
    "by_severity": {"critical": 1, "moderate": 0, "minor": 1, "suggestion": 2, "new_connection": 1},
    "summary": "<2-sentence librarian-generated digest of unresolved entries>",
    "recent_critical": [{"index": 12, "ts": "...", "author": "adversary", "body": "..."}],
    "recent_moderate": []
  }
}
```

Critical and moderate entries are inlined verbatim. Everything else collapses into the digest. Resolved entries are excluded from all counts and from `recent_*` (they only show up in `wiki_talk_read`).

- [ ] **Step 1: Add the `phase6a_daemon_server` fixture and write the failing tests**

Append to `tests/test_daemon/test_server.py`:

```python
@pytest_asyncio.fixture
async def phase6a_daemon_server(sample_vault: Path, tmp_path: Path):
    """Daemon server for Phase 6a tests where wiki_dir == vault_root.

    The default config sets wiki_dir='wiki/', but sample_vault places its
    pages in cluster subdirectories under tmp_path with no wiki/ prefix.
    Phase 6a's enriched routes round-trip through the issue queue and the
    talk-page sidecars, so this fixture aligns wiki_dir with the actual
    vault root.
    """
    from llm_wiki.config import VaultConfig, WikiConfig
    sock_path = tmp_path / "p6a.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir=""))
    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    yield server, sock_path
    await server.stop()


@pytest.mark.asyncio
async def test_read_includes_empty_issues_and_talk_blocks(phase6a_daemon_server):
    """Reading a page with no issues / no talk page returns well-shaped empty blocks."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert "issues" in resp
    assert "talk" in resp
    assert resp["issues"]["open_count"] == 0
    assert resp["issues"]["items"] == []
    assert resp["talk"]["entry_count"] == 0
    assert resp["talk"]["open_count"] == 0
    assert resp["talk"]["recent_critical"] == []
    assert resp["talk"]["recent_moderate"] == []


@pytest.mark.asyncio
async def test_read_includes_open_issues(phase6a_daemon_server, sample_vault):
    """When the page has an open issue, it shows up in the read response."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    # wiki_dir == vault_root in this fixture, so the IssueQueue path matches
    # what the daemon's _issue_queue() will look at.
    queue = IssueQueue(sample_vault)
    queue.add(Issue(
        id=Issue.make_id("broken-link", "srna-embeddings", "fake-target"),
        type="broken-link",
        status="open",
        severity="moderate",
        title="Fake broken link",
        page="srna-embeddings",
        body="A test issue.",
        created=Issue.now_iso(),
        detected_by="auditor",
    ))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["issues"]["open_count"] == 1
    assert resp["issues"]["by_severity"]["moderate"] == 1
    assert resp["issues"]["items"][0]["title"] == "Fake broken link"


@pytest.mark.asyncio
async def test_read_inlines_critical_talk_entries(phase6a_daemon_server, sample_vault):
    """Critical and moderate talk entries appear verbatim in `recent_*`."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(
        0, "2026-04-08T10:00:00+00:00", "@adversary",
        "A critical contradiction.", severity="critical",
    ))
    talk.append(TalkEntry(
        0, "2026-04-08T10:01:00+00:00", "@compliance",
        "A moderate concern.", severity="moderate",
    ))
    talk.append(TalkEntry(
        0, "2026-04-08T10:02:00+00:00", "@user",
        "A casual suggestion.", severity="suggestion",
    ))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["talk"]["entry_count"] == 3
    assert resp["talk"]["open_count"] == 3
    assert len(resp["talk"]["recent_critical"]) == 1
    assert resp["talk"]["recent_critical"][0]["body"] == "A critical contradiction."
    assert len(resp["talk"]["recent_moderate"]) == 1
    assert resp["talk"]["recent_moderate"][0]["body"] == "A moderate concern."


@pytest.mark.asyncio
async def test_read_excludes_resolved_talk_entries_from_counts(phase6a_daemon_server, sample_vault):
    """Resolved entries don't count toward open_count or by_severity."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(0, "t1", "@adv", "first", severity="critical"))
    talk.append(TalkEntry(0, "t2", "@user", "closes 1", resolves=[1]))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["talk"]["entry_count"] == 2  # total in file
    assert resp["talk"]["open_count"] == 1   # only the closer is open
    assert resp["talk"]["recent_critical"] == []  # the critical one is resolved
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_server.py -k "read_includes or read_inlines or read_excludes" -v`
Expected: FAIL — `read` currently returns `{"status": "ok", "content": ...}` only.

- [ ] **Step 3: Update `_handle_read` to fold in issues and talk digest**

Edit `src/llm_wiki/daemon/server.py:_handle_read` (~lines 312-322). The new method:

```python
    def _handle_read(self, request: dict) -> dict:
        content = self._vault.read_viewport(
            request["page_name"],
            viewport=request.get("viewport", "top"),
            section=request.get("section"),
            grep=request.get("grep"),
            budget=request.get("budget"),
        )
        if content is None:
            return {"status": "error", "message": f"Page not found: {request['page_name']}"}

        page_name = request["page_name"]
        return {
            "status": "ok",
            "content": content,
            "issues": self._read_issues_block(page_name),
            "talk": self._read_talk_block(page_name),
        }
```

- [ ] **Step 4: Add the issues block builder**

Add this helper method to `DaemonServer`:

```python
    def _read_issues_block(self, page_name: str) -> dict:
        """Build the per-page issues digest folded into wiki_read responses."""
        queue = self._issue_queue()
        all_issues = queue.list(status="open")
        page_issues = [i for i in all_issues if i.page == page_name]

        by_severity: dict[str, int] = {"critical": 0, "moderate": 0, "minor": 0}
        for issue in page_issues:
            by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1

        items = [
            {
                "id": issue.id,
                "severity": issue.severity,
                "title": issue.title,
                "body": issue.body,
            }
            for issue in page_issues
        ]
        return {
            "open_count": len(page_issues),
            "by_severity": by_severity,
            "items": items,
        }
```

- [ ] **Step 5: Add the talk block builder**

Add this helper method to `DaemonServer`:

```python
    def _read_talk_block(self, page_name: str) -> dict:
        """Build the per-page talk-page digest folded into wiki_read responses.

        Critical and moderate open entries are inlined verbatim under
        `recent_critical` / `recent_moderate`. Everything else collapses
        into counts + the librarian's stored 2-sentence summary.
        Resolved entries are excluded from counts and `recent_*`.
        """
        from llm_wiki.librarian.talk_summary import TalkSummaryStore
        from llm_wiki.talk.page import TalkPage, compute_open_set
        from llm_wiki.vault import _state_dir_for

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        # Find the page file (may be nested) so we can derive the talk path
        page_path = None
        for candidate in wiki_dir.rglob(f"{page_name}.md"):
            rel = candidate.relative_to(wiki_dir)
            if any(p.startswith(".") for p in rel.parts):
                continue
            page_path = candidate
            break

        empty = {
            "entry_count": 0,
            "open_count": 0,
            "by_severity": {
                "critical": 0, "moderate": 0, "minor": 0,
                "suggestion": 0, "new_connection": 0,
            },
            "summary": "",
            "recent_critical": [],
            "recent_moderate": [],
        }
        if page_path is None:
            return empty

        talk = TalkPage.for_page(page_path)
        if not talk.exists:
            return empty

        all_entries = talk.load()
        open_entries = compute_open_set(all_entries)

        by_severity = {
            "critical": 0, "moderate": 0, "minor": 0,
            "suggestion": 0, "new_connection": 0,
        }
        for e in open_entries:
            by_severity[e.severity] = by_severity.get(e.severity, 0) + 1

        recent_critical = [
            {"index": e.index, "ts": e.timestamp, "author": e.author, "body": e.body}
            for e in open_entries if e.severity == "critical"
        ]
        recent_moderate = [
            {"index": e.index, "ts": e.timestamp, "author": e.author, "body": e.body}
            for e in open_entries if e.severity == "moderate"
        ]

        # Pull the librarian's stored summary if present
        summary_store = TalkSummaryStore.load(
            _state_dir_for(self._vault_root) / "talk_summaries.json"
        )
        record = summary_store.get(page_name)
        summary_text = record.summary if record is not None else ""

        return {
            "entry_count": len(all_entries),
            "open_count": len(open_entries),
            "by_severity": by_severity,
            "summary": summary_text,
            "recent_critical": recent_critical,
            "recent_moderate": recent_moderate,
        }
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_daemon/test_server.py -k "read_includes or read_inlines or read_excludes" -v`
Expected: PASS for all four tests.

- [ ] **Step 7: Run the full daemon test module to confirm no regressions**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass. Note: existing `test_read_top` / `test_read_section` tests assert the response has `content` — they should still pass because `content` is preserved alongside the new fields.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py
git commit -m "feat: phase 6a — read route folds in issues + talk digest"
```

---

### Task 12: Daemon `search` route enriched with snippet matches

**Files:**
- Modify: `src/llm_wiki/search/tantivy_backend.py` (new method `search_with_snippets`)
- Modify: `src/llm_wiki/daemon/server.py:_handle_search` (call the new method)
- Modify: `tests/test_search/test_tantivy.py` (snippet extraction unit tests)
- Modify: `tests/test_daemon/test_server.py` (response-shape integration test)

The matches array enriches each result with line-numbered hits plus the nearest preceding heading. Implementation reads the page file from disk and runs an in-Python case-insensitive search per query token. This is **not** tantivy's snippet generator — it's a small post-search pass that gives the agent enough context to skip a `wiki_read` call.

- [ ] **Step 1: Write the failing tests for the snippet extraction helper**

Append to `tests/test_search/test_tantivy.py`:

```python
def test_search_with_snippets_returns_matches_with_line_numbers(sample_vault):
    """search_with_snippets attaches a `matches` list to each result."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    backend = vault._backend  # access the underlying tantivy backend
    results = backend.search_with_snippets("PCA", limit=5, vault_root=sample_vault)
    assert results, "expected at least one result for 'PCA'"

    for r in results:
        assert hasattr(r, "matches")
        if r.matches:
            for m in r.matches:
                assert isinstance(m.line, int)
                assert isinstance(m.before, str)
                assert isinstance(m.match, str)
                assert isinstance(m.after, str)


def test_search_with_snippets_finds_correct_line(sample_vault):
    """A query token's match line corresponds to the file line that contains it."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets("k-means", limit=5, vault_root=sample_vault)
    srna_result = next((r for r in results if r.name == "srna-embeddings"), None)
    assert srna_result is not None

    page_text = (sample_vault / "bioinformatics" / "srna-embeddings.md").read_text()
    page_lines = page_text.splitlines()

    for m in srna_result.matches:
        # The line text on the matched line should contain the search term (case-insensitive)
        assert "k-means" in page_lines[m.line - 1].lower()


def test_search_with_snippets_attaches_nearest_heading(sample_vault):
    """The `before` field is the nearest preceding ## heading text."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets("k-means", limit=5, vault_root=sample_vault)
    srna_result = next((r for r in results if r.name == "srna-embeddings"), None)
    assert srna_result is not None

    for m in srna_result.matches:
        # In sample_vault, the k-means content lives in the Clustering section
        assert m.before in ("## Clustering", "## Overview", "## Method", "## Related Pages")


def test_search_with_snippets_empty_results_for_no_match(sample_vault):
    """A query that hits nothing returns an empty list, not a crash."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets(
        "absolutelynothingmatchesthistoken", limit=5, vault_root=sample_vault,
    )
    assert results == []
```

- [ ] **Step 2: Write the failing test for the daemon route**

Append to `tests/test_daemon/test_server.py`:

```python
@pytest.mark.asyncio
async def test_search_route_returns_matches_array(phase6a_daemon_server):
    """The enriched search route attaches a matches array to each result."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {"type": "search", "query": "k-means", "limit": 5})
    assert resp["status"] == "ok"
    assert resp["results"]
    for r in resp["results"]:
        assert "matches" in r
        assert isinstance(r["matches"], list)
        for m in r["matches"]:
            assert "line" in m
            assert "before" in m
            assert "match" in m
            assert "after" in m
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_search/test_tantivy.py -k "snippet" tests/test_daemon/test_server.py::test_search_route_returns_matches_array -v`
Expected: FAIL — `search_with_snippets` does not exist yet.

- [ ] **Step 4: Implement the snippet result types and helper**

Add to `src/llm_wiki/search/backend.py` (or wherever `SearchResult` lives — check current imports first):

```python
@dataclass
class SnippetMatch:
    """One per-line search hit inside a page file."""
    line: int          # 1-based line number in the file
    before: str        # nearest preceding ## heading text (or "" if none)
    match: str         # the matching line itself
    after: str         # the next non-blank line after the match (or "")


@dataclass
class SnippetSearchResult:
    """A SearchResult enriched with line-level snippet matches."""
    name: str
    score: float
    entry: ManifestEntry
    matches: list[SnippetMatch]
```

(If `SearchResult` is currently a `@dataclass`, mirror its definition. If it's defined in `backend.py`, add the new types right next to it. The Read of `tantivy_backend.py` shows `SearchResult` is imported from `llm_wiki.search.backend` — add `SnippetMatch` and `SnippetSearchResult` to the same module.)

- [ ] **Step 5: Implement `search_with_snippets` on the tantivy backend**

Add this method to `TantivyBackend` in `src/llm_wiki/search/tantivy_backend.py`:

```python
    def search_with_snippets(
        self,
        query: str,
        limit: int,
        vault_root: Path,
    ) -> list[SnippetSearchResult]:
        """Run a search and attach line-level snippet matches per result.

        Tokenizes the query into terms (whitespace split, lowercased), then
        for each result reads the page file from `vault_root` and finds the
        first few lines that contain any term. The `before` field is the
        nearest preceding `^##` or `^###` heading; `after` is the next
        non-blank line. Snippet count per result is capped at 3.
        """
        from llm_wiki.search.backend import SnippetMatch, SnippetSearchResult

        base_results = self.search(query, limit=limit)
        if not base_results:
            return []

        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return [
                SnippetSearchResult(
                    name=r.name, score=r.score, entry=r.entry, matches=[],
                )
                for r in base_results
            ]

        out: list[SnippetSearchResult] = []
        for r in base_results:
            matches = self._extract_snippets(r.name, terms, vault_root, max_matches=3)
            out.append(SnippetSearchResult(
                name=r.name, score=r.score, entry=r.entry, matches=matches,
            ))
        return out

    def _extract_snippets(
        self,
        page_name: str,
        terms: list[str],
        vault_root: Path,
        max_matches: int,
    ) -> list:
        """Read the page file and find lines containing any of the query terms."""
        from llm_wiki.search.backend import SnippetMatch

        # Find the page file by name (may be nested under cluster directories)
        page_file = None
        for candidate in vault_root.rglob(f"{page_name}.md"):
            rel = candidate.relative_to(vault_root)
            if any(p.startswith(".") for p in rel.parts):
                continue
            if candidate.name.endswith(".talk.md"):
                continue
            page_file = candidate
            break
        if page_file is None:
            return []

        try:
            text = page_file.read_text(encoding="utf-8")
        except OSError:
            return []
        lines = text.splitlines()

        matches: list = []
        last_heading = ""
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("### "):
                last_heading = stripped
                continue
            lower = line.lower()
            if any(term in lower for term in terms):
                # Find the next non-blank line for `after`
                after = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip():
                        after = lines[j].strip()
                        break
                matches.append(SnippetMatch(
                    line=i + 1,
                    before=last_heading,
                    match=line.strip(),
                    after=after,
                ))
                if len(matches) >= max_matches:
                    break
        return matches
```

- [ ] **Step 6: Update `_handle_search` to call the new method**

Edit `src/llm_wiki/daemon/server.py:_handle_search` (~lines 303-310):

```python
    def _handle_search(self, request: dict) -> dict:
        results = self._vault._backend.search_with_snippets(
            request["query"],
            limit=request.get("limit", 10),
            vault_root=self._vault_root,
        )
        return {
            "status": "ok",
            "results": [_serialize_snippet_result(r) for r in results],
        }
```

And add a new module-level serializer at the bottom of `daemon/server.py` (next to `_serialize_result`):

```python
def _serialize_snippet_result(r) -> dict:
    return {
        "name": r.name,
        "score": r.score,
        "manifest": r.entry.to_manifest_text(),
        "matches": [
            {
                "line": m.line,
                "before": m.before,
                "match": m.match,
                "after": m.after,
            }
            for m in r.matches
        ],
    }
```

(The existing `_serialize_result` can stay for now — it's no longer called from `_handle_search` but may be used elsewhere; verify with `grep -n _serialize_result src/llm_wiki/daemon/server.py` and remove only if it's truly unused.)

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `pytest tests/test_search/test_tantivy.py -k "snippet" tests/test_daemon/test_server.py::test_search_route_returns_matches_array -v`
Expected: PASS.

- [ ] **Step 8: Run the full search and daemon test modules to confirm no regressions**

Run: `pytest tests/test_search tests/test_daemon -v`
Expected: All tests pass. The existing `test_search` integration test should still find at least one result for "sRNA"; the response shape now has a `matches` field but the existing assertions only check `results` length and status.

- [ ] **Step 9: Commit**

```bash
git add src/llm_wiki/search/backend.py \
        src/llm_wiki/search/tantivy_backend.py \
        src/llm_wiki/daemon/server.py \
        tests/test_search/test_tantivy.py \
        tests/test_daemon/test_server.py
git commit -m "feat: phase 6a — search route enriched with snippet matches"
```

---

### Task 13: Daemon `lint` route enriched with `attention_map`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:_handle_lint` (~lines 345-351)
- Modify: `tests/test_daemon/test_server.py` (or `test_lint_route.py`) (assert the new shape)

The vault-wide attention map aggregates issue + talk severity counts. It is **near-instant** because every input is already-persisted state — no LLM calls.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_server.py`:

```python
@pytest.mark.asyncio
async def test_lint_response_includes_attention_map(phase6a_daemon_server):
    """The lint route response carries an attention_map block."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    assert "attention_map" in resp
    am = resp["attention_map"]
    assert "pages_needing_attention" in am
    assert "totals" in am
    assert "by_page" in am
    assert "issues" in am["totals"]
    assert "talk" in am["totals"]
    for severity in ("critical", "moderate", "minor"):
        assert severity in am["totals"]["issues"]
    for severity in ("critical", "moderate", "minor", "suggestion", "new_connection"):
        assert severity in am["totals"]["talk"]


@pytest.mark.asyncio
async def test_lint_attention_map_aggregates_issue_severities(phase6a_daemon_server, sample_vault):
    """An open critical issue raises the totals.issues.critical count."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    queue = IssueQueue(sample_vault)
    queue.add(Issue(
        id=Issue.make_id("broken-citation", "srna-embeddings", "raw/missing.pdf"),
        type="broken-citation",
        status="open",
        severity="critical",
        title="Missing source",
        page="srna-embeddings",
        body="A test critical issue.",
        created=Issue.now_iso(),
        detected_by="auditor",
        metadata={"target": "raw/missing.pdf"},
    ))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    assert am["totals"]["issues"]["critical"] >= 1
    assert "srna-embeddings" in am["pages_needing_attention"]
    assert am["by_page"]["srna-embeddings"]["issues"]["critical"] >= 1


@pytest.mark.asyncio
async def test_lint_attention_map_aggregates_talk_severities(phase6a_daemon_server, sample_vault):
    """A critical talk entry raises the totals.talk.critical count."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(
        0, "2026-04-08T10:00:00+00:00", "@adv",
        "Critical talk entry", severity="critical",
    ))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    assert am["totals"]["talk"]["critical"] >= 1
    assert "srna-embeddings" in am["pages_needing_attention"]


@pytest.mark.asyncio
async def test_lint_attention_map_excludes_resolved_talk_entries(phase6a_daemon_server, sample_vault):
    """Resolved talk entries don't show up in the attention map counts."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(0, "t1", "@adv", "first", severity="critical"))
    talk.append(TalkEntry(0, "t2", "@user", "closes 1", resolves=[1]))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    # The critical entry is resolved → must not be counted
    by_page = am["by_page"].get("srna-embeddings", {})
    talk_counts = by_page.get("talk", {})
    assert talk_counts.get("critical", 0) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_server.py -k "attention_map" -v`
Expected: FAIL — `attention_map` is not in the lint response.

- [ ] **Step 3: Update `_handle_lint` to compute and attach the attention map**

Edit `src/llm_wiki/daemon/server.py:_handle_lint` (~lines 345-351):

```python
    def _handle_lint(self) -> dict:
        from llm_wiki.audit.auditor import Auditor

        queue = self._issue_queue()
        auditor = Auditor(self._vault, queue, self._vault_root)
        report = auditor.audit()
        attention_map = self._build_attention_map(queue)
        return {
            "status": "ok",
            "attention_map": attention_map,
            **report.to_dict(),
        }
```

- [ ] **Step 4: Add the `_build_attention_map` helper to `DaemonServer`**

Add this method to `DaemonServer`:

```python
    def _build_attention_map(self, queue: "IssueQueue") -> dict:
        """Aggregate issue and talk severities across the vault.

        Issue counts come from the queue (already filtered by status='open').
        Talk counts come from walking every *.talk.md and computing the
        open set per page. Resolved entries are excluded.
        """
        from llm_wiki.talk.page import TalkPage, compute_open_set

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")

        empty_issues = lambda: {"critical": 0, "moderate": 0, "minor": 0}
        empty_talk = lambda: {
            "critical": 0, "moderate": 0, "minor": 0,
            "suggestion": 0, "new_connection": 0,
        }

        totals_issues = empty_issues()
        totals_talk = empty_talk()
        by_page: dict[str, dict] = {}

        # Issues
        for issue in queue.list(status="open"):
            sev = issue.severity if issue.severity in totals_issues else "minor"
            totals_issues[sev] += 1
            page = issue.page or "<vault>"
            page_entry = by_page.setdefault(
                page, {"issues": empty_issues(), "talk": empty_talk()},
            )
            page_entry["issues"][sev] += 1

        # Talk pages
        if wiki_dir.exists():
            for talk_path in sorted(wiki_dir.rglob("*.talk.md")):
                rel = talk_path.relative_to(wiki_dir)
                if any(p.startswith(".") for p in rel.parts):
                    continue
                stem = talk_path.stem
                page_name = stem[: -len(".talk")] if stem.endswith(".talk") else stem
                entries = TalkPage(talk_path).load()
                open_entries = compute_open_set(entries)
                for e in open_entries:
                    sev = e.severity if e.severity in totals_talk else "suggestion"
                    totals_talk[sev] += 1
                    page_entry = by_page.setdefault(
                        page_name, {"issues": empty_issues(), "talk": empty_talk()},
                    )
                    page_entry["talk"][sev] += 1

        return {
            "pages_needing_attention": sorted(by_page.keys()),
            "totals": {"issues": totals_issues, "talk": totals_talk},
            "by_page": by_page,
        }
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `pytest tests/test_daemon/test_server.py -k "attention_map" -v`
Expected: PASS for all four attention_map tests.

- [ ] **Step 6: Run the full daemon test module to confirm no regressions**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass. The existing `test_lint_route.py` tests assert the structural-checks portion of the response, which is preserved unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py
git commit -m "feat: phase 6a — lint route returns vault-wide attention_map"
```

---

### Task 14: Final regression sweep + Phase 6a tag

**Files:**
- None (verification + tag)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass. If anything fails, fix it before proceeding.

- [ ] **Step 2: Verify the daemon starts cleanly**

Run: `python -m llm_wiki.daemon /tmp/empty-vault 2>&1 | head -20` (after `mkdir /tmp/empty-vault` if needed)
Expected: Daemon logs `Daemon started: 0 pages, ... workers=['auditor', 'librarian', 'authority_recalc', 'adversary', 'talk_summary']`.

Then kill it: find the PID with `ps aux | grep llm_wiki.daemon` and send SIGTERM.

- [ ] **Step 3: Smoke-test the enriched routes against the sample vault**

```bash
# Start the daemon in another terminal:
#   llm-wiki serve $PWD
# Then:
llm-wiki search --vault $PWD k-means
llm-wiki read --vault $PWD srna-embeddings 2>/dev/null || true   # CLI may not parse new fields
llm-wiki lint --vault $PWD
```

The CLI commands may not display the new fields (the CLI presentation layer is unchanged in Phase 6a — only the daemon protocol). What matters is that the responses round-trip cleanly without errors. If the CLI errors out trying to parse the enriched response, that's a CLI bug to file as a follow-up issue, not a Phase 6a blocker.

- [ ] **Step 4: Tag the phase complete**

```bash
git tag phase-6a-complete
git log --oneline | head -20
```

- [ ] **Step 5: Update the spec status line**

Open `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md` and edit the first line:

From:
```
> Status: design approved, ready for implementation planning
```

To:
```
> Status: Phase 6a (visibility & severity) implemented; 6b (write surface) and 6c (MCP server) pending
```

Commit:

```bash
git add docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md
git commit -m "docs: phase 6a complete — spec status updated"
```

---

## Phase 6a complete

When Task 14 is done, the wiki has:
- Severity-aware issues and talk entries
- Append-only closure via positional `resolves` references, computed in pure Python
- Librarian-refreshed talk-page summaries with threshold + rate-limit
- `wiki_read` responses that fold in per-page issue + talk digests
- `wiki_search` responses that include line-numbered snippet matches with the nearest preceding heading
- `wiki_lint` responses that carry a vault-wide attention map of issue + talk severities

None of this requires MCP, none of it requires the write surface, and none of it depends on the session/journal/commit pipeline. Phase 6a is independently shippable: a CLI user running `llm-wiki lint` after merge gets the attention map immediately, even though the agent-facing surface (Phase 6c) isn't built yet.

Phases 6b and 6c will build on top of this foundation: 6b adds the daemon write routes, V4A patch parser, sessions, and commit pipeline; 6c wraps everything in an MCP server that frontier models can connect to.
