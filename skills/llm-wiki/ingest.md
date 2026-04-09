---
name: llm-wiki/ingest
description: Use when incorporating an external source (paper, PDF, document) into an llm-wiki vault. Three-act conversational protocol with hybrid manual+automated synthesis. Attended mode.
---

# LLM-Wiki Ingest — Attended Source Synthesis

Ingestion is inherently conversational and multi-turn. It touches multiple pages in one session.

## Act 1 — Read the Source First

Before touching any wiki tool, read the source:
- Abstract and intro at minimum
- Full document if short

Form a view: what are the key concepts, what claims does it make, what is genuinely novel vs. already-known territory. This calibrates all subsequent wiki interactions — you cannot search for the right things until you know what the source is about.

**COPY the source into `raw/` first.** Before any wiki tool call, copy the source verbatim to `raw/YYYY-MM-DD-slug.md` (flat — no subdirectories inside `raw/`). This is a copy, not a transcription — preserve the original text exactly. All `source_ref` values in subsequent wiki calls must point to this vault-internal path; `wiki_lint` will flag broken citations otherwise.

## Act 2 — Ask the User How to Handle It

State what you found, then offer the choice:

> "This [paper/document] covers [X, Y, Z]. [X] looks like it may already have wiki coverage; [Y and Z] appear to be new territory. A typical ingest creates 5–15 pages.
>
> **Conversational** — we orient in the wiki together, discuss what to create vs. update, I can write key pages manually and use `wiki_ingest` to catch connections and fill gaps
> **Automated** — I run `wiki_ingest --dry-run` to preview what would be created, you confirm, then I execute"

Wait for the user's response; if none comes, use the automated path.

If the dry-run or your Act 1 estimate suggests 10+ pages, flag this scope to the user before committing — large ingests benefit from conversational mode so important synthesis doesn't get buried in bulk creation.

## Act 3 — Execute

### Conversational Path

1. `wiki_manifest` + `wiki_search` for each key concept identified in Act 1
2. Discuss findings with user: what is already covered, what is new, what contradicts existing pages
3. Decide together: manual pages for important synthesis, `wiki_ingest` to catch connections and fill gaps — this hybrid is explicitly endorsed
   - **Page creation threshold:** create a page when a concept appears in 2+ sources OR is central to this source. Passing mentions → link to an existing page if it exists, or leave as prose; do not create stubs.
4. Write in one session — do not close mid-ingest
   - Link aggressively as you write: every salient noun, technical term, and named entity on its first mention. Writing habit, not a checklist.
5. **Cascade updates:** after writing primary pages, scan the same topic area for pages that should cross-reference the new content. Then check adjacent clusters: are there pages in neighbouring topic areas that gain meaningful links? This is part of the ingest, not optional cleanup.
6. `wiki_session_close`

### Automated Path

1. `wiki_ingest --dry-run` — show the user what concepts would be extracted and which pages would be created or updated
2. Confirm with user
3. `wiki_ingest` to execute
4. Report what was created and updated
5. `wiki_session_close`

## Key Synthesis Principle

Do not just extract claims into the wiki. For each concept: how does it connect to what is already there? Does it contradict, extend, or confirm existing pages?
- Contradictions → `wiki_talk_post`
- Extensions → page body with citation
- Confirmations → note in relevant claim's context
