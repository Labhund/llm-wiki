# Ingest Proposals Pipeline — Design Spec

**Date:** 2026-04-10
**Branch:** feature/ingest-improvements
**Status:** approved

## Problem Statement

The current single-pass ingest pipeline produces low-quality output on dense scientific papers:

- **Truncation:** `budget * 4` char ceiling means a 30-page paper is seen only partially
- **No wiki-awareness:** concept slugs are named without knowledge of existing pages — `boltz-2-model` created as duplicate of existing `boltz-2`
- **Wrong granularity:** sub-topics like `controllability-features` and `training-data-curation` promoted to pages instead of sections
- **No wikilinks:** generated content has only `[[raw/...]]` citations; no `[[slug]]` cross-links to other wiki concepts — the knowledge graph stays disconnected
- **No grounding enforcement:** generated claims may not be traceable to specific source passages
- **Direct writes:** bad ingest output goes straight into the wiki with no review gate

Additionally, three surgical bugs:

1. `find_broken_citations` silently misses non-`raw/`-prefixed source citations in frontmatter (e.g. `[[boltz2.pdf]]`)
2. `_write_via_service` drops `%% section %%` markers, causing `find_missing_markers` to flag the result
3. Sources ingested from outside the vault get a broken `source_ref` and no companion `.md`

---

## Design Overview

Replace direct-write ingest with a **proposal pipeline**: ingest generates structured proposals in `inbox/proposals/`, the existing auditor picks them up on its schedule and auto-merges clean updates or surfaces creates/failures as issues for interactive human review.

Two concurrent improvements to extraction quality:

- **Multi-chunk extraction** (approach B): overview pass for concept naming, then targeted passage collection across all chunks
- **Strict prompt engineering**: manifest-injected context, explicit page vs. section granularity rules, enforced `[[slug]]` wikilinks and `[[raw/...]]` citations with separate semantics

---

## Section 1 — Proposal File Format

### Location

```
inbox/proposals/YYYY-MM-DD-<source-slug>-<target-slug>.md
```

One file per (source, target page) pair. Example:

```
inbox/proposals/
  2026-04-10-boltz2-boltz-2.md
  2026-04-10-boltz2-binding-affinity-prediction.md
```

### Structure

```markdown
---
type: proposal
status: pending
source: raw/boltz2.pdf
target_page: boltz-2
action: update          # create | update
proposed_by: ingest
created: 2026-04-10T12:00:00
extraction_method: pdf  # pdf | image_ocr | markdown
quality_warning: null   # set if extraction heuristic flagged mangled output
---

%% section: binding-affinity-prediction %%
## Binding Affinity Prediction

[[Boltz-2]] achieves state-of-the-art performance on [[binding-affinity-prediction]]
tasks across the PDBbind benchmark [[raw/boltz2.pdf]].

```evidence
[
  {
    "id": "p1",
    "text": "Boltz-2 achieves state-of-the-art performance on binding affinity prediction on the PDBbind benchmark...",
    "claim": "Boltz-2 achieves state-of-the-art performance on the PDBbind benchmark",
    "score": 0.91,
    "method": "ngram",
    "verifiable": true,
    "ocr_sourced": false
  }
]
```
```

The body follows the existing `%% section: name %%` structure so the auditor can merge sections directly into the target page without reformatting. The evidence block uses a fenced code block with language tag `evidence`; the auditor parses it as JSON using a regex identical to `_parse_json_response` in `prompts.py`. The evidence block is stripped before any content is merged into the wiki page.

### Proposal Status Lifecycle

```
pending → merged    (auditor auto-merge: update + clean verification)
pending → issue     (auditor flags: create action, or verification failure)
pending → rejected  (human rejects via interactive session)
```

---

## Section 2 — Multi-Chunk Extraction Pipeline

### Overview

```
llm-wiki ingest raw/boltz2.pdf
    │
    ├─[pre-flight] source outside vault? → copy to raw/ first
    │
    ▼
[1] Extract + chunk
    Configured extractor (pdftotext / marker / local-ocr / nougat)
    → text chunked at config.ingest.chunk_tokens (default: 6000)
    with config.ingest.chunk_overlap overlap (default: 0.15)
    │
    ▼
[2] Overview pass  (chunk 0 only + vault manifest injected)
    Single LLM call with:
      - Chunk 0 text (abstract, intro — where primary concepts are named)
      - Existing wiki manifest (slug → title, one line each)
    Output: ConceptPlan list with:
      - slug (MUST match existing slug if concept already in manifest)
      - title
      - action: create | update
      - section_names: list of sub-topics to generate as sections
    │
    ▼
[3] Passage collection  (all chunks, per concept)
    For each chunk beyond chunk 0:
      LLM call: "Which of these concepts appear? Extract verbatim passages."
    Passages per concept: deduplicated, capped at max_passages_per_concept
    │
    ▼
[4] Content synthesis  (per concept)
    LLM call with: passages + vault manifest + batch concept plan + source ref
    Strict prompt (see Section 5)
    Output: proposed wiki sections with [[slug]] links and [[raw/...]] citations
    │
    ▼
[5] Grounding check  (per passage)
    Bigram F1 against extracted source text
    Scores stored in evidence block per proposal
    image_ocr: threshold relaxed, passages marked ocr_sourced: true
    Figures/equations (heuristic: "Figure X", formula chars): verifiable: false
    │
    ▼
[6] Proposal write
    One file per (source, target_page) in inbox/proposals/
    Companion .md created/updated in raw/ if not present
    │
    ▼
[7] Auditor pass  (next scheduled run, ~15 min)
    action=update + all passage scores ≥ grounding_auto_merge → merge to wiki
    action=create → issue type=proposal, requires interactive review
    any score < grounding_flag → issue type=proposal-verification-failed
    broken wikilinks in proposal → flagged, not auto-merged
```

### ConceptPlan granularity rules (enforced in overview prompt)

The overview prompt explicitly distinguishes:

- **Page-worthy**: named models, datasets, methods, tools that exist independently and may be referenced by other papers. Should match existing manifest slugs where applicable.
- **Section-level**: paper-specific methodology details, experimental configurations, ablation results, training procedures. These become `section_names` on the primary concept, not separate pages.

Examples given in prompt: "training data curation", "ablation study", "experimental setup" → sections. "ProteinMPNN", "PDBbind", "SE(3)-equivariant networks" → pages (if novel enough).

---

## Section 3 — Auditor Extension

The existing auditor gains a `find_pending_proposals` check:

```python
def find_pending_proposals(vault_root: Path) -> CheckResult:
    """Review pending proposals: auto-merge clean updates, issue everything else."""
```

**Auto-merge path** (action=update, all scores ≥ grounding_auto_merge, no broken wikilinks):
1. Read target page
2. Skip sections already present (existing deduplication logic)
3. Append new sections with `%% section %%` markers
4. Update target page's frontmatter `sources` list
5. Mark proposal `status: merged`
6. Daemon rescans vault

**Issue path** (action=create, or any score < grounding_flag, or broken wikilinks):
- Creates issue type `proposal` or `proposal-verification-failed`
- Human + LLM reviews in interactive MCP session
- MCP tools: `wiki_proposal_approve`, `wiki_proposal_reject`, `wiki_proposal_list`

**Periodic background review** — no new agents needed. Existing adversary (hourly) catches inconsistencies in merged content. Existing resonance matching surfaces cross-page synthesis opportunities. The proposal pipeline is the ingest gate; the adversary is the ongoing quality monitor.

---

## Section 4 — Wikilink Prompt Engineering

Content synthesis prompt (step 4) receives two injected lists and strict rules:

```
## Existing wiki pages — use [[slug]] when referencing these
- boltz-2                       "Boltz-2"
- protein-mpnn                  "ProteinMPNN"
- alpha-fold-2-initial-guess    "AlphaFold-2 Initial Guess"
- binding-affinity-prediction   "Binding Affinity Prediction"
... (full manifest, one line each)

## Concepts being created in this ingest batch — also use [[slug]]
- boltz-2-training-dataset      "Boltz-2 Training Dataset"

## Link rules (non-negotiable)
1. Reference to a concept in either list → [[slug]] inline, exact slug, no invention
2. Factual claim → [[raw/boltz2.pdf]] at end of sentence (every claim, no exceptions)
3. General term not in either list → plain text, no brackets
4. Never invent slugs not in the lists above
5. [[raw/...]] and [[slug]] serve different purposes — do not conflate them
```

**Example enforced output:**

```markdown
[[Boltz-2]] extends [[alpha-fold-2-initial-guess]] with a diffusion-based
architecture that predicts dynamic ensembles rather than static complexes [[raw/boltz2.pdf]].
Training on [[boltz-2-training-dataset]] enables generalisation across
protein-ligand and protein-RNA interfaces [[raw/boltz2.pdf]].
```

**Auditor wikilink validation:** before merging any proposal, verify all `[[slug]]` references resolve to either an existing wiki page or another proposal in the same batch. Unresolved → `broken-link` pre-merge issue, proposal not auto-merged.

---

## Section 5 — Bug Fixes

### Bug 1: `find_broken_citations` misses bare filename citations

**File:** `src/llm_wiki/audit/checks.py`

Current code only checks `[[raw/...]]` prefixed links. Fix: when parsing `source`/`sources` frontmatter, also flag any `[[something.pdf]]` (or other binary extension) that lacks a `raw/` prefix. Issue severity: `moderate`. Message directs user to move file to `raw/` and re-ingest.

### Bug 2: `_write_via_service` drops `%% section %%` markers

**File:** `src/llm_wiki/ingest/agent.py`

`_sections_to_body()` emits `## Heading\n\ncontent`. Fix: emit `%% section: <slug> %%\n## Heading\n\ncontent` where slug is derived from the heading text (same `_slugify` function used in `page.py`).

### Bug 3: Source outside vault → auto-copy to `raw/`

**File:** `src/llm_wiki/cli/main.py` (ingest command)

Detect `source_path` not under `vault_root`. If `config.ingest.auto_copy_to_raw` is true (default), copy to `vault_root/raw/<filename>` and proceed with the copied path. If destination already exists, skip copy and log notice. If `auto_copy_to_raw` is false, hard error with message: `"Source must be inside vault raw/ directory. Move it first or set auto_copy_to_raw: true"`.

### Bug 4 (new): Token estimates missing from `%% section %%` markers

**File:** `src/llm_wiki/daemon/writer.py` (or session close hook)

After a write session closes, the daemon re-parses each touched page and rewrites section markers to include `, tokens: N`. Token counting is pure (no LLM call) — uses existing `count_tokens()`. Pages written before this feature get patched the first time they're touched by any subsequent write.

---

## Section 6 — Configuration

New fields added to `IngestConfig` in `src/llm_wiki/config.py`:

```yaml
ingest:
  # Existing fields (unchanged)
  pdf_extractor: pdftotext
  local_ocr_endpoint: http://localhost:8006/v1
  local_ocr_model: qianfan-ocr

  # New fields
  chunk_tokens: 6000              # tokens per extraction chunk
  chunk_overlap: 0.15             # fractional overlap between chunks
  max_passages_per_concept: 6     # ceiling on passages fed to content synthesis
  grounding_auto_merge: 0.75      # passage score ≥ this → auto-merge update proposals
  grounding_flag: 0.50            # passage score < this → create issue
  auto_copy_to_raw: true          # copy source to raw/ if ingested from outside vault
```

---

## Out of Scope

- MCP streaming for proposals (MCP callers are programmatic)
- Reconnectable ingest jobs (covered by existing streaming design)
- Image-only source grounding beyond OCR quality (acknowledged limitation, flagged in proposals)
- Retroactive re-ingest of existing pages (adversary handles ongoing quality)
- Interactive approval UI beyond existing issue queue + MCP tools
