# Phase 3: Gap Analysis

> Status: not started
> Depends on: Phase 1, Phase 2

## Method

Synthesize findings from Phase 1 (philosophy interrogation) and Phase 2 (codebase alignment) into two actionable lists:

1. **Principles the codebase violates** — either the code is wrong (fix it) or the principle is wrong (amend PHILOSOPHY.md).
2. **Decisions the philosophy doesn't cover** — either add a principle or document the explicit non-decision.

---

## Principle violations

[Fill in after Phase 1 and Phase 2 are complete.]

| Finding | Principle | Resolution | Action | Status |
|---|---|---|---|---|
| [e.g.] F-07: File watcher doesn't handle concurrent external edits | Principle 2 | Code bug | Fix file watcher | open |
| ... | | | | |

## Uncovered decisions

[Latent principles from Phase 2 that aren't in PHILOSOPHY.md and aren't obviously wrong.]

| Decision | Where it lives | Should it be a principle? | Proposed action | Status |
|---|---|---|---|---|
| [e.g.] Tantivy is the only search backend | config.py | Probably not — it's an implementation choice | Document as non-goal in README | open |
| ... | | | | |

## PHILOSOPHY.md amendment proposals

[For each proposed change to the philosophy document, draft the exact amendment here before applying.]

### Proposal P-01: [title]

**Existing text:**
[quote the principle or consequence being changed]

**Proposed text:**
[the replacement]

**Justification:**
[why — reference the finding that motivated it]

**Consequences trace:**
[which existing features does this affect? do they still hold?]

---

[Repeat per proposal]

## Codebase fix items

[For violations where the code needs to change, not the philosophy.]

| Item | Module | Finding reference | Priority | Status |
|---|---|---|---|---|
| ... | | | | |
