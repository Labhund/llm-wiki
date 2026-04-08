# Phase 2: Codebase Alignment

> Status: not started

## Method

Walk `src/llm_wiki/` module by module. For each module:

1. **Principle adherence.** Does the code follow the 12 principles? Key checks:
   - Does anything in `daemon/scheduler` or background-worker code reach write routes? (Principle 3)
   - Do any wiki files carry provenance metadata? (Principle 2)
   - Does the file watcher handle external edits gracefully? (Principle 2)
   - Is state correctly split between wiki/ and state-dir? (Principle 9)
   - Are background worker outputs visible to active agents by default? (Principle 8)

2. **Latent principles.** Every `# TODO`, `# HACK`, `# FIXME`, and every comment of the form "we do X because Y" where Y isn't in PHILOSOPHY.md. These are decisions that guided code but were never written down.

---

## Module inventory

[Fill in during the audit. Suggested grouping by top-level package:]

### `src/llm_wiki/core/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/daemon/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/traversal/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/ingest/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/issues/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/talk/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/librarian/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/adversary/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/audit/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/cli/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `src/llm_wiki/config.py`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

### `tests/`

**Principle adherence:**
[Fill in]

**Latent decisions:**
[Fill in]

**Findings:**
[Findings, or "none found"]

---

## Latent principles

[After all modules are done, synthesize the "we do X because Y" findings into a candidate list:]

| Latent principle | Where it appears | Already in PHILOSOPHY.md? | Action |
|---|---|---|---|
| [e.g.] "Tantivy is the only search backend" | config.py, daemon/search.py | No | Add to Principle 1 consequences, or document as explicit non-goal |
| ... | | | |
