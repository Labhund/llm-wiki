# Phase 0: Documentation Inventory

> Status: not started

## Method

1. Walk the entire repo for documentation files: `.md`, `.rst`, docstrings in key modules, `README*`, `CHANGELOG*`, anything in `docs/`.
2. For each doc, capture: path, purpose, last modified date, which other docs it references.
3. Flag: contradictions between docs, stale docs (describe behavior that no longer exists), orphan docs (nothing links to them), missing docs (referenced but don't exist).

## Doc map

[Fill in during the audit session. Suggested structure:]

| Path | Purpose | References | Stale? | Notes |
|---|---|---|---|---|
| `README.md` | Project overview | PHILOSOPHY.md, specs/* | ? | |
| `PHILOSOPHY.md` | Design principles | Phase 6 spec | ? | Extracted 2026-04-08 |
| ... | | | | |

## Contradictions found

[List any statements that appear in multiple docs with different wording or different intent.]

## Gaps found

[Referenced docs that don't exist, important topics not covered anywhere.]

## Findings

[Use the standard finding format from README.md]
