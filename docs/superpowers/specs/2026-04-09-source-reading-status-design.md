# Source Reading Status — Design

> Status: Draft
> Author: Markus Williams, with collaborative design from Claude (Sonnet 4.6)
> Date: 2026-04-09

## Overview

Sources in `raw/` carry a `reading_status` field (`unread` | `in_progress` | `read`) tracking researcher engagement. The flaw it fixes: autonomous ingest creates wiki pages that look like verified knowledge but have never been evaluated by the researcher. Reading status makes that distinction mechanical and visible.

This is a standalone PR on top of the completed Phase 6 MCP server.

## Design principles

- **Natural to use.** Neither humans nor LLMs should have to jump through mental hoops. Sources can arrive in `raw/` through any path — manual copy, CLI, MCP tool, agent. The system notices and surfaces gaps; it does not enforce a single blessed write path.
- **Everything deterministic Python.** No LLM calls anywhere in this feature. Status tracking is bookkeeping.
- **Auditor as consistency layer.** Gaps between reality and tracked state are surfaced at scan time, not enforced at write time.

## Data model

### Companion `.md` for binary sources

Binary sources (PDF, DOCX, images) cannot hold frontmatter directly. `wiki_ingest` creates a companion alongside the binary:

```
raw/2026-04-09-vaswani-attention.pdf     ← immutable original
raw/2026-04-09-vaswani-attention.md      ← frontmatter + extracted text body
```

Companion name is always `{stem}.md` matching the binary. If the companion already exists, `wiki_ingest` does not overwrite it.

### Markdown and fetched-URL sources

Frontmatter lives at the top of the file itself. Body is immutable source text.

### Frontmatter schema

```yaml
---
reading_status: unread   # unread | in_progress | read
ingested: 2026-04-09
source_type: paper       # paper | article | transcript | book | other
---
```

`reading_status` is the only mutable field — only `wiki_source_mark` changes it. `ingested` and `source_type` are set once by `wiki_ingest` and never change.

### Performance

Auditor and `wiki_source_mark` use frontmatter-early-exit reads: open the file, read lines until the closing `---`, parse that block, close. The body (which may be large for PDF companions) is never loaded for metadata operations.

`Vault.scan()` is scoped to `wiki/` only — `raw/` never enters the page scan path.

## Components

### 1. `vault.py`

**Change:** scope the rglob from `root` to `wiki_dir`.

```python
# Before
md_files = sorted(root.rglob("*.md"))

# After
wiki_dir = root / config.vault.wiki_dir.rstrip("/")
md_files = sorted(wiki_dir.rglob("*.md"))
```

Companion files in `raw/` never appear as wiki pages.

### 2. New `ingest/source_meta.py`

Pure Python helpers used by multiple components. No LLM, no daemon calls.

**`read_frontmatter(path: Path) -> dict`**
Opens file, reads until closing `---`, parses YAML. Returns `{}` if no frontmatter block found. Never reads the body.

**`write_frontmatter(path: Path, updates: dict) -> None`**
Reads frontmatter, merges `updates` into it, reconstructs the file with updated frontmatter block. Body is preserved byte-for-byte.

**`init_companion(source_path: Path, vault_root: Path, source_type: str) -> Path | None`**
For binary sources under `vault_root/raw/`: creates `{stem}.md` alongside the binary with `reading_status: unread`, `ingested: today`, `source_type`. Creates frontmatter-only (no body). Returns `None` on all no-op paths: source not under `raw/`, or companion already exists. Returns the new companion `Path` only when a companion was freshly created. Callers guard body-write with `if companion:` — this correctly skips the write on idempotent re-runs. Purely sync — no extraction.

**`write_companion_body(path: Path, text: str) -> None`**
Appends extracted text as body after the closing `---` of an existing frontmatter-only companion file. Called by `IngestAgent` immediately after extraction, only when `init_companion` returned a path (i.e. the companion was just created). Not a general-purpose append — assumes the file currently ends at the closing `---`.

### 3. `ingest/agent.py`

At the start of `IngestAgent.ingest()`, before any LLM call:

```python
if source_path.is_relative_to(vault_root / "raw"):
    companion = init_companion(source_path, vault_root, source_type=source_type)  # passed from caller
```

`source_type` is threaded from the `wiki_ingest` MCP tool arg (optional, defaults to `"paper"`) through `_handle_ingest` into `IngestAgent.ingest()` into `init_companion`.

After extraction completes (the existing `extract_text()` call), `IngestAgent` writes the extracted text as the companion body:

```python
if companion:  # None means not newly created — skip body write on re-ingest
    write_companion_body(companion, extraction.content)
```

This keeps `source_meta.py` sync and extraction in `IngestAgent` where it already lives.

If source is not under `raw/`, ingest proceeds unchanged (backwards compat).

### 4. `audit/checks.py`

New function `find_source_gaps(vault_root: Path, config: WikiConfig) -> CheckResult`.

Walks `raw/` and raises four issue types:

| Issue type | Condition | Severity |
|---|---|---|
| `bare-source` | File with extension in `_SUPPORTED_BINARY` (`.pdf`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`) in `raw/` with no companion `.md` | `minor` |
| `missing-reading-status` | `.md` in `raw/` with no `reading_status` in frontmatter | `minor` |
| `unread-source` | `reading_status: unread` and `ingested` > N days ago (config: `audit.unread_source_days`, default 30) | `minor` |
| `in-progress-no-plan` | `reading_status: in_progress` and no plan file in `inbox/` whose `source:` frontmatter matches (see match contract below) | `moderate` |

The `in-progress-no-plan` check skips gracefully if `inbox/` does not exist (the `inbox/` convention is a separate PR).

**Match contract for `in-progress-no-plan`:** the check normalises the source file path to its vault-relative form `raw/<filename>` (e.g. `raw/2026-04-09-foo.pdf`) and compares it against the `source:` field in each inbox plan file's frontmatter. The plan file must use the same canonical form. Bare filenames (`2026-04-09-foo.pdf`) and absolute paths are not accepted as matches — the skill documents the canonical form and the daemon enforces it in `wiki_source_mark`.

**Out of scope — orphaned companions:** if a binary source is deleted but its companion `.md` remains, the companion will not trigger `bare-source` (it's a `.md`). It will instead surface as `missing-reading-status` or `unread-source`, which is acceptable noise. A dedicated `orphaned-companion` check is deferred until the `inbox/` PR is merged and the full source lifecycle is in place.

### 5. `audit/auditor.py`

`Auditor` currently holds `vault`, `queue`, `vault_root` but not `config`. Add `config: WikiConfig` to `__init__`. Add `find_source_gaps(self._vault_root, self._config)` to the `results` list in `audit()`.

### 6. `daemon/server.py` + `mcp/tools.py`

New `source-mark` daemon route and `wiki_source_mark` MCP tool.

**MCP tool signature:**
```
wiki_source_mark(source_path: str, status: "unread" | "in_progress" | "read", author: str)
```

**Handler logic:**
1. Validate `source_path` is under `raw/` — error if not.
2. Validate `status` is one of the three values — error if not.
3. `read_frontmatter(path)` to get current status.
4. `write_frontmatter(path, {"reading_status": status})`.
5. Git commit via `daemon/commit.py` with message `meta: mark {filename} {status}` and trailer `Source-Status: {old}→{new}`.

Commits directly — not through the session/journal pipeline. This is metadata, not wiki content.

### 7. `adversary/sampling.py`

When sampling claims for verification, apply a weight multiplier to claims sourced from `raw/` files with `reading_status: unread`. These pages have had no human review — they warrant higher adversary attention. Multiplier is configurable (`adversary.unread_weight`, default `1.5`). Pure scoring change, no LLM.

## Data flow

**Source arrives via `wiki_ingest`:**
```
wiki_ingest(raw/foo.pdf, author)
  → init_companion() → raw/foo.md created (frontmatter only, reading_status: unread)
  → extract_text(raw/foo.pdf) → extracted text
  → write_companion_body(raw/foo.md, extracted text)  ← body written after extraction
  → LLM identifies concepts, writes wiki pages
  → vault rescan
```

**Source dropped manually:**
```
cp paper.pdf raw/2026-04-09-paper.pdf
  → nothing immediate
  → auditor next run → bare-source minor issue filed
  → human/agent calls wiki_ingest or wiki_source_mark to clear it
```

**Status update:**
```
wiki_source_mark(raw/foo.md, "in_progress", author)
  → read frontmatter (stops at ---)
  → write updated frontmatter
  → git commit: "meta: mark foo.md in_progress\n\nSource-Status: unread→in_progress"
```

## Skill protocol

The `skills/llm-wiki/ingest.md` skill documents when to call `wiki_source_mark`:

| Moment | Action |
|---|---|
| Brief mode start | `wiki_source_mark(source, "in_progress")` |
| Brief mode complete, no deep session planned | `wiki_source_mark(source, "read")` |
| Deep mode session start | `wiki_source_mark(source, "in_progress")` |
| Deep mode plan complete | `wiki_source_mark(source, "read")` |
| Autonomous ingest | Sets `unread` only — never calls `wiki_source_mark` |

The skill calls these at the right moments. The human never edits frontmatter manually.

## Error handling

All errors are deterministic:

- `wiki_source_mark` with path outside `raw/` → `error: source_path must be under raw/`
- `wiki_source_mark` with invalid status → `error: status must be unread|in_progress|read`
- `init_companion()` when companion exists → no-op
- `find_source_gaps()` when `inbox/` absent → skips `in-progress-no-plan` check silently
- `read_frontmatter()` on file with no frontmatter block → returns `{}`

## Testing

| Test file | Covers |
|---|---|
| `test_source_meta.py` | `read_frontmatter` stops at `---` on large file; `init_companion` idempotent; `write_frontmatter` preserves body |
| `test_checks_source_gaps.py` | Fixture for each of the four issue types; `inbox/` absent skips gracefully |
| `test_ingest_companion.py` | `wiki_ingest` on `raw/` path creates companion; on non-`raw/` path does nothing new |
| `test_vault_scan.py` | Companion files in `raw/` do not appear as wiki pages |

## Files changed

| File | Change |
|---|---|
| `src/llm_wiki/vault.py` | Scope rglob to `wiki_dir` |
| `src/llm_wiki/ingest/source_meta.py` | **New** — frontmatter helpers + `init_companion` + `write_companion_body` |
| `src/llm_wiki/ingest/agent.py` | Call `init_companion` at ingest start |
| `src/llm_wiki/audit/checks.py` | Add `find_source_gaps` |
| `src/llm_wiki/audit/auditor.py` | Add `config` param, call `find_source_gaps` |
| `src/llm_wiki/daemon/server.py` | Add `source-mark` route |
| `src/llm_wiki/mcp/tools.py` | Add `WIKI_SOURCE_MARK` tool |
| `src/llm_wiki/adversary/sampling.py` | Unread source weight multiplier |
| `skills/llm-wiki/ingest.md` | Document `wiki_source_mark` call protocol |
| `tests/test_source_meta.py` | **New** |
| `tests/test_checks_source_gaps.py` | **New** |
| `tests/test_ingest_companion.py` | **New** |
| `tests/test_vault_scan.py` | Extend existing |
