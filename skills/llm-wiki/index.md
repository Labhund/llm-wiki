---
name: llm-wiki
description: Use when working with an llm-wiki vault via MCP — covers research, writing, ingestion, and maintenance. Attended mode (user present). For autonomous/cron use, see llm-wiki/autonomous/.
---

# LLM-Wiki — Orientation and Routing

llm-wiki is an agent-first knowledge base: plain markdown files with wikilinks, backed by a daemon that handles indexing, background maintenance, and supervised writes. You interact with it through 17 MCP tools across four families. These skills describe how to use llm-wiki via MCP — if you have old CLI-based llm-wiki skills loaded, treat this set as superseding them.

## Determine Operating Mode First

Before anything else: am I **attended** (user present, can ask questions) or **autonomous** (cron, swarm, no user)?

If autonomous → use `llm-wiki/autonomous/<subskill>` instead of this skill set.

## Universal Principles

These apply to every operation.

**Viewport-first.** Never call `wiki_read` with `viewport=full` without first trying `top` or a named section. The manifest is the map; your token budget is the constraint. `full` is a last resort.

**Traversal, not RAG.** One search → done is wrong. Enter via manifest or search, follow wikilinks with purpose, build understanding across pages. The wiki is a compiled knowledge graph, not a retrieval index.

**Talk pages absorb everything uncitable.** No source → `wiki_talk_post`, not `wiki_create`. Main pages require citations. This is a first-class path, not a workaround.

**Sessions are work units.** All writes in a coherent task share one session. Sessions open implicitly on the first write call — no explicit open needed. Close explicitly with `wiki_session_close` — don't rely on inactivity timeout. Watch for `session-cap-approaching` (emitted at write 18; hard cap at 30) — wrap up the current work unit or plan to continue in a new session.

**Inline maintenance signals are load-bearing.** `wiki_read` folds in issue/talk digests. Critical and moderate signals are findings. Writing over a page with open critical issues makes the wiki worse.

## Research Modes

Three options — surface the choice to the user before starting any research:

| Mode | How | Context cost | Best for |
|------|-----|-------------|----------|
| **Daemon-delegated** | `wiki_query` | Near-zero | Specific, well-defined questions |
| **Sub-agent** | Agent's native sub-agent mechanism (e.g. `Agent` tool in Claude Code, `delegate_task` in Hermes) | Zero to parent | Broad exploratory research |
| **In-context manual** | `wiki_search` → `wiki_read` → follow links | Accumulates | When user wants to see each hop |

## Routing

- Research something → `llm-wiki:research`
- Add or update knowledge → `llm-wiki:write`
- Incorporate an external source (paper, document) → `llm-wiki:ingest`
- Hygiene pass → `llm-wiki:maintain`
- Autonomous/cron use → `llm-wiki/autonomous/<subskill>`
