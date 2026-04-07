# LLM Wiki Tool — Design Specification

**Date:** 2026-04-07  
**Status:** Draft  
**Origin:** Karpathy's LLM wiki concept — persistent, self-updating knowledge bases as an alternative to RAG.

## Vision

An agent-first knowledge base tool where LLMs maintain a persistent wiki instead of re-deriving knowledge from sources on every query. The wiki is plain markdown with wikilinks — natively browsable in Obsidian by humans, natively navigable by agents via CLI and MCP. Borrows Wikipedia's governance model: specialized agent roles (editors, librarians, adversaries, auditors) keep the knowledge base honest through continuous feedback loops.

## Core Insight

RAG re-derives on every query. A compiled wiki accumulates knowledge, maintains cross-references, tracks provenance, and improves over time through usage-driven maintenance. The agent navigates the wiki like a human browses the web — search, scan, click through, skim, Ctrl+F, follow links — but with token budgets instead of screen pixels.

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────┐
│  Interfaces                                     │
│  CLI  │  MCP Server  │  Obsidian (file access)  │
├─────────────────────────────────────────────────┤
│  Daemon (llm-wiki-d)                            │
│  - Request router (query/ingest/search/read)    │
│  - LLM request queue (concurrency-limited)      │
│  - Search index (tantivy + optional embeddings) │
│  - Write coordinator (per-page queue)           │
│  - File watcher (inotify/fswatch)               │
│  - Background workers (librarian, adversary,    │
│    auditor)                                     │
│  - Session manager                              │
├─────────────────────────────────────────────────┤
│  Core Library (Python, async)                   │
│  - Traversal engine                             │
│  - Ingest pipeline                              │
│  - Working memory management                    │
│  - LLM abstraction (litellm/ollama)             │
│  - Search interface (pluggable)                 │
├─────────────────────────────────────────────────┤
│  Storage (filesystem)                           │
│  - Wiki pages (markdown = Obsidian vault)       │
│  - Raw sources (immutable, user-curated)        │
│  - Schema (config, prompts, agent definitions)  │
│  - Index (tantivy + optional vector store)      │
│  - Issue queue (markdown)                       │
└─────────────────────────────────────────────────┘
```

**Obsidian** is not a client of the daemon — it reads/writes the same markdown files directly. The daemon's file watcher detects Obsidian edits and re-indexes them.

**Two vault modes:**
- **Managed** — full pipeline with `raw/`, `wiki/`, `schema/` directories. Ingest compiles raw sources into wiki pages.
- **Vault** — point at any existing directory of markdown. Query and traversal work over whatever's there. No ingest pipeline, no raw/wiki separation. Manifests are built lazily: first query triggers index build (tantivy + basic manifest from page content). No summaries or tags until the librarian runs (optional in vault mode). Viewports work if pages have `%%` markers or headings. Vault mode is "best effort" — less metadata, still functional.

## 2. Daemon Design

### Lifecycle
- Auto-starts on first CLI invocation (checks pidfile, starts if absent).
- `llm-wiki stop` shuts down cleanly.
- Crash recovery: checks for stale pidfile on startup, rebuilds index from wiki files if needed.

### IPC
- **v1: Unix socket** — fast, no port conflicts, filesystem permission model.
- **Future: HTTP listener** — addable alongside the socket for LAN/multi-machine access. The transport is a thin shell around the same request handler. The protocol is JSON-serialized request/response — transport-agnostic by design.

```python
class WikiDaemon:
    async def handle_request(self, request: WikiRequest) -> WikiResponse:
        match request:
            case QueryRequest(): return await self.query(request)
            case IngestRequest(): return await self.ingest(request)
            case SearchRequest(): return await self.search(request)
            case ReadRequest(): return await self.read(request)
            ...
```

### File Watcher
- Detects new/modified markdown files (Obsidian edits, manual changes).
- Triggers re-indexing of changed files.
- Optionally triggers ingest for new files in `raw/`.
- **Write conflict handling:** See Write Coordination section above.

### LLM Request Queue

The daemon gates all LLM calls through a concurrency-limited async queue. This is critical when sharing a GPU (e.g., llama-server serving Gemma 4 to 8 agents).

- **Concurrency slots:** configurable max concurrent LLM requests (default: 2). Prevents overloading the inference server.
- **Priority levels:** query > ingest > maintenance. A user-facing query doesn't wait behind a librarian batch job.
- **Token accounting:** tracks tokens consumed per hour/day. Configurable daily limits for cloud APIs (e.g., `cloud_daily_limit: 1000000`). Local inference is unlimited by default but slot-limited.
- **Backpressure:** when the queue is full, callers get an estimated wait time. CLI can show a spinner; MCP callers can decide to retry or reduce budget.

```yaml
# config.yaml
llm_queue:
  max_concurrent: 2            # slots on inference server
  priority_order: [query, ingest, maintenance]
  cloud_daily_limit: null      # token limit for cloud APIs (null = unlimited)
  cloud_hourly_limit: null
```

### Write Coordination

The daemon maintains a per-page write queue. Concurrent writes to the same page are serialized in arrival order. Writes to different pages proceed in parallel.

- If an external edit (Obsidian) arrives mid-write, the daemon's write is aborted.
- The daemon re-reads the externally modified file and flags the conflict in the issue queue. The librarian picks up open conflicts and performs a three-way diff (raw source → old wiki version → new external edit), proposing a merged version in the issue. Human approves, edits, or the librarian auto-resolves if the diff is non-conflicting.
- External edits always win — the daemon never overwrites unsaved human work.

### Background Workers
- Run as coroutines in the daemon's async event loop.
- Configurable schedules via `schema/config.yaml`.
- Background workers use the LLM queue at `maintenance` priority — they yield to queries and ingests.

## 3. Core Operations

### Ingest
Source goes in, wiki pages come out.

```
raw/paper.pdf → daemon receives ingest request
  → extract text (liteparse — PDF, DOCX, images with OCR)
  → LLM summarizes, identifies entities, key claims with citations
  → creates/updates wiki pages (one per concept, not one per source)
  → updates index (programmatic, not LLM — avoids count drift)
  → re-indexes affected pages in tantivy
  → logs operation
```

Pages are organized by **concept**, not by source. A single paper might touch 3 existing pages and create 1 new one. Raw sources are immutable — the LLM never modifies them.

**Strict ingest rules:**
1. Every claim written to a wiki page must cite the raw source (`[[raw/source-name]]`) and relevant passage.
2. The ingest agent must not introduce interpretations beyond what the source states. If the source says "X correlates with Y", the wiki page must not say "X causes Y."
3. Pages must follow the section structure format (see Section 4: Section Markers).
4. The ingest agent writes initial manifest metadata (summary, tags, section boundaries with token counts). The librarian refines these over time based on usage.
5. When updating an existing page with new source material, the ingest agent must preserve existing citations — it adds, it does not silently replace.

### Query
Question goes in, cited answer comes out.

```
"How do we validate sRNA embeddings?"
  → daemon receives query + budget + optional user_context
  → search index → ranked candidates with manifest entries
  → traversal engine:
      - composes each turn's context fresh (TUI model)
      - working memory carries forward as compressed summaries
      - structural contract: every turn produces
        {pages_read, learned, remaining_questions, next_candidates}
      - stops when: answer complete, candidates exhausted, turn budget hit
  → returns: synthesized answer + citation path + traversal metadata
```

Traversal metadata (what was read, what was useful) feeds back to the librarian.

### Maintain
Background daemon workers — not user-initiated.

See Section 5 (Agent Roles) for full details.

## 4. Token Budget System

Context budget is a first-class parameter on every operation — the equivalent of viewport size for agents. Governs manifest loading, pages per turn, compaction aggressiveness, pagination, and synthesis depth.

```python
wiki_query("question", budget=8192)   # tight: manifest scan → 1 page → synthesize
wiki_query("question", budget=32000)  # normal: manifest → 3-4 pages → deeper synthesis
wiki_query("question", budget=100000) # generous: broad traversal, full citations
```

### Hierarchical Manifest

The index is not one artifact — it's a tree of progressively detailed manifests. The budget determines how much of the tree the agent holds at once.

```
Level 0: Topic clusters (always fits)
  "bioinformatics (47 pages) | machine-learning (23 pages) | protocols (12 pages)"

Level 1: Cluster manifests (paginated)
  bioinformatics → top 10 pages by relevance, [more...]

Level 2: Page manifest entries
  srna-embeddings → summary, tags, token count, sections, authority score

Level 3: Full page content (read on demand, viewport-able)
```

### Page Manifest Entry (Tool-Shape)

Each wiki page presents like an agent tool — minimal token-count metadata for informed decisions without reading the full page:

```yaml
srna-embeddings:
  summary: "Validates sRNA embeddings via PCA and k-means (k=10), uses silhouette scores"
  tags: [bioinformatics, embeddings, validation, clustering]
  tokens: 847
  sections:
    - name: "Overview"            # tokens: 120
    - name: "Method"              # tokens: 280
    - name: "Validation Results"  # tokens: 310
    - name: "Open Questions"      # tokens: 137
  links_to: [inter-rep-variant-analysis, clustering-metrics]
  links_from: [index]
  last_modified: 2026-04-01
  last_corroborated: 2026-03-28    # last adversary check that validated claims
  read_count: 12
  usefulness: 0.82
  authority: 0.74
```

**Manifest lifecycle:** Manifest entries are derived artifacts stored in the tantivy index, not in page frontmatter. Initial summary and tags come from the ingest LLM when a page is created. The librarian refines them based on usage patterns (traversal logs, relevance scores). Manifest is refreshed when: page content changes (file watcher trigger), traversal logs accumulate past a configurable threshold (`manifest_refresh_after_traversals`, default: 10), or authority scores recalculate on schedule.

**Citation format:** `[[page-name]]` for page-level, `[[page-name#section]]` for section-level (section optional). The auditor verifies citations in two passes: (1) programmatic check that page/section exists, (2) LLM spot-check of random citations for claim-support validity. Failed checks go to the issue queue.

### Section Markers

Pages use Obsidian-native hidden comments (`%% %%`) as authoritative section boundaries. These are invisible in Obsidian's preview and reading modes but parseable by the daemon:

```markdown
---
title: sRNA Embeddings
source: [[raw/smith-2026-srna.pdf]]
---

%% section: overview, tokens: 120 %%
## Overview

Content here...

%% section: method, tokens: 280 %%
## Method

Content here...
```

- `%%` markers are the authoritative boundaries for the daemon. `##` headings coexist for human readability but are not relied on for slicing.
- The ingest pipeline writes both markers and headings. Manually authored pages in vault mode fall back to heading detection (`##` and `###`) if no `%%` markers are present.
- Pages without any structure (no markers, no headings) are treated as a single section — viewport=top returns the first N tokens.
- Section token counts in the markers are updated by the librarian when page content changes.
- **Human editing:** Humans can add, remove, or edit `%%` markers directly in Obsidian (source mode). If a human adds/removes a section without updating markers, the librarian detects structural drift (new headings without markers, orphaned markers) and rewrites markers to match current page structure. Humans don't need to understand the marker system — the librarian keeps it consistent.

### Intra-Page Viewports

Budget management extends inside individual pages. `wiki_read` supports viewport-style access:

- **top** — summary + first section + table of contents of remaining sections
- **section** — read a specific section by name
- **grep** — sections/paragraphs matching a pattern with surrounding context
- **full** — entire page (if budget allows)

Section boundaries and per-section token counts are pre-computed from `%%` markers (or heading fallback) and stored in the tantivy index alongside page content.

### Pagination

When results exceed budget, return partial results + continuation cursor. The caller decides whether to spend more budget. Same pattern at every level — manifest pages, search results, intra-page viewports.

## 5. Agent Roles & Feedback Loops

Six agent roles modeled on Wikipedia's governance structure:

| Agent | Trigger | Purpose |
|-------|---------|---------|
| **Ingest** | New source added | Compile raw sources into concept-oriented wiki pages with citations |
| **Query** | User/agent question | Multi-turn traversal, synthesize answers with citation paths |
| **Librarian** | Periodic + post-traversal | Improve wiki from usage logs — tags, cross-refs, cluster summaries, authority scores, manifest maintenance |
| **Adversary** | Periodic | Challenge claims — trace wiki assertions back to raw sources, flag drift/over-generalization/misreadings. Checks N claims per run (configurable, default 5). Claim selection weighted by: age since last check, inverse authority (low-authority pages checked more), and random sampling. Logs all checks including validations. |
| **Auditor** | Periodic | Structural integrity — orphan pages, broken links, index drift, citation verification |
| **Lint** | On-demand | Quick subset of auditor checks, user-invokable via CLI |

### Feedback Loops

```
Ingest → Wiki ← Librarian (improves based on usage)
                ← Adversary (challenges against raw sources)
                ← Auditor (structural integrity)
                     ↓
               Issue Queue → auto-fix or human review
```

The adversary is the critical differentiator. Without adversarial checking, the wiki drifts from its sources over time — each ingestion introduces small interpretation errors that compound. The adversary reads raw sources AND wiki pages in the same context and asks "do these actually agree?"

The issue queue is a directory (`wiki/.issues/`) of individual markdown files — one per issue, with status in frontmatter (open/resolved/wontfix). Visible and editable in Obsidian, actionable by humans or agents. Resolved issues are retained for audit trail.

### Compliance Review Queue

Any unstructured edit to a wiki page — whether from a human in Obsidian, an external agent via `wiki_write`, or a non-ingest process — enters a compliance review queue:

1. **File watcher detects change** → starts a configurable debounce timer (default: 30s) to batch rapid edits.
2. **After debounce** → daemon diffs the change and creates a review item.
3. **Auditor picks up the review** → checks: do new claims have citations? Do `%%` markers need updating? Are strict ingest rules maintained? Is this a new idea that should be flagged for broader review?
4. **Result** → auto-approve (minor/compliant edit), auto-fix (add missing markers, update manifest), or flag to issue queue (substantive uncited claims, structural violations, new ideas worth discussing).

New ideas detected during compliance review (e.g., a human adds a speculative paragraph) are flagged with a `type: new-idea` issue. The librarian evaluates whether the idea should be integrated, needs sourcing, or warrants discussion on the talk page.

**Minor change heuristic:** edits under 50 characters with no new claims and no structural changes are auto-approved without LLM review. This prevents noise from typo fixes and formatting tweaks.

**Human prose is sacred:** compliance review never rewrites human-authored text. Agents may add missing `%%` markers (invisible to humans), flag missing citations in the issue queue or talk page, and suggest improvements — but never alter a human's words directly. If the auditor believes a human-written passage needs changes, it posts a suggestion to the talk page. The human decides whether to accept. This is non-negotiable — trust in the system depends on humans knowing their edits won't be silently "corrected."

### Talk Pages

Each wiki page has an optional sidecar talk page (`page-name.talk.md`) for asynchronous discussion between agents and humans. Modeled on Wikipedia talk pages.

```markdown
---
page: srna-embeddings
---

**2026-04-07 14:32 — @human**
The silhouette threshold of 0.5 feels arbitrary. Is there a source for this?

**2026-04-07 15:01 — @adversary**
Checked [[raw/smith-2026-srna.pdf]] — the paper uses 0.5 as "well-separated" but cites
[[raw/rousseeuw-1987.pdf]] which defines the scale. Added citation to clustering-metrics page.

**2026-04-08 09:15 — @librarian**
Traversal logs show 8/12 queries about sRNA validation also read clustering-metrics.
Adding cross-reference in the Overview section.
```

- v1: flat chronological log, no threading. Entries are timestamped with author (human name or agent role).
- The librarian reads talk pages as part of its maintenance loop — unresolved questions become audit items.
- Humans write to talk pages directly in Obsidian. Agents append via the daemon.
- Talk pages are optional — they're created on first discussion, not for every wiki page.
- **Discovery:** when a talk page exists, the daemon injects a visible link at the bottom of the wiki page in Obsidian: `%% talk: [[page-name.talk]] %%` — hidden in preview but visible in source mode, and the manifest entry includes `has_talk: true` so agents know to check.
- **Future (v2+):** threading via `@reply-to:timestamp` syntax, issue archive rotation to `.archive/` after configurable retention period.

### Authority Scoring (PageRank-Inspired)

Pages are ranked using signals from the link graph and usage data:

```
authority = (inlink_count × 0.3) + (traversal_usefulness × 0.4) + (freshness × 0.2) + (outlink_quality × 0.1)
```

**Freshness** is defined as recency of last adversary corroboration (stored as `last_corroborated` in the manifest). A page that was recently verified by the adversary scores higher than one whose claims haven't been checked in months. Pages never checked by the adversary get a neutral freshness score, not zero — they haven't failed, they just haven't been verified yet.

High-authority pages surface first in search results and manifest ordering. The librarian recalculates authority scores on a configurable schedule.

## 6. MCP Interface

Two tiers — agents pick their level of engagement:

### High-Level Tools (the "browser" — tool does the thinking)

| Tool | Input | Output |
|------|-------|--------|
| `wiki_query` | question, budget, user_context? | synthesized answer + citations + traversal metadata |
| `wiki_ingest` | source_path or URL | confirmation + pages created/updated |
| `wiki_lint` | scope? | list of issues found |

### Low-Level Tools (the "DOM" — agent drives traversal)

| Tool | Input | Output |
|------|-------|--------|
| `wiki_search` | query, limit, budget, cursor? | manifest entries (ranked, paginated) |
| `wiki_read` | page_path, viewport?, section?, grep?, budget? | page content (full or viewport) + metadata |
| `wiki_write` | page_path, content, citation | confirmation (daemon re-indexes) |
| `wiki_manifest` | cluster?, depth?, budget, cursor? | hierarchical manifest (paginated) |
| `wiki_status` | — | vault stats, daemon health, index freshness |

When used via MCP high-level tools, the daemon does multi-turn traversal internally with perfectly managed context (the TUI model — each LLM call gets a fresh, curated prompt). The calling agent's context receives only the synthesized answer.

When an agent drives traversal via low-level tools, the daemon returns smart responses with navigation suggestions — the agent manages its own context but the tool coaches it.

## 7. Search Infrastructure

Pluggable search with sensible defaults:

- **Default: tantivy** (Rust-backed, Python bindings via tantivy-py) — BM25 keyword search with stemming/tokenization.
- **Optional: vector embeddings** — auto-detects ollama (default: nomic-embed-text) or configurable endpoint. Stored alongside tantivy index.
- **Hybrid mode** — BM25 + vector with reciprocal rank fusion. Configurable weight balance.
- **Pluggable interface** — `SearchBackend` protocol. Users can swap in custom implementations.

Search results include manifest entries (not raw content) so the agent can make informed budget decisions before reading.

## 8. Traversal Engine

### Working Memory Lifecycle

Each traversal maintains ephemeral working memory — not persisted to the wiki.

```
Turn 0: Search → manifest entries → pick starting page(s)
Turn N: Working memory (compressed) + new page viewport → update:
  - pages_read: [{path, sections_read, learned, relevance_score}]
  - remaining_questions: [...]
  - next_candidates: [{path, reason, priority}]
  - hypothesis: current working theory
  - budget_remaining: tokens left
Turn Final: Synthesize answer from working memory + citations
```

Each turn's LLM context is composed fresh — old page content is replaced by its summary in working memory. The budget tracks how much context is consumed and governs compaction aggressiveness.

### Termination Criteria

Traversal stops when any of:
- **Answer complete** — LLM judges all sub-questions answered (LLM judgment call, not a confidence score).
- **Candidates exhausted** — no more pages to read that haven't been visited.
- **Hard budget ceiling** — forced stop at 80% of budget consumed. If the LLM hasn't converged by then, return a partial answer + `needs_more_budget: true` flag. The caller decides whether to spend more.
- **Turn limit** — configurable max turns (default: 10). Safety net against runaway traversals.

The per-turn budget is not a separate parameter — it's derived from the overall budget divided by estimated remaining turns. As the traversal progresses, each turn's allocation adjusts based on budget consumed so far.

### Structural Contracts (Non-Negotiable)

These are enforced regardless of prompt customization:
1. Every turn must produce the working memory fields above (including `hypothesis`).
2. Every claim in the final answer must cite a wiki page using `[[page-name]]` or `[[page-name#section]]` format.
3. Citation paths must be traceable — which pages were read in what order.
4. The traversal must respect the budget parameter.
5. The traversal must use the LLM request queue — no direct LLM calls bypassing concurrency limits.

### Configurable Behavior

How the agent reasons about relevance, when it stops, synthesis style — these are tunable via prompt files in `schema/prompts/`. Ship with good defaults, override for domain-specific needs.

## 9. Configuration & Schema

Lives in the vault root under `schema/`:

```
schema/
  config.yaml          # daemon settings, LLM endpoints, budgets
  prompts/
    ingest.md          # system prompt for ingestion
    traverse.md        # system prompt for traversal decisions
    synthesize.md      # system prompt for answer synthesis
    librarian.md       # system prompt for maintenance
    adversary.md       # system prompt for adversarial checks
    auditor.md         # system prompt for structural audits
  agents.md            # agent role definitions
```

### config.yaml

```yaml
llm:
  default: "litellm/gemma4"         # litellm format — points to llama-server, ollama, or cloud
  embeddings: "ollama/nomic-embed-text"
  api_key: null                      # for cloud providers

daemon:
  socket: ~/.llm-wiki/daemon.sock
  log_level: info

llm_queue:
  max_concurrent: 2              # slots on inference server
  priority_order: [query, ingest, maintenance]
  cloud_daily_limit: null        # token limit for cloud APIs
  cloud_hourly_limit: null

search:
  backend: tantivy
  embeddings_enabled: true
  hybrid_weight: 0.6

budgets:
  default_query: 16000
  default_ingest: 32000
  manifest_page_size: 20
  manifest_refresh_after_traversals: 10
  page_viewport_default: "top"
  hard_ceiling_pct: 0.8          # forced stop at this % of budget
  max_traversal_turns: 10

maintenance:
  librarian_interval: "6h"
  adversary_interval: "12h"
  adversary_claims_per_run: 5
  auditor_interval: "24h"
  authority_recalc: "12h"
  compliance_debounce_secs: 30   # wait after last edit before review
  talk_pages_enabled: true

vault:
  mode: managed
  raw_dir: raw/
  wiki_dir: wiki/
  watch: true

honcho:
  enabled: false
  endpoint: "http://localhost:8000"
```

### Prompt Files

Each prompt file has non-negotiable structural constraints at the top (must produce working memory format, must cite sources) with reasoning style left flexible below. Users override for domain-specific needs.

### agents.md

Optional. Defines specialized agent roles for multi-agent setups. Single-user setups use defaults.

## 10. Integrations

| Integration | Role | Required? |
|------------|------|-----------|
| **liteparse** | Document text extraction (PDF, DOCX, images) | Default for ingest |
| **litellm** | Unified LLM abstraction — routes to any backend | Required |
| **tantivy-py** | Keyword search index | Default search backend |
| **llama-server / ollama** | Local LLM inference server. litellm routes to whichever is running. Default model: Gemma 4. | Default backend (either works) |
| **Honcho** | User context enrichment for search | Optional |
| **Obsidian** | Human browsing interface (file-level) | No integration needed — same files |

**LLM routing chain:** `daemon → LLM request queue → litellm → inference server (llama-server, ollama, or cloud API)`. litellm handles the abstraction — the daemon doesn't know or care which backend is running. Users configure the endpoint in `config.yaml` using litellm's model format.

## 11. Scale Considerations

**Target:** 8 concurrent agents on a single GPU sharing 100k KV cache.

- Daemon serializes writes, parallelizes reads.
- Token budgets prevent any single agent from monopolizing context.
- tantivy search is fast enough that index queries don't bottleneck.
- LLM calls are the true bottleneck — managed by the daemon's LLM request queue (which gates access to whatever inference server is running).
- Unix socket IPC adds negligible overhead.
- File watcher batches rapid edits to avoid re-index storms.
- Future: HTTP listener for LAN access, enabling multi-machine deployment.

## 12. Future Work

Captured in TODO.md:
- **Vault → Managed migration tool** — let 4-8 local LLMs reorganize an unstructured vault into managed structure over days/weeks.
- **All 9 optimization strategies** from implementation-ideas/ — to be prioritized and implemented incrementally after core is working.
- **SOUL.md / agent individuality** — Honcho-driven agent personalization where different agents develop different reading preferences and synthesis styles from accumulated experience.
- **Knowledge half-life** — trust decay on claims that haven't been corroborated by recent sources.
- **Incremental compilation** — dependency graph (from wikilinks) for targeted re-compilation when sources change.
