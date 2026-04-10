# llm-wiki Philosophy

> The principles plans are derived from. Mostly immutable. Amend with cause.

This document captures the load-bearing commitments behind llm-wiki's design — the *why* behind every feature, before any specific implementation. When a future plan, feature, or refactor seems to conflict with one of these principles, that's the signal to either reconsider the change or amend this document explicitly. Drift is the enemy.

The document is mutable in the sense that any principle can be revised — but each revision should be accompanied by a changelog entry below explaining what changed and what motivated it. Changing a principle silently is the same as drifting away from it.

---

## 1. The wiki is a compounding artifact, not RAG

RAG re-derives knowledge from sources on every query. A wiki compiles knowledge once and keeps it current. The compounding effect — accumulated cross-references, resolved contradictions, refined synthesis — is the entire point. Every design decision has to either preserve compounding or earn the cost of breaking it.

**Consequences:**
- Knowledge that has been distilled stays distilled. We don't re-extract it on every query.
- Pages improve over time through usage signals (librarian) and adversarial verification (adversary), not through being regenerated from scratch.
- Search is a navigation tool, not a primary information-retrieval mechanism. The agent traverses the compiled wiki the way a human browses; vector search is an optional accelerant, not the foundation.

---

## 2. Plain markdown on a filesystem is the substrate

The wiki is just files. Any tool can edit them — Obsidian, vim, an MCP-connected agent, `cat >>`, `rsync`. This is the source of the project's portability, longevity, and human-friendliness. Anything that requires the daemon to be the *only* path to the wiki is a step away from the substrate and needs strong justification.

**Consequences:**
- We do not enforce attribution at the file level. The filesystem can't tell us who wrote what (a `mtime` change could be Obsidian, sync, rsync, git pull, or another agent), and pretending otherwise produces metadata that immediately becomes a lie.
- We do not require all edits to flow through the daemon. The daemon must be tolerant of the file watcher discovering changes it didn't make.
- We never write metadata to the file that could become inconsistent with the file's actual content. Provenance frontmatter, "last modified by" tags, and similar attribution attempts are all rejected — if it's not derivable from the file, it doesn't belong in the file.

---

## 3. Background vs supervised — the boundary that replaces human vs machine

"Human prose is sacred" was always a defense against unsupervised autonomy, not against machines writing per se. The actual fear is a cron job silently rewriting notes. A frontier model in conversation with a human is fundamentally different — it is *in the loop*. Treating those two cases identically wastes the active model's context and forces the wiki into a worse outcome than the conversation could have produced.

The cleaner principle is:

> **Unsupervised processes never write body content. Supervised ones can.**

The boundary is *background vs interactive*, not *human vs machine*.

**What "supervised" means:**
- A frontier model in conversation with a human via MCP — supervised, because there's a person in the room.
- An autonomous research worker the user spun up as a separate process — *also* supervised, because the user configured and started that process and is responsible for it. Same as Unix: the kernel doesn't write files for you, but it lets any process you start do so.
- A long-running swarm of agents reading and writing the wiki collaboratively — supervised, because each agent identifies itself and the operator is accountable for the swarm.

**What "unsupervised" means:**
- Anything in the daemon's scheduler — librarian, adversary, auditor, compliance reviewer. These run on cron without a configuring human in the loop for any individual action. They are forbidden from *originating* body content.

**Consequences:**
- The daemon's three write routes (`page-create`, `page-update`, `page-append`) are MCP-only. A test enforces this mechanically by AST-walking background-worker code paths.
- Background workers can still write metadata sidecars, file issues, post to talk pages, and insert invisible structural markers — but never body content they originated themselves.
- All MCP write tools require an `author` identifier. The daemon refuses writes without one. Self-asserted, not validated; accountability comes from git history, not from a registry.

**Refinement: the boundary is on origination, not delivery.**

A write is supervised if it was initiated by an identified author in a supervised session — regardless of when or by whom it is physically applied. A *proposal file* is the unit of that authority: it carries an explicit author, a specific target, and content that passed validation gates before being queued. A daemon worker that applies a proposal file is acting as courier, delivering an already-authorized write. A daemon worker that decides to modify a page on its own inference is acting as author. Those are different acts.

Consequences of this refinement:
- The auditor may auto-merge update proposals where all grounding scores meet threshold and wikilinks resolve — because those proposals were generated and attributed during a supervised ingest session, not by the auditor. The auditor commits as the *proposal's attributed author*, not as the auditor.
- A proposal without a named originating author is anonymous body content from a daemon process. The auditor rejects it rather than applying it.
- The librarian's refinements still do not qualify: continuous diffuse inference with no discrete proposal file, no named author, no defined target. Sidecar-only.
- Any background worker seeking write access must produce discrete proposal files attributable to a supervised session. Role alone confers no authority.

---

## 4. Main pages are sourced. Talk pages are everything pre-source.

Every claim in the main wiki must be traceable to a primary source. The daemon enforces this by rejecting `wiki_create` and `wiki_append` calls with empty citations. Half-formed ideas, agent proposals, ambiguous adversary verdicts, contradictions waiting on resolution — all of those go on talk pages, which have no citation requirement and accept anything.

This split is the same one Wikipedia makes: main page is the consensus, talk page is the working space. The reason it works is that *separation preserves the integrity of both*. Mixing them corrupts both: the wiki loses its "everything traceable to a source" property, and the talk space loses its freedom to be wrong out loud.

**Consequences:**
- A future brainstorming companion tool will live entirely outside the wiki and consume it as a read-only research surface. It will be a sibling, not a child. The wiki has zero awareness of "brainstorming mode."
- When an agent has a thought it can't cite, the right action is `wiki_talk_post`, not `wiki_create`.
- Talk page closure is append-only — a new entry that references prior entries via `resolves: [int]`. The original entries are never mutated. Wikipedia model done right.

---

## 5. The framework absorbs boredom on behalf of both sides

Humans get lazy about citation hygiene, cross-referencing, and tagging. Models get lazy too — they'll happily skip "the boring part" of integrating a new fact into the existing graph if you let them. The daemon's job is to make the boring parts invisible: schema enforcement, indexing, manifest updates, lint, journal, commit — all of it happens behind the write tools without the agent or the human ever thinking about it.

This is the principle that justifies the daemon's continued existence in the face of "couldn't a skill file do all this?" The skill-file approach (e.g., the hermes_agent llm-wiki skill) works, but it burns the active model's context on schema details every call. With the daemon, the agent's reasoning stays focused on *what* to write; the daemon handles *how* to integrate it.

**Consequences:**
- We split writes into three tools (`wiki_create`, `wiki_update`, `wiki_append`) instead of one with a `mode` parameter, so each tool can have its own hidden enforcement pipeline without the agent thinking about it.
- Tool descriptions tell the agent *what* and *why*, never *how*. The "how" lives in the daemon.
- New maintenance work (lint passes, sidecar bumps, manifest updates, structural checks) gets added to the hidden pipelines as the wiki grows. The agent's interface stays small.

---

## 6. The active agent is the writer; the daemon is the kernel

The daemon is infrastructure. It does not have intelligence of its own — it does not decide what to write, what to summarize, or what to explore. It runs continuous bookkeeping (indexing, refinement, verification) and exposes capabilities to processes that *do* have intelligence: MCP-connected agents and the humans driving them.

This is the same model as Unix: the kernel manages resources; processes do the actual work. A program that needs to write a file calls `write(2)` and trusts the kernel to handle the storage layer. The kernel doesn't second-guess what the program is writing.

**Consequences:**
- The daemon does not refuse writes based on *content*, only on *contract* (citations missing, frontmatter invalid, name collision, patch context mismatch). Content judgment is the agent's job.
- The daemon's background workers (librarian, adversary, auditor) refine sidecars and file issues; they don't try to "improve" the wiki directly.
- "Cleverness" in the daemon is a smell. If a feature requires the daemon to be smart about content, it probably belongs in the agent or in a tool description.

---

## 7. Continuous between-session work is what justifies the daemon

A skill file or a one-shot tool can replicate most of what the daemon does *during* an agent's session. What it can't replicate is what happens *between* sessions:

- Continuous indexing keeps tantivy warm so queries don't pay 30s startup.
- The file watcher reflects Obsidian edits in seconds without manual rescan.
- The librarian refines tags and summaries from accumulated usage logs.
- The adversary samples old claims and re-verifies them against sources.
- The auditor files structural issues from periodic scans.
- The compliance reviewer responds to file edits with debounced lint.
- Talk page summaries get regenerated as new entries arrive.

None of these happen unless something is running when no agent is in the room. **The wiki only gets cared for between sessions if there's a daemon caring for it.** This is the load-bearing reason the daemon exists — not to be a write gatekeeper, not to enforce schema (it could do that with sync hooks), but to be the entity that does the work agents can't.

**Consequences:**
- We do not feature-cut anything from the maintenance substrate to make the daemon "lighter." Lightness is not the goal; presence is.
- The daemon must survive long enough between user interactions to actually run its workers. If the LLM queue is so busy with active queries that maintenance starves, that's a real bug.
- All maintenance LLM calls go through the queue at `priority="maintenance"` so they never compete with user-facing work, but they always get *some* time.

---

## 8. Visibility creates load-bearing — talk pages and issues need both ends

Background workers can write to talk pages and file issues forever, but if no one ever reads them, they're write-only graveyards. The asynchronous channel between background workers and users only works when there's a reader on the other end.

The active agent is that reader, but only if it can't ignore them. `wiki_read` folds open issues and unresolved talk-entry digests directly into its response — the agent literally cannot read a page without seeing what background workers and prior sessions have said about it. `wiki_lint` returns the vault-wide attention map so the agent can see the whole maintenance backlog at a glance.

**Consequences:**
- Any background worker that produces output (issues, talk entries, structural findings) needs a corresponding read path that surfaces that output to active agents *by default*. Optional surfacing doesn't count — it has to be the default.
- The cost-aware design (severity tiers, librarian-summarized digests, count-only by default) is what makes default-surfacing tractable. We don't dump the whole talk page into every read response; we summarize.
- New maintenance signals require new visibility paths. Adding a feature that only gets read on demand is the same as not adding it.

---

## 9. State splits cleanly by versioning needs

Two stores, with a hard rule about which lives where:

**In-wiki paths** (under `wiki/`, committed to git):
- `wiki/<page>.md` — page content
- `wiki/<page>.talk.md` — talk page sidecars
- `wiki/.issues/<id>.md` — issue files

**State-dir paths** (under `~/.llm-wiki/vaults/<vault>/`, *never* in git):
- `index/` — tantivy index
- `overrides.json` — librarian's tag/summary refinements
- `sessions/*.journal` — recovery state
- `traversal_logs.jsonl` — usage signal

The state directory is *rebuildable from the wiki on rescan*. Losing it means a slightly slower next startup, not lost work. This is the property that makes "everything is revertable" actually true: there is no second, unversioned source of truth that could become inconsistent with the page files.

**Consequences:**
- We never put anything in the state dir that isn't rebuildable from the wiki. If we discover something that needs to persist independently, the right answer is to put it in the wiki, not to start backing up the state dir.
- We never put anything in the wiki that we don't want in git history. Page files, talk pages, issues — these are content, and git history is part of their value.
- When the state dir is lost, the system recovers by rescanning. The cost is a one-time rebuild, not data loss.

---

## 10. Git is the audit trail. Not a shadow log.

Every supervised mutation produces a git commit, attributed to its author via the commit trailer. There is no shadow audit log, no provenance frontmatter, no per-page attribution metadata. Git is the source of truth for what happened. `git revert` is the undo button.

This is also the reason we don't try to track who wrote what at the file level (see Principle 2). The filesystem can't tell us, but git can: every commit has an `Agent: <id>` trailer, and `git log --grep "Agent: researcher-3"` returns everything that agent has ever done in the wiki. The granularity of attribution is the commit, not the line.

**Consequences:**
- Per-author session journaling: writes are grouped into per-author sessions, settled either by inactivity timeout or write-count cap or explicit close, and each session produces one commit summarized by the cheap maintenance LLM (with a deterministic fallback if the LLM is unavailable).
- The daemon only auto-commits things attributed to a supervised author. Background-worker writes (compliance, auditor, adversary, librarian) and Obsidian edits sit on disk uncommitted by default — they're the user's to commit, not the daemon's.
- We never silently overwrite git history, never amend commits the daemon didn't make in the current settle, and never `git add -A` (always specific paths from the journal).

---

## 11. Soft tools beat hard rules when the agent is supervised

The daemon could enforce many things in code: refuse writes to "human pages," reject patches that touch certain sections, require human confirmation before overwriting prose. Most of these are *temptations to lock down a system that doesn't need locking*. A supervised agent can be reasoned with through tool descriptions and well-shaped errors. The agent's prompt + the tool's contract are usually enough.

Mechanical enforcement is reserved for the cases where reasoning can't reach: unsupervised paths (the AST hard-rule test that prevents background workers from calling write routes), and contract violations the daemon must enforce because they affect correctness (citation requirements, name collisions, patch context mismatches).

**Consequences:**
- We prefer "the tool description tells the agent what to do" over "the daemon refuses to let the agent do the wrong thing."
- We prefer "the daemon returns a clear, actionable error" over "the daemon silently rewrites the request to be valid."
- We never add a config option to "lock down" supervised writes by default. If someone wants stricter behavior, they can opt in — but the default trusts the agent.

---

## 12. Reduce friction for the human; the same machinery serves swarms

The primary user is a human doing research. The wiki should make it dramatically easier for them to compile, recall, and refine knowledge. Every design decision should be measured first against "does this reduce friction for the human."

But the same machinery — MCP write tools, session journaling, attention-aware reads — should also serve autonomous research swarms with no changes. A solo researcher with one Claude window and a 20-agent autonomous research team should both be first-class citizens of the same wiki.

**Consequences:**
- We do not optimize for the swarm case at the expense of the solo case, or vice versa. Any feature that makes one of them worse needs strong justification.
- Sessions are keyed by `(author, connection_id)` by default so accidental author collisions in swarms don't merge work; this also costs the solo case nothing.
- Tool descriptions are written for "an agent in conversation with a human" but don't presume the human is the agent's only audience.

---

## 13. LLM is for understanding. Code is for bookkeeping.

LLM inference is slow, expensive, and non-deterministic. It is reserved for tasks that genuinely require language understanding: claim extraction, adversarial verification, synthesis, commit summarisation. Everything that can be done with deterministic code must be.

The auditor does not use an LLM to detect a broken wikilink — it parses the file. The reading status check does not use an LLM to determine if a source is stale — it reads a date. The compliance reviewer uses heuristics first; LLM only for the fraction of cases where heuristics can't reach. Any feature whose maintenance pass requires crawling an LLM over the vault is a feature that will not run — the cost will quietly suppress it.

The rule: if a task can be expressed as a file scan, date comparison, regular expression, or graph traversal, it must be. LLM is the last resort, not the first.

**Consequences:**
- Structural integrity checks (orphans, broken links, missing markers, missing frontmatter fields, stale reading status) are pure Python — O(n) file reads, no LLM.
- New metadata fields (reading_status, synthesis markers, plan file tracking) are written and read by deterministic code. The LLM never "fills in" missing metadata.
- Any background worker that can't complete its core check without an LLM call is a design smell. Factor the check into a cheap code pass and a richer LLM enhancement pass — the code pass must be able to run alone.
- When adding a new auditor check, the first question is: can I write this as a regex or a parse? Only escalate to LLM if the answer is genuinely no.

---

## How to amend this document

These principles are mostly immutable. Each is the distilled result of a design conversation that took hours to converge, and changing one usually means undoing decisions downstream. Treat amendments as serious — but don't treat them as forbidden. The wiki itself will teach us things over time, and when it does, the philosophy should reflect what we learned.

To amend a principle:

1. **State the existing principle and what's wrong with it.** Be specific. "It doesn't scale" is not enough; "Principle 7 says continuous between-session work justifies the daemon, but the maintenance loop has been off for three months and nothing broke" is enough.
2. **Propose the replacement.** What new principle replaces or refines the old one? What does it permit that the old one didn't? What does it forbid?
3. **Trace the consequences.** Which existing features were derived from the old principle? Do they still hold under the new one? Anything that no longer fits should be either redesigned or grandfathered with explicit acknowledgment.
4. **Add a changelog entry below.** Date, principle changed, summary of why. Do not silently overwrite the old text — at minimum, a brief reference to what was there before so a future reader can reconstruct the evolution.

A principle that has no consequences in the rest of the system is dead weight. A principle that contradicts another is a bug. A principle that's vague enough to permit anything is decorative. Keep this document sharp.

---

## Changelog

- **2026-04-08** — Initial document. Principles 1–12 distilled from the Phase 6 design conversation. Captures the load-bearing commitments behind the daemon architecture, the supervised/unsupervised split, the talk-page/issue visibility model, and the git-as-audit-trail decision. No prior version to reference.
- **2026-04-10** — Principle 3 refined. Added the "origination vs delivery" clarification under the supervised/unsupervised definitions. Motivation: the ingest proposals pipeline design required the auditor to apply proposal files generated during supervised ingest sessions. The original bright line ("unsupervised processes never write body content") was accurate but underspecified — it didn't distinguish between a daemon worker generating content on its own inference vs. delivering content that a supervised process already generated and attributed. The refinement makes that distinction explicit and states its consequences: auditor may apply proposals with a named originating author; librarian refinements still cannot qualify (no discrete proposal, no named author). The constraint that prevents scope creep is the proposal file itself — without one, no delivery authority exists.
