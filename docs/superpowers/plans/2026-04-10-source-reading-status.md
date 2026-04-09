# Source Reading Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `reading_status` tracking to `raw/` sources — automatic metadata initialization on ingest, auditor checks for gaps, a `wiki_source_mark` MCP tool to update status, and adversary upweighting for unread-source claims.

**Architecture:** A new `ingest/source_meta.py` module provides pure-Python frontmatter helpers used by everything else. `Vault.scan()` is scoped to `wiki/` only so companion files in `raw/` are never treated as wiki pages. The auditor gains four new source checks. A new `wiki_source_mark` MCP tool commits status changes directly (no session pipeline). The adversary sampling weight is multiplied for claims from unread sources.

**Tech Stack:** Python stdlib (`pathlib`, `datetime`, `subprocess`), PyYAML (already a dependency), pytest-asyncio for ingest tests. No new dependencies.

---

## File Structure

| File | Change |
|---|---|
| `src/llm_wiki/ingest/source_meta.py` | **New** — `read_frontmatter`, `write_frontmatter`, `init_companion`, `write_companion_body` |
| `src/llm_wiki/vault.py` | Scope `rglob` to `wiki_dir`; update cluster calculation |
| `src/llm_wiki/config.py` | Add `auditor_unread_source_days` and `adversary_unread_weight` to `MaintenanceConfig` |
| `src/llm_wiki/ingest/agent.py` | Add `source_type` param; call `init_companion` + `write_companion_body` |
| `src/llm_wiki/audit/checks.py` | Add `find_source_gaps` |
| `src/llm_wiki/audit/auditor.py` | Add `config` param; call `find_source_gaps` |
| `src/llm_wiki/daemon/server.py` | Add `source-mark` route handler |
| `src/llm_wiki/mcp/tools.py` | Add `WIKI_SOURCE_MARK` tool definition |
| `src/llm_wiki/adversary/sampling.py` | Add `unread_sources` + `unread_weight` params to `sample_claims` |
| `src/llm_wiki/adversary/agent.py` | Build `unread_sources` set; pass to `sample_claims` |
| `skills/llm-wiki/ingest.md` | Document `wiki_source_mark` call protocol |
| `tests/conftest.py` | Move fixture pages into `wiki/` subdir |
| `tests/test_ingest/test_source_meta.py` | **New** |
| `tests/test_audit/test_checks.py` | Add `find_source_gaps` tests |
| `tests/test_audit/test_auditor.py` | Update `Auditor` construction; add source-gaps check count |
| `tests/test_ingest/test_ingest_companion.py` | **New** |
| `tests/test_vault.py` | Add raw/ isolation test |
| `tests/test_adversary/test_sampling.py` | Add unread-weight tests |
| `tests/test_mcp/test_source_mark.py` | **New** |

---

### Task 1: `ingest/source_meta.py` — frontmatter helpers

**Files:**
- Create: `src/llm_wiki/ingest/source_meta.py`
- Create: `tests/test_ingest/test_source_meta.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest/test_source_meta.py`:

```python
from __future__ import annotations

import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# read_frontmatter
# ---------------------------------------------------------------------------

def test_read_frontmatter_returns_dict(tmp_path: Path):
    f = tmp_path / "source.md"
    f.write_text("---\nreading_status: unread\ningested: 2026-04-10\n---\n")
    from llm_wiki.ingest.source_meta import read_frontmatter
    result = read_frontmatter(f)
    assert result["reading_status"] == "unread"


def test_read_frontmatter_stops_at_closing_dashes(tmp_path: Path):
    """Body content must never be parsed — even if it looks like YAML."""
    large_body = "key: value\n" * 5000
    f = tmp_path / "large.md"
    f.write_text(f"---\nreading_status: unread\n---\n{large_body}")
    from llm_wiki.ingest.source_meta import read_frontmatter
    result = read_frontmatter(f)
    assert list(result.keys()) == ["reading_status"]


def test_read_frontmatter_no_frontmatter_returns_empty(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("Just plain text, no frontmatter.\n")
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(f) == {}


def test_read_frontmatter_missing_file_returns_empty(tmp_path: Path):
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(tmp_path / "nonexistent.md") == {}


# ---------------------------------------------------------------------------
# write_frontmatter
# ---------------------------------------------------------------------------

def test_write_frontmatter_merges_single_field(tmp_path: Path):
    f = tmp_path / "source.md"
    f.write_text("---\nreading_status: unread\ningested: 2026-04-10\n---\n")
    from llm_wiki.ingest.source_meta import write_frontmatter, read_frontmatter
    write_frontmatter(f, {"reading_status": "in_progress"})
    result = read_frontmatter(f)
    assert result["reading_status"] == "in_progress"
    assert "ingested" in result  # untouched


def test_write_frontmatter_preserves_body(tmp_path: Path):
    body = "\nExtracted text body.\nSecond line.\n"
    f = tmp_path / "source.md"
    f.write_text(f"---\nreading_status: unread\n---{body}")
    from llm_wiki.ingest.source_meta import write_frontmatter
    write_frontmatter(f, {"reading_status": "read"})
    content = f.read_text()
    assert "Extracted text body." in content
    assert "Second line." in content


def test_write_frontmatter_adds_frontmatter_to_plain_file(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("Plain content here.\n")
    from llm_wiki.ingest.source_meta import write_frontmatter, read_frontmatter
    write_frontmatter(f, {"reading_status": "unread"})
    assert read_frontmatter(f)["reading_status"] == "unread"
    assert "Plain content here." in f.read_text()


# ---------------------------------------------------------------------------
# init_companion
# ---------------------------------------------------------------------------

def test_init_companion_creates_companion_for_pdf(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "2026-04-10-paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion, read_frontmatter
    companion = init_companion(pdf, tmp_path)
    assert companion is not None
    assert companion == raw_dir / "2026-04-10-paper.md"
    assert companion.exists()
    fm = read_frontmatter(companion)
    assert fm["reading_status"] == "unread"
    assert fm["source_type"] == "paper"
    assert "ingested" in fm


def test_init_companion_returns_none_if_already_exists(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    (raw_dir / "paper.md").write_text("---\nreading_status: in_progress\n---\n")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(pdf, tmp_path) is None


def test_init_companion_returns_none_outside_raw(tmp_path: Path):
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    pdf = other_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(pdf, tmp_path) is None


def test_init_companion_returns_none_for_markdown_source(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    md = raw_dir / "article.md"
    md.write_text("# Article\n\nContent.\n")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(md, tmp_path) is None


def test_init_companion_idempotent_no_overwrite(tmp_path: Path):
    """Calling init_companion twice must not change the companion written first."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion, write_frontmatter
    companion = init_companion(pdf, tmp_path)
    assert companion is not None
    write_frontmatter(companion, {"reading_status": "in_progress"})
    second = init_companion(pdf, tmp_path)
    assert second is None
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"


# ---------------------------------------------------------------------------
# write_companion_body
# ---------------------------------------------------------------------------

def test_write_companion_body_appends_after_frontmatter(tmp_path: Path):
    f = tmp_path / "companion.md"
    f.write_text("---\nreading_status: unread\n---\n")
    from llm_wiki.ingest.source_meta import write_companion_body, read_frontmatter
    write_companion_body(f, "Extracted paper text.")
    content = f.read_text()
    assert "Extracted paper text." in content
    # Frontmatter still readable
    assert read_frontmatter(f)["reading_status"] == "unread"
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
pytest tests/test_ingest/test_source_meta.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'llm_wiki.ingest.source_meta'`

- [ ] **Step 3: Create `src/llm_wiki/ingest/source_meta.py`**

```python
from __future__ import annotations

import datetime
from pathlib import Path

import yaml

_SUPPORTED_BINARY = frozenset({
    ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"
})


def read_frontmatter(path: Path) -> dict:
    """Read YAML frontmatter from a file, stopping at the closing ---.

    Never reads the body. Returns {} if no frontmatter block is found,
    the file is missing, or YAML parsing fails.
    """
    try:
        with path.open(encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return {}
            lines: list[str] = []
            for _ in range(100):  # safety cap — standard frontmatter is < 20 lines
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                lines.append(line)
        return yaml.safe_load("".join(lines)) or {}
    except (OSError, yaml.YAMLError):
        return {}


def write_frontmatter(path: Path, updates: dict) -> None:
    """Merge updates into the frontmatter of path. Body preserved byte-for-byte."""
    content = path.read_text(encoding="utf-8")
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            body = content[end + 4:]  # everything after the closing \n---
        else:
            fm_text = ""
            body = content
    else:
        fm_text = ""
        body = content
    fm: dict = yaml.safe_load(fm_text) if fm_text else {}
    fm = fm or {}
    fm.update(updates)
    new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{new_fm}\n---{body}", encoding="utf-8")


def init_companion(
    source_path: Path,
    vault_root: Path,
    source_type: str = "paper",
) -> Path | None:
    """Create a frontmatter-only companion .md for a binary source in raw/.

    Returns the new companion Path only when freshly created. Returns None
    on ALL no-op paths: source is .md/.markdown, not under vault_root/raw/,
    or companion already exists. Callers must guard body-write with
    ``if companion:``.
    """
    if source_path.suffix.lower() in (".md", ".markdown"):
        return None
    raw_dir = vault_root / "raw"
    try:
        source_path.relative_to(raw_dir)
    except ValueError:
        return None
    companion = source_path.with_suffix(".md")
    if companion.exists():
        return None
    today = datetime.date.today().isoformat()
    companion.write_text(
        f"---\nreading_status: unread\ningested: {today}\nsource_type: {source_type}\n---\n",
        encoding="utf-8",
    )
    return companion


def write_companion_body(path: Path, text: str) -> None:
    """Append extracted text as body to a frontmatter-only companion file.

    Called once immediately after init_companion. Assumes the file
    currently ends at the closing ``---``. The body is separated from
    the frontmatter by a blank line.
    """
    current = path.read_text(encoding="utf-8")
    path.write_text(current + "\n" + text, encoding="utf-8")
```

- [ ] **Step 4: Run tests to confirm they all pass**

```bash
pytest tests/test_ingest/test_source_meta.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/ingest/source_meta.py tests/test_ingest/test_source_meta.py
git commit -m "feat: add source_meta helpers (read/write frontmatter, init_companion, write_companion_body)"
```

---

### Task 2: `vault.py` — scope scan to `wiki/`

**Files:**
- Modify: `src/llm_wiki/vault.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_vault.py`
- Modify: `tests/test_audit/test_checks.py` (two tests that create pages outside `wiki/`)

- [ ] **Step 1: Add vault scan test that raw/ companions stay invisible**

Append to `tests/test_vault.py`:

```python
def test_vault_scan_ignores_raw_companion_files(tmp_path: Path):
    """Companion .md files in raw/ must never appear as wiki pages."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (wiki_dir / "real-page.md").write_text("---\ntitle: Real\n---\n\nContent.\n")
    (raw_dir / "2026-04-10-paper.pdf").write_bytes(b"%PDF-1.4 fake")
    (raw_dir / "2026-04-10-paper.md").write_text(
        "---\nreading_status: unread\n---\nExtracted text.\n"
    )
    vault = Vault.scan(tmp_path)
    assert "real-page" in vault.manifest_entries()
    assert "2026-04-10-paper" not in vault.manifest_entries()
    assert vault.page_count == 1
```

- [ ] **Step 2: Run new test to confirm it fails**

```bash
pytest tests/test_vault.py::test_vault_scan_ignores_raw_companion_files -v
```

Expected: FAIL — `assert vault.page_count == 1` fails because `2026-04-10-paper` is scanned

- [ ] **Step 3: Update `tests/conftest.py` — move fixture pages into `wiki/`**

The `sample_vault` fixture in `tests/conftest.py` currently places pages under `tmp_path` directly. Replace the fixture body so all pages live under `wiki/`:

```python
@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample pages under wiki/."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    bio = wiki_dir / "bioinformatics"
    bio.mkdir()
    (bio / "srna-embeddings.md").write_text(SAMPLE_PAGE_WITH_MARKERS)
    (bio / "inter-rep-variant-analysis.md").write_text(
        "---\ntitle: Inter-Rep Variant Analysis\n---\n\n"
        "%% section: overview, tokens: 30 %%\n"
        "## Overview\n\nVariant analysis across embedding representations.\n\n"
        "%% section: method, tokens: 35 %%\n"
        "## Method\n\nUses silhouette scores > 0.5 for quality.\n"
        "See [[srna-embeddings]] and [[clustering-metrics]].\n"
    )

    ml = wiki_dir / "machine-learning"
    ml.mkdir()
    (ml / "clustering-metrics.md").write_text(SAMPLE_PAGE_NO_MARKERS)

    (wiki_dir / "no-structure.md").write_text(SAMPLE_PAGE_NO_STRUCTURE)

    yield tmp_path

    import shutil
    from llm_wiki.vault import _state_dir_for
    state_dir = _state_dir_for(tmp_path)
    if state_dir.exists():
        shutil.rmtree(state_dir)
```

- [ ] **Step 4: Fix two tests in `tests/test_audit/test_checks.py` that create pages outside `wiki/`**

`test_find_orphans_skips_index_readme_home` creates pages directly at `tmp_path`. Move them into `wiki/`:

```python
def test_find_orphans_skips_index_readme_home(tmp_path: Path):
    """Pages named index/readme/home are entry points, not orphans."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "index.md").write_text("# Index\n\nEntry point.\n")
    (wiki_dir / "README.md").write_text("# Readme\n")
    (wiki_dir / "home.md").write_text("# Home\n")

    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "index" not in orphan_pages
    assert "readme" not in orphan_pages
    assert "home" not in orphan_pages
```

`test_find_broken_citations_detects_inline_raw_reference` creates `doc.md` at `tmp_path` root. Move it into `wiki/`:

```python
def test_find_broken_citations_detects_inline_raw_reference(tmp_path: Path):
    """A [[raw/missing.pdf]] reference in page body is also flagged."""
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "doc.md"
    page.write_text(
        "---\ntitle: Doc\n---\n\nSee [[raw/missing.pdf]] for details.\n"
    )

    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/missing.pdf" in targets
```

- [ ] **Step 5: Update `vault.py` — scope rglob to `wiki_dir`**

In `src/llm_wiki/vault.py`, replace the rglob block (around line 63–82):

```python
        # Find all markdown files inside wiki_dir only.
        # raw/ companions and inbox/ plan files are intentionally excluded.
        wiki_dir = root / config.vault.wiki_dir.rstrip("/")
        wiki_dir.mkdir(parents=True, exist_ok=True)
        md_files = sorted(wiki_dir.rglob("*.md"))
        md_files = [
            f for f in md_files
            if not any(p.startswith(".") for p in f.relative_to(wiki_dir).parts)
            and not f.name.endswith(".talk.md")
        ]

        # Parse pages
        pages: dict[str, Page] = {}
        entries: list[ManifestEntry] = []
        for md_file in md_files:
            if not md_file.is_file():
                continue
            page = Page.parse(md_file)
            pages[page.path.stem] = page

            # Cluster from first subdir within wiki/, or "root" for top-level files
            rel = md_file.relative_to(wiki_dir)
            cluster = rel.parts[0] if len(rel.parts) > 1 else "root"

            entry = build_entry(page, cluster=cluster)
            entries.append(entry)
```

- [ ] **Step 6: Run the full test suite to check no regressions**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all tests PASS. If `test_scan_vault` fails with a wrong page count, check that the fixture pages are under `wiki/`.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/vault.py tests/conftest.py tests/test_vault.py tests/test_audit/test_checks.py
git commit -m "feat: scope Vault.scan to wiki/ — raw/ companions no longer appear as pages"
```

---

### Task 3: `config.py` — new maintenance fields

**Files:**
- Modify: `src/llm_wiki/config.py`

- [ ] **Step 1: Add two fields to `MaintenanceConfig`**

In `src/llm_wiki/config.py`, extend `MaintenanceConfig`:

```python
@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    adversary_unread_weight: float = 1.5      # ← new
    auditor_interval: str = "24h"
    auditor_unread_source_days: int = 30       # ← new
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600
    failure_escalation_threshold: int = 3
```

- [ ] **Step 2: Verify defaults parse correctly**

```bash
python -c "from llm_wiki.config import WikiConfig; c = WikiConfig(); print(c.maintenance.auditor_unread_source_days, c.maintenance.adversary_unread_weight)"
```

Expected: `30 1.5`

- [ ] **Step 3: Commit**

```bash
git add src/llm_wiki/config.py
git commit -m "feat: add auditor_unread_source_days and adversary_unread_weight to MaintenanceConfig"
```

---

### Task 4: `ingest/agent.py` — companion creation on ingest

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`
- Modify: `src/llm_wiki/daemon/server.py` (thread `source_type`)
- Create: `tests/test_ingest/test_ingest_companion.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ingest/test_ingest_companion.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.traverse.llm_client import LLMResponse


class MockLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def complete(self, messages, temperature=0.7, priority="query") -> LLMResponse:
        content = self._responses[self._idx]
        self._idx += 1
        return LLMResponse(content=content, tokens_used=10)


def _concept_json(concepts):
    return json.dumps({"concepts": concepts})

def _sections_json(sections):
    return json.dumps({"sections": sections})


@pytest.mark.asyncio
async def test_ingest_creates_companion_for_pdf_in_raw(tmp_path: Path):
    """wiki_ingest on a PDF in raw/ creates a companion .md with reading_status: unread."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = raw_dir / "2026-04-10-paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    llm = MockLLMClient([
        _concept_json([{"name": "pca", "title": "PCA", "passages": ["PCA reduces dims."]}]),
        _sections_json([{"heading": "Overview", "content": "PCA overview."}]),
    ])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path, source_type="paper")

    companion = raw_dir / "2026-04-10-paper.md"
    assert companion.exists()
    from llm_wiki.ingest.source_meta import read_frontmatter
    fm = read_frontmatter(companion)
    assert fm["reading_status"] == "unread"
    assert fm["source_type"] == "paper"
    # Body should contain extracted text (even if liteparse fake returns "")
    content = companion.read_text()
    assert "---" in content


@pytest.mark.asyncio
async def test_ingest_does_not_create_companion_outside_raw(tmp_path: Path):
    """wiki_ingest on a file outside raw/ never creates a companion."""
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = other / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    llm = MockLLMClient([
        _concept_json([]),
    ])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path)

    assert not (other / "paper.md").exists()


@pytest.mark.asyncio
async def test_ingest_does_not_overwrite_existing_companion(tmp_path: Path):
    """If companion already exists, ingest must not touch it."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    companion = raw_dir / "paper.md"
    companion.write_text("---\nreading_status: in_progress\n---\nPrior body.\n")

    llm = MockLLMClient([_concept_json([])])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path)

    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"
    assert "Prior body." in companion.read_text()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_ingest/test_ingest_companion.py -v 2>&1 | head -20
```

Expected: FAIL — `IngestAgent.ingest()` does not accept `source_type` keyword

- [ ] **Step 3: Update `IngestAgent.ingest()` signature and body**

In `src/llm_wiki/ingest/agent.py`, update `ingest()`:

Add `source_type: str = "paper"` to the keyword-only params:

```python
    async def ingest(
        self,
        source_path: Path,
        vault_root: Path,
        *,
        author: str = "cli",
        connection_id: str = "cli",
        write_service: "PageWriteService | None" = None,
        dry_run: bool = False,
        source_type: str = "paper",
    ) -> IngestResult:
```

At the top of `ingest()`, before the extraction call, add:

```python
        from llm_wiki.ingest.source_meta import init_companion, write_companion_body
        companion = init_companion(source_path, vault_root, source_type=source_type)
```

After the extraction call (after `result.source_chars = len(extraction.content)`), add:

```python
        if companion and extraction.success:
            write_companion_body(companion, extraction.content)
```

The full start of `ingest()` after the edit should look like:

```python
        from llm_wiki.ingest.source_meta import init_companion, write_companion_body
        companion = init_companion(source_path, vault_root, source_type=source_type)

        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")
        result = IngestResult(
            source_path=source_path,
            dry_run=dry_run,
        )

        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        extraction = await extract_text(source_path)
        if not extraction.success:
            logger.warning(
                "Extraction failed for %s: %s", source_path, extraction.error
            )
            return result

        result.source_chars = len(extraction.content)

        if companion and extraction.success:
            write_companion_body(companion, extraction.content)
```

- [ ] **Step 4: Thread `source_type` through `_handle_ingest` and update the MCP tool schema**

In `src/llm_wiki/daemon/server.py`, find `_handle_ingest`. Add `source_type` extraction and pass it to `agent.ingest()`:

```python
        source_type = request.get("source_type", "paper")
        # ...existing code...
        result = await agent.ingest(
            source_path, self._vault_root,
            author=author,
            connection_id=connection_id,
            write_service=write_service,
            dry_run=dry_run,
            source_type=source_type,
        )
```

In `src/llm_wiki/mcp/tools.py`, find `WIKI_INGEST` and add `source_type` to its `input_schema.properties`:

```python
            "source_type": {
                "type": "string",
                "enum": ["paper", "article", "transcript", "book", "other"],
                "default": "paper",
                "description": "Type of source, used to populate reading_status metadata.",
            },
```

Add it to the `properties` dict but NOT to `required` — it is optional.

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_ingest/test_ingest_companion.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full ingest test suite — no regressions**

```bash
pytest tests/test_ingest/ -q
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/ingest/agent.py src/llm_wiki/daemon/server.py tests/test_ingest/test_ingest_companion.py
git commit -m "feat: IngestAgent creates companion .md with reading_status when source is in raw/"
```

---

### Task 5: `audit/checks.py` + `audit/auditor.py` — source gap checks

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `src/llm_wiki/audit/auditor.py`
- Modify: `tests/test_audit/test_checks.py`
- Modify: `tests/test_audit/test_auditor.py`

- [ ] **Step 1: Write failing tests for `find_source_gaps`**

Append to `tests/test_audit/test_checks.py`:

```python
from llm_wiki.audit.checks import find_source_gaps
from llm_wiki.config import WikiConfig
import datetime


def _write_companion(path: Path, reading_status: str, ingested: str, source_type: str = "paper") -> None:
    path.write_text(
        f"---\nreading_status: {reading_status}\ningested: {ingested}\nsource_type: {source_type}\n---\n"
    )


def test_find_source_gaps_bare_source(tmp_path: Path):
    """A PDF in raw/ with no companion .md raises a bare-source issue."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.check == "source-gaps"
    types = {i.type for i in result.issues}
    assert "bare-source" in types


def test_find_source_gaps_no_issue_when_companion_exists(tmp_path: Path):
    """A PDF with a companion .md does not trigger bare-source."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    _write_companion(raw_dir / "paper.md", "unread", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "bare-source" not in types


def test_find_source_gaps_missing_reading_status(tmp_path: Path):
    """A .md in raw/ without reading_status raises missing-reading-status."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "article.md").write_text("---\ntitle: Article\n---\nContent.\n")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "missing-reading-status" in types


def test_find_source_gaps_unread_source_over_threshold(tmp_path: Path):
    """reading_status: unread older than threshold raises unread-source."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    old_date = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    _write_companion(raw_dir / "old-paper.md", "unread", old_date)
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "unread-source" in types


def test_find_source_gaps_unread_source_within_threshold(tmp_path: Path):
    """reading_status: unread within threshold is NOT flagged."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    recent = datetime.date.today().isoformat()
    _write_companion(raw_dir / "recent-paper.md", "unread", recent)
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "unread-source" not in types


def test_find_source_gaps_in_progress_no_plan_with_inbox(tmp_path: Path):
    """in_progress source with no matching plan file raises in-progress-no-plan."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" in types


def test_find_source_gaps_in_progress_with_matching_plan(tmp_path: Path):
    """in_progress source WITH a matching plan is not flagged."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    (inbox_dir / "2026-04-10-paper-plan.md").write_text(
        "---\nsource: raw/paper.md\nstatus: in-progress\n---\n"
    )
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" not in types


def test_find_source_gaps_in_progress_skips_if_no_inbox(tmp_path: Path):
    """in-progress-no-plan check is silently skipped if inbox/ doesn't exist."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    # No inbox/ directory
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" not in types


def test_find_source_gaps_empty_raw_dir(tmp_path: Path):
    """Empty raw/ produces no issues."""
    (tmp_path / "raw").mkdir()
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.issues == []


def test_find_source_gaps_no_raw_dir(tmp_path: Path):
    """Missing raw/ produces no issues (vault not yet initialized)."""
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.issues == []


def test_find_source_gaps_severity(tmp_path: Path):
    """bare-source, missing-reading-status, unread-source are minor; in-progress-no-plan is moderate."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (raw_dir / "bare.pdf").write_bytes(b"%PDF-1.4")
    old_date = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    _write_companion(raw_dir / "unread.md", "unread", old_date)
    _write_companion(raw_dir / "inprog.md", "in_progress", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    by_type = {i.type: i.severity for i in result.issues}
    assert by_type["bare-source"] == "minor"
    assert by_type["unread-source"] == "minor"
    assert by_type["in-progress-no-plan"] == "moderate"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_audit/test_checks.py -k "source_gaps" -v 2>&1 | head -15
```

Expected: `ImportError: cannot import name 'find_source_gaps'`

- [ ] **Step 3: Add `find_source_gaps` to `src/llm_wiki/audit/checks.py`**

First, add `import datetime` to the top-level imports at the top of `src/llm_wiki/audit/checks.py` (after `from __future__ import annotations`):

```python
import datetime
```

Then add the following imports and function at the end of the file:

```python
from llm_wiki.config import WikiConfig
from llm_wiki.ingest.source_meta import _SUPPORTED_BINARY, read_frontmatter


def _canonical_source(companion: Path, raw_dir: Path) -> str:
    """Return the canonical raw/<filename> path used in plan file source: fields.

    For a companion foo.md, checks whether a sibling binary (foo.pdf etc.) exists.
    If yes, returns raw/<binary_name>. If no, returns raw/<companion_name> (native .md source).
    """
    for ext in _SUPPORTED_BINARY:
        binary = companion.with_suffix(ext)
        if binary.exists():
            return f"raw/{binary.name}"
    return f"raw/{companion.name}"


def find_source_gaps(vault_root: Path, config: WikiConfig) -> CheckResult:
    """Scan raw/ for sources with missing or stale reading_status metadata.

    Four issue types:
      bare-source             (minor)   — binary with no companion .md
      missing-reading-status  (minor)   — .md with no reading_status field
      unread-source           (minor)   — unread for > auditor_unread_source_days
      in-progress-no-plan     (moderate)— in_progress with no matching inbox/ plan
    """
    raw_dir = vault_root / "raw"
    if not raw_dir.is_dir():
        return CheckResult(check="source-gaps", issues=[])

    threshold_days = config.maintenance.auditor_unread_source_days
    today = datetime.date.today()
    issues: list[Issue] = []

    for file in sorted(raw_dir.iterdir()):
        if not file.is_file():
            continue
        suffix = file.suffix.lower()

        # bare-source: binary with no companion .md
        if suffix in _SUPPORTED_BINARY:
            companion = file.with_suffix(".md")
            if not companion.exists():
                issues.append(Issue(
                    id=Issue.make_id("bare-source", file.name, ""),
                    type="bare-source",
                    status="open",
                    severity="minor",
                    title=f"Source has no metadata companion: raw/{file.name}",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` has no companion `.md` file. "
                        f"Run `wiki_ingest` on it, or call `wiki_source_mark` to register it."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}"},
                ))
            continue

        # Only process .md files below this point
        if suffix not in (".md", ".markdown"):
            continue

        fm = read_frontmatter(file)

        # missing-reading-status
        if "reading_status" not in fm:
            issues.append(Issue(
                id=Issue.make_id("missing-reading-status", file.name, ""),
                type="missing-reading-status",
                status="open",
                severity="minor",
                title=f"Source missing reading_status: raw/{file.name}",
                page=f"raw/{file.name}",
                body=(
                    f"`raw/{file.name}` has no `reading_status` field. "
                    f"Call `wiki_source_mark` to set it."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"path": f"raw/{file.name}"},
            ))
            continue

        reading_status = fm["reading_status"]
        ingested = fm.get("ingested")

        # unread-source: unread for longer than threshold
        if reading_status == "unread" and ingested is not None:
            if isinstance(ingested, datetime.date):
                ingested_date = ingested
            else:
                try:
                    ingested_date = datetime.date.fromisoformat(str(ingested))
                except (ValueError, TypeError):
                    ingested_date = None
            if ingested_date is not None and (today - ingested_date).days > threshold_days:
                issues.append(Issue(
                    id=Issue.make_id("unread-source", file.name, ""),
                    type="unread-source",
                    status="open",
                    severity="minor",
                    title=f"Unread source: raw/{file.name} (ingested {ingested})",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` has been `reading_status: unread` for "
                        f"{(today - ingested_date).days} days (ingested {ingested}). "
                        f"Read it or queue it for ingest."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}", "ingested": str(ingested)},
                ))

        # in-progress-no-plan: check inbox/ if it exists
        elif reading_status == "in_progress":
            inbox_dir = vault_root / "inbox"
            if not inbox_dir.is_dir():
                continue  # inbox/ not yet created — skip gracefully
            canonical = _canonical_source(file, raw_dir)
            has_plan = any(
                read_frontmatter(plan).get("source") == canonical
                for plan in inbox_dir.glob("*.md")
                if plan.is_file()
            )
            if not has_plan:
                issues.append(Issue(
                    id=Issue.make_id("in-progress-no-plan", file.name, ""),
                    type="in-progress-no-plan",
                    status="open",
                    severity="moderate",
                    title=f"In-progress source has no plan file: raw/{file.name}",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` is `reading_status: in_progress` but no "
                        f"plan file in `inbox/` has `source: {canonical}`. "
                        f"Create an inbox plan file or mark the source as read."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}", "canonical_source": canonical},
                ))

    return CheckResult(check="source-gaps", issues=issues)
```

- [ ] **Step 4: Run source-gaps tests**

```bash
pytest tests/test_audit/test_checks.py -k "source_gaps" -v
```

Expected: all PASS

- [ ] **Step 5: Update `Auditor` to accept `config` and call `find_source_gaps`**

In `src/llm_wiki/audit/auditor.py`:

```python
from llm_wiki.audit.checks import (
    find_broken_citations,
    find_broken_wikilinks,
    find_missing_markers,
    find_orphans,
    find_source_gaps,
)
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault


class Auditor:
    """Runs all structural checks and routes results through the issue queue."""

    def __init__(
        self,
        vault: Vault,
        queue: IssueQueue,
        vault_root: Path,
        config: WikiConfig | None = None,
    ) -> None:
        self._vault = vault
        self._queue = queue
        self._vault_root = vault_root
        self._config = config or WikiConfig()

    def audit(self) -> AuditReport:
        """Run every check and file each issue idempotently."""
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_broken_citations(self._vault, self._vault_root),
            find_source_gaps(self._vault_root, self._config),
        ]
        # ... rest of method unchanged ...
```

- [ ] **Step 6: Update `test_audit_empty_vault` — check count is now 5**

In `tests/test_audit/test_auditor.py`, find `test_audit_empty_vault` and update:

```python
def test_audit_empty_vault(tmp_path: Path):
    """An empty vault produces an empty report without raising."""
    (tmp_path / "wiki").mkdir()
    queue = IssueQueue(tmp_path)
    auditor = Auditor(Vault.scan(tmp_path), queue, tmp_path)
    report = auditor.audit()
    assert report.total_issues == 0
    assert report.total_checks_run == 5  # was 4
```

Also update `test_audit_runs_all_checks_on_sample_vault`:

```python
    assert report.total_checks_run == 5  # was 4
```

- [ ] **Step 7: Run audit tests**

```bash
pytest tests/test_audit/ -q
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/audit/checks.py src/llm_wiki/audit/auditor.py \
        tests/test_audit/test_checks.py tests/test_audit/test_auditor.py
git commit -m "feat: auditor source gap checks (bare-source, missing-reading-status, unread-source, in-progress-no-plan)"
```

---

### Task 6: `daemon/server.py` + `mcp/tools.py` — `wiki_source_mark`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `src/llm_wiki/mcp/tools.py`
- Create: `tests/test_mcp/test_source_mark.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp/test_source_mark.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_raw_companion(vault_root: Path, name: str, status: str) -> Path:
    raw_dir = vault_root / "raw"
    raw_dir.mkdir(exist_ok=True)
    companion = raw_dir / name
    companion.write_text(
        f"---\nreading_status: {status}\ningested: 2026-04-10\nsource_type: paper\n---\n"
    )
    return companion


@pytest.fixture
def git_vault(tmp_path: Path):
    """Minimal git-initialized vault for commit tests."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").mkdir()
    # Initial commit so HEAD exists
    (tmp_path / "README.md").write_text("vault\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_source_mark_updates_reading_status(git_vault: Path):
    """source-mark route updates reading_status in companion frontmatter."""
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    companion = _make_raw_companion(git_vault, "paper.md", "unread")
    subprocess.run(["git", "add", "."], cwd=git_vault, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add companion"], cwd=git_vault, check=True, capture_output=True)

    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = git_vault
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(companion),
        "status": "in_progress",
        "author": "test-user",
    })

    assert response["status"] == "ok"
    assert response["new_status"] == "in_progress"
    assert response["old_status"] == "unread"

    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"


@pytest.mark.asyncio
async def test_source_mark_rejects_path_outside_raw(tmp_path: Path):
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = tmp_path
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(tmp_path / "wiki" / "page.md"),
        "status": "read",
        "author": "test",
    })
    assert response["status"] == "error"
    assert "raw/" in response["message"]


@pytest.mark.asyncio
async def test_source_mark_rejects_invalid_status(tmp_path: Path):
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    (tmp_path / "raw").mkdir()
    companion = _make_raw_companion(tmp_path, "paper.md", "unread")
    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = tmp_path
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(companion),
        "status": "maybe",
        "author": "test",
    })
    assert response["status"] == "error"
    assert "unread|in_progress|read" in response["message"]
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_mcp/test_source_mark.py -v 2>&1 | head -20
```

Expected: FAIL — `AttributeError: '_handle_source_mark'`

- [ ] **Step 3: Add `_handle_source_mark` to `DaemonServer`**

In `src/llm_wiki/daemon/server.py`, add to the dispatch match statement:

```python
            case "source-mark":
                return await self._handle_source_mark(request)
```

Add the handler method:

```python
    async def _handle_source_mark(self, request: dict) -> dict:
        source_path_str = request.get("source_path")
        status = request.get("status")
        author = request.get("author", "cli")

        if not source_path_str:
            return {"status": "error", "message": "Missing required field: source_path"}
        if status not in ("unread", "in_progress", "read"):
            return {"status": "error", "message": "status must be unread|in_progress|read"}

        path = Path(source_path_str)
        raw_dir = self._vault_root / "raw"
        try:
            path.relative_to(raw_dir)
        except ValueError:
            return {"status": "error", "message": "source_path must be under raw/"}

        if not path.exists():
            return {"status": "error", "message": f"Source not found: {source_path_str}"}

        from llm_wiki.ingest.source_meta import read_frontmatter, write_frontmatter

        old_status = read_frontmatter(path).get("reading_status", "unknown")
        write_frontmatter(path, {"reading_status": status})

        # Direct git commit — outside the session/journal pipeline
        rel_path = str(path.relative_to(self._vault_root))
        commit_message = (
            f"meta: mark {path.name} {status}\n\n"
            f"Source-Status: {old_status}\u2192{status}\n"
            f"Author: {author}"
        )
        async with self._commit_lock:
            import subprocess
            subprocess.run(
                ["git", "add", rel_path],
                cwd=self._vault_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=self._vault_root,
                check=True,
                capture_output=True,
            )

        return {
            "status": "ok",
            "path": source_path_str,
            "old_status": old_status,
            "new_status": status,
        }
```

- [ ] **Step 4: Add `WIKI_SOURCE_MARK` to `mcp/tools.py`**

In `src/llm_wiki/mcp/tools.py`, add handler and definition. Find `WIKI_TOOLS` list at the bottom of the file and add the new tool.

Handler:

```python
async def handle_wiki_source_mark(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "source-mark",
        "source_path": args["source_path"],
        "status": args["status"],
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))
```

Definition:

```python
WIKI_SOURCE_MARK = ToolDefinition(
    name="wiki_source_mark",
    description=(
        "Update the reading_status of a source file in raw/. Call this to track "
        "your engagement with a source: 'in_progress' when you start reading it, "
        "'read' when you finish. The change is committed to git with a "
        "Source-Status trailer for audit. Valid statuses: unread, in_progress, read.\n\n"
        "Skill protocol:\n"
        "- Brief mode start → in_progress\n"
        "- Brief mode complete (no deep session) → read\n"
        "- Deep mode session start → in_progress\n"
        "- Deep mode plan complete → read"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to the source file or its companion .md (must be under raw/)",
            },
            "status": {
                "type": "string",
                "enum": ["unread", "in_progress", "read"],
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier (e.g. 'claude-researcher')",
            },
        },
        "required": ["source_path", "status", "author"],
    },
    handler=handle_wiki_source_mark,
)
```

Add `WIKI_SOURCE_MARK` to the `WIKI_TOOLS` list at the bottom of `tools.py`.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_mcp/test_source_mark.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/server.py src/llm_wiki/mcp/tools.py tests/test_mcp/test_source_mark.py
git commit -m "feat: wiki_source_mark MCP tool — update reading_status with git trailer"
```

---

### Task 7: `adversary/sampling.py` + `adversary/agent.py` — unread source weight

**Files:**
- Modify: `src/llm_wiki/adversary/sampling.py`
- Modify: `src/llm_wiki/adversary/agent.py`
- Modify: `tests/test_adversary/test_sampling.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_adversary/test_sampling.py`:

```python
def test_sample_claims_unread_source_weight():
    """Claims from unread sources are picked more often when unread_weight > 1."""
    now = datetime.datetime(2026, 4, 10, tzinfo=datetime.timezone.utc)
    unread = [_claim(f"u{i}", i) for i in range(10)]
    read_claims = [_claim(f"r{i}", i) for i in range(10)]

    # Give all entries equal authority and freshness so only unread_weight differs
    entries = {
        **{f"u{i}": _entry(f"u{i}", authority=0.5) for i in range(10)},
        **{f"r{i}": _entry(f"r{i}", authority=0.5) for i in range(10)},
    }
    # Unread source paths match claim citations: "raw/u0.pdf" etc.
    unread_sources = {f"raw/u{i}.pdf" for i in range(10)}

    unread_picked = 0
    for seed in range(50):
        sample = sample_claims(
            unread + read_claims, entries, n=2,
            rng=Random(seed), now=now,
            unread_sources=unread_sources,
            unread_weight=3.0,
        )
        unread_picked += sum(1 for c in sample if c.page.startswith("u"))

    assert unread_picked > 60, f"unread claims should be favored, got {unread_picked}/100"


def test_sample_claims_unread_weight_none_has_no_effect():
    """Passing unread_sources=None must not change sampling behavior."""
    now = datetime.datetime(2026, 4, 10, tzinfo=datetime.timezone.utc)
    claims = [_claim(f"p{i}", i) for i in range(10)]
    entries = {f"p{i}": _entry(f"p{i}") for i in range(10)}
    a = sample_claims(claims, entries, n=5, rng=Random(7), now=now, unread_sources=None)
    b = sample_claims(claims, entries, n=5, rng=Random(7), now=now)
    assert [c.id for c in a] == [c.id for c in b]
```

Note: `_claim` in the existing test file produces `citation=f"raw/{page}.pdf"` — this matches the `unread_sources` set format.

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_adversary/test_sampling.py -k "unread" -v 2>&1 | head -15
```

Expected: `TypeError: sample_claims() got an unexpected keyword argument 'unread_sources'`

- [ ] **Step 3: Update `sample_claims` in `src/llm_wiki/adversary/sampling.py`**

```python
def sample_claims(
    claims: list[Claim],
    entries: dict[str, "ManifestEntry"],
    n: int,
    rng: Random,
    now: datetime.datetime,
    unread_sources: "set[str] | None" = None,
    unread_weight: float = 1.5,
) -> list[Claim]:
    """Weighted sample without replacement using the Efraimidis-Spirakis trick.

    weight(claim) = age_factor(claim_page) * (1.5 - authority(claim_page))
                  * unread_weight  [if claim.citation is in unread_sources]
    """
    if not claims or n <= 0:
        return []

    keyed: list[tuple[float, Claim]] = []
    for claim in claims:
        entry = entries.get(claim.page)
        if entry is not None:
            authority = entry.authority
            last_corr = entry.last_corroborated
        else:
            authority = 0.0
            last_corr = None
        weight = age_factor(last_corr, now) * (1.5 - authority)
        if unread_sources and claim.citation in unread_sources:
            weight *= unread_weight
        if weight <= 0:
            weight = 1e-9
        u = rng.random()
        if u <= 0:
            u = 1e-9
        key = -math.log(u) / weight
        keyed.append((key, claim))

    keyed.sort(key=lambda kv: kv[0])
    return [c for _, c in keyed[:n]]
```

- [ ] **Step 4: Update `AdversaryAgent.run()` to build `unread_sources` and pass it**

In `src/llm_wiki/adversary/agent.py`, modify the `run()` method. After extracting claims and before calling `sample_claims`, build the unread sources set:

```python
        # 2. Build unread sources set for adversary upweighting
        unread_sources: set[str] = set()
        raw_dir = self._vault_root / "raw"
        if raw_dir.is_dir():
            from llm_wiki.ingest.source_meta import read_frontmatter
            for md_file in raw_dir.glob("*.md"):
                fm = read_frontmatter(md_file)
                if fm.get("reading_status") == "unread":
                    # Add both the companion path and the likely binary path
                    unread_sources.add(f"raw/{md_file.name}")
                    for ext in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"):
                        binary = md_file.with_suffix(ext)
                        if binary.exists():
                            unread_sources.add(f"raw/{binary.name}")

        # 3. Sample
        n = self._config.maintenance.adversary_claims_per_run
        unread_weight = self._config.maintenance.adversary_unread_weight
        now = datetime.datetime.now(datetime.timezone.utc)
        sampled = sample_claims(
            all_claims, entries, n=n, rng=self._rng, now=now,
            unread_sources=unread_sources,
            unread_weight=unread_weight,
        )
```

- [ ] **Step 5: Run sampling tests**

```bash
pytest tests/test_adversary/test_sampling.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/adversary/sampling.py src/llm_wiki/adversary/agent.py \
        tests/test_adversary/test_sampling.py
git commit -m "feat: adversary upweights claims from unread sources"
```

---

### Task 8: Skill documentation

**Files:**
- Modify: `skills/llm-wiki/ingest.md`

- [ ] **Step 1: Check the file exists**

```bash
ls skills/llm-wiki/ingest.md
```

If the file doesn't exist yet, create it with the content below. If it exists, add the `wiki_source_mark` protocol as a new section.

- [ ] **Step 2: Add `wiki_source_mark` call protocol**

Find or create a section called `## Reading Status Protocol`. Add:

```markdown
## Reading Status Protocol

Call `wiki_source_mark` to track your engagement with a source. The daemon
commits the change to git with a `Source-Status:` trailer for audit. You
never edit frontmatter manually.

| Moment | Call |
|---|---|
| Brief mode — start reading | `wiki_source_mark(source_path, "in_progress", author)` |
| Brief mode — done, no deep session planned | `wiki_source_mark(source_path, "read", author)` |
| Deep mode — session start | `wiki_source_mark(source_path, "in_progress", author)` |
| Deep mode — plan file complete | `wiki_source_mark(source_path, "read", author)` |
| Autonomous ingest | Do not call `wiki_source_mark` — autonomous ingest sets `unread` only |

`source_path` is the path to either the binary source (`raw/foo.pdf`) or
its companion (`raw/foo.md`) — both are accepted by the daemon.
```

- [ ] **Step 3: Commit**

```bash
git add skills/llm-wiki/ingest.md
git commit -m "docs: document wiki_source_mark call protocol in ingest skill"
```

---

### Task 9: Full test run + final verification

- [ ] **Step 1: Run the complete test suite**

```bash
pytest tests/ -q 2>&1 | tail -30
```

Expected: all tests PASS, no regressions from the `conftest.py` migration.

- [ ] **Step 2: Smoke-check `find_source_gaps` issue types in isolation**

```bash
python -c "
from pathlib import Path
import tempfile, datetime
from llm_wiki.config import WikiConfig
from llm_wiki.audit.checks import find_source_gaps

with tempfile.TemporaryDirectory() as d:
    vr = Path(d)
    (vr / 'raw').mkdir()
    (vr / 'raw' / 'bare.pdf').write_bytes(b'%PDF-1.4')
    r = find_source_gaps(vr, WikiConfig())
    print('bare-source check:', [i.type for i in r.issues])
"
```

Expected: `bare-source check: ['bare-source']`

- [ ] **Step 3: Verify `WIKI_SOURCE_MARK` appears in the tool list**

```bash
python -c "
from llm_wiki.mcp.tools import WIKI_TOOLS
print([t.name for t in WIKI_TOOLS])
"
```

Expected: `wiki_source_mark` is in the list.

- [ ] **Step 4: Final commit if any loose ends**

```bash
git status
```

If clean, nothing to do. If there are uncommitted changes, investigate — do not commit blindly.
