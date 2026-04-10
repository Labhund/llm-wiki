---
title: Synthesis Cache — Query-to-Page Design
date: 2026-04-10
status: implemented
---

# Synthesis Cache

## Overview

Every cited query answer becomes a first-class wiki page (`type: synthesis`). These pages live in `wiki/`, enter the BM25 index like any ingest page, and surface on future similar queries — so the query agent can accept, update, or create rather than re-synthesising from scratch every time.

The quality gate is simple: only write if the answer contains wiki citations. An answer with no backing has nothing to compound on.

The adversary pipeline runs on synthesis pages over time, same as ingest pages. Low-scoring pages die naturally; no special culling logic needed.

## Architecture

### Write trigger

After every query response that contains at least one `[[wiki-link]]` citation, the server writes or updates a synthesis page. No score threshold, no explicit pre-traversal check — synthesis pages compete naturally with ingest pages in the BM25 index. If fresh ingest pages are more relevant, they outrank stale synthesis; the user gets the freshest answer.

### Fast path (emergent)

If a synthesis page on the same topic was written by a prior query, it surfaces in the traversal results. The LLM receives it as context and can `accept` it verbatim — zero output tokens spent re-transcribing. The speed benefit is emergent from the index, not a separate code path.

### Action schema

The query LLM returns a JSON action envelope alongside (or instead of) the prose answer:

```json
{"action": "accept",  "page": "<slug>"}
{"action": "update",  "page": "<slug>", "title": "<title>", "content": "<full page body>", "sources": ["wiki/foo.md"]}
{"action": "create",             "title": "<title>", "content": "<page body>",              "sources": ["wiki/foo.md"]}
```

- **accept** — existing page fully answers the query; server reads and returns it verbatim
- **update** — existing page found but new information surfaced; server overwrites with extended version
- **create** — no relevant synthesis page found; server writes a new one

If the LLM returns no `action` field (backward-compatible path or no backing), the server returns the prose answer as today and skips the write.

## Data Model

Synthesis pages use standard wiki frontmatter with two additional fields:

```yaml
---
title: "Boltz-2 Structure Prediction"
type: synthesis
query: "how does boltz-2 handle structure prediction?"
created_by: query
created_at: 2026-04-10T14:23:00Z
updated_at: 2026-04-10T15:01:00Z
sources:
  - wiki/boltz-2.md
  - wiki/structure-prediction.md
---

%% section: answer %%

[[boltz-2]] uses a diffusion-based approach for structure prediction... [^1]

[^1]: [[wiki/boltz-2.md]]
```

- `type: synthesis` — distinguishes from ingest pages; traversal can use this to decide how to present context to the LLM
- `query` — the exact query text; LLM uses this to judge relevance when deciding accept/update/create
- `sources` — wiki pages that backed the synthesis; adversary pipeline uses this to detect if sources were later flagged or updated
- Filename: query slugified (e.g. `boltz-2-structure-prediction.md`); collision → append `-2`
- Body uses `%% section: answer %%` markers so `Page.parse()` works unchanged

## Prompt Design

The synthesize system prompt always includes the action schema. When synthesis pages appear in the traversal top-K results, their content is appended to the user message as an "Existing Synthesis Pages" block.

**Action trigger (implemented):** the LLM is instructed to emit a JSON action whenever the answer contains `[[wiki-link]]` citations — regardless of whether synthesis candidates were provided. This is necessary for bootstrapping: the first synthesis page on a topic can only be written if `create` fires even when no candidates exist.

The action object must be the **first** thing in the response:

```
{"action": "create", "title": "...", "sources": ["wiki/page.md"]}

Prose answer follows here...
```

For `accept`, no prose is emitted — the server reads the existing page and returns it verbatim. For `update`/`create`, prose after the JSON is the page body. If parsing fails, the write is skipped and prose is returned as-is.

**Key deviation from original spec:** the spec's prompt example said "omit the JSON action if no synthesis pages are provided." This prevented bootstrapping (first synthesis page never written). The implementation emits `create` for any cited answer regardless of synthesis candidate presence.

## Error Handling

- **Malformed JSON / missing `action`**: skip write, return prose answer as-is. Never crash the query on a caching failure.
- **`accept` points to deleted page**: fall back to `create`.
- **`update` with empty content**: treat as `accept`.
- **Write failure (disk, permissions)**: log warning, return prose answer, do not surface error to user.

## Testing

Unit tests (mock LLM):
- Traversal returns synthesis page → assert LLM prompt includes existing-page block
- LLM returns `accept` → server reads existing page, returns verbatim, no write
- LLM returns `update` → existing page overwritten, updated frontmatter
- LLM returns `create` → new page written with correct frontmatter
- LLM returns no `action` (no citations) → no write, prose returned

Integration test:
- Run query → synthesis page written to `wiki/`
- Run same query again → LLM receives existing page in context
- Run query after new ingest → fresh ingest page outranks synthesis in BM25, LLM sees both

## What This Is Not

- Not a separate cache layer; no `cache/` directory, no TTL logic
- Not a pre-traversal short-circuit; traversal always runs (fresh content matters)
- Not a replacement for ingest; synthesis pages are attributed to a query session, never treated as primary sources

## Implementation Deviations

Deviations from the original spec discovered during implementation and production testing:

**Bootstrap fix (prompt):** the original spec said the action JSON should only be emitted when synthesis candidates are present. This prevented the first synthesis page from ever being written. Fixed: `create` fires whenever the answer has wiki citations, regardless of candidate presence. Commit: `bee46df`.

**`accept` on deleted page:** the spec said fall back to `create`. Not possible — the engine discards prose for `accept` before the server can check if the page still exists (`answer = ""`). Outcome: empty answer returned. Documented in code comment.

**`update` body source:** the spec's action schema showed `"content": "..."` as a field inside the JSON object. The implementation uses the prose after the JSON block as the page body instead (the prompt instructs the LLM to write the body as prose, not embed it in JSON). The `content` key in the JSON is ignored.

**Production observation (2026-04-10):** first live query wrote `applications-of-boltz.md` (synthesis page). Second identical query cited `[[applications-of-boltz]]` alongside `[[boltz-2]]`, confirming the synthesis page surfaced in BM25 and was used by the traversal.

## PHILOSOPHY.md alignment

| Principle | How this design satisfies it |
|-----------|------------------------------|
| Wiki is a compounding artifact | Synthesis pages accumulate and refine over queries |
| Unsupervised processes never originate body content | Synthesis is attributed to the query session (supervised interaction) |
| Main pages are sourced | Synthesis pages always carry `sources` + inline `[[citations]]` |
| LLM for understanding, code for bookkeeping | Accept/update/create decision is LLM reasoning; write logic is deterministic code |
