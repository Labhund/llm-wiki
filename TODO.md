# TODO

## Research-Mode Ingest — Design + Implementation

Emerged from live ingest session (2026-04-09). Core insight: attended ingest should be a **research tool that compounds**, not a supervised version of background extraction. Three distinct modes (queue / brief / deep), a persistent plan file format, synthesis claim markers, and eventual claim resonance matching across future ingests.

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

## Rendered Document Responses (Future Direction)

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
