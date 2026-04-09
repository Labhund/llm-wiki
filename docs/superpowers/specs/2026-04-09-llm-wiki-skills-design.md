# LLM-Wiki Agent Skills — Design Spec

**Date:** 2026-04-09
**Status:** Draft — pending implementation plan
**Author:** Markus Williams

---

## Overview

llm-wiki is feature-complete and ships with an MCP server. The next step is a set of agent skills that give any MCP-connected agent (Claude Code, Hermes, OpenClaw, or autonomous swarm agents) a principled, well-shaped interface to the wiki — not just the tool surface, but the *how to think about using it* layer.

These skills live in `skills/llm-wiki/` inside the llm-wiki repo and ship with the package. They are the canonical "how to use llm-wiki well" documentation expressed as behavioural protocols rather than reference docs.

---

## Goals

- Prime agents with best practices, not just tool descriptions
- Cover all major usage patterns: research, writing, ingestion, maintenance
- Support both attended (user present) and autonomous (cron, swarm) operation without any setup beyond pointing the agent at the right subskill
- Be agent-framework agnostic — these skills work for Claude Code, Hermes, OpenClaw, or any agent that can load a skill file

---

## Non-Goals

- Replacing the MCP tool descriptions — these skills layer on top of them
- Covering every edge case — the skills should encode common patterns, not be exhaustive
- Any implementation logic — skills are behavioural primers, not code

---

## Skill Structure

```
skills/llm-wiki/
  index.md          # orientation, universal principles, research modes, routing
  research.md       # attended: intent gate, three-mode choice, traversal discipline
  write.md          # attended: sessions, citations, V4A patches, talk for uncitable
  ingest.md         # attended: three-act conversation, hybrid manual+ingest
  maintain.md       # attended: lint → triage → fix/escalate → close
  autonomous/
    index.md        # conservative posture, exit report discipline, session hygiene
    research.md     # wiki_query only, structured findings
    write.md        # sourced writes only, talk-post uncertain, mandatory close
    ingest.md       # read source → wiki_ingest → structured report
    maintain.md     # lint → conservative fix → talk-post ambiguous → cap → report → close
```

The `autonomous/` subskills are full independent documents, not thin wrappers — the scope and decision loop are genuinely different when there is no user to check in with.

---

## Universal Principles (index.md)

These apply across every subskill, attended and autonomous alike. The index states them once; subskills inherit them.

### Determine operating mode first

Before anything else, determine: am I **attended** (user present, can ask questions, can surface ambiguity) or **autonomous** (cron job, swarm agent, no user in the loop)?

This gates everything that follows. If autonomous, load `llm-wiki/autonomous/<subskill>` instead.

### Viewport-first

Never request full page content without first trying `top` (first section + TOC) or a named section. The manifest hierarchy is the map; the token budget is the constraint. `full` is a last resort, not a default.

### Traversal, not RAG

One search → done is the wrong pattern. Enter via manifest or search, follow wikilinks with purpose, build understanding across pages. The wiki is a compiled knowledge graph, not a retrieval index.

### Talk pages absorb everything uncitable

If you cannot point to a source, use `wiki_talk_post`, not `wiki_create`. Main pages require citations; talk pages have no such requirement. This is not a workaround — it is the intended path for ideas, proposals, and contradictions.

### Sessions are work units

All writes in a coherent task share one session. Close explicitly with `wiki_session_close` when done. Do not rely on inactivity timeout — it is a safety net, not the intended close path. Watch for `session-cap-approaching` warnings.

### Inline maintenance signals are load-bearing

`wiki_read` folds issue and talk digests into its response. Critical and moderate signals are relevant findings. Do not skip them. Writing over a page that has open critical issues makes the wiki worse.

---

## Research Modes (index.md + research.md)

Three traversal modes are available. The index advertises all three; `research.md` details when and how to use each.

| Mode | Mechanism | Context cost | Flexibility |
|------|-----------|-------------|-------------|
| **Daemon-delegated** | `wiki_query` | Near-zero | Fixed (daemon's LLM) |
| **Sub-agent** | Spawn agent via `delegate_task` / Agent tool | Zero to parent | High — configurable model, injected context |
| **In-context manual** | `wiki_search` → `wiki_read` → follow links | Accumulates | Full visibility, user sees each hop |

The mode choice is surfaced to the user at the start of any research task. It is not made silently.

---

## Subskill Designs

### `research.md` — Attended traversal

**Hard gate:** Before any traversal, state out loud: what you are looking for, why you need it, and what you will do with the result. No exceptions.

**Mode selection:** After stating intent, offer the three modes to the user with honest trade-offs. Wait for a response. Default recommendation: `wiki_query` for specific well-defined questions; sub-agent for broad exploratory research; in-context manual only when the user explicitly wants to see each hop.

**Sub-agent prompt template:** When spawning a research agent, the prompt must include: the stated intent, the vault path, a token budget hint, and whether to return structured synthesis or raw findings.

**In-context traversal discipline:**
1. `wiki_manifest` with a budget — orient before searching
2. `wiki_search` for entry points — do not start reading without a target
3. `wiki_read` viewport order: `top` → named section → `grep` → `full` (never `full` first)
4. Follow wikilinks with purpose — for each link, ask whether following it serves the stated intent
5. Inline issue/talk digests in `wiki_read` responses are relevant findings — treat critical and moderate signals as part of the research result

**Exit condition:** Traversal ends when the stated intent can be answered, not when pages run out.

---

### `write.md` — Attended knowledge capture

**Hard gate:** Before writing, state out loud: which page(s), what you are adding, and what source supports it. No source → talk page.

**Session discipline:**
- One session per coherent work unit (a topic, a paper, a fix pass) — not one per page
- Watch for `session-cap-approaching` — wrap up or plan to continue in a subsequent session
- Always close with `wiki_session_close` when done

**Tool selection:**
- `wiki_create` — new pages; citations are required in the call or it is rejected by the daemon
- `wiki_update` — V4A patch; re-read the target section first, patch against what is actually there, handle `patch-conflict` by re-reading and retrying (never rewrite the whole page to avoid a conflict)
- `wiki_append` — safest for additive knowledge; heading-anchored

**Uncitable content:** Use `wiki_talk_post`. Talk pages are for half-formed ideas, proposals, connections you cannot yet cite, and contradictions waiting on resolution. This is a first-class path, not a consolation.

**Before writing:** Check inline signals from `wiki_read`. If a page has open critical or moderate issues, address or acknowledge them before adding new content.

---

### `ingest.md` — Attended source synthesis

Ingestion is inherently conversational and multi-turn. It touches multiple pages in one session. The three-act shape:

**Act 1 — Read the source first**

Before touching any wiki tool, read the source (abstract and intro at minimum; full paper if short). Form a view: what are the key concepts, what claims does it make, what is genuinely novel vs. already-known territory. This calibrates all subsequent wiki interactions.

**Act 2 — Ask the user how to handle it**

State what you found: "This paper covers X, Y, Z. X already looks like it has wiki coverage; Y and Z may be new." Then ask: "Do you want to talk through where this lands, or should I just run the ingest?"

Honest choices:
- **Conversational path** — orient in the wiki together, discuss what to create vs. update, decide on hybrid strategy
- **Automated path** — `wiki_ingest` directly (once `--dry-run` is available: run dry-run first for preview, confirm, then fire; until then: manifest + search as orientation substitute)

**Act 3 — Execute**

*Conversational path:*
1. `wiki_manifest` + `wiki_search` for each key concept identified in Act 1
2. Discuss findings with user: what is already covered, what is new, what contradicts existing pages
3. Decide together: manual pages for important synthesis, `wiki_ingest` to catch connections and fill gaps. This hybrid is explicitly endorsed — use each tool for what it is good at.
4. Write in one session; do not close mid-ingest
5. After writing, ensure wikilinks exist between new/updated pages and their neighbours
6. `wiki_session_close`

*Automated path:*
- Run `wiki_ingest` (with dry-run preview once available)
- Report what was created and updated
- `wiki_session_close`

**Key synthesis principle:** Do not just extract claims into the wiki. For each concept: how does it connect to what is already there? Does it contradict, extend, or confirm existing pages? Contradictions go to talk pages; extensions go to page body; confirmations update the relevant claim's context.

**Note — `wiki_ingest --dry-run`:** This flag is not yet implemented. When available, it will return concept extraction and proposed page targets without writing — making it the preferred fast-orientation step in both attended and automated paths. Tracked separately.

---

### `maintain.md` — Attended hygiene

**Hard gate:** State scope before starting — full vault pass or a specific cluster?

**Protocol:**
1. `wiki_lint` — start here always; returns vault-wide attention map with issues by severity
2. Triage by severity: critical first (broken citations, failed claim verifications), moderate next (broken wikilinks), minor last (orphans, missing markers). Do not fix minor issues while critical ones are open.
3. For each issue: `wiki_issues_get` — read the full issue before deciding anything
4. Fix or escalate:
   - Fixable in place → write tools + `wiki_issues_resolve`
   - Unclear resolution → `wiki_talk_post` on the relevant page with the issue ID, leave open
   - Requires human judgment → surface to the user explicitly before touching anything
5. Check talk pages: `wiki_talk_list` for open discussions, `wiki_talk_read` for pages with unresolved critical/moderate entries, contribute via `wiki_talk_post` where relevant
6. `wiki_session_close` if any writes were made

**Key principle:** Maintenance is not a rewrite pass. Fixes should be surgical. If you find yourself wanting to rewrite page body content during a lint pass, flag it as a separate task and move on.

---

## Autonomous Subskills (`autonomous/`)

The `autonomous/` subskills are full independent documents. They are not the attended skills with a mode flag added — the scope, decision loop, and exit behaviour are genuinely different when there is no user to check in with.

### `autonomous/index.md` — Universal autonomous posture

**Determine unattended mode:** This skill set assumes no user is present. Every decision defaults to conservative. Surface nothing — escalate via talk pages.

**Universal autonomous defaults:**
- Scope is predefined by whoever scheduled the job — do not prompt for it
- Ambiguity → `wiki_talk_post` and move on; never block waiting for input
- Judgment calls that would normally go to the user → talk page with a clear note
- Hard cap on writes per run — avoid runaway cascades; a configurable N issues per invocation
- Session close is mandatory at end of every run — no human will notice a drifting session
- Exit with a structured report: what was found, what was fixed, what was escalated to talk, what was left open

### `autonomous/research.md`

Use `wiki_query` only. The daemon-delegated traversal mode is the only appropriate path when there is no user to select a mode or monitor context growth. Return structured findings — a synthesis, not a transcript of pages visited.

### `autonomous/write.md`

Only write what is clearly and directly supported by an explicit source already in hand. If the justification for a write requires any inference or judgment, `wiki_talk_post` instead. Close session explicitly. Report all writes made.

### `autonomous/ingest.md`

1. Read the source (extract key concepts)
2. Run `wiki_ingest` directly
3. Report: what was created, what was updated, any errors
4. `wiki_session_close`

No conversational path. No mode choice. Automated pipeline only.

### `autonomous/maintain.md`

1. `wiki_lint` — get attention map
2. Triage by severity; critical issues only unless time/cap allows moderate
3. For each issue: fix if unambiguously fixable, `wiki_talk_post` otherwise
4. Hard cap: stop after N issues per run (configurable, default TBD)
5. Check talk pages for open critical/moderate entries — do not contribute, just note in report
6. `wiki_session_close`
7. Emit structured report

Never escalate to a user. Never make judgment calls. When in doubt, talk post.

---

## Location and Distribution

- **Canonical location:** `skills/llm-wiki/` in the llm-wiki repo, shipping with the package
- **How to use (attended):** Point agent at `skills/llm-wiki/` — agent loads index and routes to appropriate subskill
- **How to use (autonomous/cron):** Point agent directly at `skills/llm-wiki/autonomous/<subskill>` — no routing needed
- **Future:** Publish to Superpowers plugin registry as an official plugin once skills are validated in practice

---

## Open Questions

1. **Hard cap for autonomous/maintain:** What is the right default for issues-per-run? Needs empirical data once the system is in active use.
2. **`wiki_ingest --dry-run`:** Being designed separately (tracked by Hermes agent). When implemented, update `ingest.md` and `autonomous/ingest.md` to use it as the primary orientation step.
3. **Sub-agent prompt template for research:** Full template to be refined during implementation — needs to be tested against real traversal tasks.
4. **Session cap default in autonomous/write:** What is an appropriate write cap per autonomous run? Likely mirrors the daemon's own session cap (30 writes) or lower.
