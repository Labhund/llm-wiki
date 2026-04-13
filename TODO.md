# TODO

## Known Issues

- **MCP tool descriptions too verbose** — agents receive noisy context; trim descriptions across the 27 tools
- **`wiki_ingest` should be removed from MCP** — it dispatches a long-running background agent pipeline (minutes); wrong for interactive sessions. Ingest belongs on the CLI only
- **Autoingest quality is model-dependent** — tested with `step-3.5-flash`; unclear whether failures are model capability or skill/prompt quality
- **Tagging needs improvement** — Librarian agent auto-tags post-ingest but quality is poor; likely a prompt issue in `librarian/prompts.py`


## Rendered Document Responses (Future Direction)

**What:** Replace the JSON response envelope with rendered markdown documents. The daemon becomes a rendering layer: structured data stored and processed internally, markdown documents served to the agent. L1–L4 are incremental improvements; this is the architectural destination.

**Why this is the right direction:**
- The entire system is already in one semantic space — wiki pages, skills, talk pages, plan files, agent reasoning are all markdown. JSON responses are the only format break.
- Behavioral triggers carry full semantic weight in rendered text. `[CRITICAL ISSUE]` in a document is not the same representation as `{"sev":{"crit":1}}` in a JSON blob — the former has learned emotional salience; the latter is just a number.
- No format-switching overhead. The agent's context window is a continuous stream of one register.
- Debuggable without tooling. A developer reading MCP session logs sees documents, not escaped JSON strings.
- The manifest already proves the model. It returns plain text and is the best-behaved tool in the set.
- Extends PHILOSOPHY.md Principle 2 to the wire protocol: plain markdown is the substrate.

**Sketch of rendered formats:**

`wiki_read` response:
```
[MODERATE ISSUE] broken-link-attention-mechanism-a1b2c3
  Broken link to [[bahdanau-attention]] — no target page in vault

---

## Overview

The [[attention mechanism]] allows a model to dynamically weight positions...

[sections: Overview | Mechanism | Citations]

---

[talk: 2 entries, 1 open]
One open suggestion: cross-link to [[positional-encoding]] from Mechanism section (2026-04-07).
```

`wiki_search` response:
```
rfdiffusion (score: 0.94)
  Diffusion-based protein structure generation; de novo, partial diffusion, motif scaffolding
  Match at line 42: "...For binder design, **motif scaffolding** constrains a fixed structural motif..."

bindsweeper (score: 0.31)
  Multi-dimensional parameter sweep tool — no direct match
```

`wiki_status` response:
```
Vault: ~/wiki
Pages: 14 across 3 clusters | Total: 38,020 tokens
Last indexed: 2026-04-09T14:23:11
```

**Error states:** A consistent prefix that the agent can detect reliably without parsing:
```
[ERROR: not-found] Page "bahdanau-attention" does not exist. Did you mean: bahdanau-2015?
[ERROR: patch-conflict] Context mismatch at line 42. Re-read the page and retry.
```

**What needs to change:**
1. Add a rendering pipeline to the daemon response path — `render_response(response: dict, tool: str) -> str` — called in `_ok()` before wrapping in `TextContent`
2. Define rendered formats for all 17 tools
3. Keep JSON internally for all daemon ↔ daemon communication; only the MCP boundary uses rendered output
4. Update tool descriptions to describe the rendered format agents will receive
5. Update `docs/gallery.md` throughout — this changes every agent-view example
6. Deprecate L1 (compact JSON) once this lands — it becomes irrelevant

---

## Future Ideas

- **Vault → Managed migration tool**: Pre-packaged workflow that lets 4-8 local LLMs loose on an unstructured Obsidian vault over days/weeks to reorganize it into managed structure (raw sources separated, index built, cross-references added, provenance established). Automated "get it ship shape" pipeline.
