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

- **PDFs:** store the original as `raw/YYYY-MM-DD-slug.pdf` (immutable). Extract text to `raw/YYYY-MM-DD-slug.md` for agent use.
- **Markdown / text:** copy verbatim to `raw/YYYY-MM-DD-slug.md`. Body is immutable; frontmatter is metadata.
- **Flat** — no subdirectories inside `raw/`.

All `source_ref` values in wiki citations must point here. `wiki_lint` flags broken citations.

**PDF extraction quality varies.** Check extracted text before writing — mangled output (captions bleeding into body, garbled tables, watermarks repeating) degrades everything written from it. The vault config's `pdf_extractor` controls which tool is used: `pdftotext` (default, poor layout), `local-ocr` (vision model via llama.cpp, handles tables/figures), `marker`/`nougat` (high quality, GPU required). Flag bad extraction to the user before proceeding.

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

The daemon sets `reading_status: unread` on ingest. It stays unread — only attended engagement promotes a source.

---

## Mode 2: Brief

Read with the user's full context loaded. The output is a briefing, not pages.

1. Confirm source is in `raw/`
2. Read the source — abstract and intro at minimum, full document if short
3. `wiki_manifest` + `wiki_search` for key concepts — know what's already covered
4. `wiki_source_mark(source, "in_progress")` — or update `reading_status` in raw/ frontmatter directly if the tool is unavailable
5. Produce the briefing:

```
**New to your work:** [what this adds not already in your wiki or prior work — be specific]
**Already covered:** [concepts with existing pages — link them]
**Contradictions:** [specific claims conflicting with existing pages — name both sides]
**Worth reading yourself:** [sections needing your judgment, not just extraction]
**Scope if queued:** ~N pages
```

6. Wait. User decides:
   - "Queue it" → run Mode 1; `wiki_source_mark(source, "read")` if the brief is sufficient engagement
   - "Go deeper on X" → continue into Mode 3 for those claims
   - "I'll read it myself" → leave at `in_progress`, close session
   - "Nothing for now" → leave at `in_progress`, close session

7. `wiki_session_close`

The briefing is the value. Page creation is optional and user-directed.

---

## Mode 3: Deep

Claim-by-claim iterative analysis. The compounding is a byproduct; the research is the point.

### Setup

1. Confirm source is in `raw/`
2. `wiki_source_mark(source, "in_progress")`
3. Create the inbox plan file **before any wiki write:** `inbox/YYYY-MM-DD-slug-plan.md`

```markdown
---
source: raw/YYYY-MM-DD-slug.pdf
started: YYYY-MM-DD
status: in-progress
sessions: 1
---

# [Source Title] — Research Plan

## Claims / Ideas
- [ ] [Claim 1 — one-line scope]
- [ ] [Claim 2]

## Decisions

## Session Notes
### YYYY-MM-DD
[First session opening notes]
```

4. Present the claim list to the user. Get approval — merge, drop, reorder — before starting the loop.

### Per-Claim Loop

For each claim:

1. **Agent presents:** what the claim is, what's genuinely new vs already covered, any contradiction with existing wiki pages, what it would write and why
2. **Human reacts** — push back, add their reading, redirect
3. **Decide together:**
   - Write now → `wiki_create` / `wiki_update` / `wiki_append`; link aggressively
   - Defer → note reason in plan file, move on
   - Talk post only → `wiki_talk_post` on the relevant concept page; captures the analysis without committing to a main page
   - Skip → note in plan file
4. Tick the claim in the plan file, record the decision in Decisions

### Session Checkpoint

When `session-cap-approaching` fires or at a natural stopping point:

1. Write checkpoint to plan file: claims covered, decisions made, where to resume
2. Append to Session Notes
3. Commit plan file to git
4. `wiki_session_close`

### Resuming

Read `inbox/YYYY-MM-DD-slug-plan.md`, reconstruct task list from unchecked items, continue. The plan file is the full context — no prior session memory needed.

### Completion

1. `wiki_source_mark(source, "read")`
2. Mark plan file `status: completed`
3. **Cascade:** scan same topic area for pages that should cross-reference new content, then adjacent clusters. Part of the work, not optional cleanup.
4. `wiki_session_close`

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

---

## Reading Status Protocol

Call `wiki_source_mark` to track your engagement with a source. The daemon
commits the change to git with a `Source-Status:` trailer for audit. You
never edit frontmatter manually.

| Moment | Call |
|---|---|
| Brief mode — start reading | `wiki_source_mark(source_path, "in_progress", author)` |
| Brief mode — done, no deep session planned | `wiki_source_mark(source_path, "read", author)` |
| Deep mode — session start | `wiki_source_mark(source_path, "in_progress", author)` |
| Deep mode — plan file complete | `wiki_source_mark(source_path, "read", author)` |
| Autonomous ingest | Do not call `wiki_source_mark` — autonomous ingest sets `unread` only |

`source_path` is the path to either the binary source (`raw/foo.pdf`) or
its companion (`raw/foo.md`) — both are accepted by the daemon.
