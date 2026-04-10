---
name: llm-wiki/maintain
description: Use when running a hygiene pass on an llm-wiki vault — lint, issue triage, talk page review. Attended mode. For scheduled/cron use, see llm-wiki/autonomous/maintain.
---

# LLM-Wiki Maintain — Attended Hygiene

## Hard Gate

State scope before starting:
- Full vault pass, or a specific cluster/area?

## Protocol

**Step 1 — `wiki_lint`**
Start here always. Returns a vault-wide attention map with issues by severity. This sets the agenda.

**Step 2 — Triage by severity**
- Critical first: broken citations, failed claim verifications
- Moderate next: broken wikilinks
- Minor last: orphans, missing markers

Do not fix minor issues while critical ones are open.

**Step 3 — For each issue: `wiki_issues_get`**
Read the full issue before deciding anything. Some issues have obvious fixes; some need a write; some need a talk post because the right resolution is not clear.

**Step 4 — Fix or escalate**
- Fixable in place → write tools + `wiki_issues_resolve`
- Unclear resolution → `wiki_talk_post` on the relevant page with the issue ID; leave the issue open
- Requires human judgment → surface to the user explicitly before touching anything

**Step 5 — Check talk pages**
- `wiki_talk_list` for open discussions
- `wiki_talk_read` for pages with unresolved critical/moderate entries
- Contribute via `wiki_talk_post` where you have something relevant to add

**Step 6 — Close**
`wiki_session_close` if any writes were made.

## Key Principle

Maintenance is not a rewrite pass. Fixes should be surgical. If you find yourself wanting to rewrite body content during a lint pass, flag it as a separate task and move on.

## Resonance Review

`wiki_lint` flags open `resonance` talk entries older than the configured threshold. Each entry means the system found a possible connection between a new source and an existing claim.

For each resonance entry:
1. Read the existing page (`wiki_read`)
2. Read the new source reference in the entry body
3. Decide: corroborate (add cross-reference), extend (append with citation), contradict (post adversary talk entry), or dismiss (resolve the resonance entry as a false match)
4. Resolve the entry: `wiki_talk_post` on the same page with `resolves: [N]` referencing the resonance entry index

A resonance entry is not a finding that demands action — it is a prompt for a human judgement call. Dismissing false matches is a valid and useful outcome.
