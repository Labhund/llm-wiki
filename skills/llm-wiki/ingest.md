---
name: llm-wiki/ingest
description: Use when incorporating an external source (paper, PDF, document) into
  an llm-wiki vault. Three modes — queue (background extraction), brief (briefing
  with your context), deep (claim-by-claim research). Attended mode.
---

# LLM-Wiki Ingest — Attended Source Intake

The attended agent's unique value in ingest is what it knows about you: your context, your memory, your prior work, your entire wiki. These three modes let you decide how much of that to use.

## Before Any Mode — Copy the Source

Copy the source into `raw/` before touching any wiki tool:

- **PDFs:** store the original as `raw/YYYY-MM-DD-slug.pdf` (immutable). The daemon
  creates `raw/YYYY-MM-DD-slug.md` alongside it automatically on `wiki_ingest`
  with `reading_status: unread` and extracted text.
- **Markdown / text:** copy verbatim to `raw/YYYY-MM-DD-slug.md`. Body is immutable;
  frontmatter is metadata.
- **Flat** — no subdirectories inside `raw/`.

All `source_ref` values in wiki citations must point here. `wiki_lint` flags broken citations.

**PDF extraction quality varies.** Check extracted text before writing — mangled output
(captions bleeding into body, garbled tables, watermarks repeating) degrades everything
written from it. The vault config's `pdf_extractor` controls which tool is used:
`pdftotext` (default, poor layout), `local-ocr` (vision model via llama.cpp, handles
tables/figures), `marker`/`nougat` (high quality, GPU required). Flag bad extraction
to the user before proceeding.

## Reading Status Protocol

`reading_status` in `raw/` frontmatter tracks whether the researcher has engaged
with a source. The daemon sets it; you update it with `wiki_source_mark`. Never
edit `raw/` frontmatter manually.

| Moment | Call |
|---|---|
| Brief mode — start reading | `wiki_source_mark(source_path, "in_progress", author)` |
| Brief mode — done, no deep session planned | `wiki_source_mark(source_path, "read", author)` |
| Deep mode — session start | `wiki_source_mark(source_path, "in_progress", author)` |
| Deep mode — plan file complete | `wiki_source_mark(source_path, "read", author)` |
| Queue mode (autonomous ingest) | Do not call — daemon sets `unread` only |

`source_path` is the path to either the binary (`raw/foo.pdf`) or its companion
(`raw/foo.md`) — both accepted.

## Choose a Mode

> "I have [source]. How do you want to handle it?
>
> **Queue** — background extraction, I'll report what was created
> **Brief** — I read it with your full context and wiki loaded, tell you what matters, you decide what to do next
> **Deep** — claim-by-claim analysis together; builds a persistent plan we can resume across sessions"

If no response: default to **Brief**. One conversation turn, always produces something useful, even if the user queues everything afterward.

---

## Mode 1: Queue

Background extraction. No analysis.

1. Confirm source is in `raw/`
2. `wiki_ingest` — daemon handles concept extraction and page creation
3. Report: pages created, pages updated, errors
4. `wiki_session_close`

The daemon sets `reading_status: unread` on ingest. Only attended engagement promotes a source.

---

## Mode 2: Brief

Read with the user's full context loaded. The output is a briefing, not pages.

1. Confirm source is in `raw/`
2. Read the source — abstract and intro at minimum, full document if short
3. `wiki_manifest` + `wiki_search` for key concepts — know what's already covered
4. `wiki_source_mark(source_path, "in_progress", author)`
5. Produce the briefing:

```
**New to your work:** [what this adds not already in your wiki or prior work — be specific]
**Already covered:** [concepts with existing pages — link them]
**Contradictions:** [specific claims conflicting with existing pages — name both sides]
**Worth reading yourself:** [sections needing your judgment, not just extraction]
**Scope if queued:** ~N pages
```

6. Wait. User decides:
   - "Queue it" → run Mode 1; `wiki_source_mark(source_path, "read", author)` if the brief is sufficient engagement
   - "Go deeper on X" → continue into Mode 3 for those claims
   - "I'll read it myself" → leave at `in_progress`, close session
   - "Nothing for now" → leave at `in_progress`, close session

7. `wiki_session_close`

The briefing is the value. Page creation is optional and user-directed.

---

## Mode 3: Deep

Claim-by-claim iterative analysis. The compounding is a byproduct; the research is the point.

**Persistent cursor:** Mode 3 creates a plan file in `inbox/` at the vault root. This file is the full context for the ingest — claim list, decisions, session notes. It persists across sessions and is committed to git at each checkpoint. `wiki_lint` surfaces any `inbox/` plan with `status: in-progress` so active ingests are never silently forgotten.

### Setup

1. Confirm source is in `raw/`
2. `wiki_source_mark(source_path, "in_progress", author)`
3. Read the source fully — form a claim list before creating the plan
4. Create the inbox plan file **before any wiki write:**

```
wiki_inbox_create(
  source_path="raw/YYYY-MM-DD-slug.pdf",
  title="[Source Title]",
  claims=["Claim 1 — one-line scope", "Claim 2", ...],
  author=your_identifier
)
```

   Save the returned `plan_path` — you will need it for checkpoints and resuming.

5. Present the claim list to the user. Get approval — merge, drop, reorder — before starting the loop.

### Per-Claim Loop

For each claim:

1. **Agent presents:** what the claim is, what's genuinely new vs already covered, any contradiction with existing wiki pages, what it would write and why
2. **Human reacts** — push back, add their reading, redirect
3. **Decide together:**
   - Write now → `wiki_create` / `wiki_update` / `wiki_append`; link aggressively
   - Defer → note reason, move on
   - Talk post only → `wiki_talk_post` on the relevant concept page
   - Skip → note in plan file
4. Tick the claim in the plan file (tracked locally — written at checkpoint, not after every claim)

### Session Checkpoint

When `session-cap-approaching` fires or at a natural stopping point:

1. Read the current plan file: `wiki_inbox_get(plan_path)`
2. Update the content: tick completed claims, add decisions, append session notes section
3. Commit: `wiki_inbox_write(plan_path, updated_content, author)`
4. `wiki_session_close`

The plan file is the full context. No prior session memory needed to resume.

### Resuming

1. `wiki_inbox_list` — find the active plan if you don't have the path
2. `wiki_inbox_get(plan_path)` — reconstruct task list from unchecked `- [ ]` items
3. `wiki_source_mark(source_path, "in_progress", author)` — re-assert status
4. Continue the per-claim loop from the first unchecked item

### Completion

1. `wiki_source_mark(source_path, "read", author)`
2. Read plan file, mark `status: completed`, increment `sessions` count
3. `wiki_inbox_write(plan_path, updated_content, author)` — final commit
4. **Cascade:** scan same topic area for pages that should cross-reference new content, then adjacent clusters. Part of the work, not optional cleanup.
5. `wiki_session_close`

---

## Key Synthesis Principle

For any mode that writes pages:

Situate claims, don't extract them. For each concept, how does it connect to what is already there?
- Contradictions → `wiki_talk_post` on the relevant page
- Extensions → page body with citation
- Confirmations → note in relevant claim's context

**Page threshold:** create a page when a concept is central to this source OR appears in 2+ sources. Passing mentions → link to an existing page; do not create stubs.

**Wikilinks:** every salient noun, technical term, and named entity on first mention. Writing habit, not a checklist.

**Scope:** if 10+ pages estimated, flag before committing — large ingests benefit from Deep mode so synthesis doesn't get buried in bulk creation.

