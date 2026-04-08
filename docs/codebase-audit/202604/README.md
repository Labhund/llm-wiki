# llm-wiki Codebase Audit — April 2026

> Pre-completion audit: philosophy interrogation, codebase alignment, and gap analysis before Phase 6 implementation.

## Purpose

The philosophy document (PHILOSOPHY.md) was extracted from the Phase 6 design conversation — it describes what the project *became*, not necessarily what it *should be*. Phases 1–5 predate it and may carry baked-in assumptions the principles don't capture. This audit finds those gaps before the codebase is "done."

## Structure

This directory contains the working documents for a multi-session audit. Each file accumulates findings over time and is amended as work progresses.

```
docs/codebase-audit/202604/
├── README.md                          # This file — process overview and progress tracker
├── 00-doc-map.md                      # Phase 0: documentation inventory and contradiction map
├── 01-philosophy-interrogation.md     # Phase 1: principle-by-principle pressure testing
├── 02-codebase-alignment.md           # Phase 2: per-module alignment check + latent principles
├── 03-gap-analysis.md                 # Phase 3: synthesis — violations and uncovered decisions
├── 04-phase6-forward-check.md         # Phase 4: spec-vs-philosophy pre-implementation check
```

## Process

### Phase 0: Documentation inventory

Map every doc in the repo — specs, README, architecture notes, inline design comments. Build a dependency graph: which doc references which, where are there contradictions, where is something stated in two places with different wording.

Output: a doc-map with gaps and conflicts flagged.

### Phase 1: Philosophy interrogation (principle-by-principle)

For each of the 12 principles in PHILOSOPHY.md, three questions:

1. **Edge case pressure test.** What's the scenario that makes this principle hurt? (e.g., Principle 4 says "main pages are sourced" — what happens when the agent has a genuinely novel insight from synthesis that no single source captures? Is the talk page really the right place, or does it create a structural incentive to never surface original thinking?)

2. **Consequences audit.** Each principle lists consequences. Are there consequences that *aren't* listed but follow from the principle? Are there listed consequences the codebase doesn't actually implement?

3. **Principle conflicts.** Where do two principles pull in opposite directions? (e.g., Principle 2 says "plain markdown, any tool can edit" and Principle 10 says "git is the audit trail" — if a user edits a page in Obsidian and doesn't commit, the audit trail has a gap.)

Output: per-principle findings with severity ratings (cosmetic / design tension / actual violation).

### Phase 2: Codebase alignment

Walk the source tree module by module. For each module, two checks:

1. **Does it follow the principles?** Specifically: does anything in `daemon/scheduler` reach the write routes (Principle 3)? Do any files carry provenance metadata (Principle 2)? Does the file watcher handle external edits gracefully (Principle 2)?

2. **Does it embed decisions the philosophy doesn't capture?** Every `# TODO`, every `HACK`, every comment that says "we do X because Y" where Y isn't in the philosophy. Those are the latent principles.

Output: per-module alignment report + a "latent principles" list.

### Phase 3: Gap analysis

Synthesize Phase 1 and Phase 2 into two lists:

1. **Principles the codebase violates** — bugs (code should change) or wrong principles (philosophy should change).
2. **Decisions the philosophy doesn't cover** — new principle candidates or explicit "we chose not to have a principle here" entries.

Output: amendment proposals for PHILOSOPHY.md, or codebase fix items.

### Phase 4: Phase 6 forward check

Run the Phase 6 spec against the (now-amended) philosophy. Does anything in the spec contradict a principle? Does the spec introduce machinery that will strain a principle under load?

Output: spec amendment proposals or design tension documentation.

## Finding format

Each finding in the phase documents follows this structure:

```
### F-XX: [short title]

- **Area:** [principle number / module / doc]
- **Severity:** cosmetic | design-tension | actual-violation
- **Status:** open | resolved | deferred

**What was found:**
[description]

**Proposed resolution:**
[action or "accept as documented tension"]

**Notes:**
[session context, who found it, cross-references]
```

## Progress tracker

| Phase | Status | Sessions used | Findings |
|---|---|---|---|
| Phase 0: Doc map | not started | 0 | — |
| Phase 1: Philosophy interrogation | not started | 0 | — |
| Phase 2: Codebase alignment | not started | 0 | — |
| Phase 3: Gap analysis | not started | 0 | — |
| Phase 4: Phase 6 forward check | not started | 0 | — |

Estimated total: 4–6 sessions. Update the tracker as work progresses.

## Ground rules

- Findings are honest, not diplomatic. A design tension isn't a failure — it's a documented trade-off. An actual violation is a bug.
- The audit doesn't fix things. It finds them and proposes resolutions. Fixing is separate work.
- Latent principles are as valuable as principle violations. "The codebase consistently does X but we never wrote it down" is a real finding.
- Amend these documents in place. The git history shows what changed. Don't create v2 copies.
