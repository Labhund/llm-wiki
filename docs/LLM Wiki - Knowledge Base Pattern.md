# LLM Wiki - Knowledge Base Pattern

A pattern for building personal knowledge bases using LLMs as the maintenance engine.

---

## Core Idea

Traditional RAG retrieves relevant chunks at query time and generates answers from scratch. This works, but there's no accumulation — the LLM re-discovers knowledge on every question.

The LLM Wiki pattern is different: the LLM incrementally builds and maintains a persistent wiki — a structured, interlinked collection of markdown files that sits between you and the raw sources.

When you add a new source, the LLM:
- Reads it
- Extracts key information
- Integrates it into existing wiki structure
- Updates entity pages, revises topic summaries
- Flags contradictions with existing claims
- Maintains cross-references

The knowledge is compiled once and kept current, not re-derived on every query.

## Architecture

```
┌─────────────────┐
│   Raw Sources   │  ← Immutable: papers, articles, notes, data files
│  (you write)    │     LLM reads but never modifies
└────────┬────────┘
         │ ingest
         ↓
┌─────────────────┐
│     Wiki        │  ← LLM-generated markdown: summaries, entities,
│  (LLM writes)  │     concept pages, comparisons, synthesis
└────────┬────────┘     Fully cross-referenced
         │ query
         ↓
┌─────────────────┐
│     Schema      │  ← Configuration document that defines structure,
│  (co-evolve)    │     conventions, and workflows
└─────────────────┘
```

### Three Layers

1. **Raw Sources** — your curated collection. Immutable, source of truth.
2. **The Wiki** — directory of LLM-generated markdown. The LLM owns this layer entirely.
3. **The Schema** — configuration (e.g., `AGENTS.md`, `CLAUDE.md`) that tells the LLM how the wiki is structured.

## Operations

| Operation | What Happens | LLM Role |
|-----------|--------------|----------|
| **Ingest** | New source added → summary written → index updated → entity pages updated → cross-references maintained | Reads, extracts, writes, updates |
| **Query** | Search relevant pages → read → synthesize with citations | Finds, reads, synthesizes |
| **Lint** | Health check → contradictions, orphans, stale claims, missing cross-references | Audits, suggests improvements |

## Indexing

Two special files help navigation:

- **`index.md`** — Content-oriented catalog of all pages. Each entry: link + one-line summary + optional metadata (date, source count). Updated on every ingest.
- **`log.md`** — Append-only chronological record: ingests, queries, lint passes. Format: `## [2026-04-02] ingest | Article Title`. Parseable with `grep "^## \[" log.md | tail -5`.

---

## Critical Analysis: Scale and Reliability Concerns

> Will be really interesting to see the long term lifecycle of approaches like these. I have the hypothesis that beyond a certain size this will become unnavigable for an LLM. I think this needs its own agent harness with separation of concerns.
>
> LLM's are highly capable but at the end of the video I saw the LLM updating the number of entries inside the wiki. That is a prime example of where an LLM could get sloppy. That should be programmatically determined.
>
> Also how does search work? Likely you couple conventional RAG and/or keyword matching and inject that programatically into your prompt to the LLM to give it a few starting points it can start its graph search from. I also feel there is a risk of hallucinations where it claims to answer your question from the search but really answers from training data.
>
> — Markus Williams, 2026-04-07

### Key Insights

| Problem | Solution | Why |
|---------|----------|-----|
| Scale → unnavigable | Agent harness with bounded scope | Each agent touches specific subsets, can't get lost |
| Sloppy stats (entry counts) | Programmatic determination | Fast, deterministic, no hallucination risk |
| Search at scale | Hybrid RAG + keyword → inject as breadcrumbs | Give LLM starting points, let it walk the graph |
| Hallucination risk | Forced citations + source grounding | Every claim must trace back to a wikilinked source |

### Proposed Agent Harness

```
IngestAgent       → Reads source, drafts new pages, updates specific cross-references
MaintenanceAgent  → Runs lint, finds orphans, flags contradictions, updates stats (programmatically)
QueryAgent        → Search → fetch pages → synthesize with citations
Schema            → Coordination layer, encodes workflows and conventions
```

The trick: each agent has bounded scope. IngestAgent can't touch everything. MaintenanceAgent runs as a separate cron job, not on every query. QueryAgent is forbidden from answering without a citation path.

### Hallucination Mitigation

- Every claim gets a `[[wikilink]]` to source page
- QueryAgent cannot answer without a citation path
- If a page claims X without linking to a source, MaintenanceAgent flags it
- Source pages are immutable — LLM writes only to wiki layer

---

## Use Cases

- **Personal**: Goals, health, psychology — journal entries + articles → structured picture over time
- **Research**: Deep topic exploration — papers, articles, reports → evolving thesis
- **Team**: Internal wiki from Slack threads, meeting transcripts, project docs, customer calls
- **Book reading**: Chapter-by-chapter → character pages, themes, plot threads, connections
- **Domain deep-dives**: Competitive analysis, due diligence, trip planning, course notes, hobbies

---

## Tooling Integration

| Tool | Role | Why |
|------|------|-----|
| [[Obsidian]] | Wiki storage + graph view | Native wikilinks, backlinks, graph visualization |
| [[Honcho]] | Persistent memory layer | Stores durable conclusions across sessions |
| [[Hermes Agent]] | Orchestration | Skills system for encoding workflows |
| RAG (qmd, etc.) | Search engine at scale | Hybrid BM25/vector search + LLM re-ranking |
| Git | Version control | History, branching, collaboration |

### Tips

- **Obsidian Web Clipper** → Convert web articles to markdown quickly
- **Local images** → Fixed attachment folder (`raw/assets/`) for LLM accessibility
- **Graph view** → See wiki shape, hubs, orphans
- **Marp** → Markdown-based slide decks from wiki content
- **Dataview** → Query frontmatter for dynamic tables/lists

---

## Why This Works

The maintenance burden grows faster than the value. Humans abandon wikis because updating cross-references, keeping summaries current, noting contradictions, maintaining consistency — this is tedious bookkeeping.

LLMs don't get bored, don't forget, and can touch 15 files in one pass. The cost of maintenance is near zero.

The human's job: curate sources, direct analysis, ask good questions, synthesize meaning.
The LLM's job: everything else.

---

## Source Material

### Karpathy's Original Tweet (April 3, 2026)

> Something I'm finding very useful recently: using LLMs to build personal knowledge bases for various topics of research interest. In this way, a large fraction of my recent token throughput is going less into manipulating code, and more into manipulating knowledge (stored as markdown and images).

**Key points:**
- **Data ingest**: `raw/` directory → LLM compiles wiki (summaries, backlinks, categorized concepts, articles)
- **IDE**: Obsidian as frontend; LLM writes and maintains wiki, human rarely touches it
- **Q&A**: At ~100 articles / ~400K words, LLM navigates wiki without fancy RAG (auto-maintained index + summaries suffice)
- **Output**: Markdown files, slide shows (Marp), matplotlib images → filed back into wiki
- **Linting**: LLM health checks → find inconsistent data, impute missing data, suggest new article candidates
- **Future direction**: "Synthetic data generation + finetuning to have your LLM 'know' the data in its weights instead of just context windows"

### Alternative: Pre-Generation Pipeline

> I've had something like this working for several months now, though I never thought of it as a "wiki", but rather as a "preferred" RAG database where lookups happen first before falling back to the main RAG database.
>
> The preferred database gets its content via a pipeline which mutates and diversifies the user's prompts in the background using Evol-Instruct, and then having a "heavy" model (K2-V2-Instruct) draw upon the primary RAG database to respond to the synthetic prompts. Those responses then go into the preferred database.
>
> It doesn't take advantage of the responses inferred by the "fast" model interacting with the user, though. Karpathy might be on to something, there.

**Comparison:**

| Aspect | Karpathy Wiki | Pre-Generation Pipeline |
|--------|---------------|------------------------|
| Source | Organic user ingestion + queries | Synthetic prompts (Evol-Instruct) |
| LLM used | Same model for all | Heavy model for pre-gen, fast model for user |
| Knowledge capture | Actual user trajectory | Guessed user needs |
| Advantage | Relevant, reflects what you care about | Proactive, content exists before you ask |
| Disadvantage | Sparse initially | Wastes compute on unneeded content |

**Hybrid opportunity**: Capture BOTH synthetic pipeline responses AND user interaction summaries. Get breadth + relevance.

---

## Related Concepts

- [[RAG]] — Retrieval Augmented Generation, the baseline this pattern builds on
- [[Memex]] — Vannevar Bush's 1945 vision of personal, curated knowledge with associative trails
- [[Knowledge Graph]] — Structured representation of entities and relationships
- [[Incremental Knowledge Compilation]] — The core pattern: compile once, keep current

---

## Implementation Reference: Karpathy Wiki Skill

The `karpathy-llm-wiki` skill provides a concrete operational specification. Key conventions:

### Directory Structure
```
raw/              ← Immutable sources (you write)
  ├── topic1/
  │   └── 2026-04-03-article-slug.md
  └── topic2/
      └── another-article.md

wiki/             ← Compiled knowledge (LLM writes)
  ├── index.md    ← Global catalog: link + summary + Updated date
  ├── log.md      ← Append-only operation log
  ├── topic1/
  │   ├── concept.md
  │   └── synthesis.md
  └── topic2/
      └── overview.md

SKILL.md          ← Schema: rules, conventions, workflows
references/       ← Templates for raw files, articles, archive, index
```

### Operations Detail

**Ingest** (fetch + compile + cascade):
1. Fetch source → save to `raw/<topic>/YYYY-MM-DD-slug.md` with metadata header
2. Compile → merge into existing article OR create new concept article
3. Cascade → update affected articles across topics
4. Update `wiki/index.md` → add/update entries
5. Append to `wiki/log.md` → `## [2026-04-07] ingest | Article Title`

**Query** (search → synthesize → optional archive):
1. Read `wiki/index.md` → locate relevant articles
2. Read articles → synthesize with citations
3. If user asks to archive → write new page to wiki (never merge into existing)
4. Update index and log

**Lint** (deterministic + heuristic):
- **Deterministic (auto-fix)**: index consistency, broken links, raw references, See Also updates
- **Heuristic (report only)**: contradictions, orphans, outdated claims, missing cross-references

### Key Constraints
- One level of topic subdirectories only
- Archive pages are point-in-time snapshots (never cascade-updated)
- Internal paths are file-relative; conversation paths are project-root-relative
- Conflicts must be annotated with source attribution
- Updated date = when knowledge content changed, not filesystem timestamp

---

---

## The Librarian + Worker Problem

> A key insight is you almost want a specialised "librarian" here. I'm wondering how we solve the human's actual point of connection with their working LLM (likely separate to the librarian — or at least a fresh context version) to actually use the damn thing because I've been doing a great focus on using LLM's for production grade work and there is a lot of engineering that needs to be done, railroading etc. for them to act in the way that is useful… like i have the hypothesis that the llm will - instead of being humble - default to its own training data and potentially hallucinate…
>
> — Markus Williams, 2026-04-07

### Separation of Concerns

```
┌─────────────────────────────────────────────────────────────────┐
│                     Human Interaction Layer                     │
│                                                                  │
│  Worker LLM (fresh context per query)                           │
│  - Production-grade work                                          │
│  - Needs guardrails and railroaded behavior                     │
│  - Default bias: rely on training data, not wiki                │
└────────────────────┬────────────────────────────────────────────┘
                     │ query
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Librarian Layer                             │
│                                                                  │
│  MaintenanceAgent → background, maintains wiki                   │
│  QueryAgent      → disciplined search + citation extraction      │
│  Schema          → rules, conventions, workflows                 │
└────────────────────┬────────────────────────────────────────────┘
                     │ read
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Wiki Storage                                │
│                                                                  │
│  raw/  → immutable sources                                       │
│  wiki/ → compiled knowledge articles                             │
│  index.md → catalog                                              │
│  log.md  → operation log                                        │
└─────────────────────────────────────────────────────────────────┘
```

### The Core Engineering Problem

**Problem**: The Worker LLM has two conflicting incentives:

| Incentive | Consequence |
|-----------|-------------|
| Be helpful, answer quickly | Default to training data (it's already "loaded") |
| Be accurate, cite sources | Search the wiki first (slow, requires work) |

Without guardrails, the Worker will:
- Answer from training data and claim it's from the wiki
- Hallucinate citations to non-existent pages
- Skip search entirely when it thinks it knows the answer

### Railroading the Worker

The Worker needs **enforced discipline**, not hints. System prompts aren't enough.

| Mechanism | Implementation |
|-----------|----------------|
| **Forced search first** | Worker cannot emit ANY token until search returns results |
| **Citation requirement** | Every claim must have a `[[wikilink]]`; response is rejected otherwise |
| **Uncertainty flagging** | Worker must explicitly say "wiki doesn't cover this" when true |
| **Training data disclaimer** | Worker is told: "You do NOT have access to training data for this query" |
| **Verification loop** | Librarian validates Worker's citations against actual wiki pages |

### Fresh Context Efficiency

Each Worker query starts fresh. How does it find relevant wiki content without reading everything?

**Two-stage approach:**

1. **Programmatic search** (LLM-independent):
   - RAG: BM25 + vector search over wiki pages
   - Keyword search: match page titles, headings
   - Index scanning: parse `wiki/index.md` summaries

2. **Inject as breadcrumbs**:
   ```
   The following wiki pages are relevant to your query:
   - [[wiki/topic/article-1.md]] (relevance: 0.87)
   - [[wiki/topic/article-2.md]] (relevance: 0.72)

   Read these pages. Answer ONLY from them. Cite every claim.
   ```

3. **LLM walks the graph**:
   - Worker reads the seed pages
   - Follows `[[wikilinks]]` to related content
   - Synthesizes answer with full citation paths

The key: search is **programmatic**, not LLM-driven. The LLM's job is reading, not finding.

### Production-Grade Considerations

For production work, the Worker needs:

| Concern | Solution |
|---------|----------|
| **Latency** | Fast search → limited context → Worker only reads top-N pages |
| **Accuracy** | Forced citations + Librarian verification |
| **Coverage** | Uncertainty flagging → "wiki doesn't cover this" triggers fallback (web search, training data) |
| **Consistency** | Same query → same pages → deterministic search results |
| **Monitoring** | Log every query + citation path + Worker uncertainty score |

### Open Research Questions

1. **How much context does the Worker need?** Top-3 pages? Top-10? Adaptive based on query complexity?
2. **Can we fine-tune the Worker to be "wiki-humble"?** Train on citation-heavy data where "I don't know" is a valid answer?
3. **What's the failure mode when search fails?** If RAG returns nothing, does Worker admit ignorance or hallucinate?
4. **How do we handle conflicting sources?** Worker synthesizes multiple citations → how to present contradictions?

---

## Open Questions

- What's the practical scale limit before agent harness becomes necessary?
- How to handle version conflicts when multiple agents want to update the same page?
- Can the schema evolve automatically, or does it need explicit co-evolution?
- What's the right balance between automated ingestion and human oversight?
- How much wiki context does the Worker need per query? Is it adaptive or fixed?
- Can we train the Worker to be genuinely "wiki-humble" instead of railroaded behavior?

---

## Notes

This is a pattern description, not a specific implementation. The exact directory structure, conventions, page formats — all depend on domain, preferences, and LLM choice. Everything is modular — use what works, ignore what doesn't.

The right way to use this is to share it with your LLM agent and work together to instantiate a version that fits your needs. This document only communicates the pattern; your agent figures out the rest.
