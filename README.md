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

# Daemon management
llm-wiki serve /path/to/your/vault   # start daemon in foreground
llm-wiki stop --vault /path/to/your/vault
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
    __main__.py        # Daemon entry point
  ingest/
    extractor.py       # Text extraction (PDF, DOCX, markdown, images via liteparse)
    prompts.py         # LLM prompts for concept extraction + page content generation
    agent.py           # IngestAgent orchestrator (extract → LLM → write)
    page_writer.py     # Wiki page creation and idempotent source appending
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

- **[Design Spec](docs/superpowers/specs/2026-04-07-llm-wiki-tool-design.md)** — Full system design: daemon, traversal, agents, MCP interface, token budgets
- **[Phase 1 Plan](docs/superpowers/plans/2026-04-07-phase1-core-library-cli.md)** — Implementation plan for core library + CLI
- **[Phase 2 Plan](docs/superpowers/plans/2026-04-07-phase2-daemon.md)** — Implementation plan for daemon
- **[Phase 4 Plan](docs/superpowers/plans/2026-04-07-phase4-ingest-pipeline.md)** — Implementation plan for ingest pipeline
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
- [ ] **Phase 5: Maintenance Agents** — Librarian, adversary, auditor, compliance review, talk pages
- [ ] **Phase 6: MCP Server** — High-level + low-level tools for agent integration

## Philosophy

> The LLM writes and maintains the wiki; the human reads and asks questions.
>
> — Andrej Karpathy

The wiki is a persistent, compounding artifact. Knowledge is compiled once and kept current, not re-derived on every query.
