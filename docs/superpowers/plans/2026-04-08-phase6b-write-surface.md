# Phase 6b: Write Surface, Sessions, Commit Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Spec reference:** `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md`. Read §"Daemon-side changes" (especially the three new write routes), §"Session lifecycle, journal, and commit pipeline" in full, §"`ingest` becomes session-aware", §"The hard rule — and how it's enforced", and §"Configuration" before starting Task 1.
>
> **Prerequisites:** Phase 6a (visibility & severity) must be merged. This plan builds on:
> - `Issue.severity` (6a Task 2) and `TalkEntry.severity / .resolves / .index` (6a Task 4) — already in place.
> - The `talk_summary` worker registration (6a Task 10) — Phase 6b's AST hard-rule test must explicitly *exclude* this worker from the unsupervised-write check (it doesn't write body content).
>
> Note: Phase 6a's `phase6a_daemon_server` fixture uses `WikiConfig(vault=VaultConfig(wiki_dir=""))` to align with the `sample_vault` layout (which puts pages directly under `tmp_path`). Phase 6b does NOT inherit this pattern — it creates pages from scratch in `tmp_path/wiki/` and uses the production-default `WikiConfig()`. This is what makes the journal-path code path correct: every supervised write derives `journal_path_rel` from `page_path.relative_to(vault_root)`, which works for any `wiki_dir` (default `wiki/`, flat `""`, or custom).
>
> **Phase 6a carryover:** the rollup review of Phase 6a flagged four Important and four Minor items that were intentionally deferred so 6a could ship. They are documented in detail in the **"Phase 6a carryover"** section immediately below the cross-cutting reminders. Address them as an inline cleanup pass during Task 1 — the type/layering items in particular should land before the new write routes add more call sites.

**Goal:** Add the daemon's write surface — three new routes (`page-create`, `page-update`, `page-append`) plus a session-management route (`session-close`) — backed by a session/journal/commit pipeline that captures every supervised mutation as a git commit attributed to the calling agent. Add a V4A patch parser/applier so `page-update` can take diff-style patches. Add an AST hard-rule test that mechanically prevents background workers from reaching the new write routes. Make `wiki_ingest` session-aware so its internal page writes flow through the same routes (and the same commit pipeline) as any other supervised write.

**Architecture:** Five new modules in `daemon/`: `v4a_patch.py` (parser + applier), `sessions.py` (session model, journal, registry, recovery), `commit.py` (serial git lock, summarizer, fallback), `writes.py` (the `PageWriteService` that does the actual create/update/append work — the route handlers are thin shims), and `name_similarity.py` (the Jaccard + Levenshtein hybrid for `wiki_create` near-match detection). One AST-walking test in `tests/test_daemon/test_ast_hard_rule.py` enforces "background workers never reach write routes" by inspecting every coroutine in the scheduler. The existing `daemon/server.py` gains four route handlers and an inactivity-timer task; `ingest/agent.py` is rewritten to call `PageWriteService` instead of `write_page`, threading the calling agent's `author` through the session.

**Tech Stack:** Python 3.11+, pytest-asyncio, `subprocess` for git operations (no `GitPython` dep), existing `LLMClient`/`LLMQueue` for the cheap maintenance summarizer call. **All summarizer calls use `priority="maintenance"`.** No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  daemon/
    v4a_patch.py        # NEW: Patch/Hunk dataclasses, parser, applier
    sessions.py         # NEW: Session, JournalEntry, SessionRegistry, recovery scan
    commit.py           # NEW: serial git lock, summarizer call, stage/commit/archive, fallback
    writes.py           # NEW: PageWriteService (create/update/append impls), used by routes AND ingest
    name_similarity.py  # NEW: Jaccard + Levenshtein hybrid for wiki_create near-match
    server.py           # MODIFIED: wire new routes, start inactivity-timer task, settle on shutdown
  ingest/
    agent.py            # MODIFIED: route through PageWriteService, accept author + session
  config.py             # MODIFIED: add MCPConfig, SessionsConfig, WriteConfig dataclasses

tests/
  test_daemon/
    test_v4a_patch.py        # NEW: parser + applier unit tests + error cases
    test_sessions.py         # NEW: Session, journal append/load, registry, recovery
    test_commit.py           # NEW: commit module unit tests with subprocess git
    test_writes.py           # NEW: PageWriteService unit tests
    test_name_similarity.py  # NEW: Jaccard + Levenshtein hybrid tests
    test_write_routes.py     # NEW: integration tests for the three route handlers
    test_session_close_route.py  # NEW: session-close route + idempotency
    test_ast_hard_rule.py    # NEW: the mechanical hard-rule test
    test_session_lifecycle.py    # NEW: end-to-end inactivity/cap/shutdown/explicit settle
    test_recovery.py         # NEW: orphaned-journal recovery on startup
  test_ingest/
    test_session_aware_ingest.py  # NEW: ingest routes through PageWriteService with author
```

**Type flow across tasks:**

- `daemon.v4a_patch.PatchOp = Literal["create", "update", "delete"]`. Phase 6b only uses `"update"` — `create` and `delete` are reserved for future expansion.
- `daemon.v4a_patch.HunkLine` is a `@dataclass` of `kind: Literal["context", "add", "remove"]` and `text: str`. Hunks are sequences of these.
- `daemon.v4a_patch.Hunk` is `@dataclass(context_hint: str, lines: list[HunkLine])`. The `context_hint` is the text after `@@` on the hunk header line; empty string when omitted.
- `daemon.v4a_patch.Patch` is `@dataclass(op: PatchOp, target_path: str, hunks: list[Hunk])`. Built by `parse_patch(text: str) -> Patch`.
- `daemon.v4a_patch.apply_patch(patch: Patch, current_content: str) -> tuple[str, ApplyResult]`. `ApplyResult` carries `additions: int`, `removals: int`, and `applied_via: Literal["exact", "fuzzy"]`. Raises `PatchConflict(message: str, current_excerpt: str)` on context drift.
- `daemon.sessions.JournalEntry` is `@dataclass(ts: str, tool: str, path: str, author: str, intent: str | None, summary: str, content_hash_after: str)`. Persisted as one JSONL line per write.
- `daemon.sessions.Session` is `@dataclass(id: str, author: str, connection_id: str, opened_at: str, last_write_at: str, write_count: int, journal_path: Path)`. The `id` is a UUID string.
- `daemon.sessions.SessionRegistry`: in-memory `dict[(author, connection_id), Session]`. Keys honor `config.sessions.namespace_by_connection`. Methods: `get_or_open(author, connection_id, state_dir) -> Session`, `get_active(author, connection_id) -> Session | None` (read-only lookup, used by `session-close`), `lookup_by_author(author) -> Session | None` (find-any convenience for tests/single-connection callers), `close(session) -> None`, `all_sessions() -> list[Session]`.
- `daemon.sessions.append_journal_entry(session: Session, entry: JournalEntry) -> None`: opens the journal in append-binary mode, writes one JSON-encoded line, calls `os.fsync()` on the fd. Synchronous (called from inside the daemon's per-page lock).
- `daemon.sessions.load_journal(path: Path) -> list[JournalEntry]`: reads a journal file, tolerates a malformed final line (treats it as the cutoff), returns the parsed entries.
- `daemon.sessions.scan_orphaned_journals(state_dir: Path) -> list[Path]`: returns every `<state_dir>/sessions/*.journal` not in `<state_dir>/sessions/.archived/`. Used by `recover_sessions()` at daemon startup.
- `daemon.commit.SettleResult` is `@dataclass(commit_sha: str | None, paths_committed: list[str], summary_used: Literal["llm", "fallback", "none"])`. `commit_sha` is `None` if there was nothing to commit (e.g. user already committed manually).
- `daemon.commit.CommitService(vault_root: Path, llm: LLMClient | None, lock: asyncio.Lock)`: holds the serial commit lock so two settling sessions don't race on git. Methods: `async settle(session, journal_entries) -> SettleResult` and `async settle_with_fallback(session, journal_entries) -> SettleResult` (the latter swallows summarizer failures and uses the deterministic fallback).
- `daemon.writes.WriteResult` is `@dataclass(status: Literal["ok", "error"], page_path: str, journal_id: str, session_id: str, content_hash: str, warnings: list[dict], code: str | None, details: dict)`. The route handlers turn this into the daemon's response dict.
- `daemon.writes.PageWriteService(vault, vault_root, config, write_coordinator, registry, commit_service)`. Methods: `async create(...)`, `async update(...)`, `async append(...)`. Each acquires `write_coordinator.lock_for(page_name)`, validates inputs, performs the write, computes the content hash, builds a `JournalEntry`, calls `append_journal_entry`, and returns a `WriteResult`. **Background workers must NEVER instantiate or call this service** — that's what the AST hard-rule test enforces.
- `daemon.name_similarity.is_near_match(name: str, existing: str, jaccard_threshold: float, levenshtein_threshold: float) -> bool`: the two-stage hybrid from the spec.
- `daemon.name_similarity.find_near_matches(name: str, existing_names: Iterable[str], cfg: WriteConfig) -> list[str]`: returns the subset of `existing_names` that match.
- `daemon.server.DaemonServer` gains an `_inactivity_timer` task (kept alive across the server's lifetime) and a `_commit_service` field. New routes dispatch to `PageWriteService`. The `stop()` method settles all open sessions before tearing down the server. Write and session-close handlers extract `connection_id` directly from the request payload (the daemon never generates it from the Unix-socket connection); missing `connection_id` returns a `missing-connection-id` error.
- `ingest.agent.IngestAgent.ingest(source_path, vault_root, *, author, connection_id, write_service)`: the agent now takes the calling agent's session keys explicitly and uses `write_service.create(...)` / `write_service.append(...)` instead of `ingest.page_writer.write_page`. The CLI ingest command generates a per-invocation `connection_id` UUID and passes `author="cli"`.

**Cross-cutting reminders:**
- Every supervised write produces exactly one journal entry with exactly one `path` field — the file's path relative to `vault_root`, derived from `page_path.relative_to(vault_root)` rather than hardcoded. Under the default `wiki_dir = "wiki/"` config this is `wiki/<page>.md` (or `wiki/<page>.talk.md` / `wiki/.issues/<id>.md` for the talk and issue routes); under a flat Obsidian-style vault with `wiki_dir = ""` it becomes `<page>.md`. The settle pipeline `git add`s only the paths from the journal — never `git add -A`. The 1:1 invariant (one MCP write call → one journal line → one path → one file) is what makes path-scoped staging possible.
- The serial commit lock (`asyncio.Lock` on `CommitService`) wraps **every git command**. Two sessions settling concurrently will serialize on this lock.
- Background-worker writes (compliance, auditor, adversary, librarian, talk_summary) do NOT pass through `PageWriteService` and do NOT produce journal entries. They sit on disk dirty until the user commits them via Obsidian/git. The `auto_commit_user_edits` config flag (default `false`) is the opt-in for the daemon to commit these too.
- The AST hard-rule test (Task 18) enforces this contract mechanically. **It is the only test that prevents Phase 6b from drifting back into "anything can write."** Treat its failure as a P0 — never `pytest -k "not hard_rule"` it away.
- Sessions key on `(author, connection_id)` by default. **The `connection_id` is supplied explicitly by the calling client in every write/session-close request payload — the daemon does not generate it.** For MCP clients (Phase 6c), the MCP server generates one UUID at stdio-session startup and passes it in every `client.request({...})` call, so all tool calls from one MCP session group into one daemon session. For CLI ingest, the CLI generates one UUID per invocation. The daemon's per-Unix-socket UUID is **not** used for session keying — the daemon's protocol is one-message-per-Unix-socket-connection, so a per-connection UUID would create one session per write, defeating the purpose of session grouping. Write/session-close handlers return `missing-connection-id` if the field is absent. With `sessions.namespace_by_connection: false` (advanced), sessions key on `author` alone and the supplied `connection_id` is ignored for keying purposes.
- All summarizer calls use `priority="maintenance"`. The commit ALWAYS happens — if the LLM is unreachable or fails, the deterministic fallback message is used.
- Journal writes are `os.fsync`'d. Journal append happens BEFORE the daemon's response to the agent — the daemon never returns `{"status": "ok"}` for a write whose journal entry wasn't durably on disk.

---

## Phase 6a carryover

These items are not part of the original 20 Phase 6b tasks. They are deferred findings from the rollup review of `feature/phase6a-visibility-severity` (post-merge to `master` at commit `f7715be`, tag `phase-6a-complete`). They were intentionally not blocked on for the 6a merge, but they should be addressed during Phase 6b — the **Important** items before Task 14 (the route handlers) lands more severity call sites, and the **Minor** items at the implementer's discretion.

The originating reviewer agent's full report lives in the conversation history of the 2026-04-09 Phase 6a session; this section is the load-bearing summary. Each item is independently revertable and has a small, well-bounded fix. Add tests for each fix; do not let any of these land as drive-by changes inside an unrelated commit.

### Important — should land alongside Task 1 or Task 2 of Phase 6b

**P6A-I3: `TalkSummaryStore` never prunes entries for deleted pages.**

- **Location:** `src/llm_wiki/librarian/talk_summary.py` (the store) and `src/llm_wiki/librarian/agent.py` (`refresh_talk_summaries`, lines ~134-226).
- **Symptom:** `TalkSummaryStore` has a `delete()` method, but nobody calls it. `LibrarianAgent.refresh_talk_summaries` walks live `*.talk.md` files and only writes; it never enumerates the store and drops entries for pages that no longer exist. Compare to `ManifestOverrides.prune(set(entries))` at `librarian/agent.py:129`.
- **Severity:** unbounded growth path. Not catastrophic (the store is small JSON, rebuildable from the wiki) but drifts from the pruning discipline applied elsewhere.
- **Fix:** at the end of `refresh_talk_summaries`, enumerate `store._entries` keys, drop any that don't correspond to an existing `*.talk.md` file, save if modified. Add a test that creates two talk files, runs the refresh, deletes one talk file, runs the refresh again, and asserts the deleted page's entry is gone from the store.

**P6A-I4: Daemon reaches into `Vault._backend.search_with_snippets` (layering violation).**

- **Location:** `src/llm_wiki/daemon/server.py:327` (`_handle_search`), `src/llm_wiki/search/backend.py:34-37` (the `SearchBackend` Protocol), `src/llm_wiki/search/tantivy_backend.py:77-170` (the impl).
- **Symptom:** `_handle_search` does `self._vault._backend.search_with_snippets(...)`. The `Vault` public API has `search()` (`vault.py:100`) but no `search_with_snippets`. Production code reaching into a private attribute of a neighbor object is a layering violation, AND the `SearchBackend` Protocol doesn't even declare `search_with_snippets` — so a future backend swap would break at runtime, not type-check time.
- **Severity:** soft API contract violation. Tests passing today, but Phase 6b will add more call sites that should not perpetuate the pattern.
- **Fix:** add `search_with_snippets` to `Vault` as a public method (thin pass-through that delegates to the backend), AND either add it to the `SearchBackend` Protocol or make it a concrete extension method that `Vault` wraps with an `isinstance`-check + fallback. Update `_handle_search` to call `self._vault.search_with_snippets(...)`. Tests in `tests/test_search/test_tantivy.py` reaching into `vault._backend` are fine (test code can be privileged); only production paths need to change.

**P6A-I5: `compute_open_set` docstring and implementation disagree about temporal ordering; no protection against self-closure.**

- **Location:** `src/llm_wiki/talk/page.py:85-97`.
- **Symptom:** The docstring says "An entry is closed iff some entry with a **strictly greater** `index` references it." The implementation:
  ```python
  closed: set[int] = set()
  for entry in entries:
      for target in entry.resolves:
          closed.add(target)
  ```
  …does not compare indices. An entry at index 2 with `resolves=[5]` would close entry 5 even though 2 < 5. Not test-visible because no test exercises forward-closure and the `talk-append` route doesn't allow it in practice (indices are positional and the resolver always writes later than the resolved). But the docstring lies about the contract.
  Additionally, `TalkEntry(index=3, resolves=[3])` would close entry 3 — itself. The `test_compute_open_set_resolver_itself_remains_open` test doesn't catch this because it uses `resolves=[1]` on entry 2, never the same index.
- **Severity:** correctness gap that will surface the moment Phase 6b's write routes accept caller-supplied `resolves` lists with looser validation.
- **Fix:** pick one of:
  - **Tighten implementation to match docstring:** change to `if target < entry.index: closed.add(target)`. Add a test that asserts an entry with a forward-pointing `resolves` does NOT close the future entry.
  - **OR loosen docstring to match implementation:** remove the "strictly greater" claim. Document that any cross-reference closes regardless of order.
  - **EITHER WAY:** add `if target != entry.index:` to prevent self-closure (recommended: silently ignore — it's a no-op gesture and shouldn't error). Add a test for the self-closure case.

**P6A-I6: `phase6a_daemon_server` fixture cleanup is fragile if the auditor body ever gains an `await`.**

- **Location:** `tests/test_daemon/test_server.py` (the `phase6a_daemon_server` fixture, ~lines 175-197).
- **Symptom:** The fixture does `await asyncio.sleep(0)` then `shutil.rmtree(.issues)` to clear the noise the auditor produces on its first run. This works today because `Auditor.audit()` has no `await` inside it, so `run_auditor()` runs its body to completion in one event-loop slice. The moment someone adds an async operation to the auditor — an LLM call for classification, a network probe, a file-watcher hook — the race is real: `rmtree` will delete the `.issues` dir mid-write.
- **Severity:** non-blocking today, but the failure mode is non-obvious (a flaky test where issues sometimes appear during a later assertion).
- **Fix options** (in order of cost):
  - **Cheapest:** add a `scheduler.start(defer_initial_runs=True)` flag. Phase 5b's scheduler runs workers immediately, which is the right production default — but tests that don't care about immediate runs should opt out. The fixture passes the flag.
  - **Better:** give the scheduler a `wait_for_initial_runs()` primitive that completes when every worker has run once. Fixture awaits it, then `rmtree`s.
  - **Best:** allow `DaemonServer(..., enabled_workers={"talk_summary"})` so test fixtures can register only the workers they need. Mirror the existing `auto_commit_user_edits` config switch pattern.
- The Phase 6b Task 1 implementer should pick whichever option is cheapest given the scheduler API they're already touching.

### Minor — at the implementer's discretion

**P6A-M1: Pre-existing path traversal in talk routes (not introduced by 6a, but 6a extends the surface).**

- **Location:** `src/llm_wiki/daemon/server.py:589, 607, 395`.
- **Symptom:** `wiki_dir / f"{request['page']}.md"` with no validation of `request['page']`. A malicious request with `page="../../etc/passwd"` joins to `wiki_dir / "../../etc/passwd.md"`. The `talk-append` path would create a sibling `.talk.md` file outside the wiki on disk.
- **Severity:** existed in Phase 5d. Phase 6a's `_read_talk_block` and `_read_issues_block` both consume the same unvalidated `page_name`. Not a regression.
- **Fix:** add a `_validate_page_name(name: str) -> str | None` helper that clamps to the same lowercase-alnum-plus-hyphen shape that `_ISSUE_ID_RE` in `issues/queue.py` uses. Reject any name that doesn't match. Apply to every route that takes a `page_name` field. **Phase 6b should land this** — the new `page-create`/`page-update`/`page-append` routes are exactly the kind of write surface where path traversal becomes a real attack, not just a test-case curiosity.

**P6A-M2: `search_with_snippets` reads each hit page with no size cap.**

- **Location:** `src/llm_wiki/search/tantivy_backend.py:_extract_snippets`.
- **Symptom:** `page_file.read_text(encoding="utf-8")` then `splitlines()` on every search hit. On a large vault with multi-MB pages, this is O(pages × size) per query.
- **Severity:** acceptable at small-vault scale today. Bounded by `max_matches=3` per page, but unbounded by page size.
- **Fix:** add a `max_bytes` parameter (default ~64KB) and read with `page_file.read_text(encoding="utf-8", errors="replace")[:max_bytes]`, or stream with `page_file.open(encoding="utf-8")` and break on byte budget. Add a test that creates a synthetic 10MB page and asserts the function returns within a budget.

**P6A-M3: Three routes duplicate the talk-walking pattern.**

- **Location:** `_read_talk_block` (server.py), `_build_attention_map` (server.py), `LibrarianAgent.refresh_talk_summaries` (librarian/agent.py).
- **Symptom:** All three do `wiki_dir.rglob("*.talk.md")` + `TalkPage(...).load()` + skip-hidden-dirs + (optionally) `compute_open_set(...)`. Not a bug; just a pattern ripe for extraction.
- **Fix:** extract `iter_talk_pages(wiki_dir: Path) -> Iterator[tuple[str, TalkPage]]` into `talk/page.py`. Yields `(page_name, talk_page)` pairs, hides the rglob + hidden-dir filter + name-derivation logic. Update all three call sites. Save for whichever Phase 6b task next touches one of these files.

**P6A-M4: `_deterministic_summary` fallback sorts severities alphabetically.**

- **Location:** `src/llm_wiki/librarian/talk_summary.py:148`.
- **Symptom:** `sorted(by_severity.items())` produces `"critical, minor, moderate, new_connection, suggestion"` — not in severity-rank order, alphabetical.
- **Severity:** cosmetic. Affects only the user-facing fallback summary when the LLM is unreachable.
- **Fix:** define `_SEVERITY_RANK = {"critical": 0, "moderate": 1, "minor": 2, "suggestion": 3, "new_connection": 4}` and sort by it. One-line change. Add a test asserting the order.

### Process notes for the Phase 6b implementer

- **Numbering:** these are **P6A-I3** through **P6A-M4**, not new Phase 6b task numbers. Don't renumber Tasks 1-20 to fit them. They're cleanup that lands in dedicated commits with the `phase 6a` scope.
- **Commit message convention:** `fix: phase 6a — <summary>` for the Important items, `refactor: phase 6a — <summary>` for the Minor items. This keeps the Phase 6b commit history clean and makes the carryover items easy to find with `git log --grep "phase 6a"` after the fact.
- **Don't bundle these into Phase 6b feature commits.** The reviewer's discipline rule from Phase 6a applies: each cleanup item is its own small commit so it's individually revertable.
- **The minor items can be skipped entirely if Phase 6b runs long.** The Important items should not be skipped — particularly P6A-I4 (layering) and P6A-I5 (correctness), which will compound as Phase 6b adds new call sites.

---

### Task 1: Configuration extensions

**Files:**
- Modify: `src/llm_wiki/config.py` (add `MCPConfig`, `SessionsConfig`, `WriteConfig`; wire into `WikiConfig`)
- Modify: `tests/test_config.py` (round-trip the new sections)

This is the smallest task and unblocks every other task that reads config knobs. Defaults match the spec's §"Configuration" section.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_mcp_config_defaults():
    from llm_wiki.config import WikiConfig
    cfg = WikiConfig()
    assert cfg.mcp.transport == "stdio"
    assert cfg.mcp.ingest_response_max_pages == 15


def test_sessions_config_defaults():
    from llm_wiki.config import WikiConfig
    cfg = WikiConfig()
    assert cfg.sessions.namespace_by_connection is True
    assert cfg.sessions.inactivity_timeout_seconds == 300
    assert cfg.sessions.write_count_cap == 30
    assert cfg.sessions.cap_warn_ratio == 0.6
    assert cfg.sessions.auto_commit_user_edits is False
    assert cfg.sessions.user_edit_settle_interval_seconds == 600


def test_write_config_defaults():
    from llm_wiki.config import WikiConfig
    cfg = WikiConfig()
    assert cfg.write.require_citations_on_create is True
    assert cfg.write.require_citations_on_append is True
    assert cfg.write.patch_fuzzy_match_threshold == 0.85
    assert cfg.write.name_jaccard_threshold == 0.5
    assert cfg.write.name_levenshtein_threshold == 0.85


def test_phase6b_config_loads_overrides(tmp_path):
    import yaml
    from llm_wiki.config import WikiConfig
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "mcp": {"ingest_response_max_pages": 30},
        "sessions": {
            "inactivity_timeout_seconds": 60,
            "write_count_cap": 10,
            "namespace_by_connection": False,
        },
        "write": {
            "require_citations_on_create": False,
            "name_jaccard_threshold": 0.4,
        },
    }))
    cfg = WikiConfig.load(cfg_file)
    assert cfg.mcp.ingest_response_max_pages == 30
    assert cfg.sessions.inactivity_timeout_seconds == 60
    assert cfg.sessions.write_count_cap == 10
    assert cfg.sessions.namespace_by_connection is False
    assert cfg.write.require_citations_on_create is False
    assert cfg.write.name_jaccard_threshold == 0.4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py -k "mcp_config or sessions_config or write_config or phase6b" -v`
Expected: FAIL with `AttributeError: 'WikiConfig' object has no attribute 'mcp'`.

- [ ] **Step 3: Add the three new dataclasses and wire them into `WikiConfig`**

Edit `src/llm_wiki/config.py`. After the existing `HonchoConfig` block, add:

```python
@dataclass
class MCPConfig:
    transport: str = "stdio"
    ingest_response_max_pages: int = 15


@dataclass
class SessionsConfig:
    namespace_by_connection: bool = True
    inactivity_timeout_seconds: int = 300
    write_count_cap: int = 30
    cap_warn_ratio: float = 0.6
    auto_commit_user_edits: bool = False
    user_edit_settle_interval_seconds: int = 600


@dataclass
class WriteConfig:
    require_citations_on_create: bool = True
    require_citations_on_append: bool = True
    patch_fuzzy_match_threshold: float = 0.85
    name_jaccard_threshold: float = 0.5
    name_levenshtein_threshold: float = 0.85
```

Then add the three fields to `WikiConfig`:

```python
@dataclass
class WikiConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_queue: LLMQueueConfig = field(default_factory=LLMQueueConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    honcho: HonchoConfig = field(default_factory=HonchoConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    write: WriteConfig = field(default_factory=WriteConfig)

    @classmethod
    def load(cls, path: Path) -> "WikiConfig":
        ...  # unchanged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -k "mcp_config or sessions_config or write_config or phase6b" -v`
Expected: PASS.

- [ ] **Step 5: Run the full config module to confirm no regressions**

Run: `pytest tests/test_config.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: phase 6b config — mcp/sessions/write sections"
```

---

### Task 2: V4A patch — dataclasses and skeleton

**Files:**
- Create: `src/llm_wiki/daemon/v4a_patch.py` (skeleton with dataclasses + module-level constants)
- Create: `tests/test_daemon/test_v4a_patch.py` (skeleton + dataclass tests)

This task lands the type surface. The parser and applier come in Tasks 3–6.

The V4A format used by codex/cline looks like:

```
*** Begin Patch
*** Update File: wiki/sRNA-tQuant.md
@@ ## Methods @@
 We trained on 50k sequences using k-means
-with cosine similarity, learning rate 1e-4.
+with cosine similarity, learning rate 3e-4.
 The clustering converged in 12 epochs.
*** End Patch
```

Phase 6b implements only `*** Update File:` (the `op="update"` case). `*** Add File:` and `*** Delete File:` are reserved literals — the parser recognizes them only enough to return a clear error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_v4a_patch.py`:

```python
from __future__ import annotations

import pytest


def test_hunk_line_dataclass():
    from llm_wiki.daemon.v4a_patch import HunkLine
    line = HunkLine(kind="context", text="some context")
    assert line.kind == "context"
    assert line.text == "some context"


def test_hunk_dataclass_default_context_hint():
    from llm_wiki.daemon.v4a_patch import Hunk
    hunk = Hunk(context_hint="", lines=[])
    assert hunk.context_hint == ""
    assert hunk.lines == []


def test_patch_dataclass():
    from llm_wiki.daemon.v4a_patch import Hunk, Patch
    patch = Patch(op="update", target_path="wiki/foo.md", hunks=[Hunk("", [])])
    assert patch.op == "update"
    assert patch.target_path == "wiki/foo.md"
    assert len(patch.hunks) == 1


def test_patch_conflict_carries_excerpt():
    from llm_wiki.daemon.v4a_patch import PatchConflict
    exc = PatchConflict("context drift", current_excerpt="actual line")
    assert "context drift" in str(exc)
    assert exc.current_excerpt == "actual line"


def test_apply_result_dataclass():
    from llm_wiki.daemon.v4a_patch import ApplyResult
    result = ApplyResult(additions=2, removals=1, applied_via="exact")
    assert result.additions == 2
    assert result.removals == 1
    assert result.applied_via == "exact"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.daemon.v4a_patch'`.

- [ ] **Step 3: Create the skeleton module**

Create `src/llm_wiki/daemon/v4a_patch.py`:

```python
"""V4A patch parser and applier.

The V4A format is the diff format used by OpenAI's codex and the cline tool.
This module is the daemon-side implementation that backs the `wiki_update`
MCP tool. Only `*** Update File:` is supported in Phase 6b — `*** Add File:`
and `*** Delete File:` are recognized only enough to return a clear error.

Format example:

    *** Begin Patch
    *** Update File: wiki/sRNA-tQuant.md
    @@ ## Methods @@
     context line
    -removed line
    +added line
     context line
    *** End Patch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PatchOp = Literal["update", "create", "delete"]
HunkLineKind = Literal["context", "add", "remove"]


@dataclass
class HunkLine:
    """One line within a hunk: context, addition, or removal."""
    kind: HunkLineKind
    text: str


@dataclass
class Hunk:
    """A contiguous block of changes within a patch.

    `context_hint` is the text after `@@` on the hunk header line — usually
    a heading or a section name. Empty string when the header is bare `@@`.
    The hint is used as a starting anchor for the applier when there are
    multiple plausible matches.
    """
    context_hint: str
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class Patch:
    """A complete V4A patch operating on one file."""
    op: PatchOp
    target_path: str
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Outcome of applying a patch."""
    additions: int
    removals: int
    applied_via: Literal["exact", "fuzzy"]


class PatchConflict(Exception):
    """Raised when patch context lines do not match the current file content.

    The `current_excerpt` field carries a few lines of the actual file content
    around the failed match site so the agent can re-read and regenerate
    the patch.
    """

    def __init__(self, message: str, current_excerpt: str = "") -> None:
        super().__init__(message)
        self.current_excerpt = current_excerpt


class PatchParseError(Exception):
    """Raised when patch text is malformed (missing markers, bad header, etc.)."""


# Parser and applier follow in Tasks 3–6 below.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: PASS for all five dataclass tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/v4a_patch.py tests/test_daemon/test_v4a_patch.py
git commit -m "feat: phase 6b — v4a_patch skeleton (dataclasses)"
```

---

### Task 3: V4A parser — single hunk

**Files:**
- Modify: `src/llm_wiki/daemon/v4a_patch.py` (add `parse_patch` function)
- Modify: `tests/test_daemon/test_v4a_patch.py` (parser tests)

The parser reads V4A text and returns a `Patch`. Task 3 covers:
- The `*** Begin Patch` / `*** End Patch` envelope
- A single `*** Update File:` line
- A single `@@ ... @@` hunk header
- Body lines starting with `' '`, `'+'`, or `'-'`

Multi-hunk and addition-only hunks come in Task 4. Fuzzy matching is in the applier (Task 6), not the parser.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_v4a_patch.py`:

```python
SIMPLE_PATCH = """\
*** Begin Patch
*** Update File: wiki/sRNA-tQuant.md
@@ ## Methods @@
 We trained on 50k sequences using k-means
-with cosine similarity, learning rate 1e-4.
+with cosine similarity, learning rate 3e-4.
 The clustering converged in 12 epochs.
*** End Patch
"""


def test_parse_patch_simple_update():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    assert patch.op == "update"
    assert patch.target_path == "wiki/sRNA-tQuant.md"
    assert len(patch.hunks) == 1


def test_parse_patch_extracts_context_hint():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    assert patch.hunks[0].context_hint == "## Methods"


def test_parse_patch_extracts_hunk_lines_in_order():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    lines = patch.hunks[0].lines
    assert len(lines) == 4
    assert lines[0].kind == "context"
    assert lines[0].text == "We trained on 50k sequences using k-means"
    assert lines[1].kind == "remove"
    assert lines[1].text == "with cosine similarity, learning rate 1e-4."
    assert lines[2].kind == "add"
    assert lines[2].text == "with cosine similarity, learning rate 3e-4."
    assert lines[3].kind == "context"
    assert lines[3].text == "The clustering converged in 12 epochs."


def test_parse_patch_missing_begin_marker_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = "*** Update File: wiki/foo.md\n@@ x @@\n context\n*** End Patch\n"
    with pytest.raises(PatchParseError, match="Begin Patch"):
        parse_patch(text)


def test_parse_patch_missing_end_marker_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = "*** Begin Patch\n*** Update File: wiki/foo.md\n@@ x @@\n context\n"
    with pytest.raises(PatchParseError, match="End Patch"):
        parse_patch(text)


def test_parse_patch_unknown_op_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Add File: wiki/foo.md\n"
        "@@ x @@\n"
        "+ new line\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchParseError, match="Add File"):
        parse_patch(text)


def test_parse_patch_no_hunk_header_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        " just some line\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchParseError):
        parse_patch(text)


def test_parse_patch_bare_at_at_header():
    """A `@@ @@` header with no context hint is valid; hint is empty string."""
    from llm_wiki.daemon.v4a_patch import parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " context\n"
        "+ added\n"
        "*** End Patch\n"
    )
    patch = parse_patch(text)
    assert patch.hunks[0].context_hint == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "parse_patch" -v`
Expected: FAIL with `ImportError: cannot import name 'parse_patch'`.

- [ ] **Step 3: Implement `parse_patch`**

Append to `src/llm_wiki/daemon/v4a_patch.py`:

```python
import re

_BEGIN_MARKER = "*** Begin Patch"
_END_MARKER = "*** End Patch"
_UPDATE_HEADER_RE = re.compile(r"^\*\*\* Update File:\s*(?P<path>\S.*)$")
_ADD_HEADER_RE = re.compile(r"^\*\*\* Add File:\s*(?P<path>\S.*)$")
_DELETE_HEADER_RE = re.compile(r"^\*\*\* Delete File:\s*(?P<path>\S.*)$")
_HUNK_HEADER_RE = re.compile(r"^@@\s*(?P<hint>.*?)\s*@@\s*$")


def parse_patch(text: str) -> Patch:
    """Parse V4A patch text into a Patch object.

    Phase 6b supports only `*** Update File:`. `*** Add File:` and
    `*** Delete File:` raise PatchParseError with a clear message — they
    are reserved for future expansion.
    """
    lines = text.splitlines()

    # Envelope checks
    if not any(line.strip() == _BEGIN_MARKER for line in lines):
        raise PatchParseError(f"Missing '{_BEGIN_MARKER}' marker")
    if not any(line.strip() == _END_MARKER for line in lines):
        raise PatchParseError(f"Missing '{_END_MARKER}' marker")

    # Slice between markers
    begin_idx = next(i for i, l in enumerate(lines) if l.strip() == _BEGIN_MARKER)
    end_idx = next(i for i, l in enumerate(lines) if l.strip() == _END_MARKER)
    if end_idx <= begin_idx:
        raise PatchParseError("End Patch appears before Begin Patch")
    body = lines[begin_idx + 1 : end_idx]

    if not body:
        raise PatchParseError("Patch body is empty")

    # First non-blank line must be a file-op header
    op_line_idx = 0
    while op_line_idx < len(body) and not body[op_line_idx].strip():
        op_line_idx += 1
    if op_line_idx >= len(body):
        raise PatchParseError("Patch body has no file-op header")

    op_line = body[op_line_idx]
    update_match = _UPDATE_HEADER_RE.match(op_line)
    if update_match is None:
        if _ADD_HEADER_RE.match(op_line):
            raise PatchParseError(
                "*** Add File: is not supported in Phase 6b — use wiki_create instead"
            )
        if _DELETE_HEADER_RE.match(op_line):
            raise PatchParseError(
                "*** Delete File: is not supported in Phase 6b — delete pages outside the daemon"
            )
        raise PatchParseError(f"Unrecognized file-op header: {op_line!r}")

    target_path = update_match.group("path").strip()
    if not target_path:
        raise PatchParseError("Update File: target path is empty")

    # Walk hunks. Each hunk starts with @@ and continues until the next @@
    # or end of body.
    hunks: list[Hunk] = []
    current_hunk: Hunk | None = None
    saw_any_hunk_header = False
    for line in body[op_line_idx + 1 :]:
        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match is not None:
            saw_any_hunk_header = True
            if current_hunk is not None:
                hunks.append(current_hunk)
            current_hunk = Hunk(
                context_hint=hunk_match.group("hint").strip(),
                lines=[],
            )
            continue

        if current_hunk is None:
            # Body content before the first @@ header
            if line.strip():
                raise PatchParseError(
                    f"Patch body has content before first @@ header: {line!r}"
                )
            continue

        # Body line within a hunk
        if not line:
            # Blank lines are treated as empty context lines (preserve them)
            current_hunk.lines.append(HunkLine(kind="context", text=""))
            continue
        prefix, _, rest = line[0], None, line[1:]
        if prefix == " ":
            current_hunk.lines.append(HunkLine(kind="context", text=rest))
        elif prefix == "+":
            current_hunk.lines.append(HunkLine(kind="add", text=rest))
        elif prefix == "-":
            current_hunk.lines.append(HunkLine(kind="remove", text=rest))
        else:
            raise PatchParseError(
                f"Hunk body line must start with ' ', '+', or '-': {line!r}"
            )

    if not saw_any_hunk_header:
        raise PatchParseError("Patch has no @@ hunk headers")
    if current_hunk is not None:
        hunks.append(current_hunk)

    return Patch(op="update", target_path=target_path, hunks=hunks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "parse_patch" -v`
Expected: PASS for all eight parser tests.

- [ ] **Step 5: Run the full V4A test module to confirm no regressions**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/v4a_patch.py tests/test_daemon/test_v4a_patch.py
git commit -m "feat: phase 6b — v4a_patch parser (single hunk)"
```

---

### Task 4: V4A parser — multi-hunk and addition-only

**Files:**
- Modify: `tests/test_daemon/test_v4a_patch.py` (multi-hunk + addition-only tests)

The Task 3 implementation already supports multi-hunk and addition-only correctly (the loop walks hunks and an addition-only hunk just has zero context/remove lines). This task is mostly verification — write tests for the cases the spec calls out, confirm they pass against the existing parser, and add the small fix needed if any test reveals a bug.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_v4a_patch.py`:

```python
MULTI_HUNK_PATCH = """\
*** Begin Patch
*** Update File: wiki/foo.md
@@ ## Section A @@
 first context
-old text
+new text
@@ ## Section B @@
 second context
+entirely new line
 trailing context
*** End Patch
"""


def test_parse_patch_multi_hunk():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(MULTI_HUNK_PATCH)
    assert len(patch.hunks) == 2
    assert patch.hunks[0].context_hint == "## Section A"
    assert patch.hunks[1].context_hint == "## Section B"
    # Hunk A: 1 context + 1 remove + 1 add
    assert sum(1 for l in patch.hunks[0].lines if l.kind == "context") == 1
    assert sum(1 for l in patch.hunks[0].lines if l.kind == "remove") == 1
    assert sum(1 for l in patch.hunks[0].lines if l.kind == "add") == 1
    # Hunk B: 2 context + 0 remove + 1 add (addition-only-ish)
    assert sum(1 for l in patch.hunks[1].lines if l.kind == "context") == 2
    assert sum(1 for l in patch.hunks[1].lines if l.kind == "remove") == 0
    assert sum(1 for l in patch.hunks[1].lines if l.kind == "add") == 1


ADDITION_ONLY_PATCH = """\
*** Begin Patch
*** Update File: wiki/foo.md
@@ ## End @@
 last existing line
+brand new line one
+brand new line two
*** End Patch
"""


def test_parse_patch_addition_only_hunk():
    """A hunk with only context + add lines (no removes) is valid."""
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(ADDITION_ONLY_PATCH)
    assert len(patch.hunks) == 1
    lines = patch.hunks[0].lines
    assert lines[0].kind == "context"
    assert lines[1].kind == "add"
    assert lines[2].kind == "add"
    assert sum(1 for l in lines if l.kind == "remove") == 0


def test_parse_patch_blank_context_line_inside_hunk():
    """A blank line in the middle of a hunk is treated as an empty context line."""
    from llm_wiki.daemon.v4a_patch import parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " before\n"
        "\n"
        "+ added\n"
        " after\n"
        "*** End Patch\n"
    )
    patch = parse_patch(text)
    assert len(patch.hunks) == 1
    lines = patch.hunks[0].lines
    assert lines[0].text == "before"
    assert lines[1].kind == "context" and lines[1].text == ""
    assert lines[2].kind == "add" and lines[2].text == "added"
    assert lines[3].text == "after"
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "multi_hunk or addition_only or blank_context" -v`
Expected: PASS — the Task 3 parser already handles these cases. If any test fails, fix the parser before committing.

- [ ] **Step 3: Run the full V4A test module**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_daemon/test_v4a_patch.py
git commit -m "test: phase 6b — v4a_patch parser multi-hunk + addition-only coverage"
```

---

### Task 5: V4A applier — exact match

**Files:**
- Modify: `src/llm_wiki/daemon/v4a_patch.py` (add `apply_patch` function)
- Modify: `tests/test_daemon/test_v4a_patch.py` (applier tests)

The applier takes a parsed `Patch` and the current file content, locates each hunk's context lines in the file, applies the +/- changes, and returns the updated content. Task 5 covers the exact-match path: every context/remove line in the hunk must appear verbatim in the file in the right order. Fuzzy match is Task 6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_v4a_patch.py`:

```python
SIMPLE_PAGE = """\
---
title: T
---

## Methods

We trained on 50k sequences using k-means
with cosine similarity, learning rate 1e-4.
The clustering converged in 12 epochs.

## Results

Accuracy: 0.93.
"""


def test_apply_patch_exact_match_single_hunk():
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    new_content, result = apply_patch(patch, SIMPLE_PAGE)
    assert "learning rate 3e-4" in new_content
    assert "learning rate 1e-4" not in new_content
    assert result.applied_via == "exact"
    assert result.additions == 1
    assert result.removals == 1


def test_apply_patch_preserves_unrelated_content():
    """Lines outside the hunk's context are unchanged."""
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    new_content, _ = apply_patch(patch, SIMPLE_PAGE)
    assert "## Results" in new_content
    assert "Accuracy: 0.93." in new_content


def test_apply_patch_addition_only_inserts_after_context():
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    page = (
        "## End\n\n"
        "last existing line\n"
    )
    patch = parse_patch(ADDITION_ONLY_PATCH)
    new_content, result = apply_patch(patch, page)
    assert "brand new line one" in new_content
    assert "brand new line two" in new_content
    # Original line preserved
    assert "last existing line" in new_content
    assert result.removals == 0
    assert result.additions == 2


def test_apply_patch_multi_hunk():
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    page = (
        "## Section A\n\n"
        "first context\n"
        "old text\n"
        "\n"
        "## Section B\n\n"
        "second context\n"
        "trailing context\n"
    )
    patch = parse_patch(MULTI_HUNK_PATCH)
    new_content, result = apply_patch(patch, page)
    assert "new text" in new_content
    assert "old text" not in new_content
    assert "entirely new line" in new_content
    assert result.additions == 2
    assert result.removals == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "apply_patch" -v`
Expected: FAIL with `ImportError: cannot import name 'apply_patch'`.

- [ ] **Step 3: Implement `apply_patch` (exact-match path)**

Append to `src/llm_wiki/daemon/v4a_patch.py`:

```python
def apply_patch(patch: Patch, current_content: str) -> tuple[str, ApplyResult]:
    """Apply a parsed Patch to file content. Returns (new_content, result).

    Exact-match path only in this revision: every context line and every
    remove line must appear in the file at the position indicated by the
    hunk. Fuzzy fallback is added in Task 6.

    Raises:
        PatchConflict: if a hunk's context cannot be located exactly.
    """
    if patch.op != "update":
        raise PatchConflict(
            f"apply_patch only supports op='update', got {patch.op!r}"
        )

    lines = current_content.splitlines(keepends=True)
    cursor = 0  # Index into `lines` — we walk forward as we apply hunks.
    additions = 0
    removals = 0

    for hunk in patch.hunks:
        new_cursor, new_lines, h_adds, h_rems = _apply_hunk_exact(
            hunk, lines, start=cursor,
        )
        lines = new_lines
        cursor = new_cursor
        additions += h_adds
        removals += h_rems

    return "".join(lines), ApplyResult(
        additions=additions,
        removals=removals,
        applied_via="exact",
    )


def _apply_hunk_exact(
    hunk: Hunk,
    lines: list[str],
    start: int,
) -> tuple[int, list[str], int, int]:
    """Apply one hunk to `lines` starting at index `start`.

    Returns (cursor_after_hunk, new_lines, additions, removals).
    Raises PatchConflict if the hunk cannot be matched exactly.
    """
    # Build the sequence of "expected file lines" — context + remove, in order.
    expected: list[str] = [
        l.text for l in hunk.lines if l.kind in ("context", "remove")
    ]

    # Search forward from `start` for a window of `lines` that matches `expected`.
    match_start = _find_window(lines, expected, search_from=start)
    if match_start is None:
        excerpt = _excerpt_around(lines, start, hunk.context_hint)
        raise PatchConflict(
            f"Could not locate hunk context: {hunk.context_hint or '<no hint>'}",
            current_excerpt=excerpt,
        )

    # Build the replacement: walk hunk.lines, emit context+add, drop remove.
    replacement: list[str] = []
    for hl in hunk.lines:
        if hl.kind == "remove":
            continue
        replacement.append(hl.text + "\n")

    additions = sum(1 for l in hunk.lines if l.kind == "add")
    removals = sum(1 for l in hunk.lines if l.kind == "remove")

    new_lines = (
        lines[:match_start]
        + replacement
        + lines[match_start + len(expected) :]
    )
    new_cursor = match_start + len(replacement)
    return new_cursor, new_lines, additions, removals


def _find_window(
    lines: list[str],
    expected: list[str],
    search_from: int = 0,
) -> int | None:
    """Find the index in `lines` where `expected` appears verbatim.

    Compares stripped trailing newlines so the patch's "context line text"
    matches the file's "line including trailing newline."
    """
    if not expected:
        return None
    n = len(expected)
    for i in range(search_from, len(lines) - n + 1):
        match = True
        for j in range(n):
            file_line = lines[i + j].rstrip("\n").rstrip("\r")
            if file_line != expected[j]:
                match = False
                break
        if match:
            return i
    return None


def _excerpt_around(lines: list[str], start: int, hint: str) -> str:
    """Return ~6 lines of context around the failed match site."""
    lo = max(0, start - 2)
    hi = min(len(lines), start + 6)
    return "".join(lines[lo:hi])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "apply_patch" -v`
Expected: PASS for all four applier tests.

- [ ] **Step 5: Run the full V4A test module**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/v4a_patch.py tests/test_daemon/test_v4a_patch.py
git commit -m "feat: phase 6b — v4a_patch applier (exact match)"
```

---

### Task 6: V4A applier — fuzzy match and conflict errors

**Files:**
- Modify: `src/llm_wiki/daemon/v4a_patch.py` (extend `apply_patch` with fuzzy fallback)
- Modify: `tests/test_daemon/test_v4a_patch.py` (fuzzy + conflict tests)

When the file has drifted slightly since the agent generated the patch (whitespace changes, a typo fix, an inserted blank line), the exact-match path will fail. The fuzzy path retries by ignoring trailing/leading whitespace per line and accepting matches whose Levenshtein-normalized similarity is at or above `config.write.patch_fuzzy_match_threshold` (default 0.85).

If both exact and fuzzy fail, raise `PatchConflict` with an excerpt of the actual file content around the expected location.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_v4a_patch.py`:

```python
def test_apply_patch_fuzzy_tolerates_trailing_whitespace_drift():
    """A patch context line with no trailing space matches a file line with trailing spaces."""
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    page = (
        "## Methods\n\n"
        "We trained on 50k sequences using k-means   \n"  # trailing spaces
        "with cosine similarity, learning rate 1e-4.\n"
        "The clustering converged in 12 epochs.\n"
    )
    patch = parse_patch(SIMPLE_PATCH)
    new_content, result = apply_patch(patch, page)
    assert "learning rate 3e-4" in new_content
    assert result.applied_via == "fuzzy"


def test_apply_patch_fuzzy_tolerates_minor_typo():
    """A patch context line matches a file line within the configured similarity threshold."""
    from llm_wiki.daemon.v4a_patch import apply_patch, parse_patch
    page = (
        "## Methods\n\n"
        "We trained on 50k sequences using k means\n"  # missing hyphen
        "with cosine similarity, learning rate 1e-4.\n"
        "The clustering converged in 12 epochs.\n"
    )
    patch = parse_patch(SIMPLE_PATCH)
    new_content, result = apply_patch(patch, page)
    assert "learning rate 3e-4" in new_content
    assert result.applied_via == "fuzzy"


def test_apply_patch_conflict_when_context_missing_entirely():
    from llm_wiki.daemon.v4a_patch import PatchConflict, apply_patch, parse_patch
    page = (
        "## Different Section\n\n"
        "totally unrelated content\n"
    )
    patch = parse_patch(SIMPLE_PATCH)
    with pytest.raises(PatchConflict) as exc_info:
        apply_patch(patch, page)
    assert exc_info.value.current_excerpt
    assert "Could not locate" in str(exc_info.value)


def test_apply_patch_conflict_excerpt_carries_actual_content():
    from llm_wiki.daemon.v4a_patch import PatchConflict, apply_patch, parse_patch
    page = (
        "## Methods\n\n"
        "Completely different content\n"
        "Nothing matches\n"
    )
    patch = parse_patch(SIMPLE_PATCH)
    with pytest.raises(PatchConflict) as exc_info:
        apply_patch(patch, page)
    assert "Completely different content" in exc_info.value.current_excerpt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_v4a_patch.py -k "fuzzy or conflict" -v`
Expected: The fuzzy tests fail because the exact-match path doesn't tolerate drift; the conflict tests should already pass from Task 5.

- [ ] **Step 3: Add the fuzzy fallback**

Edit `src/llm_wiki/daemon/v4a_patch.py`. Replace the `apply_patch` function and add the fuzzy helpers:

```python
def apply_patch(
    patch: Patch,
    current_content: str,
    fuzzy_threshold: float = 0.85,
) -> tuple[str, ApplyResult]:
    """Apply a parsed Patch to file content. Returns (new_content, result).

    Two-stage matching:
      1. Exact: every context/remove line must appear verbatim.
      2. Fuzzy: trailing whitespace tolerated; per-line normalized
         Levenshtein similarity must be >= `fuzzy_threshold`.

    Raises:
        PatchConflict: if neither stage can locate the hunk.
    """
    if patch.op != "update":
        raise PatchConflict(
            f"apply_patch only supports op='update', got {patch.op!r}"
        )

    lines = current_content.splitlines(keepends=True)
    cursor = 0
    additions = 0
    removals = 0
    used_fuzzy = False

    for hunk in patch.hunks:
        try:
            new_cursor, new_lines, h_adds, h_rems = _apply_hunk_exact(
                hunk, lines, start=cursor,
            )
        except PatchConflict:
            new_cursor, new_lines, h_adds, h_rems = _apply_hunk_fuzzy(
                hunk, lines, start=cursor, threshold=fuzzy_threshold,
            )
            used_fuzzy = True
        lines = new_lines
        cursor = new_cursor
        additions += h_adds
        removals += h_rems

    return "".join(lines), ApplyResult(
        additions=additions,
        removals=removals,
        applied_via="fuzzy" if used_fuzzy else "exact",
    )


def _apply_hunk_fuzzy(
    hunk: Hunk,
    lines: list[str],
    start: int,
    threshold: float,
) -> tuple[int, list[str], int, int]:
    """Fuzzy fallback: tolerate trailing whitespace and per-line typos."""
    expected: list[str] = [
        l.text for l in hunk.lines if l.kind in ("context", "remove")
    ]

    match_start = _find_window_fuzzy(
        lines, expected, search_from=start, threshold=threshold,
    )
    if match_start is None:
        excerpt = _excerpt_around(lines, start, hunk.context_hint)
        raise PatchConflict(
            f"Could not locate hunk context (fuzzy): {hunk.context_hint or '<no hint>'}",
            current_excerpt=excerpt,
        )

    replacement: list[str] = []
    for hl in hunk.lines:
        if hl.kind == "remove":
            continue
        replacement.append(hl.text + "\n")

    additions = sum(1 for l in hunk.lines if l.kind == "add")
    removals = sum(1 for l in hunk.lines if l.kind == "remove")

    new_lines = (
        lines[:match_start]
        + replacement
        + lines[match_start + len(expected) :]
    )
    new_cursor = match_start + len(replacement)
    return new_cursor, new_lines, additions, removals


def _find_window_fuzzy(
    lines: list[str],
    expected: list[str],
    search_from: int,
    threshold: float,
) -> int | None:
    """Like `_find_window` but tolerates trailing whitespace and per-line drift."""
    if not expected:
        return None
    n = len(expected)
    for i in range(search_from, len(lines) - n + 1):
        per_line_scores = []
        for j in range(n):
            file_line = lines[i + j].rstrip("\n").rstrip("\r").rstrip()
            patch_line = expected[j].rstrip()
            sim = _line_similarity(file_line, patch_line)
            if sim < threshold:
                per_line_scores = None
                break
            per_line_scores.append(sim)
        if per_line_scores is not None:
            return i
    return None


def _line_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0.0, 1.0]. 1.0 means identical."""
    if a == b:
        return 1.0
    if not a and not b:
        return 1.0
    distance = levenshtein(a, b)
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - (distance / longest)


def levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein edit distance, iterative DP.

    Public so `name_similarity.py` can reuse it without dipping into
    private symbols across modules.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = curr
    return prev[-1]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_v4a_patch.py -v`
Expected: All V4A tests pass — fuzzy + conflict + previously-passing exact tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/v4a_patch.py tests/test_daemon/test_v4a_patch.py
git commit -m "feat: phase 6b — v4a_patch applier (fuzzy fallback + conflict)"
```

---

### Task 7: Sessions module — `Session`, journal append/load, recovery scan

**Files:**
- Create: `src/llm_wiki/daemon/sessions.py`
- Create: `tests/test_daemon/test_sessions.py`

The `Session` dataclass holds the per-author state. The journal is one JSONL file per session at `<state_dir>/sessions/<session-uuid>.journal`. `append_journal_entry` is **synchronous** and `fsync`'d so the daemon can guarantee durability before responding to the agent. `load_journal` tolerates a malformed final line (treats it as the cutoff). `scan_orphaned_journals` returns every non-archived journal under `<state_dir>/sessions/` for the recovery pass at startup.

The `SessionRegistry` is in-memory; it does not need to be persisted because the journals on disk are the durable state. Process restart recovers from the journals.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_sessions.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_session_dataclass_round_trip(tmp_path):
    from llm_wiki.daemon.sessions import Session
    sess = Session(
        id="abc-123",
        author="claude-opus-4-6",
        connection_id="conn-1",
        opened_at="2026-04-08T10:00:00+00:00",
        last_write_at="2026-04-08T10:00:01+00:00",
        write_count=0,
        journal_path=tmp_path / "abc-123.journal",
    )
    assert sess.id == "abc-123"
    assert sess.author == "claude-opus-4-6"
    assert sess.write_count == 0


def test_journal_append_and_load(tmp_path):
    from llm_wiki.daemon.sessions import (
        JournalEntry,
        Session,
        append_journal_entry,
        load_journal,
    )
    journal_path = tmp_path / "s.journal"
    sess = Session(
        id="s", author="a", connection_id="c",
        opened_at="t", last_write_at="t", write_count=0,
        journal_path=journal_path,
    )
    entry = JournalEntry(
        ts="2026-04-08T10:00:00+00:00",
        tool="wiki_create",
        path="wiki/foo.md",
        author="a",
        intent="test",
        summary="created foo",
        content_hash_after="sha256:abc",
    )
    append_journal_entry(sess, entry)
    assert journal_path.exists()

    loaded = load_journal(journal_path)
    assert len(loaded) == 1
    assert loaded[0].tool == "wiki_create"
    assert loaded[0].path == "wiki/foo.md"
    assert loaded[0].intent == "test"


def test_journal_append_multiple_entries(tmp_path):
    from llm_wiki.daemon.sessions import (
        JournalEntry, Session, append_journal_entry, load_journal,
    )
    journal_path = tmp_path / "multi.journal"
    sess = Session(
        id="s", author="a", connection_id="c",
        opened_at="t", last_write_at="t", write_count=0,
        journal_path=journal_path,
    )
    for i in range(3):
        entry = JournalEntry(
            ts=f"t{i}",
            tool="wiki_update",
            path=f"wiki/p{i}.md",
            author="a",
            intent=f"intent {i}",
            summary=f"summary {i}",
            content_hash_after=f"sha256:{i}",
        )
        append_journal_entry(sess, entry)

    loaded = load_journal(journal_path)
    assert [e.intent for e in loaded] == ["intent 0", "intent 1", "intent 2"]


def test_journal_load_tolerates_malformed_final_line(tmp_path):
    """A truncated final line (power-failure window) is treated as the cutoff."""
    from llm_wiki.daemon.sessions import load_journal
    journal_path = tmp_path / "p.journal"
    # Two valid lines + one truncated line
    valid_line = json.dumps({
        "ts": "t1", "tool": "wiki_create", "path": "wiki/a.md",
        "author": "a", "intent": "i", "summary": "s", "content_hash_after": "h",
    })
    journal_path.write_text(
        valid_line + "\n" + valid_line + "\n" + '{"ts": "t3", "tool":',
        encoding="utf-8",
    )
    loaded = load_journal(journal_path)
    assert len(loaded) == 2  # third line ignored


def test_journal_load_missing_file_returns_empty(tmp_path):
    from llm_wiki.daemon.sessions import load_journal
    assert load_journal(tmp_path / "missing.journal") == []


def test_session_registry_get_or_open_creates_new(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    sess = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert sess.author == "alice"
    assert sess.connection_id == "conn-1"
    assert sess.write_count == 0
    assert sess.journal_path.parent == state_dir / "sessions"


def test_session_registry_returns_same_session_for_same_keys(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert s1.id == s2.id


def test_session_registry_namespace_by_connection(tmp_path):
    """Default mode: same author, different connection_id → different sessions."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert s1.id != s2.id


def test_session_registry_no_namespace_by_connection(tmp_path):
    """Advanced mode: same author across connections → one session."""
    from llm_wiki.config import SessionsConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cfg = SessionsConfig(namespace_by_connection=False)
    registry = SessionRegistry(cfg)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert s1.id == s2.id


def test_session_registry_close_removes_session(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    sess = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    registry.close(sess)
    # Re-opening should produce a NEW session (different id)
    sess2 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert sess2.id != sess.id


def test_session_registry_get_active_returns_specific_session(tmp_path):
    """get_active scopes lookup to (author, connection_id), not just author."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert registry.get_active("alice", "conn-1").id == s1.id
    assert registry.get_active("alice", "conn-2").id == s2.id
    assert registry.get_active("alice", "conn-missing") is None
    assert registry.get_active("bob", "conn-1") is None


def test_session_registry_get_active_does_not_create(tmp_path):
    """get_active is read-only — never opens a new session."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    assert registry.get_active("alice", "conn-1") is None
    assert registry.all_sessions() == []


def test_scan_orphaned_journals_excludes_archived(tmp_path):
    from llm_wiki.daemon.sessions import scan_orphaned_journals
    state_dir = tmp_path / "state"
    sessions_dir = state_dir / "sessions"
    archived_dir = sessions_dir / ".archived"
    sessions_dir.mkdir(parents=True)
    archived_dir.mkdir()

    (sessions_dir / "open.journal").write_text("{}\n")
    (archived_dir / "old.journal").write_text("{}\n")

    orphans = scan_orphaned_journals(state_dir)
    assert len(orphans) == 1
    assert orphans[0].name == "open.journal"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.daemon.sessions'`.

- [ ] **Step 3: Implement `sessions.py`**

Create `src/llm_wiki/daemon/sessions.py`:

```python
"""Session model + journal IO + recovery scan.

A session is the unit of write grouping for commits. Its key is
`(author, connection_id)` by default; with
`config.sessions.namespace_by_connection: False`, the key is `author` alone.

The journal is one JSONL file per session at
`<state_dir>/sessions/<session-uuid>.journal`. Append is synchronous and
fsync'd — the daemon must not return `ok` for a write until its journal
entry is durably on disk. Load tolerates a truncated final line.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from llm_wiki.config import SessionsConfig

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """One supervised write event in a session journal."""
    ts: str
    tool: str
    path: str
    author: str
    intent: str | None
    summary: str
    content_hash_after: str


@dataclass
class Session:
    """In-memory state for one author's open writing session."""
    id: str
    author: str
    connection_id: str
    opened_at: str
    last_write_at: str
    write_count: int
    journal_path: Path


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def append_journal_entry(session: Session, entry: JournalEntry) -> None:
    """Append one journal entry, fsync, return.

    Synchronous on purpose: callers (PageWriteService) hold the per-page
    write lock and must guarantee the journal line is durable before
    releasing the lock and returning to the agent.
    """
    session.journal_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(entry), ensure_ascii=False)
    with open(session.journal_path, "ab") as fh:
        fh.write((payload + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    session.write_count += 1
    session.last_write_at = _now_iso()


def load_journal(path: Path) -> list[JournalEntry]:
    """Load all entries from a journal file. Truncated final line is dropped."""
    if not path.exists():
        return []
    entries: list[JournalEntry] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Failed to read journal %s", path)
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Truncated/malformed final line — treat as cutoff
            logger.info("Skipping malformed journal line in %s", path)
            continue
        try:
            entries.append(JournalEntry(**data))
        except TypeError:
            logger.info("Skipping journal entry with unexpected fields in %s", path)
            continue
    return entries


def scan_orphaned_journals(state_dir: Path) -> list[Path]:
    """Return non-archived journal files under `<state_dir>/sessions/`."""
    sessions_dir = state_dir / "sessions"
    if not sessions_dir.exists():
        return []
    return [
        p for p in sorted(sessions_dir.glob("*.journal"))
        if ".archived" not in p.parts
    ]


class SessionRegistry:
    """In-memory map from (author, connection_id) → Session.

    Honors `SessionsConfig.namespace_by_connection`. The connection_id is
    supplied by the daemon's per-client handler.
    """

    def __init__(self, config: SessionsConfig) -> None:
        self._config = config
        self._sessions: dict[tuple[str, str], Session] = {}

    def _key(self, author: str, connection_id: str) -> tuple[str, str]:
        if self._config.namespace_by_connection:
            return (author, connection_id)
        return (author, "")

    def get_or_open(
        self,
        author: str,
        connection_id: str,
        state_dir: Path,
    ) -> Session:
        key = self._key(author, connection_id)
        existing = self._sessions.get(key)
        if existing is not None:
            return existing

        sess_id = uuid.uuid4().hex
        now = _now_iso()
        sess = Session(
            id=sess_id,
            author=author,
            connection_id=connection_id,
            opened_at=now,
            last_write_at=now,
            write_count=0,
            journal_path=state_dir / "sessions" / f"{sess_id}.journal",
        )
        self._sessions[key] = sess
        return sess

    def lookup_by_author(self, author: str) -> Session | None:
        """Find ANY active session for the given author.

        Convenience for tests and other call sites where exactly one
        session per author is known to exist (single-connection setups).
        Production callers that have a connection_id should use
        `get_active(author, connection_id)` instead — that's the only
        unambiguous lookup when `namespace_by_connection=true` and the
        same author has multiple concurrent connections.
        """
        for (a, _conn), sess in self._sessions.items():
            if a == author:
                return sess
        return None

    def get_active(
        self,
        author: str,
        connection_id: str,
    ) -> Session | None:
        """Find the session for `(author, connection_id)`, or None.

        Honors `namespace_by_connection` via `_key()`. Unlike
        `get_or_open`, this is read-only — it never creates a session.
        Used by `session-close` to settle exactly the session that the
        calling connection owns, never sweeping up unrelated sessions
        that happen to share the author identifier.
        """
        return self._sessions.get(self._key(author, connection_id))

    def close(self, session: Session) -> None:
        """Remove the session from the registry. Does not touch the journal."""
        key = self._key(session.author, session.connection_id)
        self._sessions.pop(key, None)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_sessions.py -v`
Expected: PASS for all eleven session tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/sessions.py tests/test_daemon/test_sessions.py
git commit -m "feat: phase 6b — sessions module (Session, journal IO, registry, recovery scan)"
```

---

### Task 8: Commit module — git operations + summarizer + serial lock

**Files:**
- Create: `src/llm_wiki/daemon/commit.py`
- Create: `tests/test_daemon/test_commit.py`
- Modify: `src/llm_wiki/librarian/prompts.py` (add `compose_commit_summary_messages` + `parse_commit_summary`)

The `CommitService` holds a single `asyncio.Lock` that serializes every git operation across all sessions — two settling sessions can't race on `git`. Git operations are invoked via `subprocess` so we don't take a `GitPython` dependency.

The summarizer call uses the cheap maintenance LLM via `priority="maintenance"`. If it fails (model unreachable, parse error, etc.), the deterministic fallback message is used. **The commit always happens — the worst case is a less narrative subject line.**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_commit.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.sessions import JournalEntry, Session


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with a noop initial commit."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_session(tmp_path: Path) -> Session:
    return Session(
        id="abc123",
        author="researcher-3",
        connection_id="conn-1",
        opened_at="2026-04-08T10:00:00+00:00",
        last_write_at="2026-04-08T10:00:01+00:00",
        write_count=2,
        journal_path=tmp_path / "state" / "sessions" / "abc123.journal",
    )


def _make_entry(path: str, intent: str = "test edit") -> JournalEntry:
    return JournalEntry(
        ts="2026-04-08T10:00:00+00:00",
        tool="wiki_update",
        path=path,
        author="researcher-3",
        intent=intent,
        summary="+1 -1 @ ## Methods",
        content_hash_after="sha256:abc",
    )


@pytest.mark.asyncio
async def test_commit_service_settle_with_fallback_writes_commit(tmp_path):
    """A settle with no LLM produces a deterministic-message commit."""
    from llm_wiki.daemon.commit import CommitService
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("---\ntitle: Foo\n---\n\nbody.\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    entries = [_make_entry("wiki/foo.md")]

    result = await service.settle_with_fallback(sess, entries)
    assert result.commit_sha is not None
    assert "wiki/foo.md" in result.paths_committed
    assert result.summary_used == "fallback"

    # Verify the commit landed in git
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "researcher-3" in log
    assert "Session: abc123" in log
    assert "Agent: researcher-3" in log
    assert "Writes: 2" in log


@pytest.mark.asyncio
async def test_commit_service_settle_with_llm_uses_summary(tmp_path):
    """When the LLM returns a summary, it shows up in the commit message."""
    from llm_wiki.daemon.commit import CommitService
    from llm_wiki.traverse.llm_client import LLMResponse

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("body.\n")

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            return LLMResponse(
                content=(
                    "fix learning rate per source table 3\n\n"
                    "- updated Methods section\n"
                    "- corrected the cited number"
                ),
                tokens_used=20,
            )

    service = CommitService(vault_root=tmp_path, llm=MockLLM(), lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(
        sess, [_make_entry("wiki/foo.md", intent="fix learning rate")],
    )
    assert result.summary_used == "llm"
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "fix learning rate per source table 3" in log
    assert "updated Methods section" in log


@pytest.mark.asyncio
async def test_commit_service_falls_back_when_llm_raises(tmp_path):
    """LLM exception → deterministic fallback message; commit still lands."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    class FailingLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("model unreachable")

    service = CommitService(vault_root=tmp_path, llm=FailingLLM(), lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(sess, [_make_entry("wiki/foo.md")])
    assert result.commit_sha is not None
    assert result.summary_used == "fallback"


@pytest.mark.asyncio
async def test_commit_service_serial_lock_serializes(tmp_path):
    """Two concurrent settle calls do not race on git."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("a\n")
    (tmp_path / "wiki" / "b.md").write_text("b\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess_a = Session("ida", "alice", "c1", "t", "t", 1, tmp_path / "state" / "sessions" / "ida.journal")
    sess_b = Session("idb", "bob", "c2", "t", "t", 1, tmp_path / "state" / "sessions" / "idb.journal")

    results = await asyncio.gather(
        service.settle_with_fallback(sess_a, [_make_entry("wiki/a.md")]),
        service.settle_with_fallback(sess_b, [_make_entry("wiki/b.md")]),
    )
    # Both should have committed
    assert all(r.commit_sha is not None for r in results)
    # Two distinct commits in history
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 3  # initial + two settles


@pytest.mark.asyncio
async def test_commit_service_archives_journal_after_commit(tmp_path):
    """After settle, the journal is moved to .archived/."""
    from llm_wiki.daemon.commit import CommitService
    from llm_wiki.daemon.sessions import append_journal_entry

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("foo\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    # Make the journal real on disk by appending
    entry = _make_entry("wiki/foo.md")
    append_journal_entry(sess, entry)
    assert sess.journal_path.exists()

    await service.settle_with_fallback(sess, [entry])
    assert not sess.journal_path.exists()
    archived = tmp_path / "state" / "sessions" / ".archived" / "abc123.journal"
    assert archived.exists()


@pytest.mark.asyncio
async def test_commit_service_handles_nothing_to_commit(tmp_path):
    """If the user already committed the journaled paths, settle skips cleanly."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("body.\n")
    # User commits manually
    subprocess.run(["git", "-C", str(tmp_path), "add", "wiki/foo.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "manual"], check=True)

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(sess, [_make_entry("wiki/foo.md")])
    assert result.commit_sha is None  # nothing to commit
    assert result.summary_used == "none"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_commit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.daemon.commit'`.

- [ ] **Step 3: Add the summarizer prompt helpers to `librarian/prompts.py`**

Append to `src/llm_wiki/librarian/prompts.py`:

```python
def compose_commit_summary_messages(
    author: str,
    entries: "list[JournalEntry]",
) -> list[dict[str, str]]:
    """Build a 2-message prompt asking for a commit summary.

    The cheap maintenance LLM gets the journal entries and produces a
    one-line subject (≤60 chars) plus 2-5 bullet points. The settle
    pipeline parses this into the commit body.
    """
    body_lines = []
    for e in entries:
        intent = e.intent or ""
        body_lines.append(
            f"- {e.tool} {e.path}: {e.summary}"
            + (f" — {intent}" if intent else "")
        )
    body_text = "\n".join(body_lines)

    return [
        {
            "role": "system",
            "content": (
                "You write git commit messages for wiki edits made by AI agents. "
                "Format: a single one-line subject (max 60 characters), then a "
                "blank line, then 2-5 bullet points describing what changed and "
                "why. Use the intent field when present."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Here are {len(entries)} wiki edits from one session by agent {author}:\n\n"
                f"{body_text}\n\n"
                f"Produce the commit message."
            ),
        },
    ]


def parse_commit_summary(text: str) -> tuple[str, list[str]]:
    """Split LLM commit-message output into (subject, bullets).

    Returns ("", []) if the response is empty or unparseable. The subject
    is truncated to 60 characters; bullets are taken from lines starting
    with `-` or `*`.
    """
    if not text or not text.strip():
        return "", []
    cleaned = text.strip()
    parts = cleaned.split("\n\n", 1)
    subject_block = parts[0].strip()
    rest = parts[1] if len(parts) > 1 else ""

    # Subject is the first non-empty line of the subject block
    subject_lines = [l for l in subject_block.splitlines() if l.strip()]
    subject = subject_lines[0].strip() if subject_lines else ""
    if len(subject) > 60:
        subject = subject[:57] + "..."

    bullets: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            bullets.append(stripped[1:].strip())

    return subject, bullets
```

- [ ] **Step 4: Implement `commit.py`**

Create `src/llm_wiki/daemon/commit.py`:

```python
"""Commit pipeline: serial lock, summarizer, git stage/commit/archive.

The CommitService holds a single asyncio.Lock that serializes every git
operation across all sessions. It is the only entity that calls git.
The summarizer call goes through the LLMClient at priority='maintenance'
and falls back to a deterministic message if the model is unreachable.

The commit ALWAYS happens — the worst case is a less narrative subject.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from llm_wiki.daemon.sessions import JournalEntry, Session

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class SettleResult:
    """Outcome of one session settle."""
    commit_sha: str | None
    paths_committed: list[str] = field(default_factory=list)
    summary_used: Literal["llm", "fallback", "none"] = "none"


class CommitService:
    """Serializes git operations across sessions and produces commit messages.

    Construction: CommitService(vault_root, llm, lock).
    `lock` is the shared asyncio.Lock — typically owned by the DaemonServer
    so all CommitService instances (if any future code creates more than one)
    share it.
    """

    def __init__(
        self,
        vault_root: Path,
        llm: "LLMClient | None",
        lock: asyncio.Lock,
    ) -> None:
        self._vault_root = vault_root
        self._llm = llm
        self._lock = lock

    async def settle_with_fallback(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> SettleResult:
        """Try LLM-summarized settle; on failure use deterministic fallback.

        The settle is wrapped in the serial commit lock so two concurrent
        settles never race on git.
        """
        async with self._lock:
            return await self._settle_locked(session, entries)

    async def _settle_locked(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> SettleResult:
        if not entries:
            return SettleResult(commit_sha=None, summary_used="none")

        # 1. Try the LLM summarizer
        summary_subject = ""
        summary_bullets: list[str] = []
        summary_used: Literal["llm", "fallback", "none"] = "none"
        if self._llm is not None:
            try:
                from llm_wiki.librarian.prompts import (
                    compose_commit_summary_messages,
                    parse_commit_summary,
                )
                messages = compose_commit_summary_messages(session.author, entries)
                response = await self._llm.complete(
                    messages, temperature=0.0, priority="maintenance",
                )
                subject, bullets = parse_commit_summary(response.content)
                if subject:
                    summary_subject = subject
                    summary_bullets = bullets
                    summary_used = "llm"
            except Exception:
                logger.warning(
                    "Commit summarizer failed for session %s; using fallback",
                    session.id, exc_info=True,
                )

        if summary_used != "llm":
            summary_subject, summary_bullets = self._fallback_summary(session, entries)
            summary_used = "fallback"

        # 2. Stage exactly the paths from the journal
        paths = sorted({e.path for e in entries})
        for path in paths:
            self._git("add", path)

        # 3. Check if there is anything to commit
        status = self._git("status", "--porcelain", capture=True)
        staged = [
            line[3:] for line in status.splitlines()
            if line[:2] in ("A ", "M ", "D ", "AM", "MM")
        ]
        if not staged:
            logger.info(
                "Session %s: nothing to commit (paths already in tree)", session.id,
            )
            self._archive_journal(session)
            return SettleResult(commit_sha=None, summary_used="none")

        # 4. Build the message
        message = self._build_commit_message(
            session, entries, summary_subject, summary_bullets,
        )

        # 5. Commit
        self._git("commit", "-q", "-m", message)
        sha = self._git("rev-parse", "HEAD", capture=True).strip()

        # 6. Archive the journal
        self._archive_journal(session)

        return SettleResult(
            commit_sha=sha,
            paths_committed=paths,
            summary_used=summary_used,
        )

    def _git(self, *args: str, capture: bool = False) -> str:
        cmd = ["git", "-C", str(self._vault_root), *args]
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout
        subprocess.run(cmd, check=True)
        return ""

    def _build_commit_message(
        self,
        session: Session,
        entries: list[JournalEntry],
        subject: str,
        bullets: list[str],
    ) -> str:
        if not subject:
            subject = (
                f"wiki: {len(entries)} writes from {session.author} "
                f"[session {session.id[:4]}]"
            )
        else:
            subject = f"wiki: {subject}"
        if len(subject) > 72:
            subject = subject[:69] + "..."

        body_lines = [subject, ""]
        for bullet in bullets:
            body_lines.append(f"- {bullet}")
        if not bullets:
            for e in entries[:5]:
                line = f"- {e.tool} {e.path} — {e.summary}"
                if e.intent:
                    line += f" ({e.intent})"
                body_lines.append(line)
            if len(entries) > 5:
                body_lines.append(f"- ... and {len(entries) - 5} more")
        body_lines.append("")
        body_lines.append(f"Session: {session.id}")
        body_lines.append(f"Agent: {session.author}")
        body_lines.append(f"Writes: {len(entries)}")
        return "\n".join(body_lines)

    def _fallback_summary(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> tuple[str, list[str]]:
        subject = (
            f"{len(entries)} writes from {session.author} [session {session.id[:4]}]"
        )
        bullets: list[str] = []
        for e in entries[:5]:
            bullet = f"{e.tool} {e.path}"
            if e.intent:
                bullet += f" — {e.intent}"
            bullets.append(bullet)
        if len(entries) > 5:
            bullets.append(f"... and {len(entries) - 5} more")
        return subject, bullets

    def _archive_journal(self, session: Session) -> None:
        if not session.journal_path.exists():
            return
        archived_dir = session.journal_path.parent / ".archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        target = archived_dir / session.journal_path.name
        shutil.move(str(session.journal_path), str(target))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_commit.py -v`
Expected: PASS for all six commit tests.

- [ ] **Step 6: Run the full daemon test suite to confirm no regressions**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/daemon/commit.py \
        src/llm_wiki/librarian/prompts.py \
        tests/test_daemon/test_commit.py
git commit -m "feat: phase 6b — commit module (serial lock, summarizer, fallback)"
```

---

### Task 9: Recovery pass — process orphaned journals on startup

**Files:**
- Modify: `src/llm_wiki/daemon/sessions.py` (add `recover_sessions` helper)
- Create: `tests/test_daemon/test_recovery.py`

When the daemon starts, it scans `<state_dir>/sessions/*.journal` for non-archived journal files and runs `CommitService.settle_with_fallback` on each. This handles:
- Daemon crash mid-session
- Power failure mid-session
- Daemon shutdown that didn't complete settle for some reason

The recovery pass uses the **same** code path as the inactivity-timeout settle and the explicit `wiki_session_close` settle — there is exactly one settle implementation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_recovery.py`:

```python
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.commit import CommitService


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _write_journal(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


@pytest.mark.asyncio
async def test_recover_sessions_processes_orphaned_journal(tmp_path):
    """An orphaned journal on startup is settled into a commit."""
    from llm_wiki.daemon.sessions import recover_sessions

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    state_dir = tmp_path / "state"
    journal_path = state_dir / "sessions" / "abc123.journal"
    _write_journal(journal_path, [{
        "ts": "2026-04-08T10:00:00+00:00",
        "tool": "wiki_create",
        "path": "wiki/foo.md",
        "author": "researcher-3",
        "intent": "create test",
        "summary": "created foo",
        "content_hash_after": "sha256:abc",
    }])

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 1

    # Journal should be archived
    assert not journal_path.exists()
    archived = state_dir / "sessions" / ".archived" / "abc123.journal"
    assert archived.exists()

    # The commit should be in git history
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "researcher-3" in log


@pytest.mark.asyncio
async def test_recover_sessions_handles_truncated_journal(tmp_path):
    """A journal with a truncated final line still recovers earlier entries."""
    from llm_wiki.daemon.sessions import recover_sessions

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    state_dir = tmp_path / "state"
    journal_path = state_dir / "sessions" / "trunc.journal"
    journal_path.parent.mkdir(parents=True)
    valid = json.dumps({
        "ts": "t", "tool": "wiki_create", "path": "wiki/foo.md",
        "author": "a", "intent": "i", "summary": "s", "content_hash_after": "h",
    })
    journal_path.write_text(valid + "\n" + '{"ts": "t2", "tool":', encoding="utf-8")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 1


@pytest.mark.asyncio
async def test_recover_sessions_no_orphans(tmp_path):
    from llm_wiki.daemon.sessions import recover_sessions
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_sessions_skips_archived(tmp_path):
    from llm_wiki.daemon.sessions import recover_sessions

    state_dir = tmp_path / "state"
    archived_dir = state_dir / "sessions" / ".archived"
    archived_dir.mkdir(parents=True)
    (archived_dir / "old.journal").write_text("{}\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_recovery.py -v`
Expected: FAIL with `ImportError: cannot import name 'recover_sessions' from 'llm_wiki.daemon.sessions'`.

- [ ] **Step 3: Add `recover_sessions` to `sessions.py`**

Append to `src/llm_wiki/daemon/sessions.py`:

```python
async def recover_sessions(
    state_dir: Path,
    commit_service: "CommitService",
) -> int:
    """Settle every orphaned journal under <state_dir>/sessions/.

    Called once at daemon startup. For each non-archived journal, builds
    a stub Session, loads its entries, and runs the commit service's
    settle pipeline. Returns the number of journals successfully recovered.
    """
    from llm_wiki.daemon.commit import CommitService  # noqa: F401 — type-only import for IDE

    orphans = scan_orphaned_journals(state_dir)
    recovered = 0
    for journal_path in orphans:
        entries = load_journal(journal_path)
        if not entries:
            logger.info("Skipping empty journal %s", journal_path)
            continue

        # Reconstruct a stub Session from the journal's first entry
        first = entries[0]
        sess = Session(
            id=journal_path.stem,
            author=first.author,
            connection_id="recovered",
            opened_at=first.ts,
            last_write_at=entries[-1].ts,
            write_count=len(entries),
            journal_path=journal_path,
        )
        try:
            await commit_service.settle_with_fallback(sess, entries)
            recovered += 1
        except Exception:
            logger.exception("Failed to recover journal %s", journal_path)
            # Don't archive on failure — leave for the next attempt
            continue
    return recovered
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_recovery.py -v`
Expected: PASS for all four recovery tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/sessions.py tests/test_daemon/test_recovery.py
git commit -m "feat: phase 6b — recover_sessions for orphaned journals on startup"
```

---

### Task 10: Name similarity hybrid

**Files:**
- Create: `src/llm_wiki/daemon/name_similarity.py`
- Create: `tests/test_daemon/test_name_similarity.py`

The two-stage hybrid from the spec: Jaccard token-overlap (catches `sRNA-tQuant-Pipeline` ↔ `sRNA-tQuant`) plus normalized Levenshtein (catches typos like `attentnion` ↔ `attention`). Either stage flagging is enough to call it a near-match.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_name_similarity.py`:

```python
from __future__ import annotations

import pytest

from llm_wiki.config import WriteConfig


def test_is_near_match_proper_subset_via_jaccard():
    from llm_wiki.daemon.name_similarity import is_near_match
    # 'sRNA-tQuant-Pipeline' is a token superset of 'sRNA-tQuant'
    assert is_near_match(
        "sRNA-tQuant-Pipeline", "sRNA-tQuant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_typo_via_levenshtein():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "attentnion-mechanism", "attention-mechanism",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_high_token_overlap():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "the-attention-mechanism", "attention-mechanism",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_completely_different_returns_false():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert not is_near_match(
        "transformer-architecture", "k-means-clustering",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_case_insensitive():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "SRNA-TQUANT", "srna-tquant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_underscore_normalized_to_hyphen():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "srna_tquant", "srna-tquant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_find_near_matches_returns_subset():
    from llm_wiki.daemon.name_similarity import find_near_matches
    cfg = WriteConfig()
    existing = ["transformer-architecture", "srna-tquant", "k-means-clustering"]
    matches = find_near_matches("sRNA-tQuant-Pipeline", existing, cfg)
    assert matches == ["srna-tquant"]


def test_find_near_matches_empty_when_nothing_close():
    from llm_wiki.daemon.name_similarity import find_near_matches
    cfg = WriteConfig()
    existing = ["transformer-architecture", "k-means-clustering"]
    matches = find_near_matches("brand-new-topic", existing, cfg)
    assert matches == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_name_similarity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.daemon.name_similarity'`.

- [ ] **Step 3: Implement the module**

Create `src/llm_wiki/daemon/name_similarity.py`:

```python
"""Name similarity for wiki_create near-match detection.

Two-stage hybrid:
  1. Jaccard token overlap (handles supersets like 'sRNA-tQuant-Pipeline'
     vs 'sRNA-tQuant' that Levenshtein misses).
  2. Normalized Levenshtein (handles typos like 'attentnion' vs 'attention'
     that token overlap misses).

Either stage flagging is enough to return True. The exact case-insensitive
collision check is the caller's responsibility (it runs first as a hard
'name-collision' error before this hybrid runs).
"""

from __future__ import annotations

from typing import Iterable

from llm_wiki.config import WriteConfig
from llm_wiki.daemon.v4a_patch import levenshtein


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _tokens(name: str) -> set[str]:
    return set(_normalize(name).split("-")) - {""}


def is_near_match(
    name: str,
    existing: str,
    jaccard_threshold: float,
    levenshtein_threshold: float,
) -> bool:
    """Return True if `name` and `existing` are likely the same concept."""
    a_tokens = _tokens(name)
    b_tokens = _tokens(existing)

    if a_tokens and b_tokens:
        union = a_tokens | b_tokens
        if union:
            jaccard = len(a_tokens & b_tokens) / len(union)
            if jaccard > jaccard_threshold:
                return True
        if a_tokens < b_tokens or b_tokens < a_tokens:
            return True

    a_str = _normalize(name)
    b_str = _normalize(existing)
    if a_str and b_str:
        longest = max(len(a_str), len(b_str))
        if longest > 0:
            sim = 1.0 - (levenshtein(a_str, b_str) / longest)
            if sim > levenshtein_threshold:
                return True

    return False


def find_near_matches(
    name: str,
    existing_names: Iterable[str],
    cfg: WriteConfig,
) -> list[str]:
    """Return existing names that are near-matches of `name`."""
    return [
        existing for existing in existing_names
        if is_near_match(
            name, existing,
            jaccard_threshold=cfg.name_jaccard_threshold,
            levenshtein_threshold=cfg.name_levenshtein_threshold,
        )
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_name_similarity.py -v`
Expected: PASS for all eight tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/name_similarity.py tests/test_daemon/test_name_similarity.py
git commit -m "feat: phase 6b — name_similarity hybrid (Jaccard + Levenshtein)"
```

---

### Task 11: `PageWriteService` skeleton + `create` method

**Files:**
- Create: `src/llm_wiki/daemon/writes.py`
- Create: `tests/test_daemon/test_writes.py`

The `PageWriteService` is the entity that actually performs writes. Both the daemon route handlers (Tasks 14–16) and the session-aware ingest agent (Task 19) call it. **Background workers must not call it** — the AST hard-rule test (Task 18) enforces this mechanically.

Task 11 lands the skeleton plus the `create` method (the simplest of the three). `update` and `append` come in Tasks 12 and 13.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_writes.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.sessions import SessionRegistry
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.vault import Vault, _state_dir_for


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_service(tmp_path: Path):
    """Build a PageWriteService against a fresh empty vault under tmp_path."""
    from llm_wiki.daemon.writes import PageWriteService

    _init_git_repo(tmp_path)
    config = WikiConfig()
    vault = Vault.scan(tmp_path)
    coordinator = WriteCoordinator()
    registry = SessionRegistry(config.sessions)
    commit_service = CommitService(
        vault_root=tmp_path, llm=None, lock=asyncio.Lock(),
    )
    service = PageWriteService(
        vault=vault,
        vault_root=tmp_path,
        config=config,
        write_coordinator=coordinator,
        registry=registry,
        commit_service=commit_service,
    )
    return service, registry


@pytest.mark.asyncio
async def test_create_writes_file_with_frontmatter(tmp_path):
    service, registry = _make_service(tmp_path)
    result = await service.create(
        title="Test Page",
        body="Some body text [[raw/source.pdf]].",
        citations=["raw/source.pdf"],
        tags=["test"],
        author="alice",
        connection_id="conn-1",
        intent="create test page",
    )
    assert result.status == "ok"
    assert result.page_path == "wiki/test-page.md"

    page_file = tmp_path / "wiki" / "test-page.md"
    assert page_file.exists()
    content = page_file.read_text()
    assert "title: Test Page" in content
    assert "Some body text" in content


@pytest.mark.asyncio
async def test_create_appends_journal_entry(tmp_path):
    from llm_wiki.daemon.sessions import load_journal

    service, registry = _make_service(tmp_path)
    result = await service.create(
        title="Foo",
        body="text [[raw/x.pdf]]",
        citations=["raw/x.pdf"],
        author="alice",
        connection_id="conn-1",
        intent="i",
    )
    assert result.status == "ok"

    sess = registry.lookup_by_author("alice")
    assert sess is not None
    entries = load_journal(sess.journal_path)
    assert len(entries) == 1
    assert entries[0].tool == "wiki_create"
    assert entries[0].path == "wiki/foo.md"
    assert entries[0].intent == "i"


@pytest.mark.asyncio
async def test_create_refuses_empty_citations(tmp_path):
    service, _ = _make_service(tmp_path)
    result = await service.create(
        title="Foo",
        body="body",
        citations=[],
        author="alice",
        connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "missing-citations"


@pytest.mark.asyncio
async def test_create_rejects_name_collision(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    # Re-scan so the vault sees the new page
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="Foo", body="body [[raw/b.pdf]]", citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "name-collision"


@pytest.mark.asyncio
async def test_create_warns_on_near_match(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="srna-tquant", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="sRNA-tQuant-Pipeline",
        body="body [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "name-near-match"
    assert "srna-tquant" in result.details.get("similar_pages", [])


@pytest.mark.asyncio
async def test_create_force_bypasses_near_match(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="srna-tquant", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="sRNA-tQuant-Pipeline",
        body="body [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
        force=True,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_create_requires_author(tmp_path):
    service, _ = _make_service(tmp_path)
    result = await service.create(
        title="Foo", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "missing-author"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_writes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.daemon.writes'`.

- [ ] **Step 3: Create the `writes.py` module**

Create `src/llm_wiki/daemon/writes.py`:

```python
"""PageWriteService — the entity that actually performs supervised writes.

Both the daemon route handlers and the session-aware ingest agent use
this service. Background workers MUST NOT instantiate or call it — that
contract is enforced mechanically by tests/test_daemon/test_ast_hard_rule.py.

Each write:
  1. Validates inputs (citations required, author required, etc.)
  2. Acquires the per-page write lock
  3. Performs the file operation
  4. Computes the post-write content hash
  5. Builds a JournalEntry and appends it (synchronous, fsync'd)
  6. Returns a WriteResult that the route handler turns into a response dict
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.name_similarity import find_near_matches
from llm_wiki.daemon.sessions import (
    JournalEntry,
    Session,
    SessionRegistry,
    _now_iso,
    append_journal_entry,
)
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.vault import Vault, _state_dir_for

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    status: Literal["ok", "error"]
    page_path: str = ""
    journal_id: str = ""
    session_id: str = ""
    content_hash: str = ""
    warnings: list[dict] = field(default_factory=list)
    code: str | None = None
    details: dict = field(default_factory=dict)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug or "untitled"


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class PageWriteService:
    """Performs all supervised page writes. Used by route handlers and ingest."""

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        config: WikiConfig,
        write_coordinator: WriteCoordinator,
        registry: SessionRegistry,
        commit_service: CommitService,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._config = config
        self._coordinator = write_coordinator
        self._registry = registry
        self._commit_service = commit_service
        self._state_dir = _state_dir_for(vault_root)

    @property
    def _wiki_dir(self) -> Path:
        return self._vault_root / self._config.vault.wiki_dir.rstrip("/")

    async def create(
        self,
        *,
        title: str,
        body: str,
        citations: list[str],
        author: str,
        connection_id: str,
        tags: list[str] | None = None,
        intent: str | None = None,
        force: bool = False,
    ) -> WriteResult:
        """Create a new page with frontmatter, body, and citations."""
        if not author:
            return WriteResult(status="error", code="missing-author")
        if (
            self._config.write.require_citations_on_create
            and not citations
        ):
            return WriteResult(
                status="error",
                code="missing-citations",
                details={
                    "message": (
                        "wiki_create requires at least one citation. If you cannot "
                        "cite a source, post your idea to the talk page instead via "
                        "wiki_talk_post."
                    ),
                },
            )

        slug = _slugify(title)
        page_path = self._wiki_dir / f"{slug}.md"
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        # Hard collision check (case-insensitive exact match)
        existing_names = list(self._vault.manifest_entries().keys())
        existing_lower = {n.lower() for n in existing_names}
        if slug.lower() in existing_lower:
            return WriteResult(
                status="error",
                code="name-collision",
                details={"page_path": journal_path_rel},
            )

        # Soft near-match check (Jaccard + Levenshtein)
        if not force:
            near = find_near_matches(slug, existing_names, self._config.write)
            if near:
                return WriteResult(
                    status="error",
                    code="name-near-match",
                    details={
                        "similar_pages": near,
                        "force": (
                            "Pass force=true to wiki_create to override "
                            "this check."
                        ),
                    },
                )

        async with self._coordinator.lock_for(slug):
            page_path.parent.mkdir(parents=True, exist_ok=True)
            content = self._build_page_content(title, body, citations, tags or [])
            page_path.write_text(content, encoding="utf-8")
            content_hash = _content_hash(content)

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_create",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=f"created {slug}",
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
        )

    def _build_page_content(
        self,
        title: str,
        body: str,
        citations: list[str],
        tags: list[str],
    ) -> str:
        fm = {"title": title}
        if len(citations) == 1:
            fm["source"] = f"[[{citations[0]}]]"
        else:
            fm["sources"] = [f"[[{c}]]" for c in citations]
        if tags:
            fm["tags"] = tags
        frontmatter = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_writes.py -v`
Expected: PASS for all seven `create` tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/writes.py tests/test_daemon/test_writes.py
git commit -m "feat: phase 6b — PageWriteService.create with collision/near-match/citation checks"
```

---

### Task 12: `PageWriteService.update` (V4A patches)

**Files:**
- Modify: `src/llm_wiki/daemon/writes.py` (add `update` method)
- Modify: `tests/test_daemon/test_writes.py` (update tests)

`update` parses a V4A patch via `parse_patch`, applies it to the current page content via `apply_patch`, writes the result, and journals the operation. On `PatchConflict`, returns a structured `patch-conflict` error so the agent can re-read and retry.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_writes.py`:

```python
@pytest.mark.asyncio
async def test_update_applies_v4a_patch(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo", body="line one\nline two\nline three\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " line one\n"
        "-line two\n"
        "+line two REVISED\n"
        " line three\n"
        "*** End Patch\n"
    )
    result = await service.update(
        page="foo",
        patch=patch_text,
        author="alice",
        connection_id="conn-1",
        intent="revise line two",
    )
    assert result.status == "ok"
    content = (tmp_path / "wiki" / "foo.md").read_text()
    assert "line two REVISED" in content
    assert "line two\n" not in content


@pytest.mark.asyncio
async def test_update_returns_patch_conflict(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo", body="alpha\nbeta\ngamma\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " nonexistent context\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    result = await service.update(
        page="foo",
        patch=patch_text,
        author="alice",
        connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "patch-conflict"
    assert "current_excerpt" in result.details


@pytest.mark.asyncio
async def test_update_journal_entry_carries_diff_summary(tmp_path):
    from llm_wiki.daemon.sessions import load_journal

    service, registry = _make_service(tmp_path)
    await service.create(
        title="Foo", body="a\nb\nc\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        " c\n"
        "*** End Patch\n"
    )
    await service.update(
        page="foo", patch=patch_text,
        author="alice", connection_id="conn-1",
    )

    sess = registry.lookup_by_author("alice")
    entries = load_journal(sess.journal_path)
    update_entries = [e for e in entries if e.tool == "wiki_update"]
    assert len(update_entries) == 1
    assert "+1" in update_entries[0].summary
    assert "-1" in update_entries[0].summary


@pytest.mark.asyncio
async def test_update_missing_page_returns_error(tmp_path):
    service, _ = _make_service(tmp_path)
    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/missing.md\n"
        "@@ @@\n"
        " x\n"
        "+y\n"
        "*** End Patch\n"
    )
    result = await service.update(
        page="missing", patch=patch_text,
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "page-not-found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_writes.py -k "update" -v`
Expected: FAIL with `AttributeError: 'PageWriteService' object has no attribute 'update'`.

- [ ] **Step 3: Add the `update` method**

Append to `PageWriteService` in `src/llm_wiki/daemon/writes.py`:

```python
    async def update(
        self,
        *,
        page: str,
        patch: str,
        author: str,
        connection_id: str,
        intent: str | None = None,
    ) -> WriteResult:
        """Apply a V4A patch to an existing page."""
        if not author:
            return WriteResult(status="error", code="missing-author")

        page_path = self._wiki_dir / f"{page}.md"
        if not page_path.exists():
            return WriteResult(
                status="error",
                code="page-not-found",
                details={"page": page},
            )
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        from llm_wiki.daemon.v4a_patch import (
            PatchConflict,
            PatchParseError,
            apply_patch,
            parse_patch,
        )

        try:
            parsed = parse_patch(patch)
        except PatchParseError as exc:
            return WriteResult(
                status="error",
                code="patch-parse-error",
                details={"message": str(exc)},
            )

        async with self._coordinator.lock_for(page):
            current = page_path.read_text(encoding="utf-8")
            try:
                new_content, apply_result = apply_patch(
                    parsed,
                    current,
                    fuzzy_threshold=self._config.write.patch_fuzzy_match_threshold,
                )
            except PatchConflict as exc:
                return WriteResult(
                    status="error",
                    code="patch-conflict",
                    details={
                        "message": str(exc),
                        "current_excerpt": exc.current_excerpt,
                    },
                )

            page_path.write_text(new_content, encoding="utf-8")
            content_hash = _content_hash(new_content)
            diff_summary = f"+{apply_result.additions} -{apply_result.removals}"

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_update",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=diff_summary,
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            details={"diff_summary": diff_summary},
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_writes.py -k "update" -v`
Expected: PASS for all four update tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/writes.py tests/test_daemon/test_writes.py
git commit -m "feat: phase 6b — PageWriteService.update with V4A patch"
```

---

### Task 13: `PageWriteService.append` (heading insertion)

**Files:**
- Modify: `src/llm_wiki/daemon/writes.py` (add `append` method)
- Modify: `tests/test_daemon/test_writes.py` (append tests)

`append` inserts a new section into an existing page. Heading-lookup semantics from the spec:
- No `after_heading` → append at end of file
- `after_heading` provided, exact single match → insert immediately after that section closes (right before the next heading at the same/shallower level)
- Multiple matches → insert after the first; emit `heading-multiple-matches` warning
- No match → return `heading-not-found` error with `available_headings`

The new section is inserted with a `%% section: <slug> %%` marker so it's immediately viewport-addressable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon/test_writes.py`:

```python
@pytest.mark.asyncio
async def test_append_at_end_of_file_no_after_heading(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo",
        body="## Existing\n\nbody.\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.append(
        page="foo",
        section_heading="New Section",
        body="The new content [[raw/b.pdf]].",
        citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "ok"
    content = (tmp_path / "wiki" / "foo.md").read_text()
    assert "## New Section" in content
    assert "%% section: new-section %%" in content
    assert content.find("## Existing") < content.find("## New Section")


@pytest.mark.asyncio
async def test_append_after_specific_heading(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo",
        body=(
            "## Methods\n\nA method.\n\n"
            "## Results\n\nResults.\n"
        ),
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.append(
        page="foo",
        section_heading="Discussion",
        body="Some discussion [[raw/b.pdf]].",
        citations=["raw/b.pdf"],
        after_heading="Methods",
        author="alice", connection_id="conn-1",
    )
    assert result.status == "ok"
    content = (tmp_path / "wiki" / "foo.md").read_text()
    methods_idx = content.find("## Methods")
    discussion_idx = content.find("## Discussion")
    results_idx = content.find("## Results")
    # Discussion lands between Methods and Results
    assert methods_idx < discussion_idx < results_idx


@pytest.mark.asyncio
async def test_append_heading_not_found_returns_error(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo",
        body="## Methods\n\nA method.\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.append(
        page="foo",
        section_heading="X",
        body="x [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        after_heading="Nonexistent",
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "heading-not-found"
    assert "Methods" in result.details["available_headings"]


@pytest.mark.asyncio
async def test_append_heading_multiple_matches_warns(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo",
        body=(
            "## Methods\n\nFirst.\n\n"
            "## Results\n\nResults.\n\n"
            "## Methods\n\nSecond.\n"
        ),
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.append(
        page="foo",
        section_heading="X",
        body="x [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        after_heading="Methods",
        author="alice", connection_id="conn-1",
    )
    assert result.status == "ok"
    assert any(w["code"] == "heading-multiple-matches" for w in result.warnings)


@pytest.mark.asyncio
async def test_append_refuses_empty_citations(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo", body="## X\n\nx.\n",
        citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.append(
        page="foo",
        section_heading="Y",
        body="y",
        citations=[],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "missing-citations"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_writes.py -k "append" -v`
Expected: FAIL with `AttributeError: 'PageWriteService' object has no attribute 'append'`.

- [ ] **Step 3: Add the `append` method**

Append to `PageWriteService` in `src/llm_wiki/daemon/writes.py`:

```python
    async def append(
        self,
        *,
        page: str,
        section_heading: str,
        body: str,
        citations: list[str],
        author: str,
        connection_id: str,
        after_heading: str | None = None,
        intent: str | None = None,
    ) -> WriteResult:
        """Append a new section to an existing page."""
        if not author:
            return WriteResult(status="error", code="missing-author")
        if (
            self._config.write.require_citations_on_append
            and not citations
        ):
            return WriteResult(
                status="error",
                code="missing-citations",
                details={
                    "message": (
                        "wiki_append requires at least one citation. Post to "
                        "the talk page instead if you cannot cite a source."
                    ),
                },
            )

        page_path = self._wiki_dir / f"{page}.md"
        if not page_path.exists():
            return WriteResult(
                status="error",
                code="page-not-found",
                details={"page": page},
            )
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        async with self._coordinator.lock_for(page):
            current = page_path.read_text(encoding="utf-8")
            lines = current.splitlines(keepends=True)

            section_slug = _slugify(section_heading)
            new_block = (
                f"\n%% section: {section_slug} %%\n"
                f"## {section_heading}\n\n"
                f"{body.strip()}\n"
            )
            warnings: list[dict] = []

            if after_heading is None:
                # Append at end of file
                new_lines = lines + [new_block]
            else:
                # Find heading line(s) — exact match only
                heading_indices = self._find_heading_lines(lines, after_heading)
                if not heading_indices:
                    available = self._list_headings(lines)
                    return WriteResult(
                        status="error",
                        code="heading-not-found",
                        details={
                            "after_heading": after_heading,
                            "available_headings": available,
                        },
                    )
                if len(heading_indices) > 1:
                    warnings.append({
                        "code": "heading-multiple-matches",
                        "count": len(heading_indices),
                        "used_line": heading_indices[0] + 1,
                        "message": (
                            f"after_heading={after_heading!r} matched "
                            f"{len(heading_indices)} headings; using the first."
                        ),
                    })
                # Insert immediately after the matched section closes
                insert_at = self._end_of_section(lines, heading_indices[0])
                new_lines = lines[:insert_at] + [new_block] + lines[insert_at:]

            new_content = "".join(new_lines)
            page_path.write_text(new_content, encoding="utf-8")
            content_hash = _content_hash(new_content)

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_append",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=f"+section {section_slug}",
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            warnings=warnings,
        )

    @staticmethod
    def _find_heading_lines(lines: list[str], heading_text: str) -> list[int]:
        """Return line indices where `## <heading_text>` appears (exact match)."""
        target = f"## {heading_text}"
        return [
            i for i, line in enumerate(lines)
            if line.rstrip("\n").rstrip("\r").strip() == target
        ]

    @staticmethod
    def _list_headings(lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            stripped = line.rstrip("\n").rstrip("\r").strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                out.append(stripped[3:])
        return out

    @staticmethod
    def _end_of_section(lines: list[str], heading_idx: int) -> int:
        """Return the line index where the section starting at `heading_idx` ends.

        The section ends at the next `##` or `#` heading at the same or shallower
        level, or at end of file.
        """
        for i in range(heading_idx + 1, len(lines)):
            stripped = lines[i].lstrip()
            if stripped.startswith("## ") or stripped.startswith("# "):
                return i
        return len(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_writes.py -k "append" -v`
Expected: PASS for all five append tests.

- [ ] **Step 5: Run the full writes test module**

Run: `pytest tests/test_daemon/test_writes.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/writes.py tests/test_daemon/test_writes.py
git commit -m "feat: phase 6b — PageWriteService.append with heading semantics"
```

---

### Task 14: Daemon route handlers (`page-create`, `page-update`, `page-append`)

**Files:**
- Modify: `src/llm_wiki/daemon/server.py` (instantiate `PageWriteService`, add three route handlers, register them in `_route`)
- Create: `tests/test_daemon/test_write_routes.py`

The route handlers are thin shims over `PageWriteService`. Each one:
1. Validates required request fields
2. Extracts the connection ID from the per-client handler context
3. Calls the service method
4. Translates the `WriteResult` into the response dict

The connection ID is generated per-client by `_handle_client` (one UUID per Unix-socket connection) and threaded into `_route` via a context dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_write_routes.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


@pytest_asyncio.fixture
async def write_daemon(tmp_path):
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "write.sock"
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    yield server, sock_path
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


async def _request(
    sock_path: Path,
    msg: dict,
    *,
    connection_id: str | None = "test-conn",
) -> dict:
    """Send a request, auto-injecting connection_id for session continuity.

    Tests within the same test file share `connection_id="test-conn"` by
    default, so multiple writes from one test land in one daemon session.
    Pass `connection_id=None` to omit the field entirely (e.g. to test the
    `missing connection_id` error path) or `connection_id="other"` to
    simulate a separate MCP client.
    """
    if connection_id is not None and "connection_id" not in msg:
        msg = {**msg, "connection_id": connection_id}
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_page_create_route_writes_file(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Test Page",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
        "intent": "test",
    })
    assert resp["status"] == "ok"
    assert resp["page_path"] == "wiki/test-page.md"
    assert "session_id" in resp
    assert (server._vault_root / "wiki" / "test-page.md").exists()


@pytest.mark.asyncio
async def test_page_create_missing_citations_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body",
        "citations": [],
        "author": "alice",
    })
    assert resp["status"] == "error"
    assert resp["code"] == "missing-citations"


@pytest.mark.asyncio
async def test_page_create_missing_author_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
    })
    assert resp["status"] == "error"
    # Handler's required-field loop returns `message`, not `code`
    assert "author" in resp["message"]


@pytest.mark.asyncio
async def test_page_create_missing_connection_id_returns_error(write_daemon):
    """connection_id is required in the request payload — the daemon won't guess."""
    server, sock_path = write_daemon
    resp = await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id=None,  # opt out of auto-injection
    )
    assert resp["status"] == "error"
    assert "connection_id" in resp["message"]


@pytest.mark.asyncio
async def test_page_update_route_applies_patch(write_daemon):
    server, sock_path = write_daemon
    # Create the page first
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "alpha\nbeta\ngamma\n",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    patch = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
        " gamma\n"
        "*** End Patch\n"
    )
    resp = await _request(sock_path, {
        "type": "page-update",
        "page": "foo",
        "patch": patch,
        "author": "alice",
        "intent": "uppercase beta",
    })
    assert resp["status"] == "ok"
    assert "BETA" in (server._vault_root / "wiki" / "foo.md").read_text()


@pytest.mark.asyncio
async def test_page_update_patch_conflict_carries_excerpt(write_daemon):
    server, sock_path = write_daemon
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo", "body": "a\nb\n", "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    bad_patch = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " nonexistent\n"
        "+new\n"
        "*** End Patch\n"
    )
    resp = await _request(sock_path, {
        "type": "page-update",
        "page": "foo",
        "patch": bad_patch,
        "author": "alice",
    })
    assert resp["status"] == "error"
    assert resp["code"] == "patch-conflict"
    assert "current_excerpt" in resp


@pytest.mark.asyncio
async def test_page_append_route_inserts_section(write_daemon):
    server, sock_path = write_daemon
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "## Existing\n\nbody.\n",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    resp = await _request(sock_path, {
        "type": "page-append",
        "page": "foo",
        "section_heading": "New",
        "body": "new content [[raw/y.pdf]]",
        "citations": ["raw/y.pdf"],
        "author": "alice",
    })
    assert resp["status"] == "ok"
    content = (server._vault_root / "wiki" / "foo.md").read_text()
    assert "## New" in content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_write_routes.py -v`
Expected: FAIL — `_route` doesn't dispatch the new request types.

- [ ] **Step 3: Wire `PageWriteService` and `CommitService` into `DaemonServer.__init__` / `start`**

Edit `src/llm_wiki/daemon/server.py`. In `DaemonServer.__init__`, add fields:

```python
        self._commit_lock = asyncio.Lock()
        self._commit_service: "CommitService | None" = None
        self._session_registry: "SessionRegistry | None" = None
        self._page_write_service: "PageWriteService | None" = None
        self._write_coordinator = None  # set in start()
```

In `DaemonServer.start()`, after building the existing maintenance substrate, add:

```python
        from llm_wiki.daemon.commit import CommitService
        from llm_wiki.daemon.sessions import SessionRegistry, recover_sessions
        from llm_wiki.daemon.writer import WriteCoordinator
        from llm_wiki.daemon.writes import PageWriteService
        from llm_wiki.traverse.llm_client import LLMClient

        self._write_coordinator = WriteCoordinator()
        self._session_registry = SessionRegistry(self._config.sessions)

        commit_llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
        self._commit_service = CommitService(
            vault_root=self._vault_root,
            llm=commit_llm,
            lock=self._commit_lock,
        )
        self._page_write_service = PageWriteService(
            vault=self._vault,
            vault_root=self._vault_root,
            config=self._config,
            write_coordinator=self._write_coordinator,
            registry=self._session_registry,
            commit_service=self._commit_service,
        )

        # Recovery: settle any orphaned journals from a prior crash
        await recover_sessions(state_dir=state_dir, commit_service=self._commit_service)
```

- [ ] **Step 4: Add the three route handlers to `DaemonServer`**

Add these methods to `DaemonServer`:

```python
    async def _handle_page_create(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("title", "body", "citations", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.create(
            title=request["title"],
            body=request["body"],
            citations=list(request["citations"]),
            tags=list(request.get("tags", [])),
            author=request["author"],
            connection_id=request["connection_id"],
            intent=request.get("intent"),
            force=bool(request.get("force", False)),
        )
        return _serialize_write_result(result)

    async def _handle_page_update(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("page", "patch", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.update(
            page=request["page"],
            patch=request["patch"],
            author=request["author"],
            connection_id=request["connection_id"],
            intent=request.get("intent"),
        )
        return _serialize_write_result(result)

    async def _handle_page_append(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("page", "section_heading", "body", "citations", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.append(
            page=request["page"],
            section_heading=request["section_heading"],
            body=request["body"],
            citations=list(request["citations"]),
            author=request["author"],
            connection_id=request["connection_id"],
            after_heading=request.get("after_heading"),
            intent=request.get("intent"),
        )
        return _serialize_write_result(result)
```

Add the serializer at module level (next to the existing `_serialize_result`):

```python
def _serialize_write_result(result) -> dict:
    out = {
        "status": result.status,
        "page_path": result.page_path,
        "journal_id": result.journal_id,
        "session_id": result.session_id,
        "content_hash": result.content_hash,
    }
    if result.warnings:
        out["warnings"] = result.warnings
    if result.code is not None:
        out["code"] = result.code
    if result.details:
        out.update(result.details)
    return out
```

- [ ] **Step 5: Wire the new routes into `_route`**

The existing `_handle_client` and `_route` methods don't need any per-connection UUID generation — under Option C, `connection_id` is supplied by the caller (the MCP server) in the request payload, not derived from the Unix-socket connection. The daemon's protocol is one-message-per-connection, so a per-connection UUID would mean one session per write, which defeats session grouping. Each write handler extracts `connection_id` from the request directly (see Step 4).

Just add the dispatch cases to `_route`:

```python
    async def _route(self, request: dict) -> dict:
        req_type = request.get("type", "")
        match req_type:
            # ... existing cases unchanged ...
            case "page-create":
                return await self._handle_page_create(request)
            case "page-update":
                return await self._handle_page_update(request)
            case "page-append":
                return await self._handle_page_append(request)
            case _:
                return {"status": "error", "message": f"Unknown request type: {req_type}"}
```

`_handle_client` is unchanged from its Phase 5 form — no UUID generation, no `connection_id` threading. The handlers extract everything they need from the request payload itself.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_write_routes.py -v`
Expected: PASS for all six write-route tests.

- [ ] **Step 7: Run the full daemon test suite to confirm no regressions**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass. Note: existing daemon tests called `_route(request)` without `connection_id`; the default value `"default"` keeps them working.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_write_routes.py
git commit -m "feat: phase 6b — page-create / page-update / page-append routes"
```

---

### Task 15: Daemon `session-close` route + idempotency

**Files:**
- Modify: `src/llm_wiki/daemon/server.py` (add `_handle_session_close` + dispatch case)
- Create: `tests/test_daemon/test_session_close_route.py`

The `session-close` route gives swarm orchestrators a way to explicitly settle a session ("agent-3 is done"). It calls the same settle pipeline as the inactivity timer and shutdown — there is exactly one settle code path. Idempotent: closing an already-settled session returns `{"status": "ok", "settled": false}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_session_close_route.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


async def _request(
    sock_path: Path,
    msg: dict,
    *,
    connection_id: str | None = "test-conn",
) -> dict:
    """Send a request, auto-injecting connection_id for session continuity.

    Tests within the same test file share `connection_id="test-conn"` by
    default, so multiple writes from one test land in one daemon session.
    Pass `connection_id=None` to omit the field entirely (e.g. to test the
    `missing connection_id` error path) or `connection_id="other"` to
    simulate a separate MCP client.
    """
    if connection_id is not None and "connection_id" not in msg:
        msg = {**msg, "connection_id": connection_id}
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest_asyncio.fixture
async def write_daemon(tmp_path):
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "sc.sock"
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    yield server, sock_path
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


@pytest.mark.asyncio
async def test_session_close_settles_active_session(write_daemon):
    server, sock_path = write_daemon
    # Create a page so there's a session with one journal entry
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    resp = await _request(sock_path, {
        "type": "session-close",
        "author": "alice",
    })
    assert resp["status"] == "ok"
    assert resp["settled"] is True
    assert resp["commit_sha"]

    # The commit should be in git
    log = subprocess.run(
        ["git", "-C", str(server._vault_root), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "alice" in log


@pytest.mark.asyncio
async def test_session_close_idempotent(write_daemon):
    server, sock_path = write_daemon
    # Close without ever opening — must not error
    resp = await _request(sock_path, {
        "type": "session-close",
        "author": "noone",
    })
    assert resp["status"] == "ok"
    assert resp["settled"] is False


@pytest.mark.asyncio
async def test_session_close_missing_author_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {"type": "session-close"})
    assert resp["status"] == "error"
    assert "author" in resp["message"]


@pytest.mark.asyncio
async def test_session_close_missing_connection_id_returns_error(write_daemon):
    """connection_id is required: the daemon won't guess which session to settle."""
    server, sock_path = write_daemon
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id=None,  # opt out of auto-injection
    )
    assert resp["status"] == "error"
    assert "connection_id" in resp["message"]


@pytest.mark.asyncio
async def test_session_close_only_settles_matching_connection(write_daemon):
    """Two sessions with same author, different connection_id → close affects only one."""
    server, sock_path = write_daemon
    # Open two sessions for alice on different connection_ids
    await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Page A",
            "body": "body A [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id="conn-A",
    )
    await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Page B",
            "body": "body B [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id="conn-B",
    )

    # Close only conn-A
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id="conn-A",
    )
    assert resp["settled"] is True

    # conn-B's session is still open — closing it produces another commit
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id="conn-B",
    )
    assert resp["settled"] is True

    # Two commits total (in addition to the initial)
    log = subprocess.run(
        ["git", "-C", str(server._vault_root), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 3  # initial + conn-A settle + conn-B settle
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_daemon/test_session_close_route.py -v`
Expected: FAIL — the route doesn't exist yet.

- [ ] **Step 3: Add the route handler**

Add to `DaemonServer`:

```python
    async def _handle_session_close(self, request: dict) -> dict:
        for f in ("author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        if self._session_registry is None or self._commit_service is None:
            return {"status": "error", "message": "Session machinery not initialized"}

        from llm_wiki.daemon.sessions import load_journal

        sess = self._session_registry.get_active(
            request["author"], request["connection_id"],
        )
        if sess is None:
            # Idempotent: closing a session that was never opened (or already
            # settled) is not an error. The caller may be the inactivity timer
            # racing with an explicit close, or a swarm orchestrator closing
            # eagerly.
            return {"status": "ok", "settled": False}

        entries = load_journal(sess.journal_path)
        result = await self._commit_service.settle_with_fallback(sess, entries)
        self._session_registry.close(sess)
        return {
            "status": "ok",
            "settled": True,
            "commit_sha": result.commit_sha,
        }
```

Add the dispatch case in `_route`:

```python
            case "session-close":
                return await self._handle_session_close(request)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_daemon/test_session_close_route.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Run the full daemon test suite**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_session_close_route.py
git commit -m "feat: phase 6b — session-close route (idempotent)"
```

---

### Task 16: Settle on graceful shutdown

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:DaemonServer.stop()`
- Modify: `tests/test_daemon/test_session_close_route.py` (or test_session_lifecycle.py)

When the daemon shuts down cleanly, every open session must settle before the process exits. This uses the same `CommitService.settle_with_fallback` path as `session-close` and the inactivity timer.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon/test_session_close_route.py`:

```python
@pytest.mark.asyncio
async def test_daemon_shutdown_settles_open_sessions(tmp_path):
    """When DaemonServer.stop() is called, every open session is settled into git."""
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "shutdown.sock"
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Create a page (opens a session, journals one entry)
        await _request(sock_path, {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        })
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()

    # The commit should be in git after stop()
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 2  # initial + alice's commit
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_daemon/test_session_close_route.py::test_daemon_shutdown_settles_open_sessions -v`
Expected: FAIL — `stop()` does not settle sessions yet.

- [ ] **Step 3: Settle in `stop()`**

Edit `DaemonServer.stop()`. Insert the settle pass at the very top, before tearing down the scheduler:

```python
    async def stop(self) -> None:
        """Shut down the maintenance substrate, then the server."""
        # Phase 6b: settle every open session before tearing down anything else
        if self._session_registry is not None and self._commit_service is not None:
            from llm_wiki.daemon.sessions import load_journal

            for sess in list(self._session_registry.all_sessions()):
                try:
                    entries = load_journal(sess.journal_path)
                    if entries:
                        await self._commit_service.settle_with_fallback(sess, entries)
                except Exception:
                    logger.exception(
                        "Failed to settle session %s on shutdown", sess.id,
                    )
                finally:
                    self._session_registry.close(sess)

        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        # ... rest unchanged ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_daemon/test_session_close_route.py::test_daemon_shutdown_settles_open_sessions -v`
Expected: PASS.

- [ ] **Step 5: Run the full daemon test suite**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_session_close_route.py
git commit -m "feat: phase 6b — settle open sessions on graceful shutdown"
```

---

### Task 17: Inactivity-timer settle + write-count cap

**Files:**
- Modify: `src/llm_wiki/daemon/server.py` (add `_inactivity_timer_loop`, start it in `start`, cancel in `stop`)
- Modify: `src/llm_wiki/daemon/writes.py` (emit `session-cap-approaching` warning + force-settle on cap)
- Create: `tests/test_daemon/test_session_lifecycle.py`

Two settle triggers land here:

1. **Inactivity timer.** A background task wakes every `inactivity_timeout_seconds / 2` and checks every active session. If `now - session.last_write_at >= inactivity_timeout_seconds`, settle the session.
2. **Write-count cap.** After each write, if `session.write_count >= write_count_cap`, force-settle the session immediately and start a fresh one for the next write. The agent gets `warnings: [{code: "session-cap-approaching", ...}]` once `session.write_count >= floor(cap * cap_warn_ratio)`.

Both triggers go through `CommitService.settle_with_fallback` — same code path as `session-close` and shutdown.

For testability, the inactivity loop polls at a configurable interval (default = `inactivity_timeout_seconds / 2`). Tests can construct a daemon with `inactivity_timeout_seconds=1` and watch for the settle within a few seconds.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon/test_session_lifecycle.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import SessionsConfig, WikiConfig
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


async def _request(
    sock_path: Path,
    msg: dict,
    *,
    connection_id: str | None = "test-conn",
) -> dict:
    """Send a request, auto-injecting connection_id for session continuity.

    Tests within the same test file share `connection_id="test-conn"` by
    default, so multiple writes from one test land in one daemon session.
    Pass `connection_id=None` to omit the field entirely (e.g. to test the
    `missing connection_id` error path) or `connection_id="other"` to
    simulate a separate MCP client.
    """
    if connection_id is not None and "connection_id" not in msg:
        msg = {**msg, "connection_id": connection_id}
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_inactivity_timer_settles_quiet_session(tmp_path):
    """A session with no writes for inactivity_timeout_seconds is settled automatically."""
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "ina.sock"
    config = WikiConfig(
        sessions=SessionsConfig(inactivity_timeout_seconds=1),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        await _request(sock_path, {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        })

        # Wait long enough for the inactivity timer to fire
        await asyncio.sleep(2.5)

        # The commit should now be in git
        log = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "--format=%H"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert len(log) >= 2  # initial + the inactivity-settled commit
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_write_count_cap_force_settles(tmp_path):
    """When write_count_cap is reached, the session settles immediately."""
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "cap.sock"
    config = WikiConfig(
        sessions=SessionsConfig(write_count_cap=2, inactivity_timeout_seconds=300),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Three creates → after the 2nd one, the cap is hit and the session settles
        for i in range(3):
            await _request(sock_path, {
                "type": "page-create",
                "title": f"Page {i}",
                "body": f"body {i} [[raw/x.pdf]]",
                "citations": ["raw/x.pdf"],
                "author": "alice",
            })

        log = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "--format=%H"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        # initial + at least one cap-triggered settle commit
        assert len(log) >= 2
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_session_cap_warning_emitted(tmp_path):
    """At floor(cap * cap_warn_ratio), the response carries a warning."""
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "warn.sock"
    config = WikiConfig(
        sessions=SessionsConfig(
            write_count_cap=10, cap_warn_ratio=0.6, inactivity_timeout_seconds=300,
        ),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        last_resp = None
        for i in range(7):  # warning kicks in at floor(10 * 0.6) = 6
            last_resp = await _request(sock_path, {
                "type": "page-create",
                "title": f"P{i}",
                "body": f"b{i} [[raw/x.pdf]]",
                "citations": ["raw/x.pdf"],
                "author": "alice",
            })
        assert last_resp is not None
        warnings = last_resp.get("warnings", [])
        assert any(w["code"] == "session-cap-approaching" for w in warnings)
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

Run: `pytest tests/test_daemon/test_session_lifecycle.py -v`
Expected: FAIL — neither the inactivity timer nor the cap logic exists yet.

- [ ] **Step 3: Add the cap warning + force-settle to `PageWriteService`**

Inside `PageWriteService`, add a small helper that runs after each successful write. Modify each of `create`, `update`, `append` to call it before returning. Add this method:

```python
    def _maybe_warn_cap(self, session: Session, warnings: list[dict]) -> None:
        cap = self._config.sessions.write_count_cap
        ratio = self._config.sessions.cap_warn_ratio
        threshold = int(cap * ratio)
        if session.write_count >= threshold:
            warnings.append({
                "code": "session-cap-approaching",
                "writes_used": session.write_count,
                "writes_remaining": max(0, cap - session.write_count),
                "message": (
                    "Session is approaching the write count cap. Call "
                    "wiki_session_close at a clean breakpoint before the "
                    "daemon force-settles."
                ),
            })

    async def _maybe_force_settle(self, session: Session) -> None:
        cap = self._config.sessions.write_count_cap
        if session.write_count >= cap:
            from llm_wiki.daemon.sessions import load_journal
            entries = load_journal(session.journal_path)
            await self._commit_service.settle_with_fallback(session, entries)
            self._registry.close(session)
```

In each of `create`, `update`, `append`, after `append_journal_entry(...)` and before building the `WriteResult`:

```python
            warnings_for_response: list[dict] = list(getattr(result, "warnings", []) or [])
            # (Or use the local `warnings` list already in scope for `append`.)
            self._maybe_warn_cap(session, warnings_for_response)
        await self._maybe_force_settle(session)
```

For `create`/`update` where there is no existing `warnings` local, build one before constructing the `WriteResult`. For `append`, the local `warnings` list already exists — just call `_maybe_warn_cap(session, warnings)` before exiting the lock and call `_maybe_force_settle(session)` after.

Refer to the implementation snippet at the end of this task for the full updated `create`/`update`/`append` shapes.

- [ ] **Step 4: Add the inactivity timer to `DaemonServer`**

In `DaemonServer.__init__`, add:

```python
        self._inactivity_task: "asyncio.Task | None" = None
```

In `DaemonServer.start()`, after instantiating `_page_write_service`, start the timer:

```python
        self._inactivity_task = asyncio.create_task(self._inactivity_loop())
```

Add the loop:

```python
    async def _inactivity_loop(self) -> None:
        """Settle sessions whose last_write_at is older than the timeout.

        KNOWN RACE WINDOW (low-probability, documented for honesty):
        Between `load_journal(sess.journal_path)` reading the entries and
        `settle_with_fallback` archiving the file, a concurrent write to
        the same session could append a new entry to the journal. That
        new entry would be on disk in the to-be-archived file but never
        included in the commit. After settle, `_session_registry.close(sess)`
        removes the session, so the next write from the same author opens
        a fresh session — leaving the orphan entry as a permanent gap.

        This is extremely unlikely in practice: the inactivity loop only
        fires after `inactivity_timeout_seconds` of zero activity, so by
        definition the agent has been quiet. The window between
        `load_journal` and the journal-archive step inside settle is also
        narrow (single-digit milliseconds in normal conditions).

        If this ever bites in practice, the fix is to acquire the per-page
        write lock around the journal-read + settle + close sequence so a
        concurrent write blocks until settle completes. Not done now to
        keep this hot path simple.
        """
        import datetime as _dt
        from llm_wiki.daemon.sessions import load_journal

        timeout = self._config.sessions.inactivity_timeout_seconds
        poll_interval = max(0.5, timeout / 2)
        try:
            while True:
                await asyncio.sleep(poll_interval)
                if self._session_registry is None or self._commit_service is None:
                    continue
                now = _dt.datetime.now(_dt.timezone.utc)
                stale: list[Session] = []
                for sess in list(self._session_registry.all_sessions()):
                    try:
                        last = _dt.datetime.fromisoformat(sess.last_write_at)
                    except ValueError:
                        continue
                    if (now - last).total_seconds() >= timeout:
                        stale.append(sess)
                for sess in stale:
                    try:
                        entries = load_journal(sess.journal_path)
                        if entries:
                            await self._commit_service.settle_with_fallback(sess, entries)
                    except Exception:
                        logger.exception(
                            "Inactivity settle failed for session %s", sess.id,
                        )
                    finally:
                        self._session_registry.close(sess)
        except asyncio.CancelledError:
            return
```

In `DaemonServer.stop()`, cancel the timer alongside the existing teardown:

```python
        if self._inactivity_task is not None:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except asyncio.CancelledError:
                pass
            self._inactivity_task = None
```

- [ ] **Step 5: Update `PageWriteService.create` to thread warnings through**

For reference, the updated `create` tail (replace the existing tail starting from `async with self._coordinator.lock_for(slug):`). Note that `journal_path_rel` is computed earlier in the function (immediately after `page_path = self._wiki_dir / f"{slug}.md"`):

```python
        warnings: list[dict] = []
        async with self._coordinator.lock_for(slug):
            page_path.parent.mkdir(parents=True, exist_ok=True)
            content = self._build_page_content(title, body, citations, tags or [])
            page_path.write_text(content, encoding="utf-8")
            content_hash = _content_hash(content)

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_create",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=f"created {slug}",
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)
            self._maybe_warn_cap(session, warnings)

        await self._maybe_force_settle(session)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            warnings=warnings,
        )
```

Apply the same `warnings: list[dict] = []` + `_maybe_warn_cap` + `_maybe_force_settle` pattern to `update` and `append`.

- [ ] **Step 6: Run the lifecycle tests to verify they pass**

Run: `pytest tests/test_daemon/test_session_lifecycle.py -v`
Expected: PASS for all three lifecycle tests.

- [ ] **Step 7: Run the full daemon test suite**

Run: `pytest tests/test_daemon -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py src/llm_wiki/daemon/writes.py \
        tests/test_daemon/test_session_lifecycle.py
git commit -m "feat: phase 6b — inactivity timer + write-count cap settle"
```

---

### Task 18: AST hard-rule test

**Files:**
- Create: `tests/test_daemon/test_ast_hard_rule.py`

This is the most important test in Phase 6b. It mechanically enforces the principle "**unsupervised processes never write body content**" by AST-walking every module under `daemon/`, `audit/`, `librarian/`, `adversary/`, `talk/` and failing if any background-worker code path can reach the four MCP-only routes (`page-create`, `page-update`, `page-append`, `session-close`) or the helpers that implement them.

The test fails on **any** import or attribute access of forbidden symbols from inside a coroutine that the scheduler can reach. The known background workers (auditor, librarian, authority_recalc, adversary, talk_summary) are walked transitively.

Implementation strategy — three test functions, layered defense:

1. **`test_background_workers_never_reference_write_surface`** — directory walk over `audit/`, `librarian/`, `adversary/`, `talk/`. Catches any file in those subtrees that imports or references the forbidden symbols. The bulk of the enforcement.
2. **`test_daemon_write_surface_files_are_known`** — walks every file under `daemon/` that is NOT in the explicit `DAEMON_WRITE_SURFACE_FILES` allowlist. Catches new daemon helper modules that accidentally import the write surface. The exempt set is kept minimal (only `server.py` and `writes.py`) so almost every daemon file gets checked.
3. **`test_register_maintenance_workers_never_reach_write_surface`** — surgically walks `_register_maintenance_workers` (and its nested `run_auditor` / `run_librarian` / `run_authority_recalc` / `run_adversary` / future `run_talk_summary` closures) inside `daemon/server.py`. Closes the gap that the file-level exemption of `server.py` would otherwise leave open: a future regression that adds `from llm_wiki.daemon.writes import PageWriteService` inside one of those closures would slip past test 1 (wrong directory) and test 2 (server.py is exempt) but is caught by test 3.

This is intentionally **conservative** — false positives are preferable to false negatives. If the test starts failing on a legitimate change, the right answer is to refactor the code so the background worker never *imports* the forbidden symbol, even if it doesn't *call* it.

- [ ] **Step 1: Write the test (which is the production check)**

Create `tests/test_daemon/test_ast_hard_rule.py`:

```python
"""The hard-rule test: background workers never reach the write surface.

This test enforces PHILOSOPHY.md Principle 3 mechanically. It walks every
module in the background-worker subtree (daemon/, audit/, librarian/,
adversary/, talk/) and fails if any of them references the four MCP-only
write routes or the PageWriteService that implements them.

If this test fails, do NOT add an exception. Refactor the offending code
so the background worker doesn't even import the forbidden symbol.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Forbidden symbol names. Any AST node referencing these from a
# background-worker module is a hard-rule violation.
FORBIDDEN_NAMES = {
    "PageWriteService",
    "_handle_page_create",
    "_handle_page_update",
    "_handle_page_append",
    "_handle_session_close",
}
FORBIDDEN_ROUTE_STRINGS = {
    "page-create",
    "page-update",
    "page-append",
    "session-close",
}

# Modules that are background-worker code paths. The scheduler reaches
# all of these via run_auditor / run_librarian / run_authority_recalc /
# run_adversary / run_talk_summary in daemon/server.py.
BACKGROUND_MODULE_DIRS = [
    "src/llm_wiki/audit",
    "src/llm_wiki/librarian",
    "src/llm_wiki/adversary",
    "src/llm_wiki/talk",
]

# Modules in daemon/ that are themselves the write surface (NOT background).
# These are explicitly allowed to import PageWriteService etc. The hard-rule
# test must not flag them.
#
# Kept deliberately tight: only files that legitimately need to reference
# the forbidden symbols are exempted. `commit.py`, `sessions.py`,
# `v4a_patch.py`, `name_similarity.py`, and `writer.py` are NOT exempted —
# they don't import PageWriteService or _handle_page_*, and walking them
# anyway gives us defense in depth against future regressions where one of
# them ends up importing the write surface.
DAEMON_WRITE_SURFACE_FILES = {
    "src/llm_wiki/daemon/server.py",
    "src/llm_wiki/daemon/writes.py",
}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _walk_files(dir_relative: str) -> list[pathlib.Path]:
    base = _repo_root() / dir_relative
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*.py") if p.is_file())


def _violations_in_file(path: pathlib.Path) -> list[str]:
    """Return human-readable violation messages for a single file."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"AST parse failed for {path}: {exc}")

    violations: list[str] = []

    for node in ast.walk(tree):
        # Plain identifier reference
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(
                f"{path}:{node.lineno}: references forbidden symbol {node.id!r}"
            )
        # Attribute access (e.g. server._handle_page_create)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(
                f"{path}:{node.lineno}: references forbidden attribute {node.attr!r}"
            )
        # ImportFrom
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{path}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
        # String literals containing forbidden route names
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in FORBIDDEN_ROUTE_STRINGS:
                violations.append(
                    f"{path}:{node.lineno}: contains forbidden route string {node.value!r}"
                )

    return violations


def test_background_workers_never_reference_write_surface():
    """No file in audit/, librarian/, adversary/, talk/ references the write surface."""
    all_violations: list[str] = []
    for dir_rel in BACKGROUND_MODULE_DIRS:
        for path in _walk_files(dir_rel):
            all_violations.extend(_violations_in_file(path))

    if all_violations:
        pytest.fail(
            "Hard-rule violation: background-worker code references the write "
            "surface. Refactor so the import never happens — do NOT add the "
            "file to an allowlist.\n\n" + "\n".join(all_violations)
        )


def test_daemon_write_surface_files_are_known():
    """Sanity check: every file in daemon/ is either write-surface or excluded.

    This catches the case where someone adds a new daemon/ file that
    imports PageWriteService and forgets to update DAEMON_WRITE_SURFACE_FILES.
    """
    daemon_dir = _repo_root() / "src/llm_wiki/daemon"
    actual_files = {
        str(p.relative_to(_repo_root()))
        for p in daemon_dir.rglob("*.py")
        if p.name != "__init__.py" and p.name != "__main__.py"
    }
    unknown = actual_files - DAEMON_WRITE_SURFACE_FILES
    # The remaining files are dispatcher, scheduler, llm_queue, etc. — none of
    # which should reference the write surface either. Walk them too.
    background_daemon_violations: list[str] = []
    for rel in unknown:
        path = _repo_root() / rel
        background_daemon_violations.extend(_violations_in_file(path))
    if background_daemon_violations:
        pytest.fail(
            "A daemon/ file outside the write-surface set references the write "
            "surface:\n\n" + "\n".join(background_daemon_violations)
        )


def _violations_in_function(
    func_node: ast.AST, source_label: str,
) -> list[str]:
    """Walk a single function (and any nested functions inside it).

    Returns violation strings for any FORBIDDEN_NAMES references,
    forbidden attribute accesses, forbidden ImportFrom names, or forbidden
    route-string constants found anywhere in the function's subtree.
    Used by `test_register_maintenance_workers_never_reach_write_surface`
    to surgically check the background-worker bodies inside `server.py`
    even though `server.py` is otherwise exempt from the directory walk.
    """
    violations: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(
                f"{source_label}:{node.lineno}: references forbidden symbol {node.id!r}"
            )
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(
                f"{source_label}:{node.lineno}: references forbidden attribute {node.attr!r}"
            )
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{source_label}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in FORBIDDEN_ROUTE_STRINGS:
                violations.append(
                    f"{source_label}:{node.lineno}: contains forbidden route string {node.value!r}"
                )
    return violations


def test_register_maintenance_workers_never_reach_write_surface():
    """Walk `_register_maintenance_workers` (and its nested closures) for violations.

    `daemon/server.py` is in `DAEMON_WRITE_SURFACE_FILES` because it
    legitimately holds the `_handle_page_*` route handlers. But it ALSO
    holds `_register_maintenance_workers`, which defines the background
    worker entry points (`run_auditor`, `run_librarian`, `run_authority_recalc`,
    `run_adversary`, future `run_talk_summary`) as nested async closures.
    Those closures are unsupervised code paths and MUST NOT reference the
    write surface — but the directory-level exemption would otherwise
    silently pass them.

    This test parses `server.py`, finds `_register_maintenance_workers`,
    and walks every node in its subtree (including nested FunctionDefs)
    for FORBIDDEN_NAMES / FORBIDDEN_ROUTE_STRINGS. If a future change
    adds `from llm_wiki.daemon.writes import PageWriteService` inside
    one of the closures, this test catches it where the directory walk
    cannot.
    """
    server_path = _repo_root() / "src/llm_wiki/daemon/server.py"
    text = server_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(server_path))
    except SyntaxError as exc:
        pytest.fail(f"AST parse failed for {server_path}: {exc}")

    target: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_register_maintenance_workers":
                target = node
                break

    if target is None:
        pytest.fail(
            "Could not find _register_maintenance_workers in "
            f"{server_path} — has it been renamed or removed? Update this "
            "test to point at the new background-worker registration site."
        )

    violations = _violations_in_function(
        target, source_label=str(server_path),
    )
    if violations:
        pytest.fail(
            "Hard-rule violation: a background worker registered in "
            "_register_maintenance_workers references the write surface. "
            "Refactor the worker so it never imports PageWriteService or "
            "reaches the page-create / page-update / page-append / "
            "session-close routes.\n\n" + "\n".join(violations)
        )
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_daemon/test_ast_hard_rule.py -v`
Expected: PASS. If it fails, the failure message points at the offending file:line. **Do not add allowlist entries** — refactor instead.

- [ ] **Step 3: Verify the test catches a real violation (sanity check)**

Run the sanity check **twice** — once for the directory walk, once for the surgical `_register_maintenance_workers` walker. Both must catch their respective failure modes.

**3a. Directory walk catches an audit/ violation:**
Temporarily add `from llm_wiki.daemon.writes import PageWriteService` to the top of `src/llm_wiki/audit/auditor.py`. Run `pytest tests/test_daemon/test_ast_hard_rule.py::test_background_workers_never_reference_write_surface -v`. Expected: FAIL with the file path and line number called out. **Revert the change** and re-run to confirm.

**3b. Surgical walker catches a server.py background-worker violation:**
Temporarily edit `src/llm_wiki/daemon/server.py` and add `from llm_wiki.daemon.writes import PageWriteService` inside the body of `run_auditor` (the nested async closure). Run `pytest tests/test_daemon/test_ast_hard_rule.py::test_register_maintenance_workers_never_reach_write_surface -v`. Expected: FAIL with the file path and line number, citing `'PageWriteService'` as the forbidden symbol. **Revert the change** and re-run to confirm. This is the test that closes the server.py exemption gap — if you skip this sanity check, you're trusting the walker without ever having seen it bite.

- [ ] **Step 4: Run the full test suite to confirm no other regressions**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_daemon/test_ast_hard_rule.py
git commit -m "test: phase 6b — AST hard-rule test (background workers never write)"
```

---

### Task 19: Session-aware ingest

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py` (route writes through `PageWriteService`, accept author + write_service)
- Modify: `src/llm_wiki/daemon/server.py:_handle_ingest` (require `author` and `connection_id` from request, capture pages_created/updated, apply response cap)
- Modify: `src/llm_wiki/cli/main.py:ingest` (pass `author="cli"` and a per-invocation `connection_id` UUID in the request)
- Create: `tests/test_ingest/test_session_aware_ingest.py`

The ingest agent currently calls `write_page` from `ingest/page_writer.py` directly. After Phase 6b, every ingest operation must go through `PageWriteService.create` (for new pages) or `PageWriteService.append` (for adding sections to existing pages), so that ingest writes journal under the calling agent's session and land in the same commit pipeline as any other supervised write.

The daemon's `ingest` route requires both `author` and `connection_id` (no implicit defaults — the daemon refuses ingest requests that omit either). The CLI ingest command generates a per-invocation `connection_id` UUID at startup, so each `wiki ingest` invocation lands in its own session and settles cleanly when the inactivity timer fires (or at daemon shutdown). The MCP server's `wiki_ingest` tool reuses the MCP session's connection_id like every other write tool.

The response is capped at `mcp.ingest_response_max_pages` page names; if more were affected, a `response-truncated` warning is included.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest/test_session_aware_ingest.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.sessions import SessionRegistry, load_journal
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.daemon.writes import PageWriteService
from llm_wiki.vault import Vault


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_write_service(tmp_path: Path):
    config = WikiConfig()
    vault = Vault.scan(tmp_path)
    return PageWriteService(
        vault=vault,
        vault_root=tmp_path,
        config=config,
        write_coordinator=WriteCoordinator(),
        registry=SessionRegistry(config.sessions),
        commit_service=CommitService(
            vault_root=tmp_path, llm=None, lock=asyncio.Lock(),
        ),
    )


@pytest.mark.asyncio
async def test_ingest_creates_pages_via_write_service(tmp_path):
    """An ingest run produces journal entries under the calling agent's session."""
    from llm_wiki.ingest.agent import IngestAgent

    _init_git_repo(tmp_path)
    service = _make_write_service(tmp_path)
    config = WikiConfig()

    # Mock LLM that returns one concept and a page
    from llm_wiki.traverse.llm_client import LLMResponse

    responses = iter([
        # Concept extraction response
        '{"concepts": [{"name": "test-concept", "title": "Test Concept", '
        '"passages": ["This is test content."]}]}',
        # Page content response
        '{"sections": [{"name": "overview", "heading": "Overview", '
        '"content": "Test page content [[raw/source.txt]]."}]}',
    ])

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="ingest"):
            return LLMResponse(content=next(responses), tokens_used=10)

    # Create the source file
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source = raw_dir / "source.txt"
    source.write_text("Test source content for ingestion.")

    agent = IngestAgent(MockLLM(), config)
    result = await agent.ingest(
        source, tmp_path,
        author="alice",
        connection_id="conn-1",
        write_service=service,
    )
    assert result.pages_created or result.pages_updated

    # The journal should carry an entry for the new page
    sess = service._registry.lookup_by_author("alice")
    assert sess is not None
    entries = load_journal(sess.journal_path)
    assert len(entries) >= 1
    assert any(e.tool == "wiki_create" for e in entries)


@pytest.mark.asyncio
async def test_ingest_route_requires_author_or_defaults_to_cli(tmp_path):
    """The daemon's ingest route requires an author or defaults to 'cli'."""
    # This is tested via the daemon route in test_write_routes.py / test_ingest_route.py;
    # here we just exercise the agent with author='cli' to confirm it works.
    from llm_wiki.ingest.agent import IngestAgent

    _init_git_repo(tmp_path)
    service = _make_write_service(tmp_path)
    config = WikiConfig()

    from llm_wiki.traverse.llm_client import LLMResponse
    responses = iter([
        '{"concepts": [{"name": "x", "title": "X", "passages": ["x."]}]}',
        '{"sections": [{"name": "o", "heading": "O", "content": "x [[raw/s.txt]]."}]}',
    ])

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="ingest"):
            return LLMResponse(content=next(responses), tokens_used=10)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source = raw_dir / "s.txt"
    source.write_text("x")

    agent = IngestAgent(MockLLM(), config)
    result = await agent.ingest(
        source, tmp_path,
        author="cli",
        connection_id="cli-conn",
        write_service=service,
    )
    assert result.pages_created or result.pages_updated
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_ingest/test_session_aware_ingest.py -v`
Expected: FAIL — `IngestAgent.ingest` doesn't accept `write_service`.

- [ ] **Step 3: Update `IngestAgent.ingest` to accept the new arguments and route through `PageWriteService`**

Edit `src/llm_wiki/ingest/agent.py`. Replace the entire `ingest` method:

```python
    async def ingest(
        self,
        source_path: Path,
        vault_root: Path,
        *,
        author: str = "cli",
        connection_id: str = "cli",
        write_service: "PageWriteService | None" = None,
    ) -> IngestResult:
        """Ingest one source file into the wiki.

        When `write_service` is provided, all page creates/appends are routed
        through it so they journal under the caller's session and land in the
        commit pipeline. When `write_service` is None, falls back to the
        legacy direct-write path (used by older code paths only — new code
        should always pass write_service).
        """
        result = IngestResult(source_path=source_path)
        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")

        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        extraction = await extract_text(source_path)
        if not extraction.success:
            logger.warning(
                "Extraction failed for %s: %s", source_path, extraction.error
            )
            return result

        budget = self._config.budgets.default_ingest
        messages = compose_concept_extraction_messages(
            source_text=extraction.content,
            source_ref=source_ref,
            budget=budget,
        )
        response = await self._llm.complete(messages, temperature=0.3, priority="ingest")
        concepts = parse_concept_extraction(response.content)

        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        wiki_dir.mkdir(parents=True, exist_ok=True)
        for concept in concepts:
            page_messages = compose_page_content_messages(
                concept_title=concept.title,
                passages=concept.passages,
                source_ref=source_ref,
            )
            page_response = await self._llm.complete(
                page_messages, temperature=0.5, priority="ingest"
            )
            sections = parse_page_content(page_response.content)
            if not sections:
                logger.warning(
                    "No sections generated for concept %r from %s",
                    concept.name, source_path,
                )
                continue

            if write_service is not None:
                await self._write_via_service(
                    write_service, wiki_dir, concept, sections, source_ref,
                    author=author, connection_id=connection_id, result=result,
                )
            else:
                # Legacy direct-write path
                written = write_page(
                    wiki_dir, concept.name, concept.title, sections, source_ref,
                )
                if written.was_update:
                    result.pages_updated.append(concept.name)
                else:
                    result.pages_created.append(concept.name)

        return result

    async def _write_via_service(
        self,
        service: "PageWriteService",
        wiki_dir: Path,
        concept: ConceptPlan,
        sections: list,
        source_ref: str,
        *,
        author: str,
        connection_id: str,
        result: IngestResult,
    ) -> None:
        """Route a concept through the supervised write surface."""
        page_path = wiki_dir / f"{concept.name}.md"
        body = self._sections_to_body(sections)
        if not page_path.exists():
            wr = await service.create(
                title=concept.title,
                body=body,
                citations=[source_ref],
                author=author,
                connection_id=connection_id,
                intent=f"ingest from {source_ref}",
                force=True,  # ingest must not be blocked by near-match heuristics
            )
            if wr.status == "ok":
                result.pages_created.append(concept.name)
        else:
            # Append a new section labeled with the source
            wr = await service.append(
                page=concept.name,
                section_heading=f"From {source_ref}",
                body=body,
                citations=[source_ref],
                author=author,
                connection_id=connection_id,
                intent=f"ingest update from {source_ref}",
            )
            if wr.status == "ok":
                result.pages_updated.append(concept.name)

    @staticmethod
    def _sections_to_body(sections: list) -> str:
        parts = []
        for s in sections:
            parts.append(f"## {s.heading}")
            parts.append("")
            parts.append(s.content)
            parts.append("")
        return "\n".join(parts).strip()
```

Add the new import at the top of the file:

```python
if TYPE_CHECKING:
    from llm_wiki.daemon.writes import PageWriteService
    from llm_wiki.traverse.llm_client import LLMClient
```

- [ ] **Step 4: Update `_handle_ingest` to pass author and apply the response cap**

Edit `src/llm_wiki/daemon/server.py:_handle_ingest`:

```python
    async def _handle_ingest(self, request: dict) -> dict:
        if "source_path" not in request:
            return {"status": "error", "message": "Missing required field: source_path"}
        if "connection_id" not in request:
            return {"status": "error", "message": "Missing required field: connection_id"}

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient

        author = request.get("author", "cli")
        if author == "cli":
            logger.info("ingest route called without author; defaulting to 'cli'")

        connection_id = request["connection_id"]
        source_path = Path(request["source_path"])
        llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
        agent = IngestAgent(llm, self._config)
        try:
            result = await agent.ingest(
                source_path, self._vault_root,
                author=author,
                connection_id=connection_id,
                write_service=self._page_write_service,
            )
        finally:
            try:
                await self.rescan()
            except Exception:
                logger.warning("Failed to rescan vault after ingest")

        # Apply response cap (mcp.ingest_response_max_pages)
        cap = self._config.mcp.ingest_response_max_pages
        all_pages = result.pages_created + result.pages_updated
        truncated = len(all_pages) > cap
        shown = set(all_pages[:cap]) if truncated else set(all_pages)
        warnings = []
        if truncated:
            warnings.append({
                "code": "response-truncated",
                "total_affected": len(all_pages),
                "shown": cap,
                "message": (
                    f"{len(all_pages)} pages affected, showing the first {cap}. "
                    f"Use wiki_lint to see the full attention map."
                ),
            })

        response = {
            "status": "ok",
            "pages_created": len(result.pages_created),
            "pages_updated": len(result.pages_updated),
            "created": [n for n in result.pages_created if n in shown],
            "updated": [n for n in result.pages_updated if n in shown],
            "concepts_found": result.concepts_found,
        }
        if truncated:
            response["truncated"] = True
            response["shown"] = cap
            response["warnings"] = warnings
        return response
```

Update the dispatch case in `_route`:

```python
            case "ingest":
                return await self._handle_ingest(request)
```

- [ ] **Step 5: Update the CLI ingest command to pass `connection_id` and `author`**

Edit `src/llm_wiki/cli/main.py:ingest`:

```python
def ingest(source_path: Path, vault_path: Path) -> None:
    """Ingest a source document — extracts concepts and creates wiki pages."""
    import uuid as _uuid
    client = _get_client(vault_path)
    resp = client.request({
        "type": "ingest",
        "source_path": str(source_path.resolve()),
        "author": "cli",
        "connection_id": _uuid.uuid4().hex,
    })
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Ingest failed"))
    # ... existing rendering code unchanged
```

The connection_id is generated fresh per CLI invocation, so each `wiki ingest` run produces its own session that settles on inactivity (or daemon shutdown).

- [ ] **Step 6: Run the new ingest tests to verify they pass**

Run: `pytest tests/test_ingest/test_session_aware_ingest.py -v`
Expected: PASS for both tests.

- [ ] **Step 7: Run the full ingest + daemon test suites**

Run: `pytest tests/test_ingest tests/test_daemon -v`
Expected: All tests pass. The existing `tests/test_ingest/test_ingest_route.py` and `tests/test_ingest/test_agent.py` may have tests that don't pass `write_service` — the legacy path is preserved so they continue to work. Tests that hit the daemon `ingest` route directly (rather than via the CLI) need to include both `author` and `connection_id` in their request payloads.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/ingest/agent.py src/llm_wiki/daemon/server.py \
        src/llm_wiki/cli/main.py \
        tests/test_ingest/test_session_aware_ingest.py
git commit -m "feat: phase 6b — session-aware ingest via PageWriteService"
```

---

### Task 20: Final regression sweep + Phase 6b tag

**Files:**
- None (verification + tag)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 2: Run the AST hard-rule test specifically and confirm it's the contract**

Run: `pytest tests/test_daemon/test_ast_hard_rule.py -v`
Expected: PASS. Read the failure message format once so you'd recognize it if it ever fired in the future.

- [ ] **Step 3: Smoke-test the daemon end-to-end**

Initialize a temporary git-backed vault and write a page through the daemon protocol:

```bash
mkdir -p /tmp/p6b-vault
cd /tmp/p6b-vault
git init -q
git config user.email "test@test"
git config user.name "test"
echo "# placeholder" > .gitignore
git add .gitignore
git commit -q -m "initial"

# Start the daemon in another terminal:
#   llm-wiki serve $PWD
# Then send a page-create from a python REPL:
python -c "
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.lifecycle import socket_path_for
from pathlib import Path

client = DaemonClient(socket_path_for(Path('.')))
print(client.request({
    'type': 'page-create',
    'title': 'Smoke Test',
    'body': 'body [[raw/x.pdf]]',
    'citations': ['raw/x.pdf'],
    'author': 'tester',
    'intent': 'phase 6b smoke test',
}))
print(client.request({'type': 'session-close', 'author': 'tester'}))
"

# Verify the commit landed:
git log --format="%H %s"
```

Expected: Two commits — `initial` and one `wiki: ...` commit attributed to `tester`.

- [ ] **Step 4: Tag the phase complete**

```bash
git tag phase-6b-complete
git log --oneline | head -25
```

- [ ] **Step 5: Update the spec status line**

Edit `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md`. Update the status line:

From:
```
> Status: Phase 6a (visibility & severity) implemented; 6b (write surface) and 6c (MCP server) pending
```

To:
```
> Status: Phase 6a + 6b implemented; 6c (MCP server) pending
```

Commit:

```bash
git add docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md
git commit -m "docs: phase 6b complete — spec status updated"
```

---

## Phase 6b complete

When Task 20 is done, the daemon has:
- A V4A patch parser and applier with exact + fuzzy match
- Three write routes (`page-create`, `page-update`, `page-append`) that journal every operation under the calling agent's session
- A session/journal/commit pipeline that produces git commits with the agent's identity in the `Agent:` trailer
- Recovery from orphaned journals on startup
- Inactivity-timeout, write-count-cap, explicit-close, and graceful-shutdown settle triggers — all going through the same code path
- An AST hard-rule test that mechanically prevents background workers from reaching the write surface
- A session-aware `wiki_ingest` that routes its internal page writes through the same surface as any other supervised write

What's still missing for the full Phase 6 promise: an MCP server that wraps the daemon and exposes these routes to frontier models. That's Phase 6c.

