---
name: llm-wiki/write
description: Use when adding or updating knowledge in an llm-wiki vault. Covers sessions, citations, V4A patches, and talk pages for uncitable content. Attended mode.
---

# LLM-Wiki Write — Attended Knowledge Capture

## Hard Gate

Before writing anything, state out loud:
- Which page(s) you are writing to
- What you are adding
- What source supports it

No source → `wiki_talk_post`, not `wiki_create`. Stop here and use talk.

## Session Discipline

Sessions open implicitly on the first write call — no explicit open needed. Close is always explicit.

- One session per coherent work unit (a topic, a paper, a fix pass) — not one per page
- Watch for `session-cap-approaching` warnings (emitted at write 18; hard cap at 30) — wrap up or plan to continue in a new session
- Always close with `wiki_session_close` when done — do not rely on inactivity timeout

## Tool Selection

**`wiki_create`** — new pages
- Citations are required; the daemon rejects calls with empty citations
- The daemon rejects creates that look like near-matches to existing pages by default
- If you get a near-match rejection: either update the existing page (`wiki_update`) or set `force=true` after confirming the new page is genuinely distinct from the existing one

**`wiki_update`** — V4A patch format
- Re-read the target section first (`wiki_read` with the relevant section name)
- Patch against what is actually there, not what you remember
- Handle `patch-conflict` by re-reading and retrying — never rewrite the whole page to avoid a conflict

**`wiki_append`** — additive knowledge; safest option
- Heading-anchored; requires `section_heading` parameter
- Also requires citations

## Uncitable Content

Use `wiki_talk_post`. Talk pages accept: half-formed ideas, proposals, connections you cannot yet cite, contradictions waiting on resolution. This is a first-class path, not a consolation.

## Wikilinks Posture

Link aggressively as you write — every salient noun, technical term, and named entity on its first mention in a page. `[[concept]]` syntax; the daemon resolves targets. This is a writing habit, not a post-step checklist.

When creating multiple related pages: use a one-level topic subdirectory (`topic/page-name`) to cluster them. Flat naming for standalone concepts; subdirectory for concept families (e.g., `transformers/attention`, `transformers/positional-encoding`).

## Synthesis Pages

Pages that represent original analysis rather than extracted source claims use `type: synthesis` in frontmatter. Synthesis pages:
- Do not require external `[[raw/...]]` citations (the analysis session is the source)
- Are skipped by the adversary's verification pass
- Are targets for resonance matching — the system will compare incoming sources against them
- Use `wiki_talk_post` on the synthesis page when a related source arrives

Set `type: synthesis` at page creation. Do not set it on extracted pages — use it only when the content is genuinely the agent's synthesis, not a source summary.

## Before Writing

Check inline signals from `wiki_read`. If a page has open critical or moderate issues, address or acknowledge them before adding new content. Writing over a broken page makes it worse.

---

**Close your session.** When the write task is complete: `wiki_session_close`. Sessions open implicitly on first write; they do not close themselves unless the inactivity timer fires (5 min). In short fast sessions the timer may not fire — close explicitly.
