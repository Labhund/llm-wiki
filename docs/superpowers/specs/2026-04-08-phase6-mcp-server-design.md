# Phase 6: MCP Server — Design

> Status: Phase 6a (visibility & severity) implemented; 6b (write surface) and 6c (MCP server) pending
> Author: Markus Williams, with collaborative design from Claude (Opus 4.6)
> Date: 2026-04-08

## Overview

Phase 6 ships an MCP server that exposes the existing daemon's capabilities to interactive agents over stdio, plus three new daemon write routes (`page-create` / `page-update` / `page-append`) and the session-based commit pipeline that backs them. It is the final phase of the original llm-wiki design and the layer that lets frontier models actually contribute to the wiki during a conversation, instead of having to push everything through the background ingest pipeline.

The Phase 6 MCP server is intentionally a thin pass-through over the daemon. The hard work in this phase is the *write surface* — the three new routes, their hidden enforcement pipelines, the session/journal/commit machinery, and the discipline that keeps background workers locked out of body content while letting supervised processes (interactive agents and user-spawned autonomous workers) write freely.

## Place in the roadmap

| Phase | Status |
|---|---|
| Phase 1: Core Library + CLI | Complete |
| Phase 2: Daemon | Complete |
| Phase 3: Traversal Engine | Complete |
| Phase 4: Ingest Pipeline | Complete |
| Phase 5a: Issue Queue + Auditor + Lint | Complete |
| Phase 5b: Background Workers + Compliance Review | Complete |
| Phase 5c: Librarian | Complete |
| Phase 5d: Adversary + Talk Pages | Complete |
| **Phase 6: MCP Server** | **This document** |

## Goals

- Expose the daemon's existing read, query, and maintenance surfaces as MCP tools so any MCP-capable client (Claude Code, Claude Desktop, Cursor, agent frameworks) can use the wiki.
- Add a write surface that lets supervised agents create pages, patch pages, and append sections — without compromising "human prose is sacred" for unsupervised processes.
- Make the talk-page and issue-queue machinery actually load-bearing by surfacing their state inline in `wiki_read` and `wiki_lint` so the active agent cannot ignore them.
- Capture every supervised write in git history automatically, with no shadow audit log and no provenance frontmatter — git is the audit trail.
- Reduce friction for the human and the model in equal measure by absorbing all the boring schema-enforcement work into invisible per-tool pipelines on the daemon side.

## Non-goals

- Multi-vault routing in a single MCP server (one server per vault — multiple vaults means multiple MCP server entries in the client config).
- SSE / HTTP MCP transports (stdio only for Phase 6).
- A "review-before-merge" worktree mode for cautious users (post-Phase-6 if anyone asks for it).
- A dedicated brainstorming companion tool (out of scope by design — see Principle 2).
- Authentication or per-agent permissioning (single-user tool, all attribution lives in git history and the journal).
- A `wiki_delete` tool (dead pages live in the long-tail graveyard; hard delete is `rm` + `git commit`).

## Principles

Four load-bearing principles. Everything in the rest of this document falls out of these.

### 1. Unsupervised processes never write body content. Supervised ones can.

The boundary is *background vs interactive*, not *human vs machine*.

- **Background workers** — anything in the daemon scheduler (librarian, adversary, auditor, compliance reviewer) — are unsupervised. They run on cron without a configuring human in the loop for any individual action. They are forbidden from writing body content. They can write metadata sidecars, file issues, post to talk pages, insert invisible structural markers — but they cannot call the new write routes. This is enforced by an AST-grep test.

- **Supervised processes** are anything started intentionally by a user where there is human accountability for the action. This includes:
  - Frontier models in conversation with a human via Claude Code / Claude Desktop / Cursor / etc.
  - **Autonomous agents the user has spun up as a separate process** (e.g., a long-running research worker that uses the MCP tools to read, synthesize, and write). The user configured and started that process, so they are responsible for it. The daemon treats it identically to an interactive agent — it must supply an `author` identifier, its writes go through the same per-tool pipelines, and its commits land in the same git history.

The supervision in "supervised" comes from *the user choosing to run the process*, not from minute-by-minute oversight. This is the same model as Unix: the kernel doesn't write your files for you, but it lets any process you start do so. The daemon is the kernel; MCP-connected agents are processes.

### 2. Main pages are sourced. Talk pages are everything pre-source.

Every claim in `wiki/*.md` must be traceable to a primary source. The daemon enforces this by rejecting `wiki_create` and `wiki_append` calls with empty `citations`. Half-formed ideas, agent proposals, ambiguous adversary verdicts, contradictions waiting on resolution — all of those go on talk pages, which have no citation requirement and accept anything.

The MCP write tool descriptions encode the etiquette: *if you can't cite a source, post to the talk page instead.* The agent's tool calls are how the etiquette gets enforced — the daemon's job is to make the right path available and the wrong path return a clear, actionable error.

A future brainstorming companion tool (out of scope for Phase 6) will live entirely outside the wiki and consume it as a read-only research surface. It will not write to the wiki except by promoting matured ideas via `wiki_create` / `wiki_append` once they have citations. The wiki has zero awareness of "brainstorming mode" — the cleanliness of the wiki is preserved by keeping the brainstorming tool a sibling, not a child.

### 3. Schema enforcement lives in the daemon, not the agent's context.

Each new write route has its own invisible enforcement pipeline: frontmatter validation, name-collision check, manifest update, re-index, structural lint, sidecar bump, journal append. The agent's tool description says *"create a new page with title, body, citations"* — it does not know about any of the bookkeeping. This is the architectural reason to split write into three tools (`wiki_create` / `wiki_update` / `wiki_append`) instead of one with a `mode` parameter: each tool gets its own pipeline that runs without polluting the agent's reasoning.

### 4. The framework absorbs boredom on behalf of both sides.

Humans get lazy about citation hygiene, cross-referencing, and tagging. Models get lazy too — they will happily skip "the boring part" of integrating a new fact into the existing graph if you let them. The daemon's job is to make the boring parts invisible: schema enforcement, indexing, manifest updates, lint, journal, commit — all of it happens behind the write tools without the agent or the human ever thinking about it.

This is the principle that justifies the daemon's continued existence in the face of "couldn't a skill file do all this?" The skill-file approach (e.g., the hermes_agent llm-wiki skill) works, but it burns the active model's context on schema details every call. With the daemon, the agent's reasoning stays focused on *what* to write; the daemon handles *how* to integrate it. The reduction in cognitive friction is the value proposition.

## Two derived guarantees

- **Everything in the wiki is in git, everything is revertable.** No shadow audit log, no provenance frontmatter, no per-page attribution metadata. Every supervised mutation produces a commit, attributed to its author via the trailer. `git revert` is the undo button. The state directory (`~/.llm-wiki/vaults/<vault>/`) is *never* in git by design — tantivy indexes, librarian overrides, session journals, and traversal logs all live outside the wiki and are rebuildable from the wiki on rescan. This split is what makes "everything is revertable" actually true: there is no second, unversioned source of truth that could become inconsistent with the page files.
- **Talk pages and issues become useful.** `wiki_read` folds open issues and unresolved talk entries for a page directly into its response, so the active agent always sees them inline without remembering to ask. The asynchronous channel between background workers and the interactive agent finally has both ends.

## MCP tool surface

The MCP server is a thin pass-through over the daemon's existing IPC plus three new write routes. Tools group naturally into four families.

### Read-side (low-level — agent drives)

| Tool | Purpose | Daemon route |
|---|---|---|
| `wiki_search` | BM25 keyword search; returns ranked manifest entries with grep-style match snippets so the agent can decide budgets before reading | `search` (existing, *enriched*) |
| `wiki_read` | Read a page with viewport (`top`, `section`, `grep`, `full`); response folds in any open issues + unresolved talk-entry digest for that page automatically | `read` (existing, *enriched*) |
| `wiki_manifest` | Hierarchical, budget-aware manifest of the whole vault; supports cluster scoping and pagination | `manifest` (existing) |
| `wiki_status` | Vault stats, page count, daemon health, scheduler workers, last index time | `status` + `scheduler-status` (existing, merged) |

### Query-side (high-level — daemon drives)

| Tool | Purpose | Daemon route |
|---|---|---|
| `wiki_query` | Multi-turn traversal with budget management; returns synthesized answer + citations + traversal log. The daemon does the navigation; the calling agent's context only sees the final answer. | `query` (existing) |
| `wiki_ingest` | Ingest a source (PDF, DOCX, markdown, URL, image) through the extraction → LLM → page-write pipeline; supervised write that journals all internal create/update operations under the calling agent's session; returns a truncated summary of pages affected (see "Large ingest responses" below) | `ingest` (existing, *session-aware*) |
| `wiki_lint` | Run structural integrity checks AND return the vault-wide attention map (issue + talk-entry counts per page); near-instant, no LLM | `lint` (existing, *enriched*) |

### Write-side (the new surface — Phase 6's main daemon work)

| Tool | Purpose | Daemon route |
|---|---|---|
| `wiki_create` | Create a new page. Args: `title`, `body`, `citations`, `tags?`, `author`, `intent?`. Daemon validates frontmatter, checks for name collisions, writes file, updates manifest, indexes, runs structural lint, journals the call. | `page-create` (**new**) |
| `wiki_update` | Apply a V4A-style patch to an existing page. Args: `page`, `patch` (V4A format), `author`, `intent?`. Daemon parses the patch, fuzzy-matches against current content, applies under the per-page write lock, re-indexes, journals. | `page-update` (**new**) |
| `wiki_append` | Append a new section to an existing page. Args: `page`, `section_heading`, `body`, `citations`, `after_heading?`, `author`, `intent?`. Daemon writes section with marker, re-indexes, journals. | `page-append` (**new**) |

All three carry the calling agent's `author` identifier (mandatory). All three return `{status, page_path, journal_id, session_id, content_hash, warnings?: [...]}` on success. None of them ask the agent for "provenance" or "mode" — the tool itself encodes the intent.

The `warnings` array is the general escape hatch for "this succeeded but the agent should know about it." It carries entries like `{code, message, ...details}`. Phase 6 ships these warning codes:

- `session-cap-approaching` — emitted starting at `floor(write_count_cap * cap_warn_ratio)` (so 18 of 30 by default). Carries `writes_used` and `writes_remaining`. Tells the agent to call `wiki_session_close` at a clean breakpoint before the daemon force-settles.
- `index-pending` — sync re-index after the write failed; the file watcher will reconcile within ~5 seconds. The agent should wait or read the page directly via `wiki_read` instead of searching.
- `response-truncated` — used by `wiki_ingest` when the page list exceeds `mcp.ingest_response_max_pages`.
- `heading-multiple-matches` — used by `wiki_append` when `after_heading` matches more than one heading; the daemon used the first match.

### Maintenance-side (lets the active agent participate in the loop)

| Tool | Purpose | Daemon route |
|---|---|---|
| `wiki_issues_list` | List open structural issues; supports filters by type, severity, page | `issues-list` (existing) |
| `wiki_issues_get` | Read full body of one issue | `issues-get` (existing) |
| `wiki_issues_resolve` | Mark an issue as resolved. Session-aware when called from MCP — requires `author`, journals, lands in the session commit. | `issues-update` (existing, *session-aware*) |
| `wiki_talk_read` | Read all talk entries for a page (full thread, including resolved) | `talk-read` (existing) |
| `wiki_talk_post` | Append a talk entry as the calling agent. Optional `resolves: [int]` field to close prior entries. Session-aware when called from MCP — requires `author`, journals, lands in the session commit. | `talk-append` (existing, *session-aware + extended*) |
| `wiki_talk_list` | List all pages with talk entries | `talk-list` (existing) |
| `wiki_session_close` | Optional explicit settle for a session — commits immediately instead of waiting for the inactivity timeout. Useful in swarm orchestration. | `session-close` (**new**) |

### Tools deliberately not in the surface

- **No `wiki_delete`.** Page deletion is rare and high-stakes; the user can do it from Obsidian or the shell. Adding it as an MCP tool tempts the model to delete pages it shouldn't. Dead pages live in the long-tail graveyard, surface as orphans in lint, and bury naturally under the librarian's authority score.
- **No raw `wiki_write` overwrite tool.** Every write goes through one of the three split tools, each with its hidden pipeline.
- **No `wiki_commit` / `wiki_revert`.** Commits happen automatically via the session pipeline. Reverts happen via `git revert` outside the daemon.

## Daemon-side changes

### Three new write routes

All three are async, all three acquire the existing per-page write lock (`daemon/writer.py`), all three append a journal entry on success, all three are **forbidden to scheduler-invoked code paths** (enforced by test).

#### `page-create`

**Request:** `{title, body, citations: [...], tags?: [...], author, intent?}`

**Pipeline:**
1. Validate frontmatter — required fields populated, tags exist in the schema taxonomy if one is configured.
2. Check for name collision and near-match against existing pages (algorithm below). Refuse on hard collision (`name-collision`), warn on near-match (`name-near-match` with `force` flag for override).
3. Reject if `citations` is empty (`missing-citations`). The daemon-level enforcement of "everything in main is traceable to a primary source."
4. Acquire per-page write lock; write the file.
5. Append manifest entry, re-index in tantivy, run structural lint on just the new page.
6. Initialize the page's sidecar (`section_priority`, `last_modified`, etc.).
7. Append a journal line for the active session.

**Response:** `{status: "ok", page_path, journal_id, session_id, content_hash}`

**Name similarity algorithm.** The naive Levenshtein-only check misses the most common LLM-generated near-duplicate (a *superset* name like `sRNA-tQuant-Pipeline` for an existing `sRNA-tQuant`). The naive Jaccard-only check misses typos. Phase 6 ships a two-stage hybrid that catches both:

```python
def is_near_match(name: str, existing: str) -> bool:
    # Stage 1: tokenize on hyphens/underscores/whitespace, lowercase
    a_tokens = set(name.lower().replace("_", "-").split("-"))
    b_tokens = set(existing.lower().replace("_", "-").split("-"))

    if a_tokens and b_tokens:
        # Token overlap (catches "the-attention-mechanism" vs "attention-mechanism")
        jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
        if jaccard > 0.5:
            return True
        # Proper subset either way (catches "sRNA-tQuant-Pipeline" vs "sRNA-tQuant")
        if a_tokens < b_tokens or b_tokens < a_tokens:
            return True

    # Stage 2: normalized Levenshtein on the full slug (catches typos)
    a_str = name.lower().replace("_", "-")
    b_str = existing.lower().replace("_", "-")
    if a_str and b_str:
        sim = 1 - levenshtein(a_str, b_str) / max(len(a_str), len(b_str))
        if sim > 0.85:
            return True

    return False
```

Both stages run; flagging either side is enough to return `name-near-match`. The exact case-insensitive name match still runs first as the hard `name-collision` check. Both thresholds are config knobs:

```yaml
write:
  name_jaccard_threshold: 0.5
  name_levenshtein_threshold: 0.85
```

The `force: true` flag on `wiki_create` overrides both — used by the agent when it really does want a similar-but-distinct page.

#### `page-update`

**Request:** `{page, patch (V4A format string), author, intent?}`

**Pipeline:**
1. Parse the V4A patch (port of the parser used by codex/cline; supports `*** Update File:`, `@@ context @@`, `+`/`-`/` ` lines, addition-only hunks, fuzzy match with context hint).
2. Acquire per-page write lock.
3. Apply the patch via fuzzy match against current file content. If context lines don't match (the page changed since the agent read it), return a structured `patch-conflict` error so the agent can re-read and retry.
4. Write the resulting file, re-index, re-run link/citation check on just this page.
5. Bump `last_modified` in sidecar.
6. Append a journal line.

**Response:** `{status: "ok", page_path, journal_id, session_id, content_hash, diff_summary: "+3 -1"}` on success, or `{status: "error", code: "patch-conflict", page_path, current_content_excerpt}` on context mismatch.

#### `page-append`

**Request:** `{page, section_heading, body, citations: [...], after_heading?, author, intent?}`

**Pipeline:**
1. Reject if citations empty (same rule as create).
2. Acquire per-page write lock.
3. Locate the insertion point — semantics:
   - **No `after_heading` provided** → append at end of file.
   - **`after_heading` provided, exact match found, single occurrence** → insert immediately after that section closes (i.e., right before the next heading at the same or shallower level, or at end of file if none).
   - **`after_heading` provided, exact match found, multiple occurrences** → insert after the **first** occurrence; emit a warning in the response so the agent knows to use `wiki_update` with V4A if it wanted a different one.
   - **`after_heading` provided, no match** → return `heading-not-found` error with `available_headings: [...]` so the agent can fix and retry. **Exact match only — no prefix matching.** (`Method` does not match `Methods` or `Method 1`.)
4. Insert the new section with a `%% section: <slug>, tokens: <N> %%` marker so the new section is immediately viewport-addressable.
5. Re-index, bump sidecar.
6. Append a journal line.

**Response:** `{status: "ok", page_path, journal_id, session_id, content_hash, warnings?: [...]}`. The `warnings` array is populated with `{code: "heading-multiple-matches", count: N, used_line: M}` if `after_heading` matched more than one heading.

The shared invariant: **all three routes journal the call, all three are session-aware, none of them can be reached from background-worker code paths.**

### One new session-management route

#### `session-close`

**Request:** `{author}`

**Pipeline:**
1. Look up the active session for the given author. If none exists, return `{"status": "ok", "settled": false}` (idempotent — closing an already-settled session is not an error).
2. Run the settle sequence (read journal → resolve open entries → summarize → stage → commit → archive). See the Session lifecycle section below for the full pipeline.
3. Clear the in-memory session state for that author.

**Response:** `{"status": "ok", "settled": true, "commit_sha": "..."}` if a session was settled, `{"status": "ok", "settled": false}` if the author had no active session.

This is the same code path as the inactivity-timeout settle and the daemon-shutdown settle — just triggered by an explicit MCP call instead of a timer or shutdown hook. Useful for swarm orchestration where the parent wants to know "agent-3's work is committed and final" before moving on.

Like the write routes, `session-close` is **forbidden to scheduler-invoked code paths** — it's an MCP-only route, never reachable from background workers.

### Three modified routes

#### `read` (enriched)

Same args as today, but the response now includes a context-aware digest of issues and talk-page state for the page:

```json
{
  "status": "ok",
  "content": "...",
  "issues": {
    "open_count": 2,
    "by_severity": {"critical": 0, "moderate": 1, "minor": 1},
    "items": [
      {"id": "broken-citation-7", "severity": "moderate", "title": "Broken citation in Methods section", "body": "..."}
    ]
  },
  "talk": {
    "entry_count": 14,
    "open_count": 5,
    "by_severity": {"critical": 1, "moderate": 0, "minor": 1, "suggestion": 2, "new_connection": 1},
    "summary": "<2-sentence librarian-generated digest of unresolved entries>",
    "recent_critical": [
      {"index": 12, "ts": "...", "author": "adversary", "body": "..."}
    ],
    "recent_moderate": []
  }
}
```

Critical and moderate entries are inlined verbatim because they need attention now. Everything else collapses into the digest + counts. If the agent wants the full thread, it calls `wiki_talk_read`. **Resolved entries (entries that a later entry has marked via `resolves: [...]`) are excluded from all counts and from the summary — they only appear in `wiki_talk_read`.**

#### `search` (enriched)

Search results gain a `matches` array per result, populated from tantivy's snippet generator with ±1 line context:

```json
{
  "name": "sRNA-tQuant",
  "score": 0.84,
  "manifest": "<existing entry text>",
  "matches": [
    {
      "line": 47,
      "before": "## Methods",
      "match": "We trained on 50k sequences using k-means",
      "after": "with cosine similarity..."
    }
  ]
}
```

This saves entire dives — the agent can decide "the match is in the wrong section, skip" without burning a `wiki_read` call. Tantivy already supports highlighting; this is a snippet config change, not a new code path.

#### `lint` (enriched)

The structural checks stay (orphans, broken wikilinks, missing markers, broken citations). The response gains an `attention_map` that aggregates issue/talk counts across the whole vault:

```json
{
  "status": "ok",
  "structural": {
    "orphans": [...],
    "broken_wikilinks": [...],
    "missing_markers": [...],
    "broken_citations": [...]
  },
  "attention_map": {
    "pages_needing_attention": ["sRNA-tQuant", "transformer-architecture", ...],
    "totals": {
      "issues": {"critical": 1, "moderate": 4, "minor": 12},
      "talk": {"critical": 1, "moderate": 2, "minor": 3, "suggestion": 18, "new_connection": 7}
    },
    "by_page": {
      "sRNA-tQuant": {
        "issues": {"critical": 1, "moderate": 0, "minor": 1},
        "talk": {"critical": 1, "new_connection": 2}
      }
    }
  }
}
```

`wiki_lint` is **near-instant** because every input is already-persisted Python state (the IssueQueue and the TalkPage sidecar files). No LLM calls. The agent calls it once at the start of a session to know exactly where in the vault to focus, then drills into specific pages with `wiki_read` (digest) or `wiki_issues_get` / `wiki_talk_read` (full body). Three layers of zoom: vault-wide map → per-page digest → full thread.

This is the distinction between `wiki_lint` and `wiki_issues_list`:
- `wiki_lint` runs the structural checks fresh AND aggregates the persistent queue. Cheap, near-instant. Use to know "what's the state of the vault right now?"
- `wiki_issues_list` reads the persistent queue (which contains both lint findings and the LLM-derived adversary/compliance findings that were "hard-fought through tokens"). Use to walk through the work backlog.

Both exist; their tool descriptions clarify when to use each.

### `ingest` becomes session-aware

The existing `ingest` route runs the extraction → LLM → page-write pipeline for a source file. In Phase 4 it wrote pages directly via internal helpers; Phase 6 routes its internal page-create / page-update operations through the new `page-create` / `page-update` daemon routes. This means:

- Every page the ingest pipeline creates or updates produces a journal entry under the calling agent's session.
- The whole ingest produces one commit at session settle, attributed to the calling agent.
- Ingest from MCP requires `author` like every other supervised write — the daemon refuses ingest calls without it. CLI-driven ingest passes a synthetic `cli` author.
- Ingest from background workers (if any future cron-based ingest worker is added) would NOT be allowed to call these routes — same hard rule as everywhere else.

This resolves the supervised/unsupervised boundary that the existing ingest pipeline blurred — when called from MCP, ingest is supervised because the caller is in the loop.

**Large ingest responses.** A 100-page PDF can produce 30+ created pages plus updates. MCP tool responses are single blobs (not paginated), so the daemon caps the response and surfaces the cap as a warning:

```json
{
  "status": "ok",
  "pages_created": 30,
  "pages_updated": 5,
  "created": ["rotary-position-embeddings", "grouped-query-attention", "..."],
  "updated": ["transformer-architecture", "..."],
  "truncated": true,
  "shown": 15,
  "session_id": "...",
  "commit_sha": "abc123def...",
  "warnings": [{
    "code": "response-truncated",
    "total_affected": 35,
    "shown": 15,
    "message": "35 pages affected, showing the first 15. Use wiki_lint to see the full attention map, or git show abc123def to see the complete diff."
  }]
}
```

Cap is configurable via `mcp.ingest_response_max_pages` (default 15). The agent that needs the full list has three escape hatches:
1. `wiki_lint` — the attention map walks the whole vault and lists every recently-touched page.
2. `wiki_search` — query for concepts from the source to find the just-created pages.
3. The `commit_sha` field — if the agent has shell access, `git show <sha>` returns the complete file list.

### Talk entries gain `severity` and append-only closure

Talk entries gain a `severity` field on their dataclass: `critical | moderate | minor | suggestion | new_connection`. Default `suggestion`. Background workers set it explicitly when posting (adversary's contradiction → `critical`, compliance's missing-citation → `moderate`, etc.). Existing entries on disk that lack the field default to `suggestion` on read — the parser branches on whether the new metadata is present.

**On-disk format: markdown with HTML-comment metadata, not YAML.** The dataclass and the file format are intentionally different. The dataclass carries `index`, `severity`, `resolves` as first-class fields. The file format keeps the existing markdown shape — bold-text entry headers, plain markdown body — and tucks `severity` and `resolves` into an HTML comment on the header line. HTML comments are invisible in Obsidian's render mode but trivially parseable by the daemon. This preserves Principle 2 (plain markdown on a filesystem is the substrate) — talk pages stay readable in Obsidian as they always were, the user can still edit them by hand, and no migration of existing files is required.

**Closure of talk entries is itself a new entry**, not a state mutation:

```markdown
---
page: sRNA-tQuant
---

**2026-04-07T10:30Z — @adversary** <!-- severity:critical -->
Page claims 30% improvement, but source PDF table 3 shows 27%.

**2026-04-08T14:15Z — @claude-opus-4-6** <!-- severity:minor, resolves:[1] -->
Fixed in commit 4a8b2e — updated page to match source table 3.
```

**Index semantics.** Entry indices are 1-based and positional: the first entry in the file is index 1, the second is 2, and so on. Indices are **not stored in the file** — they are computed on load from entry order. This is stable because talk pages are append-only: once an entry exists at position N, no later operation reorders or removes prior entries, so its index never changes. The `resolves: [N]` reference in a later entry's HTML-comment metadata refers to that positional index. In the example above, the second entry's `resolves:[1]` closes the first entry.

Metadata format inside the comment is `key:value` pairs separated by commas: `severity:critical`, or `severity:minor, resolves:[1,3]`, or omitted entirely (parser defaults to `severity=suggestion`, `resolves=[]`). A `suggestion`-severity entry with no resolves writes the same line shape as today's format — zero visible churn for the common case.

The new entry has an optional `resolves` list pointing at prior entry indices on the same page. The original entry is **never modified** — append-only purity preserved. The librarian's digest computation walks the entries forward in pure Python, marks any entry as "resolved" if a later entry references it via `resolves`, and only passes unresolved entries to the summarizer LLM. This means:

1. **The summarizer's context is never polluted with closed entries** — closure is enforced in code, not by hoping the LLM ignores resolved entries.
2. **`wiki_read`'s talk digest only counts unresolved entries** — closed discussions disappear from the attention surface but remain visible in `wiki_talk_read`'s full thread for history.
3. **`wiki_talk_post` gains an optional `resolves: [int]` field** — no new MCP tool needed. Same closure mechanism works for any author: background worker, interactive agent, or human via Obsidian.

This mirrors how Wikipedia talk pages actually work — threads die when someone replies "done" with their signature, no one ever edits the original out, and the resolution lives right next to the discussion.

### Issues gain `severity`

The `Issue` dataclass gains a `severity` field with the same vocabulary as talk entries (`critical | moderate | minor | suggestion | new_connection`). The auditor sets it when filing (`broken-citation` → `critical`, `orphan-page` → `minor`, etc.). Default to `minor` for legacy issues without the field. This unifies the digest computation across both surfaces — `wiki_read` and `wiki_lint` use the same severity vocabulary regardless of whether a finding came from the auditor (issue) or the adversary (talk entry).

### New librarian responsibility — talk page summaries

The librarian agent currently refreshes tags/summary in the manifest sidecar and recomputes authority. Phase 6 adds a third job: **refresh stale talk page summaries.**

- **Trigger:** when a talk page has accumulated ≥ N new unresolved entries since the last summary (default N=5, configurable via `maintenance.talk_summary_min_new_entries`).
- **Action:** in pure Python, walk the talk page entries and compute the open set (entries not closed by a later `resolves`); call the cheap maintenance LLM with only the open entries; ask for a 2-sentence summary; store it in the talk page's sidecar (alongside the librarian's existing override sidecar).
- **Cost:** runs at `priority="maintenance"` like everything else the librarian does — never competes with active queries.
- **Rate limit:** `maintenance.talk_summary_min_interval_seconds` (default 3600) prevents thrashing on hot pages.

This makes the talk-page digest in `wiki_read` always have a useful summary instead of returning stale or missing data.

### The hard rule — and how it's enforced

The principle "background workers never reach the MCP-only routes" needs to be more than a convention. Phase 6 ships a **mechanical test** that:

1. Imports every module under `daemon/`, `audit/`, `librarian/`, `adversary/`, `talk/`.
2. Walks the AST for any reference to `page-create`, `page-update`, `page-append`, `session-close`, or the helper functions / route handlers that implement them.
3. Fails if any background-worker code path (anything reachable from a `ScheduledWorker.run()` method or the `IntervalScheduler`'s task loop) can reach those symbols.

Cheap, mechanical, catches accidental future regressions. The test is the contract.

## Session lifecycle, journal, and commit pipeline

This is the heart of Phase 6. Everything that makes "writes flow into git history without anyone having to think about it" lives here.

### Session model

A **session** is the unit of write grouping for commits. Its key is **`(author, connection_id)` by default**, with `author` alone as a fallback when `sessions.namespace_by_connection: false` is set. The `author` always appears in the git commit trailer regardless, so swarm-wide attribution via `git log --grep "Agent: researcher-3"` works in either mode.

**The `connection_id` is supplied by the calling client in every write/session-close request payload — the daemon does not generate it.** The daemon's Unix-socket protocol is one-message-per-connection (open socket, send request, receive response, close), so a per-Unix-socket UUID would create one daemon session per write call and defeat the purpose of session grouping. Instead, the MCP server (Phase 6c) generates one UUID at MCP stdio-session startup and threads it into every `client.request({...})` call via a shared `ToolContext`. The CLI ingest command generates a per-invocation UUID. Direct daemon clients (e.g., custom Python scripts) supply their own. Write/session-close handlers return `missing-connection-id` if the field is absent. The session lookup that backs `wiki_session_close` uses `(author, connection_id)` directly so it settles exactly the session the calling client owns — not "any session for this author."

Why two modes:

- **`namespace_by_connection: true` (default, safer for swarms).** 20 autonomous agents sharing one MCP client process but issuing different `author` identifiers each get their own session. 20 agents on 20 separate MCP connections all using the same `author` identifier *also* each get their own session — accidental name collisions don't merge work into one mega-commit. This is the safe default.
- **`namespace_by_connection: false` (advanced).** A long-lived agent that drops and reconnects with the same `author` resumes its existing session across the reconnect. Useful for stateful single-agent setups but dangerous in swarms where two agents might collide on a name and silently merge.

Session lifecycle:

```
Open    → first wiki_create / wiki_update / wiki_append from a previously-quiet author
Active  → as long as writes from this author keep arriving within the inactivity window
Settle  → triggered by ANY of:
            (a) inactivity timeout (default 5 min, configurable)
            (b) write count cap reached (default 20, configurable)
            (c) explicit settle via wiki_session_close
            (d) daemon graceful shutdown
Commit  → daemon resolves open entries → summarizes journal → git add + commit → archive journal
```

Read calls (`wiki_read`, `wiki_search`, `wiki_query`) **do not extend the session** — they go through the existing traversal-log path for librarian usage tracking, but they don't touch the journal. Sessions are purely for write grouping.

The `author` identifier is **mandatory and self-asserted**. The daemon refuses writes without it (`missing-author` error). The daemon does not validate or register identifiers — accountability comes from git history. Two agents using the same identifier collide into one session; that's the operator's responsibility, not the daemon's.

### The journal

For each write call, the daemon appends a JSONL line to:

```
<state-dir>/sessions/<session-uuid>.journal
```

Each line is one event:

```json
{
  "ts": "2026-04-08T13:42:11Z",
  "tool": "wiki_update",
  "path": "wiki/sRNA-tQuant.md",
  "author": "claude-opus-4-6",
  "intent": "fix learning rate from 1e-4 to 3e-4 per source table 3",
  "patch_summary": "+1 -1 @ ## Methods",
  "content_hash_after": "sha256:..."
}
```

The `path` field is the **path of the file the operation touched, relative to `vault_root`**. Every supervised write produces exactly one journal entry with exactly one path. The examples below assume the default `wiki_dir = "wiki/"` config; under a flat Obsidian-style vault with `wiki_dir = ""`, the `wiki/` prefix drops and paths become `<page>.md` / `<page>.talk.md` / `.issues/<id>.md`. The daemon derives the path from the file's actual location via `page_path.relative_to(vault_root)` rather than hardcoding the `wiki/` prefix, so any `wiki_dir` configuration works.

| Tool | Path written (default config) | Example |
|---|---|---|
| `wiki_create` / `wiki_update` / `wiki_append` | `wiki/<page>.md` | `wiki/sRNA-tQuant.md` |
| `wiki_talk_post` | `wiki/<page>.talk.md` | `wiki/sRNA-tQuant.talk.md` |
| `wiki_issues_resolve` | `wiki/.issues/<id>.md` | `wiki/.issues/broken-citation-7.md` |
| `wiki_ingest` (per internal page op) | `wiki/<page>.md` | one journal entry per page the ingest pipeline creates or updates |

The `intent` field is supplied by the calling agent — a short string the model writes to explain *why* it's doing this edit, separate from the page content. The tool description encourages it. The summarizer uses these intents to compose the commit message. If the agent doesn't supply one, the journal line still gets written; the summarizer falls back to using the patch diff.

Journal writes are `fsync`'d. If the daemon crashes mid-line, the recovery pass treats the partial line as the cutoff and commits everything before it.

**What goes in git, what doesn't.** This is load-bearing for the recovery and revert guarantees:

- **In-wiki paths are committed** when they appear in a journal entry. This is everything under `wiki/` — page files, talk page sidecars, issue files. Each in-wiki path is touched by exactly one supervised operation per journal entry.
- **State-dir paths are never committed.** Anything under `~/.llm-wiki/vaults/<vault>/` (tantivy index, librarian's `overrides.json`, session journals themselves, traversal logs) lives outside the wiki by design and is *rebuildable from the wiki on rescan*. They are never in git, never in commits, and never need to be — losing the state dir means a slightly slower next startup, not lost work.
- **Background-worker writes to in-wiki paths** (compliance reviewer appending to a talk page, auditor filing an issue) are NOT journaled and NOT auto-committed. They have no `author`, no session, and the daemon's `git add` step never sees them. They sit on disk dirty until either (a) the user commits them via Obsidian/git, or (b) the optional `auto_commit_user_edits` worker sweeps them up alongside actual user edits with the generic `wiki: human edits` message.

This is the cleaner version of "supervised vs unsupervised": **the daemon only auto-commits things attributed to a supervised author. Everything else — background workers and Obsidian edits — is the user's to commit.**

### Settle and commit

When a session settles, the daemon runs this sequence under a serial commit lock (so two settling sessions don't race on `git`):

1. **Read the journal** — load all events for the session.
2. **Summarize** — call the cheap maintenance LLM via the LLM queue at `priority="maintenance"`:
   - Prompt: "Here are N wiki edits from one session by agent <id>. Produce: a 60-character one-line summary suitable as a git commit subject, then 2-5 bullet points describing what changed and why. Use the intents and page paths."
   - Output: structured summary the daemon parses.
3. **Stage** — `git add` only the paths that appear in the journal (never `git add -A` — that would sweep up unrelated user edits).
4. **Commit** — `git commit` with this format:
   ```
   wiki: <one-line summary>

   - <bullet 1>
   - <bullet 2>
   - <bullet 3>

   Session: 7f9c-...
   Agent: researcher-3
   Writes: 7
   ```
5. **Archive the journal** — move `<session-uuid>.journal` → `<state-dir>/sessions/.archived/<session-uuid>.journal` so the recovery pass doesn't reprocess it.
6. **Clear the in-memory session state** for that author.

The summarizer call is the only LLM cost in the commit pipeline, and it runs at maintenance priority so it never blocks the active agent. **The summarizer is an enrichment, not a dependency** — if the cheap model is unreachable or fails, the daemon falls back to a deterministic commit message:

```
wiki: 7 writes from researcher-3 [session 7f9c]

- wiki_update sRNA-tQuant.md (+1 -1) — fix learning rate from 1e-4 to 3e-4 per source table 3
- wiki_append transformer-architecture.md — add Methods section per source page 5
- ...

Session: 7f9c-...
Agent: researcher-3
Writes: 7
```

The commit always happens — the worst case is a less narrative summary.

The `Agent:` trailer becomes a meaningful grep target. `git log --grep "Agent: researcher-3"` shows everything that agent has ever done in the wiki. This is the swarm equivalent of `git log --author=...`, except the author of the *commit* is always the daemon — the agent identity lives in the trailer.

### Recovery from disruption

The recovery story is simple because the journal IS the recovery state:

- **Daemon crash mid-session** → on next startup, the daemon scans `<state-dir>/sessions/*.journal` (non-archived). For each, it runs the settle sequence (summarize → stage → commit → archive). Recovery is one pass at startup.
- **Daemon clean shutdown** → before exiting, the daemon settles all open sessions (same code path as the timeout settle).
- **Power failure** → same as crash. Files are on disk because writes are fsync'd. Journal entries are on disk because lines are fsync'd. Recovery handles it.
- **Partial journal line** (1ms power-failure window between fsync of file write and fsync of journal append): the recovery pass treats the malformed line as the cutoff, logs a warning, and commits everything before. The single missing journal line means the eventual commit message is one bullet short — the file change itself is intact.
- **User runs `git commit` themselves between sessions** → harmless. The next session settle sees that the paths it tracked are no longer dirty in `git status` (the user already committed them) and produces an empty commit attempt — which the daemon detects and skips.

### What about user edits in Obsidian during a session?

User edits go through the file watcher (already exists for indexing) but **the daemon does not commit them by default**. The user's authorship deserves the user's commit message, and the daemon committing for the user feels invasive.

Two configurable behaviors:

1. **Default — leave user edits uncommitted.** The daemon's `git add` only touches paths it sees in the journal. The user's edits sit in the working tree until the user commits them themselves.
2. **Optional (`sessions.auto_commit_user_edits: true`) — periodic "user-edit settling" commit.** A separate scheduler worker runs every `sessions.user_edit_settle_interval_seconds`; if the working tree has uncommitted changes that don't appear in any session journal, it commits them with a generic message: `wiki: human edits`. Off by default.

### Concurrency

- **Two agents writing simultaneously to different pages.** Each has its own session, each has its own journal, each settles independently. The serial commit lock at step 3-4 above ensures their commits don't race on `git` — one commits, then the other. Final history shows two adjacent commits with two different session ids.
- **Two agents writing simultaneously to the same page.** The per-page write lock (already exists in `daemon/writer.py`) serializes them — one's update applies first; the other's V4A patch context-matches against the new state and either applies cleanly or returns `patch-conflict`. Both end up in their respective session journals; both end up in their respective commits. No data loss.

### What the caller sees

From the calling agent's perspective, none of this is visible. The agent calls `wiki_update`, gets back `{"status": "ok", "page_path": "...", "journal_id": "...", "session_id": "..."}`, and moves on. It never thinks about journals, sessions, summarization, or commits. This is Principle 4 in action — the boring stuff is invisible.

## Configuration

Phase 6 extends the existing `WikiConfig` (`config.py`) and the per-vault `schema/config.yaml`. New sections:

```yaml
mcp:
  # No enable flag — running `llm-wiki mcp` is the enable. Reserved for future
  # transports beyond stdio.
  transport: stdio
  # Cap on the number of page names returned in a wiki_ingest response, to avoid
  # dumping a 100-page PDF's full page list into the agent's context.
  ingest_response_max_pages: 15

sessions:
  # Session key = (author, connection_id) by default. Set to false to key purely
  # by author (advanced — see Session model in §Session lifecycle).
  namespace_by_connection: true

  # When does an agent's session settle and commit?
  inactivity_timeout_seconds: 300   # 5 min — settle if no writes from this author
  write_count_cap: 30               # settle after this many writes regardless

  # Soft warning starts at floor(write_count_cap * cap_warn_ratio) — agent gets
  # told to consider closing the session before the hard cap force-settles it.
  # Default: warning at 18 of 30, leaving 12 writes of runway to wrap up.
  cap_warn_ratio: 0.6

  # User edits in Obsidian are NOT committed by default — the user owns those
  # commits. Flip to true if you want the daemon to also commit user edits
  # after a quiet period.
  auto_commit_user_edits: false
  user_edit_settle_interval_seconds: 600  # only used if auto_commit_user_edits=true

write:
  require_citations_on_create: true   # daemon refuses wiki_create without citations
  require_citations_on_append: true   # daemon refuses wiki_append without citations
  patch_fuzzy_match_threshold: 0.85   # V4A context matching tolerance
  name_jaccard_threshold: 0.5         # token overlap above this triggers name-near-match
  name_levenshtein_threshold: 0.85    # normalized edit similarity above this triggers name-near-match

maintenance:
  # Existing fields stay; new ones for talk-page summaries:
  talk_summary_min_new_entries: 5     # librarian summarizes after N new unresolved entries
  talk_summary_min_interval_seconds: 3600  # don't re-summarize more often than this
```

Defaults are tuned for "single user with one or two agents." The N-writes / T-time knobs in `sessions:` are the ones swarm operators will most likely tune — a 20-agent swarm might want a much shorter inactivity timeout to keep commits granular.

### Vault binding

The MCP server resolves the vault path in this priority order:

1. `LLM_WIKI_VAULT` env var (the standard way for MCP clients to register the server).
2. CLI arg: `llm-wiki mcp /path/to/vault` (testing and one-off invocations).
3. Refuse to start if neither is set — no implicit defaults to a guessed location.

The MCP server reuses `daemon/lifecycle.py` to auto-start the daemon on first connect if it isn't running, the same way the CLI does today.

### Quick start config (Claude Code / Claude Desktop)

```json
{
  "mcpServers": {
    "llm-wiki": {
      "command": "llm-wiki",
      "args": ["mcp"],
      "env": { "LLM_WIKI_VAULT": "/home/user/wiki" }
    }
  }
}
```

## Errors

Every new failure mode is a typed error the agent can act on. The MCP protocol supports structured tool errors; the daemon returns them as `{"status": "error", "code": "...", ...details}` and the MCP server translates each into an MCP tool error response with the same structure.

| Code | When | What the agent should do |
|---|---|---|
| `patch-conflict` | V4A patch context lines don't match current page (page changed since the agent read it) | Re-read the page, regenerate the patch, retry. Response includes `current_content_excerpt` to help. |
| `name-collision` | `wiki_create` with a name that already exists case-insensitively | Pick a different name OR call `wiki_update`/`wiki_append` on the existing page. |
| `name-near-match` | `wiki_create` where a similarity check finds a likely duplicate | Warning, not refusal. Response includes `similar_pages: [...]`. Agent decides: proceed (with `force: true`), or use the existing page. |
| `missing-citations` | `wiki_create` or `wiki_append` with empty `citations` | Post to the talk page instead via `wiki_talk_post`, or find a citation and retry. The error message tells the agent this. |
| `missing-author` | Write call without `author` field | Programming error in the calling agent. The MCP tool schema makes `author` required so this normally never reaches the daemon, but the daemon enforces it as a backstop. |
| `frontmatter-invalid` | Required frontmatter fields missing on `wiki_create`, or tags not in taxonomy | Response includes `missing_fields: [...]` and `invalid_tags: [...]`. Agent fixes and retries. |
| `heading-not-found` | `wiki_append` with `after_heading` that doesn't exist on the page | Response includes `available_headings: [...]`. Agent picks a real heading and retries (or omits `after_heading` to append at end of file). |
| `commit-failed` | git commit failed at session settle (repo not initialized, working tree in unexpected state, etc.) | Files are still written. Journal is still on disk. Daemon logs the error and leaves the session in "needs-recovery" state. Next daemon restart retries. The agent doesn't see this — it already got `ok` for the writes themselves. |

Two failure modes that don't reach the error table because they don't fail the operation:

- **Summarizer failed** (silent) → the daemon falls back to a deterministic commit message. The commit still happens. The agent never sees this — it returned long before settle anyway.
- **Re-index failed mid-write** (warning, not error) → the file is written, the agent gets `status: "ok"` with an `index-pending` entry in the `warnings` array. The daemon logs the index failure and the next file watcher cycle reconciles within ~5 seconds. The agent knows search may briefly miss the new content and can either wait, retry the search later, or read the page directly via `wiki_read`.

## Testing strategy

Six layers, each with a clear contract.

### Layer 1: The hard rule (the most important test)

AST-walk every module under `daemon/`, `audit/`, `librarian/`, `adversary/`, `talk/`. Fail if any background-worker code path can reach `page-create` / `page-update` / `page-append` / `session-close` (route names) or the helper functions / route handlers that implement them.

This is the only test that enforces "unsupervised processes never write body content," and it's mechanical so it can't drift over time.

### Layer 2: Daemon route unit tests

Each new route (`page-create`, `page-update`, `page-append`, `session-close`) and each modified route (`read`, `search`, `lint`) gets unit tests covering the happy path plus every error code in the table above. Use the existing daemon-integration test pattern (`tests/test_daemon_integration.py`).

### Layer 3: V4A patch parser tests

Port the parser from the V4A reference implementation; test against:
- Multi-hunk patches
- Addition-only hunks (no context, no removed lines)
- Fuzzy match with context hint when exact match fails
- Conflict cases (context drift beyond threshold)
- Malformed patch syntax
- Patches that try to operate on non-existent pages

### Layer 4: Session lifecycle integration tests

Spin up a fake "client" that opens a session, makes writes, and triggers each settle path:
- Inactivity timeout
- Write count cap
- Explicit `wiki_session_close`
- Daemon graceful shutdown

For each, verify journal state at each step, verify the resulting git commit's message and changed files, verify the journal is archived afterward.

Concurrency tests fold into this layer: two sessions writing different pages simultaneously, two sessions writing the same page simultaneously.

### Layer 5: Recovery tests

Write journal entries by hand, simulate a daemon restart, verify the recovery pass commits everything correctly. Test the partial-line case (truncated last journal entry) explicitly. Test the case where the user committed manually between sessions and the daemon's stage step finds nothing to add.

### Layer 6: End-to-end MCP smoke test

Spin up the actual MCP server in-process, connect via the official Python MCP SDK's test client, exercise each tool through the MCP protocol, verify responses. This is the test that catches "the daemon route works but the MCP wrapper is wrong."

Plus a one-line migration test: existing talk file with no `severity` field defaults to `suggestion` on read; existing issue without `severity` defaults to `minor`.

### What's not in the test plan

- No multi-vault tests (one vault per MCP server is the architectural rule).
- No SSE/HTTP transport tests (stdio only).
- No full-swarm integration tests (two-session concurrency is enough; we don't need 20 agents).

## Out of scope (post-Phase-6)

The following are explicit deferrals, recorded so they don't get pulled into Phase 6 by accident:

- **SSE / HTTP MCP transport.** stdio only for Phase 6.
- **Multi-vault routing in a single MCP server.** One server per vault.
- **"Review-before-merge" worktree mode.** Worktrees are tempting but break the "Obsidian sees changes immediately" property. If anyone wants a cautious mode where MCP writes go to a session branch the user reviews before merge, that's a future config flag — not Phase 6.
- **Brainstorming companion tool.** Sibling, not child. Out of scope by design.
- **`wiki_extract` — collaborative ingest.** A future tool that runs only the extraction + chunking + matching stages of ingest and returns a structured plan (`{extracted_text, suggested_existing_pages, suggested_new_pages}`) without writing anything. The active agent then drives writes via `wiki_create` / `wiki_update` / `wiki_append` using its own conversation context. Phase 6 keeps `wiki_ingest` as the autonomous-pipeline path (now session-aware) and defers `wiki_extract` until someone needs the collaborative model. Useful for the 5-page-PDF case where the active agent has enough context window to make scope decisions itself.
- **Authentication / per-agent permissioning.** Single-user tool. Multi-user is a future concern.
- **Issue reopening.** Once resolved, an issue is closed forever. If a resolution turns out to be wrong, file a new issue (or `git revert` the resolution commit). Reopening adds state-machine complexity for a rare case.
- **Hot-reload of MCP tool descriptions.** Standard MCP behavior — descriptions are sent on connect; tuning means restarting the client.
- **Global cost ceilings (`max_tokens_per_hour` / `max_tokens_per_day` / per-provider caps).** A future `cost_control:` config section will sit at the LLM queue layer and rate-limit all maintenance LLM calls (librarian summary refinement, adversary verification, talk-page summarizer, session commit summarizer) under a single budget. Phase 6 designs forward-compatibly for this: every maintenance LLM call already routes through the existing LLM queue at `priority="maintenance"`, which is the natural choke point for a future rate limiter to plug into. The queue should pass through enough metadata (calling worker name, model, estimated input tokens) that adding the limiter later doesn't require re-plumbing.

## Summary of new code surface

| Area | What's new |
|---|---|
| `src/llm_wiki/mcp/` | New package: MCP server entry point, tool definitions, request/response shapes. Thin pass-through to the daemon client. |
| `src/llm_wiki/daemon/server.py` | Four new routes: `page-create`, `page-update`, `page-append` (write), `session-close` (session management). Enrichment of `read`, `search`, `lint` responses. |
| `src/llm_wiki/daemon/sessions.py` | New module: session model, journal append/read, settle pipeline, recovery pass. |
| `src/llm_wiki/daemon/v4a_patch.py` | New module: V4A parser + applier, ported from the codex/cline reference implementation. |
| `src/llm_wiki/daemon/commit.py` | New module: serial commit lock, summarizer call, git staging/committing, fallback message. |
| `src/llm_wiki/issues/queue.py` | Add `severity` field to `Issue` dataclass; auditor sets it on file. |
| `src/llm_wiki/talk/page.py` | Add `index` (positional, computed on load), `severity`, and `resolves: list[int]` fields to `TalkEntry`; extend header parser/writer to round-trip an `<!-- ... -->` metadata comment; add pure-Python resolver to compute the open set. |
| `src/llm_wiki/librarian/agent.py` | Add talk-page summary refresh job. |
| `src/llm_wiki/audit/checks.py` | Auditor sets severity when filing issues. |
| `src/llm_wiki/cli/main.py` | New `llm-wiki mcp [vault]` command that starts the MCP server. |
| `src/llm_wiki/config.py` | New `mcp`, `sessions`, `write` config sections; extend `maintenance`. |
| `tests/test_mcp/` | New test directory: MCP integration smoke test, daemon-route unit tests, session lifecycle tests, recovery tests, V4A parser tests, the AST hard-rule test. |
| `pyproject.toml` | Add `mcp` (official Python SDK) as a dependency. |

This is the complete scope for Phase 6. The phase ends when all tests in the layers above pass and a real Claude Code session can read, query, and write through the MCP server with commits landing in git history automatically.

## Changelog

- **2026-04-08** — Initial design approved.
- **2026-04-08** — §"Talk entries gain `severity` and append-only closure" amended. Original draft showed talk entries as a YAML list on disk (`- index: 3 ...`). Replaced with markdown-with-HTML-comment format: bold-text entry headers stay as today, `severity` and `resolves` ride in a `<!-- key:value -->` comment on the header line, indices are positional (computed at load time, not stored). Motivation: a YAML on-disk format renders as raw text in Obsidian, breaking Principle 2 ("plain markdown on a filesystem is the substrate"). The HTML-comment approach is invisible in Obsidian render mode, requires no migration of existing files, keeps the dataclass cleanly typed (`index`, `severity`, `resolves` as first-class fields), and writes the same line shape as today for the common case (suggestion-severity entry, no resolves). Caught during plan review for Phase 6a.
- **2026-04-08** — §"The journal" clarified. Original text described the `path` field as "the wiki-relative path of the file the operation touched" with examples like `wiki/sRNA-tQuant.md`, implicitly assuming the default `wiki_dir = "wiki/"`. Clarified to "the path of the file the operation touched, relative to `vault_root`," and added an explicit note that under a flat Obsidian-style vault (`wiki_dir = ""`), paths drop the `wiki/` prefix. The daemon must derive the journal path from `page_path.relative_to(vault_root)` rather than hardcoding the prefix, so the commit pipeline's `git add` step finds the file under any valid `wiki_dir` config. Caught during plan review for Phase 6b — the original wording would have produced a journal/file divergence that broke `git add` for users with non-default `wiki_dir`.
- **2026-04-08** — §"Session model" amended to specify that `connection_id` is **supplied by the calling client in the request payload**, not derived from the Unix-socket connection. Original text was silent on where `connection_id` came from, which silently assumed the daemon could materialize it from per-connection state. But the daemon's Unix-socket protocol is one-message-per-connection: opening a fresh socket per request, processing one message, and closing. So a per-Unix-socket UUID would create one daemon session per write — the inactivity timer would never accumulate multiple writes, the cap warning would never trigger, and the cross-write grouping that justifies the session model would never happen. Under the amended model, the MCP server (Phase 6c) generates one UUID at stdio-session startup and threads it into every `client.request({...})` call via a `ToolContext`. CLI ingest generates a per-invocation UUID. The daemon's write/session-close handlers extract `connection_id` from the request payload and return `missing-connection-id` if absent. `session-close` uses `SessionRegistry.get_active(author, connection_id)` for an unambiguous per-MCP-client lookup, replacing the previous "find any session for this author" semantics that would have been ambiguous under multi-connection-per-author scenarios. Caught during plan review for Phase 6b — the previous design was architecturally incompatible with the per-message daemon protocol and the corresponding `session-close` test would have broken under any per-connection lookup attempt.
