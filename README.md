# LLM Wiki

> **This project is deprecated.** Development has moved to [lacuna-wiki](https://github.com/Labhund/lacuna-wiki), a cleaner rewrite with a simpler single-tool MCP surface, structured agent skills, and a compounding knowledge graph. Use that instead.

---

An agent-first knowledge base: plain markdown with wikilinks, a daemon that keeps it indexed and honest, and an MCP server that lets agents navigate it the way a researcher reads Wikipedia.

> *The LLM writes and maintains the wiki; the human reads and asks questions.*
> — [Andrej Karpathy](https://x.com/karpathy/status/2039805659525644595)

---

> **Active development warning.** This project is being stress-tested by its author and is changing rapidly. Interfaces, config formats, and on-disk layouts may break between commits without notice. Not recommended for production use yet.
>

> **Cost warning.** The daemon runs background agents (auditor, librarian, adversary, compliance reviewer) on configurable cron intervals that make real LLM calls against whatever API key you've configured. Before pointing this at a paid API (OpenRouter, OpenAI, etc.), review your `~/.llm-wiki/config.yaml` maintenance intervals and set conservative values — or disable background workers you don't need. To cap spend, set `cloud_hourly_limit` and `cloud_daily_limit` in `llm_queue` config (weighted token units: input + output×5). Background maintenance calls are hard-blocked when a limit is reached; interactive query/ingest calls log a warning and proceed.
---

## Why Not RAG? Why Not Plain Markdown?

**RAG re-derives on every query.** Your agent reads the same papers, synthesises the same conclusions, and starts from scratch next session. Nothing compounds. Every query is session zero.

**Plain markdown + a folder tool isn't much better.** The agent dumps files into context (expensive, lossy), can't navigate a large vault efficiently, and produces a wiki that drifts into inconsistency with nothing maintaining it between sessions. Stale claims, broken wikilinks, orphaned pages — they accumulate, unchecked.

**LLM Wiki compiles knowledge into a graph that improves over time.** Each ingested paper becomes pages with cross-references to existing content. Background agents keep claims honest against their sources. The wiki at month 6 is substantially richer than at day 1 — not just more pages, but better connections, fewer stale claims, and a usage-informed authority graph that knows which pages matter.

---

## What You Get

### Compounding knowledge

Ingest a paper and it becomes wiki pages with wikilinks to existing content. Next ingest builds on those. Knowledge accumulates instead of evaporating. The cross-reference graph grows with every source.

### Budget-aware traversal

Agents don't dump everything into context. They search, check a hierarchical manifest, read page headers and tables of contents, grep for specific terms, and follow wikilinks with intent — the same cognitive pattern as a human reading Wikipedia, at the same token efficiency. A 1000-page wiki doesn't cost 1000 pages of context.

### Background quality agents

Four background workers run in the daemon's event loop, keeping the wiki honest while you're not looking:

- **Auditor** — structural integrity: orphaned pages, broken wikilinks, missing citations, structural drift
- **Compliance reviewer** — debounced on file edits: checks new content for citation and structural issues
- **Librarian** — usage-driven: refines tags and summaries, scores page authority from the link graph and usage patterns
- **Adversary** — claim verification: samples claims weighted by age and inverse authority, fetches the cited source, verifies via LLM

Findings surface inline: `wiki_read` folds in issue and talk digests by default, so an agent sees the maintenance backlog when it reads a page — not hidden in a separate tool call.

### Git as the audit trail

Every supervised write is a git commit attributed to the writing agent via `Agent:` trailer. `git log --grep "Agent: researcher-3"` is a meaningful provenance query — the swarm equivalent of `git log --author`. No shadow log, no frontmatter attribution.

### Human + agent native

Open the vault in Obsidian and browse it normally. Connect an agent via MCP and let it navigate the same files. No impedance mismatch, no sync step, no separate representation.

### Cited answers, honest gaps

Queries answer from the wiki, not from model training knowledge. Every claim cites a page:

```
$ llm-wiki query "boltz"
Boltz-2 is a component utilized within the structure prediction stage
of the ProteinDJ pipeline [[protein-dj]].

Its function is to "validate designs and predict binder-target
interfaces" [[protein-dj#architecture]].

**Missing Information:**
The research notes do not provide specific technical details regarding
boltz-2, such as the type of model it employs or its specific input
and output formats.

Citations: protein-dj
```

The "Missing Information" section is a feature: if the wiki doesn't have a detail, the answer says so rather than hallucinating from training. The gap gets filled when you ingest the relevant source — `llm-wiki ingest boltz2-paper.pdf` would create the missing page and the next query would cite it.

### Synthesis cache

Every cited query answer becomes a first-class wiki page (`type: synthesis`). These pages enter the BM25 index alongside ingest pages — so the next similar query finds the prior synthesis in the traversal results and can accept it verbatim, extend it with new information, or create a fresh page if nothing fits.

Three outcomes, decided by the LLM:

- **accept** — existing synthesis fully answers the query; returned directly, no re-generation
- **update** — existing synthesis found, new information surfaced; page overwritten with extended version
- **create** — no relevant synthesis exists; new page written to `wiki/`

The speed benefit is emergent: synthesis pages compete naturally with ingest pages in BM25. If fresh ingest pages are more relevant they outrank stale synthesis; the agent always sees the freshest material. No separate cache layer, no TTL logic, no pre-traversal short-circuit.

The quality gate is simple: synthesis pages are only written when the answer contains `[[wiki-link]]` citations. Unbacked answers don't compound. Low-scoring synthesis pages are eventually culled by the adversary pipeline, same as ingest pages.

### Talk pages for the uncitable

Contradictions, half-formed connections, and ideas without sources live on talk pages alongside the pages they concern — visible to the agent, separate from sourced content. First-class path, not a consolation.

---

## Install

```bash
pip install -e .
```

> Not yet on PyPI — install from source. Requires Python 3.11+.

LLM inference is via [litellm](https://github.com/BerriAI/litellm) — bring your own model (local or API).

Run the interactive setup wizard to configure backends, create a vault, and register the MCP server with your agent framework:

```bash
llm-wiki configure
```

The wizard walks you through model selection (smart model for depth work, fast model for background tasks), vault setup, and agent framework integration (Hermes or Claude Code MCP registration, one-shot).

---

## Quick Start

```bash
# Index a vault (no daemon needed)
llm-wiki init /path/to/vault

# Search (auto-starts daemon on first use)
llm-wiki search "sRNA embeddings" --vault /path/to/vault

# Read with viewports — don't dump the whole page
llm-wiki read sRNA-tQuant --vault /path/to/vault                   # top section + TOC
llm-wiki read sRNA-tQuant --section method --vault /path/to/vault  # specific section
llm-wiki read sRNA-tQuant --grep "k-means" --vault /path/to/vault  # grep within page

# Budget-aware manifest (hierarchical index of the whole vault)
llm-wiki manifest --vault /path/to/vault --budget 5000

# Ingest a document (PDF, DOCX, markdown, images)
llm-wiki ingest paper.pdf --vault /path/to/vault

# Structural checks
llm-wiki lint --vault /path/to/vault
llm-wiki issues list --vault /path/to/vault

# Talk pages
llm-wiki talk read <page-name> --vault /path/to/vault
llm-wiki talk post <page-name> --message "..." --vault /path/to/vault
```

State lives in `~/.llm-wiki/` — your vault directory stays clean. The daemon watches for file changes (Obsidian edits) and re-indexes automatically.

---

## MCP / Agent Integration

The quickest path is `llm-wiki configure` — it handles MCP registration for Hermes and Claude Code automatically. For manual setup:

**Claude Code** — add to `~/.claude/mcp.json` (or `.claude/mcp.json` in cwd for project-local):

```json
{
  "mcpServers": {
    "llm-wiki": {
      "command": "llm-wiki",
      "args": ["mcp"],
      "env": { "LLM_WIKI_VAULT": "/absolute/path/to/vault" }
    }
  }
}
```

**Hermes** — add to `~/.hermes/config.yaml` under `mcp_servers:`:

```yaml
  llm-wiki:
    command: llm-wiki
    args: [mcp]
    env:
      LLM_WIKI_VAULT: "/absolute/path/to/vault"
    timeout: 120
```

**Other frameworks** — any stdio MCP client: command `llm-wiki mcp`, env `LLM_WIKI_VAULT`.

The daemon auto-starts on first connect (allow ~30s on first call). 21 tools across five families:

| Family | Tools |
|--------|-------|
| Read | `wiki_search`, `wiki_read`, `wiki_manifest`, `wiki_status` |
| Query | `wiki_query`, `wiki_ingest`, `wiki_lint` |
| Write | `wiki_create`, `wiki_update`, `wiki_append` |
| Maintenance | `wiki_issues_list/get/resolve`, `wiki_talk_read/post/list`, `wiki_session_close` |
| Inbox | `wiki_inbox_create`, `wiki_inbox_get`, `wiki_inbox_write`, `wiki_inbox_list` |

Every supervised write produces a git commit attributed to the calling agent via the `Agent:` trailer.

---

## Agent Setup

Full setup walkthrough — vault creation, backend config, MCP registration, and patching any existing wiki skills — is in [`skills/setup/SKILL.md`](skills/setup/SKILL.md).

**Existing Obsidian vault?** Set `LLM_WIKI_VAULT` to your vault root instead of creating a new directory. The daemon writes compiled pages to `wiki/` (configurable), which Obsidian indexes alongside your existing notes.

---

## Agent Skills

Skills that prime agents to use llm-wiki correctly by default — research traversal modes, citation discipline, conversational ingest, and maintenance hygiene. Skills are bundled with the pip package; `llm-wiki configure` installs them to your Hermes home automatically.

**Attended** (user present): load `skills/llm-wiki/` (or let the wizard install them)

**Autonomous** (cron, swarm, unattended): load `skills/llm-wiki/autonomous/<subskill>` directly — different scope, conservative defaults, structured exit reports instead of check-ins.

Compatible with Claude Code (via MCP tools) and Hermes (via skill files + MCP). Claude Code uses the MCP tool surface directly; no separate skill files needed.

---

## How It Works

```
Interfaces    CLI  |  MCP Server  |  Obsidian (direct file access)
                   |
Daemon             |  Unix socket IPC, file watcher, LLM queue,
                   |  write coordinator, background workers
                   |
Core Library       |  Page parser, traversal engine, manifest store,
                   |  search (tantivy), LLM abstraction (litellm)
                   |
Storage            |  Markdown files, tantivy index (~/.llm-wiki/),
                   |  config, prompts
```

Wikipedia's governance model as agent roles: ingest agents write pages, the librarian improves cross-references from usage patterns, the adversary challenges claims against raw sources, the auditor checks structural integrity. Talk pages for async human-agent discussion.

### Token Budgets

Every operation is budget-aware:

- **Hierarchical manifest** — cluster summaries → page entries → page content. Budget determines how deep you go.
- **Intra-page viewports** — `top` (first section + TOC), `section` (by heading), `grep` (pattern match), `full`. Pages with `%%` section markers get precise slicing; plain headings fall back gracefully.
- **Pagination** — search results, manifest entries, and viewport content all support cursor-based pagination within budget.

### Section Markers

Obsidian-native hidden comments provide machine-readable section boundaries (invisible in Obsidian preview):

```markdown
%% section: overview, tokens: 120 %%
## Overview

Content here...

%% section: method, tokens: 280 %%
## Method

Content here...
```

No markers? Falls back to `##`/`###` heading-based slicing. No headings? Treated as one section.

### Sessions and Commits

Writes are grouped into per-agent **sessions** that batch into a single git commit. Sessions settle on: inactivity timeout (5 min), write-count cap (30 writes, with a `session-cap-approaching` warning at 18), explicit `wiki_session_close`, or daemon shutdown. Each commit carries `Session:`, `Agent:`, and `Writes:` trailers — making the git log a meaningful audit of swarm activity.

---

## Philosophy

- **The wiki is a compounding artifact, not RAG.** Knowledge is compiled once and kept current. Cross-references accumulate. Synthesis improves over time.
- **Plain markdown on a filesystem is the substrate.** Any tool can edit it — Obsidian, vim, an MCP-connected agent. Anything that requires the daemon to be the *only* path to the wiki is a step away from the substrate.
- **Background vs supervised, not human vs machine.** Background workers stay locked out of page body content. Anything a user starts intentionally — interactive agents, autonomous research workers — is supervised and trusted to write. The boundary is supervision, not species.
- **Main pages are sourced; talk pages are everything pre-source.** Every claim in the wiki traces back to a primary source. Half-formed ideas, proposals, and contradictions live on talk pages.
- **Visibility creates load-bearing.** Talk pages and issues only become useful when the active agent can't ignore them. `wiki_read` folds them in by default.
- **Git is the audit trail. Not a shadow log.** Every supervised mutation is a commit, attributed to its author via trailer. No provenance frontmatter, no second source of truth.
- **The framework absorbs boredom on behalf of both sides.** Schema enforcement, indexing, journaling, committing — invisible to the agent so its context stays focused on intent.

See [PHILOSOPHY.md](PHILOSOPHY.md) for the full document.

---

## Roadmap

- [x] **Phase 1: Core Library + CLI** — Page parser, tantivy search, manifest store, viewports, CLI
- [x] **Phase 2: Daemon** — Persistent process, Unix socket IPC, file watcher, LLM queue, write coordination
- [x] **Phase 3: Traversal Engine** — Multi-turn traversal with working memory, budget management, litellm
- [x] **Phase 4: Ingest Pipeline** — PDF/DOCX/image extraction, LLM concept extraction, idempotent page creation
- [x] **Phase 5a: Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [x] **Phase 5b: Compliance Review** — Async scheduler, debounced compliance pipeline
- [x] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [x] **Phase 5d: Adversary + Talk Pages** — Claim verification against raw sources, async discussion sidecars
- [x] **Phase 6a: Visibility & Severity** — Severity-aware issues and talk entries, enriched read/search/lint
- [x] **Phase 6b: Write Surface + Sessions** — V4A patches, session journaling, serial commit pipeline, recovery
- [x] **Phase 6c: MCP Server** — 17 MCP tools over stdio, stable per-session connection IDs
- [x] **Configure Wizard** — interactive `llm-wiki configure`: smart/fast model tiers, vault setup, Hermes skill install + MCP registration, Claude Code mcp.json integration
- [x] **Synthesis Cache** — cited query answers become `type: synthesis` wiki pages; future queries accept/update/create via BM25 without re-synthesising from scratch

---

## Documentation

- **[PHILOSOPHY.md](PHILOSOPHY.md)** — The principles plans are derived from. Mostly immutable; amend with cause.
- **[LLM Wiki - Knowledge Base Pattern](docs/LLM%20Wiki%20-%20Knowledge%20Base%20Pattern.md)** — Original pattern description
- **[Multi-Turn Traversal Pattern](docs/Multi-Turn%20Traversal%20Pattern.md)** — How agents navigate the wiki
- **[Implementation Ideas](docs/implementation-ideas/README.md)** — 9 optimization designs (query federation, incremental authority, semantic clustering, and more)
