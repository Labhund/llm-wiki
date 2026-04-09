# LLM Wiki

Agent-first knowledge base tool — wiki over RAG. Inspired by [Andrej Karpathy's idea](https://x.com/karpathy/status/1908534332534366468) that LLMs should maintain persistent, self-updating wikis instead of re-deriving knowledge from sources on every query.

Plain markdown with wikilinks — natively browsable in Obsidian by humans, natively navigable by agents via CLI and MCP.

## Install

```bash
pip install -e ".[dev]"
```

## Quick Start

Point it at any directory of markdown files:

```bash
# Index a vault (no daemon needed)
llm-wiki init /path/to/your/vault

# Search (auto-starts daemon on first use)
llm-wiki search "sRNA embeddings" --vault /path/to/your/vault

# Read with viewports (don't dump the whole page)
llm-wiki read sRNA-tQuant --vault /path/to/your/vault              # top section + TOC
llm-wiki read sRNA-tQuant --section method --vault /path/to/your/vault  # specific section
llm-wiki read sRNA-tQuant --grep "k-means" --vault /path/to/your/vault  # grep within page

# Budget-aware manifest (hierarchical index)
llm-wiki manifest --vault /path/to/your/vault --budget 5000

# Ingest a document into the wiki
llm-wiki ingest paper.pdf --vault /path/to/your/vault  # PDF, DOCX, markdown, images

# Run structural checks (orphans, broken links, missing markers, broken citations)
llm-wiki lint --vault /path/to/your/vault

# Manage the resulting issue queue
llm-wiki issues list --vault /path/to/your/vault
llm-wiki issues show <issue-id> --vault /path/to/your/vault
llm-wiki issues resolve <issue-id> --vault /path/to/your/vault

# Daemon management
llm-wiki serve /path/to/your/vault   # start daemon in foreground
llm-wiki stop --vault /path/to/your/vault

# Inspect maintenance workers
llm-wiki maintenance status --vault /path/to/your/vault

# Talk pages — async discussion sidecars
llm-wiki talk read <page-name> --vault /path/to/your/vault
llm-wiki talk post <page-name> --message "..." --vault /path/to/your/vault
llm-wiki talk list --vault /path/to/your/vault
```

State lives in `~/.llm-wiki/vaults/` — your vault directory stays clean. The daemon keeps the index in memory, watches for file changes (Obsidian edits), and re-indexes automatically.

## How It Works

The core insight: RAG re-derives on every query. A compiled wiki accumulates knowledge, maintains cross-references, tracks provenance, and improves over time. The agent navigates the wiki like a human browses the web — search, scan snippets, click through, skim headings, Ctrl+F — but with token budgets instead of screen pixels.

### Token Budgets

Every operation is budget-aware. A 100-page wiki doesn't dump everything into context:

- **Hierarchical manifest** — Level 0 (cluster summaries) → Level 1 (page entries) → Level 2 (full entry) → Level 3 (page content). Budget determines how deep you go.
- **Intra-page viewports** — `top` (first section + TOC), `section` (by name), `grep` (pattern match), `full`. Pages with `%%` section markers get precise slicing; plain headings work too.
- **Pagination** — search results, manifest entries, and viewport content all support cursor-based pagination within budget.

### Section Markers

Pages can use Obsidian-native hidden comments for machine-readable section boundaries (invisible in Obsidian preview):

```markdown
%% section: overview, tokens: 120 %%
## Overview

Content here...

%% section: method, tokens: 280 %%
## Method

Content here...
```

No markers? Falls back to `##`/`###` headings. No headings? Treated as one section.

### Maintenance Agents

Five background workers run in the daemon's event loop, keeping the wiki honest over time. Each one is an asyncio coroutine on a configurable interval, and all LLM calls flow through the shared queue at `priority="maintenance"` so user-facing queries always preempt them.

- **Auditor** — structural integrity checks (orphans, broken wikilinks, missing markers, broken citations). Files persistent issues to `wiki/.issues/<id>.md` with deterministic IDs so re-runs are idempotent. Exposed on demand via `llm-wiki lint`.
- **Compliance reviewer** — debounced response to file edits. Heuristic checks for missing citations, structural drift (auto-inserts invisible `%% section: %%` markers), and substantive new ideas. Never touches human-authored prose.
- **Librarian** — consumes traversal logs to refine `tags`/`summary` via LLM and recompute authority scores from the link graph plus usage. Also refreshes per-page talk-summary digests (Phase 6a) when a page accumulates enough new unresolved entries since the last summary. State persists in sidecar JSON files so refinements survive vault rescans without ever mutating page frontmatter.
- **Adversary** — samples claims weighted by age × inverse authority, fetches the cited raw source, and verifies via LLM. Validated claims update `last_corroborated`; contradicted/unsupported verdicts file `claim-failed` issues at `critical` severity; ambiguous verdicts post to talk pages at `critical` severity for human review.

Every finding carries a severity (`critical | moderate | minor` for issues; talk entries add `suggestion | new_connection`). The auditor and compliance reviewer set severity per check type — broken citations are critical, broken wikilinks are moderate, orphans are minor — and the daemon's `read`, `search`, and `lint` routes fold these into their responses so an active agent sees the maintenance backlog inline. Critical and moderate talk entries appear verbatim in `wiki_read`; lower-severity entries collapse into a librarian-generated 2-sentence summary. Talk-page closure is append-only: a later entry with `resolves: [N]` removes prior entry N from open counts and from `recent_critical`/`recent_moderate`, but the original entry stays in the file as audit trail.

Findings flow into a shared issue queue (`llm-wiki issues list`) and append-only talk pages (`llm-wiki talk read <page>`). The agents may file issues, append to talk pages, update sidecar metadata, and insert invisible markers — but never edit human-authored markdown body content. "Human prose is sacred."

## Philosophy

The principles plans are derived from. See [PHILOSOPHY.md](PHILOSOPHY.md) for the full document.

- **The wiki is a compounding artifact, not RAG.** Knowledge is compiled once and kept current. Cross-references accumulate. Synthesis improves over time.
- **Plain markdown on a filesystem is the substrate.** Any tool can edit it — Obsidian, vim, an MCP-connected agent. Anything that requires the daemon to be the *only* path to the wiki is a step away from the substrate.
- **Background vs supervised, not human vs machine.** Cron-driven workers stay locked out of body content. Anything a user starts intentionally — interactive agents, autonomous research workers — is supervised and trusted to write. The boundary is supervision, not species.
- **Main pages are sourced; talk pages are everything pre-source.** Every claim in the wiki traces back to a primary source. Half-formed ideas, proposals, and contradictions live on talk pages. Brainstorming is a sibling, not a child.
- **The framework absorbs boredom on behalf of both sides.** Humans and models both get lazy about hygiene. The daemon makes schema enforcement, indexing, journaling, and committing invisible so the agent's context stays focused on intent.
- **The active agent is the writer; the daemon is the kernel.** The daemon doesn't decide what to write — it provides capabilities and runs continuous between-session work (indexing, refinement, verification, audits) that the agent can't replicate.
- **Visibility creates load-bearing.** Talk pages and issues only become useful when the active agent can't ignore them. `wiki_read` folds them in by default.
- **Git is the audit trail. Not a shadow log.** Every supervised mutation is a commit, attributed to its author via the trailer. No provenance frontmatter, no per-page attribution, no second source of truth.
- **Soft tools beat hard rules when the agent is supervised.** Mechanical enforcement is reserved for unsupervised paths and contract violations. Tool descriptions and well-shaped errors do the rest.

## Architecture

```
Interfaces: CLI  │  MCP Server (Phase 6)  │  Obsidian (file access)
                 │
Daemon           │  Unix socket IPC, file watcher, LLM queue,
                 │  write coordinator, background workers
                 │
Core Library     │  Page parser, traversal engine, manifest store,
                 │  search (tantivy), LLM abstraction (litellm)
                 │
Storage          │  Markdown files, tantivy index (~/.llm-wiki/),
                 │  config/prompts
```

Wikipedia's governance model as agent roles: ingest agents write pages, librarians improve cross-references from usage patterns, adversaries challenge claims against raw sources, auditors check structural integrity. Talk pages for async human-agent discussion.

## Project Structure

```
src/llm_wiki/          # Core Python package
  config.py            # Config dataclasses + YAML loading
  tokens.py            # Token counting heuristic
  page.py              # Page parser (markers, headings, wikilinks)
  manifest.py          # ManifestEntry, ManifestStore (hierarchical, budget-aware)
  vault.py             # Vault scanner + viewport reading
  search/
    backend.py         # SearchBackend protocol
    tantivy_backend.py # Tantivy (Rust-backed BM25) implementation
  daemon/
    protocol.py        # Length-prefixed JSON IPC
    server.py          # Async Unix socket server + request routing
    client.py          # Sync client for CLI
    lifecycle.py       # Pidfile, auto-start, cleanup
    watcher.py         # File watcher (mtime polling)
    llm_queue.py       # Concurrency-limited LLM request queue
    writer.py          # Per-page async write locks
    scheduler.py       # IntervalScheduler + ScheduledWorker
    dispatcher.py      # ChangeDispatcher (per-path debouncer)
    snapshot.py        # PageSnapshotStore
    __main__.py        # Daemon entry point
  ingest/
    extractor.py       # Text extraction (PDF, DOCX, markdown, images via liteparse)
    prompts.py         # LLM prompts for concept extraction + page content generation
    agent.py           # IngestAgent orchestrator (extract → LLM → write)
    page_writer.py     # Wiki page creation and idempotent source appending
  issues/
    queue.py           # Issue + IssueQueue (filesystem persistence)
  audit/
    checks.py          # Structural checks (orphans, broken links, markers, citations)
    auditor.py         # Auditor + AuditReport
    compliance.py      # ComplianceReviewer (heuristic edit review)
  librarian/
    log_reader.py      # PageUsage, aggregate_logs (reads traversal_logs.jsonl)
    authority.py       # PageRank-style scoring formula
    overrides.py       # ManifestOverrides JSON sidecar
    prompts.py         # Tag/summary refinement prompt
    agent.py           # LibrarianAgent (refresh + recalc_authority)
  adversary/
    claim_extractor.py # Sentence-level claim extraction
    sampling.py        # Weighted sampling (age + inverse authority)
    prompts.py         # Verification prompt + parser
    agent.py           # AdversaryAgent (verdict dispatch)
  talk/
    page.py            # TalkEntry, TalkPage (append-only sidecars)
    discovery.py       # ensure_talk_marker (invisible discovery marker)
  cli/
    main.py            # Click CLI (routes through daemon)
docs/
  superpowers/
    specs/             # Design specification
    plans/             # Implementation plans
  implementation-ideas/ # 9 optimization design documents
  *.md                 # Philosophy and exploration docs
wiki/                  # Sample compiled knowledge base
raw/                   # Immutable source documents
```

## Documentation

- **[PHILOSOPHY.md](PHILOSOPHY.md)** — The principles plans are derived from. Mostly immutable; amend with cause.
- **[Design Spec](docs/superpowers/specs/2026-04-07-llm-wiki-tool-design.md)** — Full system design: daemon, traversal, agents, MCP interface, token budgets
- **[Phase 6 Spec](docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md)** — MCP server design: write surface, sessions, journal/commit pipeline
- **[Phase 1 Plan](docs/superpowers/plans/2026-04-07-phase1-core-library-cli.md)** — Implementation plan for core library + CLI
- **[Phase 2 Plan](docs/superpowers/plans/2026-04-07-phase2-daemon.md)** — Implementation plan for daemon
- **[Phase 4 Plan](docs/superpowers/plans/2026-04-07-phase4-ingest-pipeline.md)** — Implementation plan for ingest pipeline
- **[Phase 5 Roadmap](docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md)** — Master plan for maintenance agents (sub-phases 5a-5d)
- **[Phase 5a Plan](docs/superpowers/plans/2026-04-08-phase5a-issue-queue-auditor-lint.md)** — Implementation plan for issue queue + auditor + lint
- **[Phase 5b Plan](docs/superpowers/plans/2026-04-08-phase5b-scheduler-compliance.md)** — Implementation plan for scheduler + compliance review
- **[Phase 5c Plan](docs/superpowers/plans/2026-04-08-phase5c-librarian.md)** — Implementation plan for librarian agent + authority scoring
- **[Phase 5d Plan](docs/superpowers/plans/2026-04-08-phase5d-adversary-talk-pages.md)** — Implementation plan for adversary agent + talk pages
- **[Phase 6a Plan](docs/superpowers/plans/2026-04-08-phase6a-visibility-severity.md)** — Implementation plan for visibility & severity (issues/talk severity, librarian talk summaries, enriched read/search/lint)
- **[Phase 6b Plan](docs/superpowers/plans/2026-04-08-phase6b-write-surface.md)** — Implementation plan for write surface, sessions, journal/commit pipeline
- **[Phase 6c Plan](docs/superpowers/plans/2026-04-08-phase6c-mcp-server.md)** — Implementation plan for MCP server + CLI subcommand
- [LLM Wiki - Knowledge Base Pattern](docs/LLM%20Wiki%20-%20Knowledge%20Base%20Pattern.md) — Original pattern description
- [Multi-Turn Traversal Pattern](docs/Multi-Turn%20Traversal%20Pattern.md) — How agents navigate wiki
- [Implementation Ideas](docs/implementation-ideas/README.md) — 9 optimization designs
- [What is an Agent?](docs/what-is-an-agent-identity-and-soul.md) — Philosophy of agent identity and persistence
- [Agent Individuality](docs/agent-individuality-philosophy-session-2026-04-07.md) — What makes agents genuinely distinct

## Roadmap

- [x] **Phase 1: Core Library + CLI** — Page parser, tantivy search, manifest store, viewports, CLI
- [x] **Phase 2: Daemon** — Persistent process, Unix socket IPC, file watcher, LLM queue, write coordination
- [x] **Phase 3: Traversal Engine** — Multi-turn traversal with working memory, budget management, litellm
- [x] **Phase 4: Ingest Pipeline** — liteparse, LLM concept extraction, idempotent page creation/updates
- [x] **Phase 5a: Issue Queue + Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [x] **Phase 5b: Background Workers + Compliance Review** — Async scheduler, debounced compliance pipeline
- [x] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [x] **Phase 5d: Adversary + Talk Pages** — Claim verification, async discussion sidecars
- [x] **Phase 6a: Visibility & Severity** — Severity-aware issues and talk entries, append-only closure, librarian talk summaries, enriched `read`/`search`/`lint` routes
- [ ] **Phase 6b: Write Surface + Sessions** — V4A patches, per-author session journaling, serial commit pipeline, AST hard-rule test
- [ ] **Phase 6c: MCP Server** — Tool definitions, `llm-wiki mcp` CLI subcommand, end-to-end smoke test

## Philosophy

> The LLM writes and maintains the wiki; the human reads and asks questions.
>
> — Andrej Karpathy

The wiki is a persistent, compounding artifact. Knowledge is compiled once and kept current, not re-derived on every query.
