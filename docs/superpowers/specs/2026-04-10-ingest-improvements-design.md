# Ingest Improvements Design

**Date:** 2026-04-10
**Branch:** feat/ingest-improvements

## Overview

Two related improvements to the ingest pipeline:

1. **Streaming progress** — CLI stays attached during live ingest, shows a spinner and a line per concept as it completes. Eliminates the 30s socket timeout problem for large documents.
2. **Lightweight dry-run** — Dry-run stops after concept extraction (1 LLM call instead of 1 + N). Shows concept list + create/update status. Fast enough that streaming is not needed.

## Motivation

`llm-wiki ingest boltz2.pdf` (7.3 MB, ~15 concepts) exceeds the 30s socket timeout in `DaemonClient._sync_request`. The root cause is that `_handle_ingest` runs the full pipeline — extract → concept identification → N page-content LLM calls — before sending any response. The same timeout hits dry-run, which also runs all N page-content calls.

## Feature A: Streaming Ingest Progress

### Protocol

No wire format change. The existing 4-byte-length-prefix framing supports multiple frames over one connection. The CLI includes `"stream": true` in the ingest request; the daemon sends N progress frames then one `done` frame before closing the connection.

Progress frame shapes:

```json
{"type": "progress", "stage": "extracting"}
{"type": "progress", "stage": "concepts_found", "count": 8}
{"type": "progress", "stage": "concept_done", "name": "boltz-diffusion", "title": "Boltz Diffusion Model", "action": "created", "num": 1, "total": 8}
{"type": "done", "status": "ok", "created": ["boltz-diffusion", ...], "updated": [...], "concepts_found": 8}
```

MCP callers omit `"stream": true` and receive the existing single-response behaviour unchanged.

### IngestAgent

`ingest()` gains an optional parameter:

```python
on_progress: Callable[[dict], Awaitable[None]] | None = None
```

Called at:
- Before extraction: `{"stage": "extracting"}`
- After concept extraction: `{"stage": "concepts_found", "count": N}`
- After each concept write: `{"stage": "concept_done", "name": ..., "title": ..., "action": "created"|"updated", "num": i, "total": N}`

`None` by default — zero overhead for MCP and test callers.

### Server

`_handle_client` detects `stream: true` before routing and calls a new `_handle_ingest_stream(request, writer)` method directly:

```python
if request.get("type") == "ingest" and request.get("stream"):
    await self._handle_ingest_stream(request, writer)
    return
response = await self._route(request)
await write_message(writer, response)
```

`_handle_ingest_stream` mirrors the existing `_handle_ingest` setup but passes `on_progress` to `agent.ingest()` as an async lambda that writes progress frames to the writer. On completion it writes the `done` frame. The existing `_handle_ingest` is untouched.

### Client

`DaemonClient` gains:

```python
async def stream_ingest(self, msg: dict) -> AsyncIterator[dict]:
    """Yield progress frames, then the done frame."""
```

Reads frames in a loop until `type == "done"`, yielding each. A sync wrapper `stream_ingest_sync(msg, on_frame)` calls `asyncio.run` and invokes `on_frame(frame)` for each yielded frame; this is what the CLI uses.

### CLI

All output goes to **stdout** so the stream is pipeable and grepable. The spinner is a local TTY concern rendered in-place on the same line — it does not emit its own output lines and is suppressed when stdout is not a TTY.

The `ingest` command (non-dry-run):

1. Adds `"stream": True` to the request
2. Enters a frame loop, printing a line per frame:
   - `progress / extracting` → start TTY spinner, no output line
   - `progress / concepts_found` → `[PROGRESS] concepts_found: 8`
   - `progress / concept_done` → `[DONE] boltz-diffusion-model (created)`
   - `done` → `[SUMMARY] 7 created, 1 updated`
3. When stdout is a TTY, a braille spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) runs in-place between lines; when piped, output is plain line-per-event with no escape codes.

Example piped output:

```
[PROGRESS] concepts_found: 8
[DONE] boltz-diffusion-model (created)
[DONE] structure-prediction (updated)
[DONE] se3-equivariant-networks (created)
[SUMMARY] 7 created, 1 updated
```

### Error handling mid-stream

If a concept write fails after streaming has started (i.e. after at least one `concept_done` frame has been sent), the daemon sends an error frame and closes the connection:

```json
{"type": "error", "status": "error", "message": "...", "concepts_written": 3}
```

The daemon does **not** continue writing remaining concepts after a failure — partial states are confusing and the user should re-run. The CLI prints the error and exits non-zero. `concepts_written` is included so the user knows which concepts landed before the failure.

## Feature B: Lightweight Dry-Run

### IngestAgent

When `dry_run=True`, `ingest()` returns immediately after `parse_concept_extraction`. No page-content LLM calls are made.

`ConceptPreview` is populated from concept extraction output:
- `name`, `title`: from extraction
- `is_update`: file-exists check (`wiki_dir / f"{concept.name}.md"`)
- `passages`: list from extraction
- `sections`: empty (not generated)

### Server

Dry-run response drops section-level fields:

```json
{
  "status": "ok",
  "dry_run": true,
  "source_path": "...",
  "source_chars": 42800,
  "concepts_found": 8,
  "extraction_warning": null,
  "concepts": [
    {"name": "boltz-diffusion-model", "title": "Boltz Diffusion Model", "action": "create", "passage_count": 6},
    ...
  ]
}
```

### CLI

Dry-run output:

```
DRY RUN — boltz2.pdf (42,800 chars)
  [NEW] boltz-diffusion-model    "Boltz Diffusion Model"    (6 passages)
  [UPD] structure-prediction     "Structure Prediction"     (4 passages)
  8 concepts total
```

No streaming needed — single LLM call is fast enough for synchronous response.

## Backward Compatibility

- MCP callers: no change. `"stream"` field absent → existing `_handle_ingest` path.
- `DaemonClient.request()` and `_sync_request`: unchanged.
- `IngestAgent.ingest()`: `on_progress=None` default means all existing call sites work as-is.
- Dry-run response: `sections` field removed from concept objects. No known consumers depend on it outside the CLI.

## Out of Scope

- Persisted job registry (reconnectable jobs). `nohup` covers the background-process use case.
- MCP streaming (MCP callers are programmatic; they don't need a spinner).
- Resonance post-step progress (it runs after all concepts are written; not surfaced to the user today).
