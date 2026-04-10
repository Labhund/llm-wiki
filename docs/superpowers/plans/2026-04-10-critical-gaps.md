# Critical Gaps — Core Agent Loop

> Discovered during session 2026-04-10. These are not cosmetic issues — they block the
> core agent loop from functioning. Until fixed, the adversary and talk pages are dead
> weight, and the index is an unreliable lie.

---

## Gap 1: Adversary chain is broken — zero verifiable claims exist

**Status:** No agent work is happening. The adversary runs, finds nothing, and exits.

**Root cause — two flavours:**

### 1a. Boltz-2 pages: wrong citation format in body
Five pages (`boltz-2-model`, `binding-affinity-prediction`, `controllability-features`,
`training-data-curation`, `virtual-screening-workflow`) have inline body citations using
`[[boltz2.pdf]]` instead of `[[raw/boltz2.pdf]]`.

The adversary's claim extractor (`src/llm_wiki/adversary/claim_extractor.py:21`) only
matches `\[\[(raw/[^\]|]+)\]\]` — bare filenames are invisible to it.

The auditor's citation check (`src/llm_wiki/audit/checks.py:149`) flags these as
`bare-filename-citation` issues. But no agent repairs them.

**Fix:** Re-ingest `raw/boltz2.pdf` using the current ingest pipeline, which should
produce body sentences ending with `[[raw/boltz2.pdf]]`. The five stub pages should be
replaced by the real ingest output. Before doing this, verify that the ingest pipeline
actually writes `[[raw/...]]` inline citations (see Gap 3).

### 1b. ProteinDJ pages: no inline citations at all
Eight pages (`protein-dj`, `protein-mpnn`, `rfdiffusion`, `bindcraft`, `fampnn`,
`alpha-fold-2-initial-guess`, `boltz-2`, `bindsweeper`) have rich body content but zero
inline `[[raw/...]]` citations. Claims are unverifiable because there are no citations.

Additionally, their frontmatter uses `sources: [../../raw/2026-04-09-silke-proteindj-2026.md]`
— a filesystem-relative path, not a vault-root wikilink. The citation checker doesn't
recognise this format, so it doesn't flag it.

**Fix:** Re-ingest `raw/2026-04-09-silke-proteindj-2026.md` once the pipeline is verified
to produce inline citations. The current pages were written by an earlier ingest version.

---

## Gap 2: Index is not maintained by code — it drifts silently

**Status:** `wiki/index.md` was hand-written. No agent reads, writes, or checks it.

**Current state of the index file:**
- Lists 8 pages; 14 pages exist in `wiki/` (6 missing: the 5 boltz2 stubs + `boltz-2.md`)
- Uses broken path-style links: `[[wiki/rfdiffusion.md]]` instead of `[[rfdiffusion]]`
  (8 broken wikilinks in the lint output are entirely from this file)
- Clusters and ordering are arbitrary; will rot further with every new ingest

**Decided approach: librarian owns the index.**

The librarian already has everything it needs: the full page manifest, summaries,
cluster assignments, authority scores. Index maintenance fits naturally alongside its
existing refresh pass.

**What the librarian should do:**
1. After each refresh pass, enumerate all pages in the vault manifest
2. Group them by cluster (pages at root level go under a default section)
3. Regenerate the index body: `- [[slug]] — one-line summary` per page, alphabetical
   within each section
4. Write the index atomically via the write service (same session, no orphan writes)
5. The existing `find_orphans` check exempts `index` — this is correct and stays

**What the auditor should check (new check: `index-out-of-sync`):**
- Every page slug appears in the index
- Every link in the index resolves to a real page (slug format, not path format)
- Missing entries → `minor` issue; broken link format → `moderate` issue

**Immediate manual fix needed:** Replace the 8 `[[wiki/X.md]]` links with `[[X]]` and
add the 6 missing pages. Do this now so lint reflects real issues, not index rot.

---

## Gap 3: Ingest pipeline — inline citation output not verified

**Status:** Unknown whether the current ingest pipeline writes `[[raw/...]]` inline
citations in body text. The boltz2 stub pages suggest it does not (body has
`[[boltz2.pdf]]` not `[[raw/boltz2.pdf]]`). The protein-dj pages suggest it doesn't
write inline citations at all.

This is the most important thing to verify before any re-ingest. If the pipeline doesn't
write inline citations, re-ingesting will just reproduce the same broken state.

**What to check:** `src/llm_wiki/ingest/prompts.py` — does the prompt instruct the LLM
to write sentences ending with `[[raw/<source-path>]]`? Does `page_writer.py` enforce
this in the output?

**What a valid verifiable claim looks like:**
```
ProteinMPNN achieves log-likelihood X on benchmark Y. [[raw/2026-04-09-silke-proteindj-2026.md]]
```

If the prompt doesn't produce this, the adversary will always be idle regardless of
how many sources are ingested.

---

## Gap 4: Frontmatter citation format is inconsistent

**Status:** Two citation formats exist in the vault; only one is understood by the system.

| Format | Example | Understood by |
|---|---|---|
| Vault-root wikilink | `source: '[[raw/boltz2.pdf]]'` | auditor (citation check), adversary |
| Filesystem-relative | `sources: [../../raw/foo.md]` | nothing |

The filesystem-relative format is invisible to both the citation checker and the
adversary. Pages using it will never generate lint issues and never have their claims
verified.

**Fix:** Standardise on `source: '[[raw/filename]]'` (singular or list) in all page
frontmatter. Enforce in the ingest prompt. Add a lint check for frontmatter entries
that look like filesystem paths rather than wikilinks.

---

## Downstream effects if these gaps persist

| What doesn't work | Because |
|---|---|
| Adversary verifies claims | Gap 1 — no `[[raw/...]]` citations in body |
| Talk pages are created | Gap 1 — adversary never posts `@adversary` entries |
| Talk summary runs | No talk pages exist |
| Lint reflects real issues | Gap 2 — 8 of 19 issues are index formatting rot |
| Re-ingest fixes Gap 1 | Gap 3 — pipeline may reproduce the problem |
| Citation checks catch all gaps | Gap 4 — filesystem paths are invisible |

---

## Gap 5: Ingest-created pages have incomplete frontmatter

**Status:** Every page created by `page_writer._create_page` is born with only 3 fields.
Every page created by the proposal merge "create" path is born with **zero** frontmatter.

**What `_create_page` writes:**
```yaml
title: ...
source: '[[raw/...]]'
created_by: ingest
```

**What is required:**
```yaml
title: ...
created: 2026-04-10       # deterministic — file mtime / ingest date
updated: 2026-04-10       # deterministic — same
type: concept             # deterministic default for ingest-created pages
status: stub              # deterministic default; librarian upgrades to "synthesis"
ingested: 2026-04-10      # deterministic — needed by auditor unread-source check
cluster: structural-bio   # already in ConceptPlan, just not passed through to writer
summary: "..."            # LLM at ingest time (synthesis prompt knows concept); librarian upgrades from usage
tags: []                  # LLM — librarian fills this on first refinement pass
source: '[[raw/...]]'
created_by: ingest
```

**`execute_proposal_merges` "create" path** (`audit/checks.py:733`) is worse: writes
`body + "\n"` with no frontmatter at all. Any auto-merged new page has zero metadata.
This page then has no `source:` field, so citation checks miss it, and the adversary
can't verify anything on it.

**Fix locations:**
- `src/llm_wiki/ingest/page_writer.py:_create_page` — add all deterministic fields
- `src/llm_wiki/audit/checks.py:execute_proposal_merges` — write frontmatter on create;
  the proposal file already carries `source`, `target_page`, `action`, `target_cluster`,
  and author — everything needed
- Pass `concept.cluster` and ingest date through `IngestAgent → write_page`

**What the auditor should check (new: `find_missing_frontmatter`):**
Per PHILOSOPHY Principle 13 — "missing frontmatter fields" is explicitly named as a
pure-Python auditor check. Scan every wiki page for:
- Missing `created` / `updated` → `minor` issue
- Missing `type` → `minor` issue
- Missing `status` → `minor` issue
- Missing `source` on a page that has `created_by: ingest` → `moderate` issue

**`summary` field — the index-summary lifecycle:**

`summary` is both a frontmatter field and the source of index entries. The lifecycle:

1. **Ingest writes `summary:`** — the synthesis prompt already understands the concept
   well enough to write a one-liner. This gives index entries immediately without
   waiting for traversal data.
2. **Librarian upgrades `summary_override`** in `ManifestOverrides` (already
   implemented) once traversal data accumulates. The librarian prompt says
   *"prioritizing how it has been used over the page's stated topic"* — this is the
   usage-aware refinement.
3. **Librarian index regeneration reads the best available:** `summary_override` (usage-
   refined) → `page.frontmatter['summary']` (ingest-written) → `page.title` (bare
   fallback). Same data also improves `wiki_search` results with no extra work.

This means `summary` in frontmatter is not just metadata — it is the index entry until
the librarian upgrades it. It must be written at ingest time.

**What the librarian can repair (without LLM):**
Backfilling structural metadata is not originating body content (Principle 3). The
librarian can write these deterministically:
- `created` / `updated` from `git log --follow` or file mtime
- `type: concept` if absent and page has concept structure
- `status: stub` if absent and `created_by: ingest`
- `ingested:` from file mtime

`tags` and `summary` require LLM — the librarian already handles both in its
refinement pass. For pages that lack `summary` at write time (pre-fix pages),
the librarian's existing refinement pass backfills it.

**New auditor check: `find_uncited_sourced_pages`:**
Pages that have a `source:` frontmatter field (or `created_by: ingest`) but zero
`[[raw/...]]` inline citations in the body. These pages are unverifiable by the
adversary. Pure regex, no LLM, Principle 13 compliant. File as `moderate` issue.

**On multi-turn ingest / prompt engineering:**
Not the fix for inline citations. `_CONTENT_SYNTHESIS_SYSTEM` already mandates
`[[source_ref]]` on every claim. The problem is upstream: wrong `source_ref` value
(bare filename vs `raw/filename`) and pre-current-pipeline pages. Fix the `source_ref`
threading and re-ingest. Multi-turn could improve page quality but doesn't address the
structural citation gap.

---

## Downstream effects if these gaps persist

| What doesn't work | Because |
|---|---|
| Adversary verifies claims | Gap 1 — no `[[raw/...]]` citations in body |
| Talk pages are created | Gap 1 — adversary never posts `@adversary` entries |
| Talk summary runs | No talk pages exist |
| Lint reflects real issues | Gap 2 — 8 of 19 issues are index formatting rot |
| Re-ingest fixes Gap 1 | Gap 3 — pipeline may reproduce the problem |
| Citation checks catch all gaps | Gap 4 — filesystem paths are invisible |
| Auditor unread-source check works | Gap 5 — `ingested:` field missing |
| Auto-merged proposals have metadata | Gap 5 — proposal merge writes zero frontmatter |

---

## Immediate actions (before next ingest)

- [ ] Fix `wiki/index.md`: replace `[[wiki/X.md]]` → `[[X]]`, add 6 missing pages
- [ ] Fix `raw/boltz2.md` companion: add `reading_status` field
- [ ] Verify ingest prompt writes `[[raw/...]]` inline citations (Gap 3)
- [ ] Fix boltz2 page frontmatter: `[[boltz2.pdf]]` → `[[raw/boltz2.pdf]]` OR re-ingest
- [ ] Fix `_create_page` — write complete frontmatter including `summary` (Gap 5)
- [ ] Add `summary` to ingest synthesis prompt output (Gap 5)
- [ ] Fix `execute_proposal_merges` "create" path — write frontmatter (Gap 5)
- [ ] Add `find_missing_frontmatter` auditor check (Gap 5, Principle 13)
- [ ] Add `find_uncited_sourced_pages` auditor check (Gap 1 + Gap 5)
- [ ] Implement librarian index maintenance using summary_override → frontmatter summary → title (Gap 2)
- [ ] Add `index-out-of-sync` auditor check (Gap 2)
- [ ] Implement librarian deterministic frontmatter backfill for pre-fix pages (Gap 5)
- [ ] Standardise frontmatter citation format across all pages (Gap 4)
