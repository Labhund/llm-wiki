# TODO

## Research-Mode Ingest — Design + Implementation

Emerged from live ingest session (2026-04-09). Core insight: attended ingest should be a **research tool that compounds**, not a supervised version of background extraction. Three distinct modes (queue / brief / deep), a persistent plan file format, synthesis claim markers, and eventual claim resonance matching across future ingests.

---

### 1. `inbox/` — New Standard Vault Directory

**What:** A fourth top-level directory alongside `raw/`, `wiki/`, `schema/`. Holds active ingest plan files — mutable, git-tracked, lifecycle-aware.

**Why not `raw/`:** `raw/` is for immutable source copies. Plan files are process artifacts — they get edited, checked off, appended to across sessions. Different nature.

**What needs to change:**
- `llm-wiki init` should create `inbox/` alongside `raw/wiki/schema/`
- `Vault.scan()` should recognise `inbox/` as a known directory (skip it for page scanning)
- Document `inbox/` in vault spec / setup skill

---

### 2. Ingest Plan File Format

**What:** A structured markdown file in `inbox/YYYY-MM-DD-slug-plan.md`, companion to a source in `raw/`. The persistent cursor for a multi-session deep ingest.

**Why a file, not talk pages:** Talk pages are append-only. A plan file is mutable — checkboxes get ticked, decisions get edited, the task list evolves. Committed to git after each session so it survives context compaction and restarts.

**Standard format:**

```markdown
---
source: raw/2026-04-09-silke-proteindj-2026.pdf
started: 2026-04-09
status: in-progress   # or: completed
sessions: 1
---

# ProteinDJ Ingest Plan

## Claims / Ideas
- [x] RFDiffusion — diffusion modes, contigs syntax, noise scale as design knob
- [x] BindCraft — hallucination-based design, induced-fit interfaces
- [ ] ProteinMPNN — sequence design, checkpoint variants, FastRelax integration  ← resume here
- [ ] FamPNN — side-chain iterative fitting, comparison to ProteinMPNN
- [ ] BindSweeper — parameter sweep, multi-dimensional YAML config
- [ ] Boltz-2 vs AF2 — contradicts existing benchmarks page (needs human call)
- [ ] Evaluation claims — success rate figures, flag for adversary

## Decisions
- rfdiffusion: new page (diffusion-models coverage insufficient)
- protein-design-pipelines comparison page: defer until 2+ pipeline sources ingested

## Session Notes

### 2026-04-09
Covered RFDiffusion and BindCraft. Boltz-2 section contradicts benchmarks page —
deferred for human review next session. Pages created: [[rfdiffusion]], [[bindcraft]].
```

**What needs to change:**
- Document this format in `skills/llm-wiki/ingest.md`
- `wiki_lint` should detect inbox/ files with `status: in-progress` and surface them as minor issues ("in-progress ingest: X, started DATE")
- No daemon code needed — this is convention + skill + lint rule

---

### 3. Synthesis Claim Markers

**What:** A `status: synthesis` frontmatter field on wiki pages (or per-claim markers within pages) that marks content as originating from analysis rather than direct source extraction.

**Why it matters:**
- Synthesis claims are the most valuable nodes in the knowledge graph — original thought that exists nowhere else
- They need different treatment from extracted claims: the adversary shouldn't try to verify them against their cited source (the source IS the analysis session); instead it should watch for corroborating sources
- Required infrastructure for claim resonance matching (item 4)

**What needs to change:**

1. **Frontmatter schema** — add `status` field with valid values: `extracted` (default), `synthesis`, `draft`. Document in schema.
2. **Compliance reviewer** — treat `status: synthesis` pages as valid; don't flag missing external citations if status is synthesis. The citation is the analysis session reference.
3. **Adversary** — skip verification pass for synthesis claims. Instead, flag synthesis pages in the resonance matching queue (item 4).
4. **Librarian** — synthesis pages get higher priority for cross-reference building (they're connection points, not endpoints).
5. **`wiki_lint`** — optionally flag synthesis claims older than N months without any resonance talk posts (configurable, default off).

---

### 4. Claim Resonance Matching (Phase 7)

**What:** A post-ingest pipeline step that compares newly ingested source claims against existing wiki claims — weighted toward synthesis claims — and files talk posts where meaningful overlap is found.

**The insight:** RAG cannot do this. The wiki accumulates claims over time. When a new source arrives that touches an existing synthesis claim, that connection should surface automatically — even if the human never thought to look. This is the compounding value made active.

**Direction:**
- Current adversary: `claim → cited source` (verify this claim against the source it came from)
- Resonance matching: `new source → existing claims` (when something new arrives, find what it touches)

**What needs to change:**

1. **New `IngestAgent` post-step** — after page creation, extract key claims from the ingested source and run semantic similarity against existing wiki claims (via the tantivy index + embedding comparison).

2. **Claim index** — claims need to be indexable for this to be efficient at scale. Either:
   - Tag claims with embeddings at write time (store in sidecar JSON)
   - Use the existing tantivy index with semantic search
   - Maintain a separate claim registry (heavier, more accurate)

3. **Resonance threshold** — configurable similarity threshold. Above threshold → file a `resonance` talk post. Start conservative (high threshold) to avoid noise.

4. **Target weighting** — weight matching toward `status: synthesis` claims first, then authority-scored pages, then everything else.

5. **New talk entry type: `resonance`** (item 5).

6. **Config keys:**
   ```yaml
   ingest:
     resonance_matching: true
     resonance_threshold: 0.82
     resonance_target: [synthesis, high_authority]
   ```

---

### 5. New Talk Entry Type: `resonance`

**What:** A distinct talk entry type for claim resonance findings, separate from adversary verdicts, suggestions, and connection notes.

**Format:**
```
type: resonance
source: raw/2026-04-09-new-paper.pdf
relevance: 0.87
note: "New source may corroborate/extend/contradict this claim. Review recommended."
```

**What needs to change:**

1. **`TalkEntry` model** — add `resonance` as a valid type alongside `suggestion`, `new_connection`, `adversary-finding`.
2. **Severity** — default `moderate` (surfaces inline but doesn't demand immediate action).
3. **`wiki_read` digest** — resonance entries appear in the inline talk digest when reading a page. Same surface as other talk entries.
4. **`wiki_lint`** — flag unreviewed resonance entries older than N weeks (configurable).
5. **Resolution path** — human reviews, either: promotes synthesis to main claim with the new source as corroborating citation, or dismisses the resonance as a false match.

---

### 6. PDF Extraction Pipeline — Configurable `pdf_extractor`

**What:** A config key controlling which tool is used to extract text from PDFs before ingest. Current default (`pdftotext`) is insufficient for academic papers with tables, figures, equations.

**Reference:** Qianfan-OCR (Abiray/Qianfan-OCR-GGUF on HuggingFace) — 4B VLM, Q4_K_M at 2.72GB, runs via llama.cpp on CPU/GPU. Layout-as-Thought approach: image→markdown in a single model pass.

**What needs to change:**

1. **Config schema** — add to `schema/config.yaml`:
   ```yaml
   ingest:
     pdf_extractor: pdftotext   # pdftotext | local-ocr | marker | nougat
     local_ocr_endpoint: "http://localhost:8006/v1"   # for local-ocr
     local_ocr_model: "qianfan-ocr"
   ```

2. **`extractor.py`** — dispatch to correct tool based on config:
   - `pdftotext`: current path (subprocess call)
   - `local-ocr`: send PDF page images to vision endpoint, collect markdown
   - `marker`: call marker API/subprocess
   - `nougat`: call nougat subprocess

3. **Extraction quality signal** — when extracted text appears mangled (heuristics: repeated short lines, excessive whitespace, low word/line ratio), emit a warning in the ingest response so the skill can flag it to the user before writing any pages.

4. **raw/ format note** — for PDFs, store the original file in `raw/` as `.pdf` (canonical), not just the extracted text. The `.pdf` is the immutable source; the extracted text is agent food.

---

### 7. Attended Ingest Skill Rewrite

**What:** Replace the current two-path (conversational / automated) attended ingest with three distinct modes that reflect actual use cases.

**Current problem:** "Conversational" mode is just automated extraction with a human watching. Both paths produce the same output (extracted pages). The attended agent's unique value — user context, memory, wiki knowledge, synthesis capability — is unused.

**Three modes:**

**Queue** — "I have a paper, ingest it, I don't have opinions."
- Agent copies source to `raw/`, hands off to `wiki_ingest` (background)
- Reports what was created
- No deep engagement required

**Brief** — "Read it for me with my context. Tell me what matters."
- Agent reads source with user memory + wiki context loaded
- Produces a briefing: what's genuinely new to *you*, what's already covered, what contradicts existing pages, which bits warrant reading yourself
- User decides: queue the rest / go deeper on specific claims / do nothing
- The briefing IS the value — not page creation

**Deep** — "Let's think through this together, claim by claim."
- Agent creates inbox plan file (item 2) before touching any wiki tool
- Task list = claims/ideas (not pages)
- Per-claim loop: agent analysis → human reaction → scratchpad (talk post on relevant page) → decision (create page now / defer / talk post only / skip)
- Session ends with checkpoint written to plan file, plan committed to git
- Multi-session: next session reads plan file, reconstructs task list, resumes

**What needs to change:**
- `skills/llm-wiki/ingest.md` — full rewrite of Act 2 and Act 3
- `skills/llm-wiki/autonomous/ingest.md` — unchanged (autonomous = queue mode only)
- Document the inbox plan file convention as part of deep mode

---

### 8. Source Reading Status

**What:** Sources in `raw/` carry a `reading_status` field (`unread` | `in_progress` | `read`) tracking researcher engagement. Enforced by code — never requires manual header editing or LLM inference.

**The flaw it fixes:** Autonomous ingest creates wiki pages that look like verified knowledge but have never been evaluated by the researcher. Reading status makes that distinction mechanical and visible.

**Implementation principle:** Everything about reading status is bookkeeping — frontmatter parses, date arithmetic, file writes. Zero LLM calls. The auditor handles detection, `wiki_ingest` handles initialisation, a new `wiki_source_mark` tool handles updates. LLM is only invoked for what genuinely requires understanding (brief, deep analysis, resonance matching).

**What needs to change:**

1. **Source metadata format.** For PDFs: source stays as `.pdf` (immutable); companion `raw/YYYY-MM-DD-slug.md` holds extracted text + frontmatter metadata. For markdown sources: frontmatter is mutable metadata, body is immutable. Tool only ever touches frontmatter, never body.

   Frontmatter fields:
   ```yaml
   ---
   reading_status: unread   # unread | in_progress | read
   ingested: 2026-04-09
   source_type: paper       # paper | article | transcript | book | other
   ---
   ```

2. **`wiki_ingest` initialises status.** On source copy, always writes `reading_status: unread` into frontmatter. Deterministic Python, no LLM. Autonomous ingest cannot promote a source past `unread` — only attended engagement does that.

3. **New MCP tool: `wiki_source_mark(path, status)`** — the only path to update reading status. Updates frontmatter field, git-commits the change with a trailer (`Source-Status: unread→in_progress`). Pure file op, no LLM. The skill calls this at the right moments; the human never edits headers manually.

4. **Extend auditor with source checks (pure Python, no LLM):**
   - Source file missing `reading_status` → `minor` issue: "Source missing reading_status: raw/X"
   - Source with `reading_status: unread` and `ingested` older than N days (configurable, default 30) → `minor` issue: "Unread source: raw/X (ingested DATE)"
   - Source with `reading_status: in_progress` and no `inbox/` plan file → `moderate` issue: "In-progress source has no plan file: raw/X"
   - Scan is O(n) file reads + frontmatter parse. No LLM.

5. **Adversary weighting.** When sampling claims for verification, weight `unread` source claims higher — they've had no human review. Pure scoring adjustment in `sampling.py`, no LLM.

6. **Skill protocol (when to call `wiki_source_mark`):**
   - Brief mode start → `wiki_source_mark(source, "in_progress")`
   - Brief mode complete, no deep session planned → `wiki_source_mark(source, "read")`
   - Deep mode session start → `wiki_source_mark(source, "in_progress")`
   - Deep mode plan file completed → `wiki_source_mark(source, "read")`
   - Autonomous ingest → sets `unread` only, never calls `wiki_source_mark`

---

## LLM-Facing Interface Optimisation — Information Density Pass

**Origin:** Building `docs/gallery.md` (2026-04-09) revealed a concrete mismatch between the designed interface and how an LLM actually consumes it. The human mental model — pretty-printed JSON, verbose key names, whitespace-aided navigation — is actively wasteful for LLM consumers. This section captures the work needed to fix that across all three layers: serialisation, response schema design, and tool description / skill quality.

**Core insight:** LLMs process all tokens in parallel via attention. They don't scan sequentially. Indentation and whitespace that help a human navigate a JSON blob do nothing for the LLM — they are pure token overhead. Research backing: "Lost in the Middle" (Liu et al. 2023) shows positional effects in long contexts; compact token sequences keep related fields closer in the attention field. The consistent finding from serialisation format comparisons is that whitespace adds tokens, not comprehension. The human gets content unrolled and re-expressed by the LLM in readable form — the LLM should receive the raw content in dense form.

**Heterogeneity caveat:** Mixed registers in the same context window (compact JSON tool results + prose conversation + markdown wiki content) create format-switching overhead. The mitigation is consistency: if tool results are compact JSON, *all* tool results should be, establishing a stable agent expectation. Inconsistent formatting is worse than either choice made uniformly.

**Deeper direction — rendered documents, not JSON API:** The whole system is already in one semantic space: wiki pages, skill files, talk pages, inbox plan files, agent reasoning — all markdown. JSON tool responses are the only thing breaking that coherence. The right architecture is a *rendering layer* in the daemon: store structured data internally, serve rendered markdown documents to the agent. The manifest already does this and is the best-behaved tool in the set. This is PHILOSOPHY.md Principle 2 extended to the wire protocol: plain markdown is the substrate. It also makes the system dramatically easier to debug — a developer can read MCP session logs directly, same format as the wiki itself. L1–L4 below are incremental improvements within the current JSON approach; this is the longer-term direction they point toward.

**Three layers, increasing effort:**

---

### L1. Compact Serialisation (One Line, Immediate Win)

**What:** Replace `json.dumps(indent=2)` with `json.dumps(separators=(',',':'))` in the `_ok()` helper that wraps all MCP tool responses.

**Where:** `src/llm_wiki/mcp/tools.py:50`

**Current:**
```python
return [TextContent(type="text", text=json.dumps(response, indent=2))]
```
**After:**
```python
return [TextContent(type="text", text=json.dumps(response, separators=(",", ":")))]
```

**Impact:** Applies to all 17 tools in one change. Removes all indentation whitespace from every tool response. No schema changes, no behaviour changes, no skill updates needed — compact JSON parses identically to pretty-printed JSON and the LLM reads it without assistance.

**Validation:** Log response sizes for a representative session before and after. Compute average token reduction. Expect 20–35% reduction on deeply nested responses (issues/talk digests).

---

### L2. Response Schema Design — Key Names and Field Audit

**What:** Audit all 17 tool response schemas for token efficiency. Two categories of fields require different treatment:

**Programmatically-read fields** — the agent extracts a value and acts on it; the key name never appears in agent reasoning or output. These can use short keys at zero comprehension cost.

**Reasoning-read fields** — the agent includes the value in its output or reasoning chain. Short keys here push ambiguity downstream. These stay verbose.

**Fields to shorten (programmatically read, agent never surfaces these in prose):**

`wiki_read` response:
```
issues.open_count    → issues.n
issues.by_severity   → issues.sev
talk.entry_count     → talk.cnt
talk.open_count      → talk.open
talk.by_severity     → talk.sev
talk.recent_critical → talk.crit
talk.recent_moderate → talk.mod
```

**Fields to keep verbose (agent reasons about or surfaces these):**
- `summary`, `body`, `title`, `message` — always stay readable
- `content` in `wiki_read` — the page text itself, never shorten
- `manifest` in `wiki_search` results — agent uses this for routing decisions

**Fields that warrant a harder look before deciding:**
- `wiki_search` `matches` array structure — the before/match/after format is verbose but the LLM uses it for relevance judgement; test whether a simpler format degrades routing accuracy
- `wiki_manifest` envelope — already returns plain text in `content`; the JSON wrapper is minimal

**What needs to change:**
1. Identify where response dicts are constructed in `src/llm_wiki/daemon/server.py` and update field names there
2. Check `translate_daemon_response()` in `tools.py` — if it remaps field names, update there too
3. Update all 17 tool descriptions to reference new field names where field names appear in descriptions
4. Audit skill files for references to specific field names — update to match
5. Update `docs/gallery.md` examples to reflect new compact field names

---

### L3. Tool Descriptions and Navigation Clarity

**What:** Tool descriptions in `tools.py` are what an agent sees without skill files loaded. They must stand alone — skill files are prompt engineering on top, not a substitute for good tool descriptions. A cold agent (no skills) should make correct tool selection from descriptions alone.

**Gaps to close:**

**1. Three-tool disambiguation: `wiki_manifest` / `wiki_search` / `wiki_query`**

These are the most likely to be misused. The tradeoff is not obvious:
- `wiki_manifest` — orient when you don't know where to look yet; zero reading cost
- `wiki_search` — know a term, want to find which pages cover it
- `wiki_query` — have a specific question, want a synthesised answer at near-zero context cost (daemon traverses internally)

The current descriptions don't make these tradeoffs explicit. An agent that uses `wiki_query` for everything never builds its own context; an agent that uses `wiki_search` for everything misses compiled synthesis. Both are wrong patterns that the tool descriptions should prevent.

**2. `wiki_read` viewport guidance**

The viewport parameter (`top` / `section` / `grep` / `full`) is the most important behavioural signal in the system. The tool description should encourage reading with intent: `top` to orient, named section when you know what you need, `full` when you genuinely need the whole page (writing a patch, short page, structural analysis). The manifest provides section sizes before you read — the description should point agents at that information. Frame as a capability, not a prohibition.

**3. Session management**

`wiki_session_close` is the most commonly dropped step. The description should make explicit: all write tools auto-open a session on first call; not closing means relying on inactivity timeout (5 min), which may not fire in short fast sessions. Consider adding a session-open reminder to each write tool description: "Opens a session on first call if none is active; close explicitly with `wiki_session_close` when done."

**4. `wiki_update` patch conflict protocol**

A `patch-conflict` response requires the agent to re-read the page and retry — never rewrite the whole page from scratch. This is a critical behavioural contract that needs to be in the tool description, not only the skill. An agent without the skill will default to full-page rewrite on conflict, which is destructive.

**What needs to change:**
1. Rewrite `wiki_manifest`, `wiki_search`, `wiki_query` descriptions to include explicit disambiguation language
2. Update `wiki_read` description: frame viewport as intent-driven, not rule-driven — `top` to orient, section when you know what you need, `full` when you genuinely need the whole page (patch, short page, structural analysis). Point at manifest section sizes.
3. Add session reminder to all three write tool descriptions (`wiki_create`, `wiki_update`, `wiki_append`)
4. Add patch-conflict re-read protocol to `wiki_update` description

---

### L4. Skill Files — Prompt Engineering Audit

**What:** The skill files are the highest-leverage prompt engineering layer but also token-heavy. Two goals: tighten information density, and verify the behavioural contracts they establish still match what the tools actually do.

**Cross-check field names against daemon output.** The gallery revealed a gap between what we *think* the agent receives and what it actually receives. Field names referenced in skill files must match the actual daemon response schema. After L2 (schema rename), this audit is mandatory — skill files that reference old field names will silently fail.

**Viewport guidance language.** All skill files that involve reading need the updated framing: read with intent, use the manifest section sizes, `full` is appropriate when you genuinely need the whole page. The old "never `full` first" prohibition has been removed from `index.md` and `research.md` — audit `write.md`, `maintain.md`, `ingest.md`, and autonomous skill files for any remaining prohibitive language and replace with the intent-driven framing.


**Session-close ritual.** `wiki_session_close` reminder is present in `index.md` but needs to be echoed at the end of every skill that involves writes — `write.md`, `ingest.md`, `maintain.md`. A principle stated once in an index file doesn't survive multi-hop skill loading.

**Traversal depth.** `research.md` says "one search → done is wrong" but doesn't anchor this. Add: a minimum of 3 hops (manifest → search → at least one read → follow at least one wikilink) before synthesis is reasonable for non-trivial questions. Makes the guidance testable.

**Autonomous skill files.** These run without user feedback — the contracts they establish need to be sharper than attended equivalents. Review `autonomous/ingest.md` and `autonomous/write.md` against actual tool descriptions and daemon behaviour. Any divergence is a silent failure.

**What needs to change:**
1. After L2, grep all skill files for old field names and update
2. Audit all skill files for prohibitive viewport language (`never full`, `full is a last resort`) and replace with intent-driven framing — `write.md`, `maintain.md`, `ingest.md`, autonomous files
3. Add session-close reminder at the end of every skill that involves writes
4. Add traversal hop-count guidance to `research.md`
5. Audit autonomous skill files line-by-line against tool descriptions

---

### Validation Approach

This pass is hard to validate without empirical feedback. Suggested gates:

**L1** — Token count before/after across a representative session. Measure average response size. Expect 20–35% reduction on nested responses. This is the only layer with a mechanical success condition.

**L3** — Cold agent test: load the MCP tools with no skill files, ask a research question, observe whether the agent makes correct tool selections. If it calls `wiki_full` immediately or skips `wiki_manifest`, the tool descriptions are still failing.

**L4** — Same cold agent test post-skill-load. Compare behaviour. The delta between cold and skilled agent reveals what the skills are contributing vs what the tool descriptions carry alone.

**Gallery as ground truth** — `docs/gallery.md` is the living reference for what agent-facing content looks like. After each layer, update the gallery examples to match. If the gallery diverges from what the daemon actually sends, that divergence is the bug.

---

### L5. Rendered Document Responses (Longer-Term Direction)

**What:** Replace the JSON response envelope with rendered markdown documents. The daemon becomes a rendering layer: structured data stored and processed internally, markdown documents served to the agent. L1–L4 are incremental improvements; this is the architectural destination.

**Why this is the right direction:**
- The entire system is already in one semantic space — wiki pages, skills, talk pages, plan files, agent reasoning are all markdown. JSON responses are the only format break.
- Behavioral triggers carry full semantic weight in rendered text. `[CRITICAL ISSUE]` in a document is not the same representation as `{"sev":{"crit":1}}` in a JSON blob — the former has learned emotional salience; the latter is just a number.
- No format-switching overhead. The agent's context window is a continuous stream of one register.
- Debuggable without tooling. A developer reading MCP session logs sees documents, not escaped JSON strings.
- The manifest already proves the model. It returns plain text and is the best-behaved tool in the set.
- Extends PHILOSOPHY.md Principle 2 to the wire protocol: plain markdown is the substrate.

**Sketch of rendered formats:**

`wiki_read` response:
```
[MODERATE ISSUE] broken-link-attention-mechanism-a1b2c3
  Broken link to [[bahdanau-attention]] — no target page in vault

---

## Overview

The [[attention mechanism]] allows a model to dynamically weight positions...

[sections: Overview | Mechanism | Citations]

---

[talk: 2 entries, 1 open]
One open suggestion: cross-link to [[positional-encoding]] from Mechanism section (2026-04-07).
```

`wiki_search` response:
```
rfdiffusion (score: 0.94)
  Diffusion-based protein structure generation; de novo, partial diffusion, motif scaffolding
  Match at line 42: "...For binder design, **motif scaffolding** constrains a fixed structural motif..."

bindsweeper (score: 0.31)
  Multi-dimensional parameter sweep tool — no direct match
```

`wiki_status` response:
```
Vault: ~/wiki
Pages: 14 across 3 clusters | Total: 38,020 tokens
Last indexed: 2026-04-09T14:23:11
```

**Error states:** A consistent prefix that the agent can detect reliably without parsing:
```
[ERROR: not-found] Page "bahdanau-attention" does not exist. Did you mean: bahdanau-2015?
[ERROR: patch-conflict] Context mismatch at line 42. Re-read the page and retry.
```

**What needs to change:**
1. Add a rendering pipeline to the daemon response path — `render_response(response: dict, tool: str) -> str` — called in `_ok()` before wrapping in `TextContent`
2. Define rendered formats for all 17 tools
3. Keep JSON internally for all daemon ↔ daemon communication; only the MCP boundary uses rendered output
4. Update tool descriptions to describe the rendered format agents will receive
5. Update `docs/gallery.md` throughout — this changes every agent-view example
6. Deprecate L1 (compact JSON) once this lands — it becomes irrelevant

**Sequencing:** L1–L4 can ship independently and are worth doing. L5 is the full rethink. Don't let perfect be the enemy of good — L1 in particular should ship now regardless.

---

## Bulk Read Tools — Minimise Tool Round-Trips

**Origin:** The current single-page `wiki_read` forces large-context models into repeated decode→tool→prefill cycles. Every tool call is a full CoT cycle plus daemon latency. Prefill is 10-100x cheaper per token than decode — the optimal pattern for a frontier model with a large context window is to orient cheaply (manifest), then load everything relevant in one prefill pass, then reason across it all in a single decode pass. The current tool surface makes that impossible.

**The economics:**

Current implicit pattern:
```
manifest → wiki_read(top) → [decode + round-trip] → wiki_read(section) → [decode + round-trip]
         → wiki_read(top, page2) → [decode + round-trip] → wiki_read(section, page2) → ...
```

Better pattern for large-context models:
```
manifest → wiki_read_many([page1, page2, page3]) → [one prefill, 15k tokens] → reason → write
```

The same applies *within* a single page. Reading "overview" then "mechanism" on the same page is two decode cycles when it should be one prefill. `wiki_read` currently accepts a single viewport — a `sections` parameter accepting a list would collapse N intra-page reads into one call.

The manifest already gives the agent the routing information it needs. The missing piece is a bulk loading tool to act on it in one shot.

**The skill philosophy also needs updating.** The careful sip-at-a-time posture in the current skills was designed for 8-32k context windows. For large-context models, it actively penalises efficiency. Skills should acknowledge that the right behaviour is model/context-dependent: if context is abundant, load relevant content generously; tool round-trips are the bottleneck, not tokens.

---

### 1. `wiki_read_many` — Batch Page Load

**What:** Read multiple pages in a single tool call, each with its own viewport specification.

**Proposed interface:**
```python
wiki_read_many(pages=[
    {"name": "rfdiffusion", "viewport": "full"},
    {"name": "bindcraft", "viewport": "full"},
    {"name": "protein-mpnn", "viewport": "section", "section": "training"},
])
```

**Response:** Array of page results, each with the same structure as a single `wiki_read` response (content + inline issue/talk digest). Issues and talk digests are per-page.

**What needs to change:**
1. New tool definition in `src/llm_wiki/mcp/tools.py`
2. New daemon handler — fan out to existing page-read logic, collect results
3. Tool description should make the efficiency rationale explicit: "use this when you need multiple pages — one tool call instead of N"
4. Update skill files to recommend batch reads when loading a cluster or multiple related pages

---

### 2. `wiki_read` — Multi-Section Viewport

**What:** Extend `wiki_read` to accept multiple sections in one call, returning them concatenated with section headers preserved.

**Proposed interface:**
```python
wiki_read("rfdiffusion", viewport="sections", sections=["overview", "motif-scaffolding", "citations"])
```

**Behaviour:** Returns the named sections in order, same inline issue/talk digest as a normal read. If a named section doesn't exist, include a `missing_sections` field in the response rather than failing — lets the agent adapt without a retry round-trip.

**Backwards compatible:** Existing `viewport="top"`, `viewport="section"`, `viewport="full"`, `viewport="grep"` all unchanged. `viewport="sections"` (plural) is the new multi-section path.

**What needs to change:**
1. Extend `wiki_read` handler in `src/llm_wiki/mcp/tools.py` to accept `sections: list[str]` when `viewport="sections"`
2. Daemon page-read logic to concatenate named sections
3. Tool description update — surface the multi-section option explicitly

---

### 3. `wiki_read_cluster` — Load an Entire Cluster

**What:** Load all pages in a named cluster in one call. The manifest already organises pages into clusters; this makes bulk cluster loading a first-class operation.

**Proposed interface:**
```python
wiki_read_cluster("protein-design", viewport="full")
# or with per-page viewport:
wiki_read_cluster("protein-design", viewport="top")  # just overviews of all pages
```

**Response:** Same as `wiki_read_many` — array of page results.

**Note:** For very large clusters, the agent should check total token count from the manifest before calling this. The tool should include the token total in the response envelope so the agent can reason about what it loaded. This is information, not enforcement.

**What needs to change:**
1. New tool definition (can delegate to `wiki_read_many` internally once cluster members are resolved from manifest)
2. Daemon handler resolves cluster name to page list, fans out
3. Response includes `cluster_tokens: N` in the envelope

---

### 3. Skill Philosophy Update

**What:** Add an explicit note to `index.md` and `research.md` acknowledging that tool round-trips have real cost (decode cycle + latency) and that bulk loading is preferred for large-context models.

**Suggested framing:**
> "Each tool call is a round-trip — a decode cycle plus daemon latency. Prefill is cheap; decode is expensive. If you need multiple pages, `wiki_read_many` in one call is strictly better than N sequential `wiki_read` calls. Orient with the manifest, then load what you need in bulk."

**What needs to change:**
1. Add inference economics note to `index.md` universal principles
2. Update Mode 3 in `research.md` to recommend `wiki_read_many` once it exists
3. Add to gallery examples once tools are implemented

---

## Future Ideas

- **Vault → Managed migration tool**: Pre-packaged workflow that lets 4-8 local LLMs loose on an unstructured Obsidian vault over days/weeks to reorganize it into managed structure (raw sources separated, index built, cross-references added, provenance established). Automated "get it ship shape" pipeline.
