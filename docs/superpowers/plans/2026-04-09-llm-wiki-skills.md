# LLM-Wiki Agent Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write 10 Superpowers-format skill files that prime any MCP-connected agent (Claude Code, Hermes, OpenClaw, autonomous swarm) to use llm-wiki correctly by default.

**Architecture:** Plain markdown files at `skills/llm-wiki/` (repo root). Each file has YAML frontmatter (`name`, `description`) and a behavioural protocol as the body. Five attended skills (`index`, `research`, `write`, `ingest`, `maintain`) and five parallel autonomous skills under `skills/llm-wiki/autonomous/`. No code, no tests — each task is: write the file, verify against spec, commit.

**Tech Stack:** Markdown, YAML frontmatter, Superpowers skill convention.

---

## File Map

```
skills/llm-wiki/
  index.md              # orientation, universal principles, research modes, routing
  research.md           # attended: intent gate, three-mode choice, traversal discipline
  write.md              # attended: sessions, citations, V4A patches, talk for uncitable
  ingest.md             # attended: three-act conversation, hybrid manual+ingest
  maintain.md           # attended: lint → triage → fix/escalate → close
  autonomous/
    index.md            # conservative posture, error recovery, exit report discipline
    research.md         # wiki_query only, structured findings
    write.md            # sourced writes only, talk-post uncertain, mandatory close
    ingest.md           # dry-run safety gate → wiki_ingest → report
    maintain.md         # lint → conservative fix → cap → report → close
```

**Spec:** `docs/superpowers/specs/2026-04-09-llm-wiki-skills-design.md`

---

## Task 1: Scaffold directory structure and write `skills/llm-wiki/index.md`

**Files:**
- Create: `skills/llm-wiki/index.md`

The index is the entry point. It states universal principles, advertises the three research modes, and routes to subskills. It also tells autonomous agents to go to `autonomous/` instead.

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p skills/llm-wiki/autonomous
```

- [ ] **Step 2: Write `skills/llm-wiki/index.md`**

```markdown
---
name: llm-wiki
description: Use when working with an llm-wiki vault via MCP — covers research, writing, ingestion, and maintenance. Attended mode (user present). For autonomous/cron use, see llm-wiki/autonomous/.
---

# LLM-Wiki — Orientation and Routing

llm-wiki is an agent-first knowledge base: plain markdown files with wikilinks, backed by a daemon that handles indexing, background maintenance, and supervised writes. You interact with it through 17 MCP tools across four families.

## Determine Operating Mode First

Before anything else: am I **attended** (user present, can ask questions) or **autonomous** (cron, swarm, no user)?

If autonomous → use `llm-wiki/autonomous/<subskill>` instead of this skill set.

## Universal Principles

These apply to every operation.

**Viewport-first.** Never call `wiki_read` with `viewport=full` without first trying `top` or a named section. The manifest is the map; your token budget is the constraint. `full` is a last resort.

**Traversal, not RAG.** One search → done is wrong. Enter via manifest or search, follow wikilinks with purpose, build understanding across pages. The wiki is a compiled knowledge graph, not a retrieval index.

**Talk pages absorb everything uncitable.** No source → `wiki_talk_post`, not `wiki_create`. Main pages require citations. This is a first-class path, not a workaround.

**Sessions are work units.** All writes in a coherent task share one session. Sessions open implicitly on the first write call — no explicit open needed. Close explicitly with `wiki_session_close` — don't rely on inactivity timeout. Watch for `session-cap-approaching` (emitted at write 18; hard cap at 30).

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
- Autonomous/cron use → `llm-wiki:autonomous`
```

- [ ] **Step 3: Verify against spec**

Check that index.md covers all items from the spec's "Universal Principles" section and "Research Modes" table. Confirm:
- [ ] All 5 universal principles present
- [ ] All 3 research modes in the table with correct descriptions
- [ ] Routing table covers all 5 subskill targets
- [ ] Autonomous pointer is clear

- [ ] **Step 4: Commit**

```bash
git add skills/
git commit -m "feat: scaffold skills/llm-wiki/ — index.md orientation and routing"
```

---

## Task 2: Write `skills/llm-wiki/research.md`

**Files:**
- Create: `skills/llm-wiki/research.md`

- [ ] **Step 1: Write `skills/llm-wiki/research.md`**

```markdown
---
name: llm-wiki/research
description: Use when researching a topic in an llm-wiki vault. Covers three traversal modes: daemon-delegated (wiki_query), sub-agent, and in-context manual. Attended mode.
---

# LLM-Wiki Research — Attended Traversal

## Hard Gate

Before any traversal, state out loud:
- What you are looking for
- Why you need it
- What you will do with the result

No exceptions. This keeps reasoning legible and prevents purposeless browsing.

## Mode Selection

After stating intent, offer the three modes to the user:

> "I can research this three ways:
> 1. **Daemon query** (`wiki_query`) — fast, low context cost, quality depends on the daemon's configured model
> 2. **Sub-agent** — I spawn a research agent; my context stays clean; you can configure the model
> 3. **In-context manual** — I traverse step by step; you see each hop; costs more context
>
> Which do you prefer?"

Wait for a response. If the user has no preference: recommend `wiki_query` for specific well-defined questions, sub-agent for broad exploratory research, in-context manual only when the user explicitly wants to see each hop.

## Mode 1: Daemon-Delegated (`wiki_query`)

Call `wiki_query` with a clear, specific query string derived from your stated intent. Return the synthesis to the user.

## Mode 2: Sub-Agent

Spawn a research agent using your framework's native sub-agent mechanism (e.g., `Agent` tool in Claude Code, `delegate_task` in Hermes). The prompt must include:
- The stated intent verbatim
- The vault path (from MCP connection context)
- A token budget hint (e.g., "stay under 20k tokens")
- Whether to return structured synthesis or raw findings

The sub-agent follows Mode 3 (in-context manual) discipline internally.

## Mode 3: In-Context Manual

1. `wiki_manifest` with a budget — orient before searching; understand the cluster landscape
2. `wiki_search` for entry points — do not start reading without a target
3. `wiki_read` viewport order: `top` → named section → `grep` → `full` (never `full` first)
4. Follow wikilinks with purpose — for each link, ask: does following this serve my stated intent?
5. Inline issue/talk digests in `wiki_read` responses are relevant findings — critical and moderate signals are part of the research result

## Exit Condition

Traversal ends when you can answer the stated intent. Not when pages run out.
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Hard gate (state intent) present and described as non-optional
- [ ] All three modes described with honest trade-offs
- [ ] Sub-agent prompt template requirements listed
- [ ] In-context viewport order is `top` → section → `grep` → `full`
- [ ] Exit condition stated

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/research.md
git commit -m "feat: skills/llm-wiki/research.md — attended traversal, three-mode choice"
```

---

## Task 3: Write `skills/llm-wiki/write.md`

**Files:**
- Create: `skills/llm-wiki/write.md`

- [ ] **Step 1: Write `skills/llm-wiki/write.md`**

```markdown
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
- Watch for `session-cap-approaching` warnings (emitted at write 18; hard cap at 30) — wrap up or plan a second session
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

## Before Writing

Check inline signals from `wiki_read`. If a page has open critical or moderate issues, address or acknowledge them before adding new content. Writing over a broken page makes it worse.
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Hard gate present
- [ ] Session lifecycle correct (implicit open, explicit close)
- [ ] Cap numbers correct (warn at 18, hard at 30)
- [ ] `wiki_create` near-match rejection guidance present
- [ ] `force=true` guidance correct
- [ ] `wiki_update` V4A patch + re-read requirement present
- [ ] `wiki_append` citations requirement noted
- [ ] Uncitable → talk page is described as first-class

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/write.md
git commit -m "feat: skills/llm-wiki/write.md — sessions, citations, V4A patches, talk"
```

---

## Task 4: Write `skills/llm-wiki/ingest.md`

**Files:**
- Create: `skills/llm-wiki/ingest.md`

- [ ] **Step 1: Write `skills/llm-wiki/ingest.md`**

```markdown
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

## Act 2 — Ask the User How to Handle It

State what you found, then offer the choice:

> "This [paper/document] covers [X, Y, Z]. [X] looks like it may already have wiki coverage; [Y and Z] appear to be new territory.
>
> **Conversational** — we orient in the wiki together, discuss what to create vs. update, I can write key pages manually and use `wiki_ingest` to catch connections and fill gaps
> **Automated** — I run `wiki_ingest --dry-run` to preview what would be created, you confirm, then I execute"

Wait for the user's response.

## Act 3 — Execute

### Conversational Path

1. `wiki_manifest` + `wiki_search` for each key concept identified in Act 1
2. Discuss findings with user: what is already covered, what is new, what contradicts existing pages
3. Decide together: manual pages for important synthesis, `wiki_ingest` to catch connections and fill gaps — this hybrid is explicitly endorsed
4. Write in one session — do not close mid-ingest
5. After writing, ensure wikilinks exist between new/updated pages and their neighbours
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
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Three-act shape present
- [ ] Act 1 requires reading source before wiki tools
- [ ] Act 2 offers both paths with honest description
- [ ] Conversational path includes hybrid manual+ingest
- [ ] Automated path uses `--dry-run` before executing
- [ ] Both paths close session explicitly
- [ ] Synthesis principle (not just extraction) present

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/ingest.md
git commit -m "feat: skills/llm-wiki/ingest.md — three-act conversational ingest, dry-run"
```

---

## Task 5: Write `skills/llm-wiki/maintain.md`

**Files:**
- Create: `skills/llm-wiki/maintain.md`

- [ ] **Step 1: Write `skills/llm-wiki/maintain.md`**

```markdown
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
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Hard gate (state scope) present
- [ ] 6-step protocol in correct order
- [ ] Severity triage order correct (critical → moderate → minor)
- [ ] Three-way fix/escalate/surface-to-user decision present
- [ ] Talk page check included
- [ ] Session close conditional on writes
- [ ] "Not a rewrite pass" principle stated

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/maintain.md
git commit -m "feat: skills/llm-wiki/maintain.md — lint triage, severity-ordered fix/escalate"
```

---

## Task 6: Write `skills/llm-wiki/autonomous/index.md`

**Files:**
- Create: `skills/llm-wiki/autonomous/index.md`

The autonomous index is the universal posture document. Every autonomous agent should read it before any autonomous subskill.

- [ ] **Step 1: Write `skills/llm-wiki/autonomous/index.md`**

```markdown
---
name: llm-wiki/autonomous
description: Universal posture for autonomous (cron, swarm, unattended) llm-wiki agents. Read this before any autonomous subskill. Covers conservative defaults, error recovery, and exit report structure.
---

# LLM-Wiki Autonomous — Universal Posture

You are running without a user present. Every decision defaults to conservative. Surface nothing — escalate via talk pages.

## Universal Autonomous Defaults

- **Scope is predefined** — set by whoever scheduled this job; do not prompt for it
- **Ambiguity → `wiki_talk_post`** — never block waiting for input; post a clear note and move on
- **Judgment calls → talk page** — anything that would normally go to a user gets a talk post with a clear note
- **Hard write cap** — stop after the cap is reached; cap is passed via cron prompt or invocation parameter (e.g. `MAX_WRITES=10`); if unset, default to 10
- **Session close is mandatory** — no human will notice a drifting session; always call `wiki_session_close` at end of run
- **Exit with a structured report** — what was found, what was fixed, what was escalated to talk, what was left open

## Error Recovery

Infrastructure failures have defined responses — do not silently swallow errors or retry indefinitely:

| Failure | Response |
|---------|----------|
| `wiki_ingest` returns no concepts | Abort, report "no concepts extracted", do not write |
| `wiki_update` returns `patch-conflict` twice | `wiki_talk_post` noting the conflict, move on |
| Daemon unreachable | Abort entire run, emit error report, do not retry |
| Session expires mid-run | Start a new session for remaining writes, note the split in report |

## Research Quality Note

`wiki_query` quality is gated by the daemon's configured query backend, not the calling agent's model. For deep autonomous research, configure the daemon's query backend to use a capable model.

## Routing

- Autonomous research → `llm-wiki/autonomous/research`
- Autonomous writes → `llm-wiki/autonomous/write`
- Autonomous ingest → `llm-wiki/autonomous/ingest`
- Autonomous maintenance → `llm-wiki/autonomous/maintain`
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] All 6 universal autonomous defaults present
- [ ] Hard cap default (10) and configuration mechanism stated
- [ ] Error recovery table covers all 4 failure modes
- [ ] Research quality note present
- [ ] Routing table covers all 4 subskills

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/autonomous/index.md
git commit -m "feat: skills/llm-wiki/autonomous/index.md — conservative posture, error recovery"
```

---

## Task 7: Write `skills/llm-wiki/autonomous/research.md`

**Files:**
- Create: `skills/llm-wiki/autonomous/research.md`

- [ ] **Step 1: Write `skills/llm-wiki/autonomous/research.md`**

```markdown
---
name: llm-wiki/autonomous/research
description: Use for autonomous (cron, swarm, unattended) research tasks against an llm-wiki vault. Uses wiki_query only — no manual traversal, no mode selection. Returns structured findings.
---

# LLM-Wiki Autonomous Research

## Mode: Daemon-Delegated Only

Use `wiki_query` exclusively. Do not attempt manual traversal — context management without a user present is not safe. Quality is gated by the daemon's configured query backend (see `llm-wiki/autonomous` for the research quality note).

## Protocol

1. Call `wiki_query` with a clear, specific query derived from the predefined scope
2. If `wiki_query` fails or returns empty results: note in report, do not retry with manual traversal
3. Return structured findings

## Output Format

```
## Research Report
**Query:** [the query used]
**Status:** [success / no results / error]
**Findings:** [synthesis of results, or "no results found"]
**Pages consulted:** [list of page names if available]
```
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] `wiki_query` only — no manual traversal
- [ ] Failure handling: note in report, do not retry with manual
- [ ] Structured output format present

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/autonomous/research.md
git commit -m "feat: skills/llm-wiki/autonomous/research.md — wiki_query only, structured output"
```

---

## Task 8: Write `skills/llm-wiki/autonomous/write.md`

**Files:**
- Create: `skills/llm-wiki/autonomous/write.md`

- [ ] **Step 1: Write `skills/llm-wiki/autonomous/write.md`**

```markdown
---
name: llm-wiki/autonomous/write
description: Use for autonomous (cron, swarm, unattended) write tasks against an llm-wiki vault. Conservative — only write clearly sourced content, talk-post anything uncertain.
---

# LLM-Wiki Autonomous Write

## Conservative Default

Only write what is clearly and directly supported by an explicit source already in hand. If the justification for a write requires any inference or judgment, `wiki_talk_post` instead — never make autonomous judgment calls in writes.

## Protocol

1. For each intended write: confirm the source is explicit and in hand
2. If source is clear → proceed with write tool
3. If source requires inference → `wiki_talk_post` noting the intent and what source would be needed; do not write
4. Watch hard write cap (from invocation parameter or default 10) — stop when reached
5. `wiki_session_close` — mandatory
6. Emit structured report

## Tool Selection

Same tools as attended write — `wiki_create` (citations required), `wiki_update` (V4A patch, re-read first), `wiki_append` (heading-anchored, citations required). Session opens implicitly on first write.

## Error Recovery

- `patch-conflict` on `wiki_update` twice → `wiki_talk_post` noting the conflict; do not rewrite the whole page; count toward cap
- Near-match rejection on `wiki_create` → `wiki_talk_post` noting the proposed page; do not use `force=true` autonomously; count toward cap

## Output Format

```
## Write Report
**Writes attempted:** N
**Writes completed:** N
**Escalated to talk:** N (pages: [list])
**Cap hit:** yes / no
**Session closed:** yes
```
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Conservative default stated clearly
- [ ] Inference → talk-post (not write) rule present
- [ ] Hard cap referenced correctly
- [ ] `force=true` explicitly prohibited autonomously
- [ ] `patch-conflict` recovery present
- [ ] Mandatory session close present
- [ ] Structured output format present

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/autonomous/write.md
git commit -m "feat: skills/llm-wiki/autonomous/write.md — sourced writes only, conservative"
```

---

## Task 9: Write `skills/llm-wiki/autonomous/ingest.md`

**Files:**
- Create: `skills/llm-wiki/autonomous/ingest.md`

- [ ] **Step 1: Write `skills/llm-wiki/autonomous/ingest.md`**

```markdown
---
name: llm-wiki/autonomous/ingest
description: Use for autonomous (cron, swarm, unattended) ingestion of external sources into an llm-wiki vault. Dry-run safety gate before executing wiki_ingest.
---

# LLM-Wiki Autonomous Ingest

## Protocol

1. **Read the source** — extract key concepts; understand what you are about to ingest before touching any wiki tool
2. **`wiki_ingest --dry-run`** — inspect what the daemon would create/update:
   - Zero concepts extracted → abort; report "no concepts extracted"; do not proceed to live ingest
   - All targets have open critical issues → `wiki_talk_post` flagging the conflict; abort
   - Otherwise → proceed
3. **`wiki_ingest`** — execute
4. **`wiki_session_close`** — mandatory
5. **Emit structured report**

No conversational path. No mode choice. The dry-run step is the autonomous safety gate — it replaces the human confirmation from the attended path.

## Error Recovery

- Daemon unreachable → abort, report
- `wiki_ingest` errors mid-run → report partial results, close session, do not retry
- Dry-run returns no concepts → abort; do not proceed to live ingest

## Output Format

```
## Ingest Report
**Source:** [path or name]
**Dry-run concepts found:** N
**Pages created:** [list]
**Pages updated:** [list]
**Errors:** [any, or "none"]
**Session closed:** yes
```
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] Read source before wiki tools
- [ ] Dry-run is step 2, not optional
- [ ] Two dry-run abort conditions present (zero concepts, all targets have critical issues)
- [ ] Dry-run described as replacing human confirmation
- [ ] Error recovery covers daemon unreachable and mid-run errors
- [ ] Structured output present

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/autonomous/ingest.md
git commit -m "feat: skills/llm-wiki/autonomous/ingest.md — dry-run safety gate, no conversation"
```

---

## Task 10: Write `skills/llm-wiki/autonomous/maintain.md`

**Files:**
- Create: `skills/llm-wiki/autonomous/maintain.md`

- [ ] **Step 1: Write `skills/llm-wiki/autonomous/maintain.md`**

```markdown
---
name: llm-wiki/autonomous/maintain
description: Use for autonomous (cron, swarm, unattended) maintenance passes on an llm-wiki vault. Conservative — lint, triage critical/moderate only, fix unambiguous issues, talk-post everything else.
---

# LLM-Wiki Autonomous Maintain

## Protocol

1. **`wiki_lint`** — get vault-wide attention map with issues by severity
2. **Triage** — critical issues only unless cap allows moderate; skip minor entirely
3. **For each issue (up to cap):**
   - `wiki_issues_get` — read the full issue
   - Unambiguously fixable → write tools + `wiki_issues_resolve`
   - Any doubt → `wiki_talk_post` with the issue ID and a clear note; leave the issue open; count toward cap
4. **Check talk pages** — `wiki_talk_list`; note open critical/moderate entries in report; do not contribute
5. **`wiki_session_close`** — mandatory even if no writes were made
6. **Emit structured report**

## Hard Cap

Stop processing after the write cap is reached (from invocation parameter or default 10). Note remaining open issues in the report — they are work for the next run.

## Error Recovery

- `wiki_lint` fails → abort, report
- `wiki_issues_get` fails for a specific issue → skip it, note in report, do not count toward cap
- Write fails → `wiki_talk_post` the intended fix, note in report, count toward cap

## Never

- Make judgment calls autonomously — when in doubt, talk post
- Fix minor issues while critical ones remain open
- Rewrite page body content — maintenance fixes are surgical

## Output Format

```
## Maintenance Report
**Issues found:** N (critical: X, moderate: Y, minor: Z)
**Issues fixed:** N
**Escalated to talk:** N
**Cap hit:** yes / no
**Talk pages with open critical/moderate:** [list, or "none"]
**Session closed:** yes
```
```

- [ ] **Step 2: Verify against spec**

Confirm:
- [ ] 6-step protocol in correct order
- [ ] Skip minor when critical open
- [ ] Hard cap with default (10) and source stated
- [ ] Talk page check is observe-only (no contribute)
- [ ] Session close mandatory even with no writes
- [ ] Error recovery covers lint failure, issue-get failure, write failure
- [ ] Three "never" rules present
- [ ] Structured output present

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/autonomous/maintain.md
git commit -m "feat: skills/llm-wiki/autonomous/maintain.md — conservative lint triage, cap, report"
```

---

## Task 11: Update README to document skills location

**Files:**
- Modify: `README.md`

Add a brief section pointing users to `skills/llm-wiki/` after the MCP quick-start.

- [ ] **Step 1: Find the right insertion point in README.md**

The MCP section ends around line 82 (after the tools list). The new section goes immediately after `wiki_session_close` in the tools list, before `## How It Works`.

- [ ] **Step 2: Add the Agent Skills section**

Insert after the MCP tools list and before `## How It Works`:

```markdown
## Agent Skills

`skills/llm-wiki/` contains Superpowers-format skill files that prime any agent to use llm-wiki correctly by default — research traversal modes, write discipline, conversational ingest, and maintenance hygiene.

**Attended use** (user present): point your agent at `skills/llm-wiki/`

**Autonomous use** (cron, swarm): point your agent at `skills/llm-wiki/autonomous/<subskill>` directly

Compatible with Claude Code, Hermes, OpenClaw, and any agent framework that loads skill files by path.
```

- [ ] **Step 3: Verify the section reads cleanly in context**

Read the surrounding README sections to confirm the new section flows naturally between the MCP quick-start and `## How It Works`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README — agent skills section pointing to skills/llm-wiki/"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Task |
|-----------------|------|
| Universal principles (5) in index | Task 1 |
| Three research modes table | Task 1 |
| Routing table | Task 1 |
| Attended/autonomous mode gate | Task 1 |
| Research hard gate (state intent) | Task 2 |
| Mode selection offered to user | Task 2 |
| Sub-agent prompt template requirements | Task 2 |
| In-context viewport discipline | Task 2 |
| Write hard gate | Task 3 |
| Session implicit open, explicit close | Task 3 |
| Cap numbers (18/30) | Task 3 |
| `wiki_create` near-match + `force=true` guidance | Task 3 |
| `wiki_update` V4A re-read requirement | Task 3 |
| `wiki_append` citations requirement | Task 3 |
| Ingest three-act shape | Task 4 |
| Act 1: read source before wiki tools | Task 4 |
| Act 2: offer both paths | Task 4 |
| Automated path uses dry-run | Task 4 |
| Hybrid manual+ingest endorsed | Task 4 |
| Synthesis principle (not just extraction) | Task 4 |
| Maintain hard gate (scope) | Task 5 |
| Severity triage order | Task 5 |
| Fix/escalate/surface-to-user | Task 5 |
| "Not a rewrite pass" | Task 5 |
| Autonomous universal defaults (6) | Task 6 |
| Hard cap default (10) + config mechanism | Task 6 |
| Error recovery table (4 failures) | Task 6 |
| Research quality note | Task 6 |
| Autonomous research: wiki_query only | Task 7 |
| Autonomous research: structured output | Task 7 |
| Autonomous write: conservative default | Task 8 |
| Autonomous write: no `force=true` | Task 8 |
| Autonomous write: structured output | Task 8 |
| Autonomous ingest: dry-run safety gate | Task 9 |
| Autonomous ingest: two abort conditions | Task 9 |
| Autonomous maintain: skip minor while critical open | Task 10 |
| Autonomous maintain: talk-check observe-only | Task 10 |
| Autonomous maintain: session close even with no writes | Task 10 |
| README skills pointer | Task 11 |

All spec requirements covered. No gaps found.

### Placeholder Scan

No TBDs, TODOs, or "implement later" in any task. All file contents are complete and ready to write verbatim.

### Consistency Check

- Hard cap default: **10** throughout (Tasks 6, 8, 10) ✓
- Session cap numbers: **warn at 18, hard cap at 30** in write.md and index.md ✓
- `force=true` guidance: present in write.md (attended) and explicitly prohibited in autonomous/write.md ✓
- `wiki_append` citations requirement: noted in write.md and autonomous/write.md ✓
- Dry-run: used in ingest.md (automated path) and autonomous/ingest.md (safety gate) ✓
