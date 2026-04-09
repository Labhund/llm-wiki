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

## Future Ideas

- **Vault → Managed migration tool**: Pre-packaged workflow that lets 4-8 local LLMs loose on an unstructured Obsidian vault over days/weeks to reorganize it into managed structure (raw sources separated, index built, cross-references added, provenance established). Automated "get it ship shape" pipeline.
