# Ingest Proposals Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-pass direct-write ingest with a wiki-aware multi-chunk proposal pipeline that stages proposed page changes in `inbox/proposals/` for auditor review, with strict wikilink prompt engineering and grounding verification.

**Architecture:** Five new modules (`chunker`, `grounding`, `proposals`, extended `prompts`, extended `checks`) plus an `IngestAgent` refactor. Bug fixes ship first (Tasks 1–5) and are independent of the pipeline tasks (6–14). The auditor gains a `find_pending_proposals` check that auto-merges clean updates and surfaces creates/failures as issues.

**Tech Stack:** Python 3.11+, `difflib`/bigram F1 for grounding, `yaml`/`json` for proposals, existing `LLMClient`, `IssueQueue`, `Auditor`, `page_writer`. No new runtime deps.

---

## File Map

| File | Change |
|---|---|
| `src/llm_wiki/config.py` | Add 6 fields to `IngestConfig` |
| `src/llm_wiki/ingest/chunker.py` | **New** — `chunk_text()` |
| `src/llm_wiki/ingest/grounding.py` | **New** — `GroundingResult`, `ground_passage()`, `_bigram_f1()` |
| `src/llm_wiki/ingest/proposals.py` | **New** — `Proposal`, `ProposalPassage`, `write_proposal()`, `read_proposal_meta()`, `read_proposal_body()`, `update_proposal_status()`, `list_pending_proposals()`, `find_wiki_page()` |
| `src/llm_wiki/ingest/prompts.py` | Add `compose_overview_messages`, `compose_passage_collection_messages`, `compose_content_synthesis_messages`, `parse_overview_extraction`, `parse_passage_collection`, `parse_content_synthesis` |
| `src/llm_wiki/ingest/agent.py` | Add `action`/`section_names`/`cluster` to `ConceptPlan`; fix `_sections_to_body`; add `ingest_as_proposals()` |
| `src/llm_wiki/ingest/page_writer.py` | Add `patch_token_estimates()`, call in `_create_page` and `_append_source` |
| `src/llm_wiki/audit/checks.py` | Fix `find_broken_citations`; add `find_pending_proposals()` |
| `src/llm_wiki/audit/auditor.py` | Add `find_pending_proposals` to `audit()` |
| `src/llm_wiki/cli/main.py` | Auto-copy source to `raw/`; add `proposals` subcommand |
| `tests/test_ingest/test_chunker.py` | **New** |
| `tests/test_ingest/test_grounding.py` | **New** |
| `tests/test_ingest/test_proposals.py` | **New** |
| `tests/test_ingest/test_prompts.py` | Extend with new prompt tests |
| `tests/test_audit/test_checks.py` | Extend with broken-citation and proposal tests |

---

### Task 1: Extend `IngestConfig`

**Files:**
- Modify: `src/llm_wiki/config.py`
- Modify: `tests/test_config.py` (or create if absent)

- [ ] **Step 1: Write failing test**

```python
# tests/test_config.py  (add to existing file or create)
from llm_wiki.config import IngestConfig, WikiConfig
from pathlib import Path
import tempfile, yaml

def test_ingest_config_new_defaults():
    cfg = IngestConfig()
    assert cfg.chunk_tokens == 6000
    assert cfg.chunk_overlap == 0.15
    assert cfg.max_passages_per_concept == 6
    assert cfg.grounding_auto_merge == 0.75
    assert cfg.grounding_flag == 0.50
    assert cfg.auto_copy_to_raw is True

def test_ingest_config_yaml_roundtrip():
    data = {"ingest": {"chunk_tokens": 4000, "auto_copy_to_raw": False}}
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        p = Path(f.name)
    cfg = WikiConfig.load(p)
    assert cfg.ingest.chunk_tokens == 4000
    assert cfg.ingest.auto_copy_to_raw is False
    assert cfg.ingest.grounding_auto_merge == 0.75  # default preserved
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_config.py::test_ingest_config_new_defaults -v
```

- [ ] **Step 3: Add fields to `IngestConfig`**

In `src/llm_wiki/config.py`, replace the `IngestConfig` dataclass:

```python
@dataclass
class IngestConfig:
    pdf_extractor: str = "pdftotext"              # pdftotext | local-ocr | marker | nougat
    local_ocr_endpoint: str = "http://localhost:8006/v1"
    local_ocr_model: str = "qianfan-ocr"
    chunk_tokens: int = 6000
    chunk_overlap: float = 0.15
    max_passages_per_concept: int = 6
    grounding_auto_merge: float = 0.75
    grounding_flag: float = 0.50
    auto_copy_to_raw: bool = True
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_config.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: extend IngestConfig with proposal pipeline fields"
```

---

### Task 2: Fix `find_broken_citations` — bare filename citations

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `tests/test_audit/test_checks.py` (create if absent — check `tests/test_audit/`)

- [ ] **Step 1: Write failing test**

```python
# tests/test_audit/test_checks.py
from pathlib import Path
import tempfile, textwrap
from llm_wiki.audit.checks import find_broken_citations
from unittest.mock import MagicMock

def _make_vault(pages: dict[str, str]) -> tuple[MagicMock, Path]:
    """Make a minimal mock Vault + temp dir with real page files."""
    tmpdir = Path(tempfile.mkdtemp())
    wiki = tmpdir / "wiki"
    wiki.mkdir()
    entries = {}
    for name, content in pages.items():
        p = wiki / f"{name}.md"
        p.write_text(content, encoding="utf-8")
        from llm_wiki.page import Page
        page = Page.parse(p)
        entry = MagicMock()
        entry.links_to = page.wikilinks
        entry.links_from = []
        entries[name] = entry
    vault = MagicMock()
    vault.manifest_entries.return_value = entries
    vault.read_page = lambda name: Page.parse(wiki / f"{name}.md")
    return vault, tmpdir

def test_find_broken_citations_catches_bare_filename():
    content = textwrap.dedent("""\
        ---
        title: Test
        source: '[[boltz2.pdf]]'
        ---
        Some content.
    """)
    vault, vault_root = _make_vault({"test-page": content})
    result = find_broken_citations(vault, vault_root)
    assert any("boltz2.pdf" in i.title for i in result.issues)

def test_find_broken_citations_allows_raw_prefix():
    content = textwrap.dedent("""\
        ---
        title: Test
        source: '[[raw/boltz2.pdf]]'
        ---
        Some content.
    """)
    vault, vault_root = _make_vault({"test-page": content})
    # Create the raw file so it's not missing
    (vault_root / "raw").mkdir()
    (vault_root / "raw" / "boltz2.pdf").write_bytes(b"")
    result = find_broken_citations(vault, vault_root)
    assert not any("boltz2.pdf" in i.title for i in result.issues)
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_audit/test_checks.py::test_find_broken_citations_catches_bare_filename -v
```

- [ ] **Step 3: Add bare-filename detection to `find_broken_citations`**

In `src/llm_wiki/audit/checks.py`, add after the existing imports:

```python
# Matches [[something.pdf]] or [[file.docx]] etc. WITHOUT a raw/ prefix
_BARE_BINARY_CITATION_RE = re.compile(
    r"\[\[([^\]/|]+\.(?:pdf|docx|png|jpg|jpeg|gif|bmp|tiff))(?:\|[^\]]+)?\]\]",
    re.IGNORECASE,
)
```

Inside `find_broken_citations`, after the existing `source_field` block, add:

```python
        # Also flag [[filename.pdf]] citations that lack the raw/ prefix
        source_field = page.frontmatter.get("source")
        if isinstance(source_field, str):
            for match in _BARE_BINARY_CITATION_RE.finditer(source_field):
                inner = match.group(1)
                if inner.startswith("raw/"):
                    continue  # already handled by the existing check
                issues.append(
                    Issue(
                        id=Issue.make_id("broken-citation", name, f"bare:{inner}"),
                        type="broken-citation",
                        status="open",
                        severity="moderate",
                        title=f"Source citation missing raw/ prefix: [[{inner}]]",
                        page=name,
                        body=(
                            f"The page [[{name}]] has `source: [[{inner}]]` but sources "
                            f"must live under `raw/`. Move the file: "
                            f"`cp {inner} <vault>/raw/{inner}` then update the citation "
                            f"to `source: [[raw/{inner}]]`."
                        ),
                        created=Issue.now_iso(),
                        detected_by="auditor",
                        metadata={"target": inner, "bare": True},
                    )
                )
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_audit/test_checks.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/audit/checks.py tests/test_audit/test_checks.py
git commit -m "fix: flag non-raw/ prefixed source citations in find_broken_citations"
```

---

### Task 3: Fix `_sections_to_body` — add `%% section %%` markers

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`
- Modify: `tests/test_ingest/test_page_writer.py` (add test) or create `tests/test_ingest/test_agent.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_ingest/test_page_writer.py
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.ingest.page_writer import PageSection

def test_sections_to_body_includes_markers():
    sections = [
        PageSection(name="overview", heading="Overview", content="Boltz-2 is a model."),
        PageSection(name="performance", heading="Performance", content="It achieves SOTA."),
    ]
    body = IngestAgent._sections_to_body(sections)
    assert "%% section: overview %%" in body
    assert "%% section: performance %%" in body
    assert "## Overview" in body
    assert "## Performance" in body
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_ingest/test_page_writer.py::test_sections_to_body_includes_markers -v
```

- [ ] **Step 3: Fix `_sections_to_body` in `src/llm_wiki/ingest/agent.py`**

Replace the existing `_sections_to_body` static method:

```python
@staticmethod
def _sections_to_body(sections: list) -> str:
    parts = []
    for s in sections:
        parts.append(f"%% section: {s.name} %%")
        parts.append(f"## {s.heading}")
        parts.append("")
        parts.append(s.content)
        parts.append("")
    return "\n".join(parts).strip()
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/ -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/agent.py tests/test_ingest/test_page_writer.py
git commit -m "fix: _sections_to_body now emits %% section %% markers"
```

---

### Task 4: Fix source outside vault — auto-copy to `raw/`

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `tests/test_cli/` (add test or create `tests/test_cli/test_ingest_copy.py`)

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli/test_ingest_copy.py
import shutil, tempfile
from pathlib import Path
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from llm_wiki.cli.main import cli

def test_ingest_copies_source_to_raw(tmp_path):
    # Set up a minimal vault
    vault = tmp_path / "vault"
    (vault / "raw").mkdir(parents=True)
    (vault / "wiki").mkdir()
    (vault / "schema").mkdir()
    # Source file outside vault
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")

    runner = CliRunner()
    mock_client = MagicMock()
    mock_client.is_running.return_value = True
    mock_client.request.return_value = {
        "status": "ok", "concepts_found": 0, "created": [], "updated": [],
    }
    with patch("llm_wiki.cli.main._get_client", return_value=mock_client):
        result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault)])

    # The source should have been copied to raw/
    assert (vault / "raw" / "paper.pdf").exists()
    # The request should use the raw/ path
    call_args = mock_client.request.call_args[0][0]
    assert "raw/paper.pdf" in call_args["source_path"] or \
           call_args["source_path"].endswith("raw/paper.pdf")
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_cli/test_ingest_copy.py -v
```

- [ ] **Step 3: Add auto-copy logic to `ingest` command in `src/llm_wiki/cli/main.py`**

At the top of the `ingest` command body, before the client request, add:

```python
@cli.command()
@click.argument("source_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, default=False,
    help="Preview: run extraction and generation but skip all writes.",
)
def ingest(source_path: Path, vault_path: Path, dry_run: bool) -> None:
    """Ingest a source document — extracts concepts and creates wiki pages."""
    import shutil as _shutil
    import uuid as _uuid

    vault_path = vault_path.resolve()
    source_path = source_path.resolve()

    # Auto-copy source to raw/ if it lives outside the vault
    try:
        source_path.relative_to(vault_path)
    except ValueError:
        from llm_wiki.config import WikiConfig
        cfg_path = vault_path / "schema" / "config.yaml"
        cfg = WikiConfig.load(cfg_path)
        if not cfg.ingest.auto_copy_to_raw:
            raise click.ClickException(
                f"Source must be inside the vault. Move it first:\n"
                f"  cp {source_path} {vault_path / 'raw' / source_path.name}\n"
                f"Or set auto_copy_to_raw: true in schema/config.yaml"
            )
        raw_dir = vault_path / cfg.vault.raw_dir.rstrip("/")
        raw_dir.mkdir(parents=True, exist_ok=True)
        dest = raw_dir / source_path.name
        if dest.exists():
            click.echo(f"Note: raw/{source_path.name} already exists — skipping copy.", err=True)
        else:
            _shutil.copy2(source_path, dest)
            click.echo(f"Copied to raw/{source_path.name}", err=True)
        source_path = dest

    client = _get_client(vault_path)
    # ... rest of existing ingest command body unchanged ...
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_cli/ -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/cli/main.py tests/test_cli/test_ingest_copy.py
git commit -m "feat: auto-copy ingest source to raw/ when outside vault"
```

---

### Task 5: Token estimate patching in `page_writer`

**Files:**
- Modify: `src/llm_wiki/ingest/page_writer.py`
- Modify: `tests/test_ingest/test_page_writer.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_ingest/test_page_writer.py
import tempfile
from pathlib import Path
from llm_wiki.ingest.page_writer import write_page, PageSection, patch_token_estimates

def test_patch_token_estimates_adds_counts(tmp_path):
    # Write a page without token counts
    content = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nSome content here.\n\n"
        "%% section: methods %%\n## Methods\n\nMore content.\n"
    )
    p = tmp_path / "test.md"
    p.write_text(content, encoding="utf-8")
    patch_token_estimates(p)
    patched = p.read_text(encoding="utf-8")
    assert ", tokens:" in patched
    assert "%% section: overview, tokens:" in patched
    assert "%% section: methods, tokens:" in patched

def test_write_page_includes_token_estimates(tmp_path):
    sections = [PageSection(name="overview", heading="Overview", content="Some text content.")]
    write_page(tmp_path, "my-concept", "My Concept", sections, "raw/paper.pdf")
    raw = (tmp_path / "my-concept.md").read_text()
    assert ", tokens:" in raw
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_ingest/test_page_writer.py::test_patch_token_estimates_adds_counts -v
```

- [ ] **Step 3: Add `patch_token_estimates` to `src/llm_wiki/ingest/page_writer.py`**

Add after the existing imports:

```python
import re as _re_tokens


def patch_token_estimates(page_path: Path) -> None:
    """Rewrite %% section: name %% markers to include token counts.

    Idempotent — safe to call repeatedly. Reads the file, rewrites only if
    any marker changed. Does not touch frontmatter or section content.
    """
    raw = page_path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    # Find all section marker positions
    boundaries: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _re_tokens.match(
            r"^%%\s*section:\s*([^,]+?)(?:\s*,\s*tokens:\s*\d+)?\s*%%\s*$",
            line.strip(),
        )
        if m:
            boundaries.append((i, m.group(1).strip()))

    if not boundaries:
        return

    new_lines = list(lines)
    changed = False
    for idx, (line_no, name) in enumerate(boundaries):
        next_line = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        content = "".join(lines[line_no + 1 : next_line]).strip()
        tokens = count_tokens(content)
        new_marker = f"%% section: {name}, tokens: {tokens} %%\n"
        if new_lines[line_no] != new_marker:
            new_lines[line_no] = new_marker
            changed = True

    if changed:
        page_path.write_text("".join(new_lines), encoding="utf-8")
```

Then call it at the end of `_create_page` and `_append_source`:

```python
def _create_page(...) -> WrittenPage:
    # ... existing body ...
    page_path.write_text(frontmatter + "\n\n" + "\n".join(body_parts).strip() + "\n")
    patch_token_estimates(page_path)   # ← add this line
    return WrittenPage(path=page_path, was_update=False)

def _append_source(...) -> WrittenPage:
    # ... existing body ...
    page_path.write_text(existing.rstrip() + "\n" + "\n".join(appended_parts))
    patch_token_estimates(page_path)   # ← add this line
    return WrittenPage(path=page_path, was_update=True)
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/test_page_writer.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/page_writer.py tests/test_ingest/test_page_writer.py
git commit -m "feat: patch token estimates into section markers on write"
```

---

### Task 6: Chunker module

**Files:**
- Create: `src/llm_wiki/ingest/chunker.py`
- Create: `tests/test_ingest/test_chunker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_chunker.py
from llm_wiki.ingest.chunker import chunk_text

def test_chunk_text_empty():
    assert chunk_text("", chunk_tokens=100) == []

def test_chunk_text_short_fits_one_chunk():
    text = "Hello world.\n\nSecond paragraph."
    chunks = chunk_text(text, chunk_tokens=1000)
    assert len(chunks) == 1
    assert "Hello world" in chunks[0]
    assert "Second paragraph" in chunks[0]

def test_chunk_text_splits_on_paragraphs():
    # 3 large paragraphs, each ~50 words → force split at chunk_tokens=30
    para = "word " * 50
    text = (para.strip() + "\n\n") * 3
    chunks = chunk_text(text, chunk_tokens=30, overlap=0.0)
    assert len(chunks) >= 2

def test_chunk_text_overlap_repeats_content():
    para = "word " * 40
    text = (para.strip() + "\n\n") * 4
    chunks = chunk_text(text, chunk_tokens=50, overlap=0.3)
    if len(chunks) >= 2:
        # Last paragraph of chunk N should appear in chunk N+1
        last_para_of_chunk0 = chunks[0].split("\n\n")[-1].strip()
        assert last_para_of_chunk0 in chunks[1]

def test_chunk_text_single_huge_paragraph():
    # A single paragraph larger than chunk_tokens is kept as-is (not split mid-para)
    big = "word " * 500
    chunks = chunk_text(big.strip(), chunk_tokens=100, overlap=0.0)
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_ingest/test_chunker.py -v
```

- [ ] **Step 3: Create `src/llm_wiki/ingest/chunker.py`**

```python
from __future__ import annotations

from llm_wiki.tokens import count_tokens


def chunk_text(
    text: str,
    chunk_tokens: int = 6000,
    overlap: float = 0.15,
) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_tokens tokens.

    Splits on paragraph boundaries (double newlines) to avoid mid-sentence
    cuts. A single paragraph larger than chunk_tokens is kept whole rather
    than split mid-word.

    Args:
        text:         Source text to chunk.
        chunk_tokens: Target token count per chunk.
        overlap:      Fraction of chunk_tokens to repeat between adjacent chunks.

    Returns:
        List of text chunks, possibly empty for blank input.
    """
    if not text.strip():
        return []

    overlap_tokens = int(chunk_tokens * overlap)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > chunk_tokens and current:
            chunks.append("\n\n".join(current))
            # Trim from the front until we are at or below overlap_tokens
            while current and (current_tokens - count_tokens(current[0])) >= overlap_tokens:
                removed = current.pop(0)
                current_tokens -= count_tokens(removed)
        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/test_chunker.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/chunker.py tests/test_ingest/test_chunker.py
git commit -m "feat: add chunker module for overlapping document splitting"
```

---

### Task 7: Grounding module

**Files:**
- Create: `src/llm_wiki/ingest/grounding.py`
- Create: `tests/test_ingest/test_grounding.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_grounding.py
from llm_wiki.ingest.grounding import ground_passage, _bigram_f1, _is_visual_content, GroundingResult

def test_bigram_f1_identical():
    assert _bigram_f1("the cat sat on the mat", "the cat sat on the mat") == 1.0

def test_bigram_f1_no_overlap():
    assert _bigram_f1("hello world foo", "bar baz qux quux") == 0.0

def test_bigram_f1_partial():
    score = _bigram_f1("the cat sat", "the cat ran away")
    assert 0.0 < score < 1.0

def test_bigram_f1_empty():
    assert _bigram_f1("", "some text") == 0.0

def test_ground_passage_high_score_for_exact_match():
    source = "Boltz-2 achieves state-of-the-art performance on binding affinity prediction."
    result = ground_passage(source, source)
    assert result.score == 1.0
    assert result.verifiable is True
    assert result.ocr_sourced is False

def test_ground_passage_low_score_for_mismatch():
    passage = "Boltz-2 achieves state-of-the-art performance."
    source = "Completely unrelated text about something else entirely."
    result = ground_passage(passage, source)
    assert result.score < 0.3

def test_ground_passage_visual_content_unverifiable():
    passage = "See Figure 3 for the architecture diagram."
    result = ground_passage(passage, "some source text")
    assert result.verifiable is False
    assert result.score == 0.0

def test_ground_passage_equation_unverifiable():
    passage = "The loss is defined as L = Σ α_i x_i."
    result = ground_passage(passage, "some source text")
    assert result.verifiable is False

def test_ground_passage_ocr_sourced_flag():
    result = ground_passage("some text", "some text here", ocr_sourced=True)
    assert result.ocr_sourced is True

def test_is_visual_content_figure():
    assert _is_visual_content("See Figure 1 for details.")
    assert _is_visual_content("Shown in Fig. 3.")
    assert not _is_visual_content("The model achieves high accuracy.")
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_ingest/test_grounding.py -v
```

- [ ] **Step 3: Create `src/llm_wiki/ingest/grounding.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass


_VISUAL_RE = re.compile(
    r"\b(figure|fig\.|equation|eq\.|table|algorithm|listing)\s*\d",
    re.IGNORECASE,
)
_FORMULA_CHARS_RE = re.compile(r"[α-ωΑ-Ω∀∃∈∉⊂⊃∪∩∫∑∏±×÷≤≥≠≈∞Σσμλβγδεζηθικνξπρτυφχψ]")


@dataclass
class GroundingResult:
    """Grounding verification result for one passage."""
    passage: str
    score: float        # bigram F1 vs source text; 0.0 if unverifiable
    method: str = "ngram"
    verifiable: bool = True
    ocr_sourced: bool = False


def ground_passage(
    passage: str,
    source_text: str,
    ocr_sourced: bool = False,
) -> GroundingResult:
    """Compute bigram F1 between passage and source_text.

    Visual content (figures, equations, formulae) is marked unverifiable
    and assigned score 0.0 — the auditor treats these with a relaxed threshold.
    """
    if _is_visual_content(passage):
        return GroundingResult(
            passage=passage,
            score=0.0,
            verifiable=False,
            ocr_sourced=ocr_sourced,
        )
    score = _bigram_f1(passage, source_text)
    return GroundingResult(
        passage=passage,
        score=score,
        verifiable=True,
        ocr_sourced=ocr_sourced,
    )


def _bigram_f1(a: str, b: str) -> float:
    """Bigram F1 score between strings a and b (case-insensitive word bigrams)."""
    a_bigrams = _bigrams(a.lower())
    b_bigrams = _bigrams(b.lower())
    if not a_bigrams or not b_bigrams:
        return 0.0
    a_set = set(a_bigrams)
    b_set = set(b_bigrams)
    common = len(a_set & b_set)
    precision = common / len(a_set)
    recall = common / len(b_set)
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _bigrams(text: str) -> list[tuple[str, str]]:
    words = re.findall(r"\b\w+\b", text)
    return [(words[i], words[i + 1]) for i in range(len(words) - 1)]


def _is_visual_content(text: str) -> bool:
    """True if text references a figure, equation, table, or contains formula chars."""
    return bool(_VISUAL_RE.search(text) or _FORMULA_CHARS_RE.search(text))
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/test_grounding.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/grounding.py tests/test_ingest/test_grounding.py
git commit -m "feat: add grounding module with bigram F1 passage verification"
```

---

### Task 8: Proposal data model + writer

**Files:**
- Create: `src/llm_wiki/ingest/proposals.py`
- Create: `tests/test_ingest/test_proposals.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_proposals.py
import json
from pathlib import Path
from llm_wiki.ingest.proposals import (
    Proposal, ProposalPassage, write_proposal,
    read_proposal_meta, read_proposal_body,
    update_proposal_status, list_pending_proposals,
    find_wiki_page, cluster_dirs,
)
from llm_wiki.ingest.page_writer import PageSection

def _sample_proposal() -> Proposal:
    return Proposal(
        source="raw/boltz2.pdf",
        target_page="boltz-2",
        action="update",
        proposed_by="ingest",
        created="2026-04-10T12:00:00",
        extraction_method="pdf",
        sections=[
            PageSection(
                name="binding-affinity",
                heading="Binding Affinity Prediction",
                content="[[Boltz-2]] achieves SOTA on PDBbind [[raw/boltz2.pdf]].",
            )
        ],
        passages=[
            ProposalPassage(
                id="p1",
                text="Boltz-2 achieves state-of-the-art on PDBbind.",
                claim="Boltz-2 achieves SOTA on PDBbind",
                score=0.91,
                method="ngram",
                verifiable=True,
                ocr_sourced=False,
            )
        ],
    )

def test_write_and_read_proposal_meta(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    assert p.exists()
    meta = read_proposal_meta(p)
    assert meta["target_page"] == "boltz-2"
    assert meta["action"] == "update"
    assert meta["status"] == "pending"
    assert meta["source"] == "raw/boltz2.pdf"

def test_write_proposal_body_contains_sections(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    body = read_proposal_body(p)
    assert "%% section: binding-affinity %%" in body
    assert "## Binding Affinity Prediction" in body
    assert "[[raw/boltz2.pdf]]" in body

def test_write_proposal_evidence_block(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    raw = p.read_text()
    assert "```evidence" in raw
    # Parse the evidence block
    import re
    m = re.search(r"```evidence\s*\n(.*?)\n```", raw, re.DOTALL)
    assert m
    evidence = json.loads(m.group(1))
    assert evidence[0]["id"] == "p1"
    assert evidence[0]["score"] == 0.91

def test_update_proposal_status(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    update_proposal_status(p, "merged")
    meta = read_proposal_meta(p)
    assert meta["status"] == "merged"

def test_list_pending_proposals(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p1 = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    prop2 = _sample_proposal()
    prop2.target_page = "other-page"
    p2 = write_proposal(proposals_dir, prop2, source_slug="boltz2")
    update_proposal_status(p2, "merged")
    pending = list_pending_proposals(proposals_dir)
    assert len(pending) == 1
    assert pending[0] == p1

def test_find_wiki_page_flat(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "boltz-2.md"
    page.write_text("---\ntitle: Boltz-2\n---\n")
    assert find_wiki_page(wiki, "boltz-2") == page
    assert find_wiki_page(wiki, "does-not-exist") is None

def test_find_wiki_page_nested(tmp_path):
    wiki = tmp_path / "wiki"
    cluster = wiki / "structural-biology"
    cluster.mkdir(parents=True)
    page = cluster / "boltz-2.md"
    page.write_text("---\ntitle: Boltz-2\n---\n")
    assert find_wiki_page(wiki, "boltz-2") == page

def test_cluster_dirs_returns_subdirectories(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "structural-biology").mkdir(parents=True)
    (wiki / "ml-methods").mkdir()
    (wiki / ".hidden").mkdir()
    result = cluster_dirs(wiki)
    assert result == ["ml-methods", "structural-biology"]
    assert ".hidden" not in result

def test_proposal_includes_target_cluster(tmp_path):
    proposals_dir = tmp_path / "proposals"
    prop = _sample_proposal()
    prop.target_cluster = "structural-biology"
    p = write_proposal(proposals_dir, prop, source_slug="boltz2")
    meta = read_proposal_meta(p)
    assert meta.get("target_cluster") == "structural-biology"
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_ingest/test_proposals.py -v
```

- [ ] **Step 3: Create `src/llm_wiki/ingest/proposals.py`**

```python
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from llm_wiki.ingest.page_writer import PageSection

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_EVIDENCE_RE = re.compile(r"```evidence\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class ProposalPassage:
    id: str
    text: str
    claim: str
    score: float
    method: str
    verifiable: bool
    ocr_sourced: bool


@dataclass
class Proposal:
    source: str
    target_page: str
    action: str                # "create" | "update"
    proposed_by: str
    created: str
    extraction_method: str
    sections: "list[PageSection]"
    passages: list[ProposalPassage] = field(default_factory=list)
    quality_warning: str | None = None
    status: str = "pending"
    target_cluster: str = ""   # wiki/ subdirectory for new pages; "" = root


def write_proposal(
    proposals_dir: Path,
    proposal: Proposal,
    source_slug: str,
) -> Path:
    """Write proposal to inbox/proposals/YYYY-MM-DD-<source>-<target>.md."""
    date = datetime.date.today().isoformat()
    filename = f"{date}-{source_slug}-{proposal.target_page}.md"
    path = proposals_dir / filename
    proposals_dir.mkdir(parents=True, exist_ok=True)

    fm = {
        "type": "proposal",
        "status": proposal.status,
        "source": proposal.source,
        "target_page": proposal.target_page,
        "target_cluster": proposal.target_cluster or None,
        "action": proposal.action,
        "proposed_by": proposal.proposed_by,
        "created": proposal.created,
        "extraction_method": proposal.extraction_method,
        "quality_warning": proposal.quality_warning,
    }
    frontmatter = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---\n\n"

    body_parts: list[str] = []
    for section in proposal.sections:
        body_parts.append(f"%% section: {section.name} %%")
        body_parts.append(f"## {section.heading}")
        body_parts.append("")
        body_parts.append(section.content)
        body_parts.append("")
    body = "\n".join(body_parts).strip()

    evidence_data = [
        {
            "id": p.id,
            "text": p.text,
            "claim": p.claim,
            "score": p.score,
            "method": p.method,
            "verifiable": p.verifiable,
            "ocr_sourced": p.ocr_sourced,
        }
        for p in proposal.passages
    ]
    evidence_block = "\n\n```evidence\n" + json.dumps(evidence_data, indent=2) + "\n```\n"

    path.write_text(frontmatter + body + evidence_block, encoding="utf-8")
    return path


def read_proposal_meta(path: Path) -> dict:
    """Return frontmatter dict from a proposal file, or {} on failure."""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def read_proposal_body(path: Path) -> str:
    """Return section body stripped of frontmatter and evidence block."""
    raw = path.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.match(raw)
    body = raw[fm.end():] if fm else raw
    ev = _EVIDENCE_RE.search(body)
    if ev:
        body = body[: ev.start()]
    return body.strip()


def update_proposal_status(path: Path, status: str) -> None:
    """Rewrite the status field in frontmatter in-place."""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return
    fm["status"] = status
    new_fm = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---\n\n"
    path.write_text(new_fm + raw[m.end():], encoding="utf-8")


def list_pending_proposals(proposals_dir: Path) -> list[Path]:
    """Return sorted paths of all pending proposals in proposals_dir."""
    if not proposals_dir.is_dir():
        return []
    return sorted(
        p for p in proposals_dir.glob("*.md")
        if p.is_file() and read_proposal_meta(p).get("status") == "pending"
    )


def find_wiki_page(wiki_dir: Path, slug: str) -> Path | None:
    """Recursively find the page file for *slug* under wiki_dir.

    Supports nested cluster directories (e.g. wiki/structural-biology/boltz-2.md).
    Returns None if the page does not exist.
    """
    for p in wiki_dir.rglob(f"{slug}.md"):
        if not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts):
            return p
    return None


def cluster_dirs(wiki_dir: Path) -> list[str]:
    """Return sorted list of existing cluster subdirectory names under wiki_dir."""
    return sorted(
        d.name for d in wiki_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/test_proposals.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/proposals.py tests/test_ingest/test_proposals.py
git commit -m "feat: add proposal data model, writer, and reader"
```

---

### Task 9: New prompts — overview pass + passage collection

**Files:**
- Modify: `src/llm_wiki/ingest/prompts.py`
- Modify: `src/llm_wiki/ingest/agent.py` (add `action`, `section_names` to `ConceptPlan`)
- Modify: `tests/test_ingest/test_prompts.py`

- [ ] **Step 1: Extend `ConceptPlan` and write failing prompt tests**

```python
# Add to tests/test_ingest/test_prompts.py
import json
from llm_wiki.ingest.prompts import (
    compose_overview_messages,
    compose_passage_collection_messages,
    parse_overview_extraction,
    parse_passage_collection,
    parse_content_synthesis,
)
from llm_wiki.ingest.agent import ConceptPlan

def test_overview_messages_embed_manifest_and_clusters():
    msgs = compose_overview_messages(
        chunk_text="Boltz-2 is a new model for structure prediction.",
        manifest_lines=["boltz-1  'Boltz-1'", "protein-mpnn  'ProteinMPNN'"],
        source_ref="raw/boltz2.pdf",
        cluster_dir_names=["structural-biology", "ml-methods"],
    )
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "boltz-1" in combined
    assert "protein-mpnn" in combined
    assert "structural-biology" in combined
    assert "Boltz-2 is a new model" in combined

def test_overview_messages_no_clusters():
    msgs = compose_overview_messages(
        chunk_text="A paper.",
        manifest_lines=[],
        source_ref="raw/paper.pdf",
    )
    combined = msgs[0]["content"]
    assert "none yet" in combined.lower()

def test_parse_overview_extraction_valid():
    text = json.dumps({
        "concepts": [
            {"name": "boltz-2", "title": "Boltz-2", "action": "update",
             "cluster": "structural-biology",
             "section_names": ["binding-affinity", "ensemble-prediction"]},
        ]
    })
    result = parse_overview_extraction(text)
    assert len(result) == 1
    assert result[0].name == "boltz-2"
    assert result[0].action == "update"
    assert result[0].cluster == "structural-biology"
    assert "binding-affinity" in result[0].section_names

def test_parse_overview_extraction_defaults_action_to_create():
    text = json.dumps({"concepts": [{"name": "new-concept", "title": "New"}]})
    result = parse_overview_extraction(text)
    assert result[0].action == "create"

def test_passage_collection_messages_embed_concepts():
    concepts = [ConceptPlan(name="boltz-2", title="Boltz-2")]
    msgs = compose_passage_collection_messages(
        chunk_text="Boltz-2 achieves high accuracy.",
        concepts=concepts,
    )
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "boltz-2" in combined
    assert "Boltz-2 achieves high accuracy" in combined

def test_parse_passage_collection_valid():
    text = json.dumps({"boltz-2": ["Boltz-2 achieves SOTA.", "It uses diffusion."]})
    result = parse_passage_collection(text, concept_names=["boltz-2"])
    assert "boltz-2" in result
    assert len(result["boltz-2"]) == 2

def test_parse_passage_collection_ignores_unknown_concepts():
    text = json.dumps({"unknown": ["Some text."]})
    result = parse_passage_collection(text, concept_names=["boltz-2"])
    assert "unknown" not in result

def test_parse_content_synthesis_valid():
    text = json.dumps({"sections": [
        {"name": "overview", "heading": "Overview", "content": "[[boltz-2]] text [[raw/paper.pdf]]."}
    ]})
    result = parse_content_synthesis(text)
    assert len(result) == 1
    assert result[0].name == "overview"
    assert "boltz-2" in result[0].content

def test_parse_content_synthesis_invalid_returns_empty():
    assert parse_content_synthesis("not json") == []
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_ingest/test_prompts.py -v
```

- [ ] **Step 3: Add `action` and `section_names` to `ConceptPlan` in `agent.py`**

In `src/llm_wiki/ingest/agent.py`, replace the `ConceptPlan` dataclass:

```python
@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str
    title: str
    passages: list[str] = field(default_factory=list)
    action: str = "create"                      # "create" | "update"
    section_names: list[str] = field(default_factory=list)
    cluster: str = ""                           # target wiki/ subdirectory; "" = root
```

- [ ] **Step 4: Add new prompts to `src/llm_wiki/ingest/prompts.py`**

Add after the existing constants and functions:

```python
_OVERVIEW_SYSTEM = """\
You are analyzing the opening of a scientific document to identify its primary \
concepts for a knowledge wiki.

## Task

Identify PRIMARY concepts — named models, datasets, methods, or tools that:
- Exist independently and may be referenced by other papers
- Warrant their own wiki page

Do NOT create concepts for paper-specific details like "training data curation", \
"ablation study", "experimental setup", "loss function variants" — these become \
sections WITHIN a primary concept page.

For each concept:
1. Assign a URL-safe slug (lowercase, hyphens only)
2. Check if it matches an existing wiki page slug — if yes, set action to "update" \
and use the EXACT existing slug
3. If new, set action to "create"
4. List 2-6 section names (sub-topics that will be sections on the page)
5. Assign a cluster (wiki subdirectory). Use an EXISTING cluster name if it fits; \
otherwise invent a short lowercase-hyphenated name (e.g. "structural-biology", \
"ml-methods"). All concepts from one paper should share a cluster unless they \
clearly belong to different domains.

## Existing Wiki Pages (check slugs before naming new concepts)

<<<MANIFEST>>>

## Existing Clusters (prefer these over inventing new ones)

<<<CLUSTER_DIRS>>>

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object:

{
  "concepts": [
    {
      "name": "exact-slug",
      "title": "Human Readable Title",
      "action": "create",
      "cluster": "structural-biology",
      "section_names": ["overview", "architecture", "benchmarks"]
    }
  ]
}"""


_PASSAGE_COLLECTION_SYSTEM = """\
You are extracting verbatim passages from a document chunk for specified concepts.

For each concept listed, extract 1-3 SHORT verbatim passages (exact quotes, \
not paraphrases) from the text below that directly describe or discuss that concept.

Only extract passages that are ACTUALLY IN THE TEXT. Do not invent or paraphrase.
If a concept does not appear in this chunk, return an empty list for it.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object mapping concept slugs to passage lists:

{
  "concept-slug": ["exact verbatim passage from text", ...],
  "other-concept": []
}"""


_CONTENT_SYNTHESIS_SYSTEM = """\
You are writing wiki content for a specific concept using verbatim source passages.

## Wikilink Rules (Non-Negotiable)

1. Reference to a concept in the EXISTING WIKI or BATCH lists below → [[slug]] inline
2. Every factual claim → [[<<<SOURCE_REF>>>]] at end of sentence, no exceptions
3. General term NOT in either list → plain text, no brackets
4. NEVER invent slugs. Only use slugs from the two lists below.
5. [[raw/...]] = factual citation. [[slug]] = conceptual link. Never conflate.

## Existing wiki pages (use [[slug]] for these)

<<<MANIFEST>>>

## Concepts in this ingest batch (also use [[slug]])

<<<BATCH_SLUGS>>>

## Content Rules

- Synthesize — do not transcribe passages verbatim
- Be concise and precise. Every sentence earns its place.
- Do not interpret beyond what passages state
- "X correlates with Y" not "X causes Y"

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object:

{
  "sections": [
    {
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [[<<<SOURCE_REF>>>]] citations."
    }
  ]
}"""


def compose_overview_messages(
    chunk_text: str,
    manifest_lines: list[str],
    source_ref: str,
    cluster_dir_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build messages for the overview concept-identification pass."""
    manifest = "\n".join(manifest_lines) if manifest_lines else "(empty wiki)"
    clusters = "\n".join(f"- {c}" for c in cluster_dir_names) if cluster_dir_names else "(none yet — invent appropriate names)"
    system = (
        _OVERVIEW_SYSTEM
        .replace("<<<MANIFEST>>>", manifest)
        .replace("<<<CLUSTER_DIRS>>>", clusters)
    )
    user = f"## Source Reference\n{source_ref}\n\n## Document Opening\n{chunk_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def compose_passage_collection_messages(
    chunk_text: str,
    concepts: "list[ConceptPlan]",
) -> list[dict[str, str]]:
    """Build messages for extracting verbatim passages from one chunk."""
    concept_list = "\n".join(f"- {c.name}: {c.title}" for c in concepts)
    user = f"## Concepts to find\n{concept_list}\n\n## Document Chunk\n{chunk_text}"
    return [
        {"role": "system", "content": _PASSAGE_COLLECTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_content_synthesis_messages(
    concept: "ConceptPlan",
    passages: list[str],
    source_ref: str,
    manifest_lines: list[str],
    batch_concepts: "list[ConceptPlan]",
) -> list[dict[str, str]]:
    """Build messages for synthesising wiki sections from passages."""
    manifest = "\n".join(manifest_lines) if manifest_lines else "(empty wiki)"
    batch_slugs = "\n".join(
        f"- {c.name}: {c.title}" for c in batch_concepts
    )
    system = (
        _CONTENT_SYNTHESIS_SYSTEM
        .replace("<<<SOURCE_REF>>>", source_ref)
        .replace("<<<MANIFEST>>>", manifest)
        .replace("<<<BATCH_SLUGS>>>", batch_slugs or "(none)")
    )
    passages_text = "\n\n".join(f"- {p}" for p in passages)
    section_hint = (
        f"## Requested sections\n" + "\n".join(f"- {s}" for s in concept.section_names)
        if concept.section_names else ""
    )
    user = (
        f"## Concept\n{concept.title}\n\n"
        f"## Source Reference\n{source_ref}\n\n"
        f"{section_hint}\n\n"
        f"## Relevant Passages\n{passages_text}"
    ).strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_overview_extraction(text: str) -> "list[ConceptPlan]":
    """Parse overview pass response → list of ConceptPlan with action + section_names."""
    from llm_wiki.ingest.agent import ConceptPlan
    try:
        data = _parse_json_response(text)
        concepts = data.get("concepts") or [] if isinstance(data, dict) else []
        return [
            ConceptPlan(
                name=c["name"],
                title=c.get("title", c["name"]),
                action=c.get("action", "create"),
                section_names=c.get("section_names") if isinstance(c.get("section_names"), list) else [],
                cluster=c.get("cluster", "") or "",
                passages=[],
            )
            for c in concepts
            if isinstance(c, dict) and isinstance(c.get("name"), str) and c.get("name")
        ]
    except (ValueError, KeyError, TypeError):
        return []


def parse_passage_collection(text: str, concept_names: list[str]) -> dict[str, list[str]]:
    """Parse passage collection response → {slug: [passage, ...]}."""
    try:
        data = _parse_json_response(text)
        if not isinstance(data, dict):
            return {}
        result: dict[str, list[str]] = {}
        for name in concept_names:
            passages = data.get(name)
            if isinstance(passages, list):
                result[name] = [p for p in passages if isinstance(p, str) and p.strip()]
        return result
    except (ValueError, KeyError, TypeError):
        return {}


def parse_content_synthesis(text: str) -> "list[PageSection]":
    """Parse content synthesis response → list of PageSection objects."""
    from llm_wiki.ingest.page_writer import PageSection
    try:
        data = _parse_json_response(text)
        sections = data.get("sections") or [] if isinstance(data, dict) else []
        return [
            PageSection(
                name=s["name"],
                heading=s.get("heading", s["name"].replace("-", " ").title()),
                content=s.get("content", ""),
            )
            for s in sections
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"]
        ]
    except (ValueError, KeyError, TypeError):
        return []
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/test_ingest/test_prompts.py -v
```

- [ ] **Step 6: Commit**

```
git add src/llm_wiki/ingest/prompts.py src/llm_wiki/ingest/agent.py tests/test_ingest/test_prompts.py
git commit -m "feat: add overview, passage collection, and synthesis prompts with wikilink rules"
```

---

### Task 10: `IngestAgent.ingest_as_proposals()` — multi-chunk pipeline

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`
- Modify: `tests/test_ingest/test_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# Add to tests/test_ingest/test_integration.py
import asyncio
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.config import WikiConfig

def _make_llm(responses: list[str]) -> MagicMock:
    """LLM mock that yields responses in order."""
    call_count = 0
    async def complete(messages, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.content = responses[call_count % len(responses)]
        call_count += 1
        return resp
    llm = MagicMock()
    llm.complete = complete
    return llm

def test_ingest_as_proposals_creates_proposal_files(tmp_path):
    import json
    source = tmp_path / "raw" / "paper.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Paper\n\nBoltz-2 is a model.\n\nIt achieves SOTA.", encoding="utf-8")

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    proposals_dir = tmp_path / "inbox" / "proposals"

    overview_resp = json.dumps({
        "concepts": [{"name": "boltz-2", "title": "Boltz-2", "action": "create", "section_names": ["overview"]}]
    })
    passage_resp = json.dumps({"boltz-2": ["Boltz-2 achieves SOTA."]})
    synthesis_resp = json.dumps({
        "sections": [{"name": "overview", "heading": "Overview", "content": "[[Boltz-2]] is a model [[raw/paper.md]]."}]
    })
    llm = _make_llm([overview_resp, passage_resp, synthesis_resp])
    cfg = WikiConfig()

    agent = IngestAgent(llm=llm, config=cfg)

    async def run():
        return await agent.ingest_as_proposals(
            source_path=source,
            vault_root=tmp_path,
            proposals_dir=proposals_dir,
            manifest_lines=[],
        )

    result = asyncio.run(run())
    assert result.concepts_found == 1
    pending = list(proposals_dir.glob("*.md"))
    assert len(pending) == 1
    raw = pending[0].read_text()
    assert "boltz-2" in raw
    assert "```evidence" in raw
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_ingest/test_integration.py::test_ingest_as_proposals_creates_proposal_files -v
```

- [ ] **Step 3: Add `ingest_as_proposals()` to `IngestAgent` in `agent.py`**

Add imports at top of `agent.py`:

```python
import re as _re
import datetime
from llm_wiki.ingest.chunker import chunk_text
from llm_wiki.ingest.grounding import ground_passage, GroundingResult
from llm_wiki.ingest.proposals import (
    Proposal, ProposalPassage, write_proposal, cluster_dirs as _get_cluster_dirs,
)
from llm_wiki.ingest.prompts import (
    compose_overview_messages,
    compose_passage_collection_messages,
    compose_content_synthesis_messages,
    parse_overview_extraction,
    parse_passage_collection,
    parse_content_synthesis,
)
```

Add method to `IngestAgent`:

```python
async def ingest_as_proposals(
    self,
    source_path: Path,
    vault_root: Path,
    proposals_dir: Path,
    manifest_lines: list[str],
    *,
    author: str = "cli",
) -> IngestResult:
    """Multi-chunk wiki-aware ingest that writes proposals instead of direct pages.

    Args:
        source_path:    Absolute path to source (must be inside vault_root/raw/).
        vault_root:     Vault root directory.
        proposals_dir:  Where to write proposal files (inbox/proposals/).
        manifest_lines: Existing wiki manifest, one "slug  title" line each.
        author:         Who triggered the ingest (for metadata).
    """
    from llm_wiki.ingest.source_meta import init_companion, write_companion_body

    result = IngestResult(source_path=source_path, dry_run=False)

    try:
        source_ref = str(source_path.relative_to(vault_root))
    except ValueError:
        source_ref = source_path.name

    # Extract text
    extraction = await extract_text(source_path, ingest_config=self._config.ingest)
    if not extraction.success:
        logger.warning("Extraction failed for %s: %s", source_path, extraction.error)
        return result

    result.source_chars = len(extraction.content)
    if extraction.extraction_warning:
        result.extraction_warning = extraction.extraction_warning

    # Create companion in raw/ if needed
    companion = init_companion(source_path, vault_root)
    if companion:
        try:
            write_companion_body(companion, extraction.content)
        except Exception as exc:
            logger.warning("Failed to write companion for %s: %s", source_path, exc)

    # Chunk the document
    chunks = chunk_text(
        extraction.content,
        chunk_tokens=self._config.ingest.chunk_tokens,
        overlap=self._config.ingest.chunk_overlap,
    )
    if not chunks:
        return result

    wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")
    existing_clusters = _get_cluster_dirs(wiki_dir)

    # Overview pass on chunk 0
    overview_msgs = compose_overview_messages(
        chunk_text=chunks[0],
        manifest_lines=manifest_lines,
        source_ref=source_ref,
        cluster_dir_names=existing_clusters,
    )
    overview_resp = await self._llm.complete(overview_msgs, temperature=0.2, priority="ingest")
    concepts = parse_overview_extraction(overview_resp.content)

    if not concepts:
        logger.info("No concepts identified in %s", source_path)
        return result

    # Passage collection across all chunks
    concept_passages: dict[str, list[str]] = {c.name: [] for c in concepts}
    max_passages = self._config.ingest.max_passages_per_concept

    for chunk in chunks:
        still_need = [c for c in concepts if len(concept_passages[c.name]) < max_passages]
        if not still_need:
            break
        coll_msgs = compose_passage_collection_messages(
            chunk_text=chunk,
            concepts=still_need,
        )
        coll_resp = await self._llm.complete(coll_msgs, temperature=0.1, priority="ingest")
        found = parse_passage_collection(
            coll_resp.content,
            concept_names=[c.name for c in still_need],
        )
        for name, passages in found.items():
            existing = concept_passages[name]
            for p in passages:
                if p not in existing and len(existing) < max_passages:
                    existing.append(p)

    source_slug = source_path.stem.lower()
    source_slug = _re.sub(r"[^a-z0-9-]", "-", source_slug).strip("-")
    ocr_sourced = extraction.extraction_method == "image_ocr"

    # Content synthesis + proposal write per concept
    for concept in concepts:
        passages = concept_passages.get(concept.name, [])
        if not passages:
            logger.warning("No passages collected for concept %r — skipping", concept.name)
            continue

        synth_msgs = compose_content_synthesis_messages(
            concept=concept,
            passages=passages,
            source_ref=source_ref,
            manifest_lines=manifest_lines,
            batch_concepts=concepts,
        )
        synth_resp = await self._llm.complete(synth_msgs, temperature=0.3, priority="ingest")
        sections = parse_content_synthesis(synth_resp.content)
        if not sections:
            logger.warning("No sections generated for %r — skipping", concept.name)
            continue

        # Ground each passage against source text
        proposal_passages: list[ProposalPassage] = []
        for idx, passage_text in enumerate(passages):
            gr = ground_passage(passage_text, extraction.content, ocr_sourced=ocr_sourced)
            # Use first sentence of the first section content as the claim
            claim = sections[0].content.split(".")[0] if sections else passage_text[:80]
            proposal_passages.append(ProposalPassage(
                id=f"p{idx + 1}",
                text=gr.passage,
                claim=claim,
                score=gr.score,
                method=gr.method,
                verifiable=gr.verifiable,
                ocr_sourced=gr.ocr_sourced,
            ))

        proposal = Proposal(
            source=source_ref,
            target_page=concept.name,
            action=concept.action,
            proposed_by=author,
            created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            extraction_method=extraction.extraction_method,
            sections=sections,
            passages=proposal_passages,
            quality_warning=result.extraction_warning,
            target_cluster=concept.cluster,
        )
        write_proposal(proposals_dir, proposal, source_slug=source_slug)

        if concept.action == "create":
            result.pages_created.append(concept.name)
        else:
            result.pages_updated.append(concept.name)

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_ingest/test_integration.py -v
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/ingest/agent.py tests/test_ingest/test_integration.py
git commit -m "feat: add IngestAgent.ingest_as_proposals() multi-chunk pipeline"
```

---

### Task 11: Wire `ingest_as_proposals` into the CLI

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `src/llm_wiki/daemon/server.py` (add `ingest_proposals` route)

The CLI already sends an `ingest` request to the daemon. We need the daemon to call `ingest_as_proposals` and return proposal paths rather than page names. The simplest approach: add a `"proposal_mode": true` flag the CLI sends, which the daemon routes to the new code path.

- [ ] **Step 1: Read `server.py` to establish ingest handler location and agent attribute name**

```
grep -n "_handle_ingest\|ingest\|_ingest_agent\|IngestAgent" src/llm_wiki/daemon/server.py | head -30
```

Note the exact attribute name the server uses for the ingest agent (e.g., `self._ingest_agent`, `self._agent`, `self.ingest_agent`) — you need this for Step 4.

- [ ] **Step 2: Write failing test for daemon proposal-mode routing**

```python
# Add to tests/test_daemon/ or tests/test_ingest/test_integration.py
# (Verify the daemon ingest response includes proposal_mode handling)
# This is a smoke test — full daemon tests require running the daemon.

# Minimal: verify the CLI sends proposal_mode=True by default
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from llm_wiki.cli.main import cli
import tempfile
from pathlib import Path

def test_cli_ingest_sends_proposal_mode(tmp_path):
    source = tmp_path / "raw" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF")

    mock_client = MagicMock()
    mock_client.is_running.return_value = True
    mock_client.request.return_value = {
        "status": "ok", "concepts_found": 0, "created": [], "updated": [],
    }
    runner = CliRunner()
    with patch("llm_wiki.cli.main._get_client", return_value=mock_client):
        runner.invoke(cli, ["ingest", str(source), "--vault", str(tmp_path)])

    call_args = mock_client.request.call_args[0][0]
    assert call_args.get("proposal_mode") is True
```

- [ ] **Step 3: Run test — expect FAIL**

```
pytest tests/test_cli/ -k "proposal_mode" -v
```

- [ ] **Step 4: Add `proposal_mode=True` to CLI request and handle in server**

In `src/llm_wiki/cli/main.py`, in `ingest()`, change the request dict to include `proposal_mode`:

```python
    resp = client.request({
        "type": "ingest",
        "source_path": str(source_path.resolve()),
        "author": "cli",
        "connection_id": _uuid.uuid4().hex,
        "dry_run": dry_run,
        "proposal_mode": True,    # ← add this
    })
```

In `src/llm_wiki/daemon/server.py`, locate `_handle_ingest` and add a branch for `proposal_mode`. Find the handler (search for `"ingest"` in the route map) and read its signature, then add:

```python
# At the top of _handle_ingest (or in the router dispatch):
if request.get("proposal_mode"):
    return await self._handle_ingest_proposals(request)
```

Add `_handle_ingest_proposals` to the server class. It mirrors `_handle_ingest` but calls `agent.ingest_as_proposals()`. Read the existing `_handle_ingest` implementation first to match the patterns (vault_root, llm, config setup), then write:

```python
async def _handle_ingest_proposals(self, request: dict) -> dict:
    """Route ingest to the proposal pipeline."""
    source_path = Path(request["source_path"])
    author = request.get("author", "cli")

    proposals_dir = self._vault_root / "inbox" / "proposals"

    # Build manifest lines from current vault
    from llm_wiki.vault import Vault
    vault = Vault.scan(self._vault_root, self._config)
    manifest_lines = [
        f"{name}  '{entry.title}'"
        for name, entry in vault.manifest_entries().items()
    ]

    result = await self._ingest_agent.ingest_as_proposals(
        source_path=source_path,
        vault_root=self._vault_root,
        proposals_dir=proposals_dir,
        manifest_lines=manifest_lines,
        author=author,
    )

    return {
        "status": "ok",
        "concepts_found": result.concepts_found,
        "created": result.pages_created,
        "updated": result.pages_updated,
        "extraction_warning": result.extraction_warning,
        "proposal_mode": True,
    }
```

- [ ] **Step 5: Run full test suite — expect PASS**

```
pytest tests/ -v --tb=short -q
```

- [ ] **Step 7: Commit**

```
git add src/llm_wiki/cli/main.py src/llm_wiki/daemon/server.py
git commit -m "feat: wire proposal_mode into CLI and daemon ingest route"
```

---

### Task 12: Auditor — `find_pending_proposals` check + auto-merge

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `src/llm_wiki/audit/auditor.py`
- Modify: `tests/test_audit/test_checks.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_audit/test_checks.py
import json, textwrap, tempfile
from pathlib import Path
from unittest.mock import MagicMock
from llm_wiki.audit.checks import find_pending_proposals
from llm_wiki.ingest.proposals import write_proposal, Proposal, ProposalPassage
from llm_wiki.ingest.page_writer import PageSection

def _make_proposal(tmp_path, action="update", score=0.9) -> Path:
    proposals_dir = tmp_path / "inbox" / "proposals"
    p = Proposal(
        source="raw/paper.pdf",
        target_page="boltz-2",
        action=action,
        proposed_by="ingest",
        created="2026-04-10T12:00:00",
        extraction_method="pdf",
        sections=[PageSection(name="overview", heading="Overview", content="[[boltz-2]] text [[raw/paper.pdf]].")],
        passages=[ProposalPassage(id="p1", text="boltz-2 text", claim="text", score=score, method="ngram", verifiable=True, ocr_sourced=False)],
    )
    return write_proposal(proposals_dir, p, source_slug="paper")

def test_find_pending_proposals_update_high_score_returns_merge_ready_issue(tmp_path):
    """Clean update with high score → merge-ready issue (no page mutation in check)."""
    from llm_wiki.audit.checks import find_pending_proposals
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True)
    target = wiki_dir / "boltz-2.md"
    target.write_text("---\ntitle: Boltz-2\n---\n\nExisting content.\n")
    _make_proposal(tmp_path, action="update", score=0.9)
    result = find_pending_proposals(tmp_path, wiki_dir=wiki_dir)
    assert result.check == "pending-proposals"
    # Check is read-only — returns a merge-ready issue, does NOT mutate the page
    assert any(i.type == "merge-ready" for i in result.issues)
    assert target.read_text() == "---\ntitle: Boltz-2\n---\n\nExisting content.\n"

def test_execute_proposal_merges_applies_high_score_updates(tmp_path):
    """execute_proposal_merges() applies merge-ready proposals to target pages."""
    from llm_wiki.audit.checks import execute_proposal_merges
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True)
    target = wiki_dir / "boltz-2.md"
    target.write_text("---\ntitle: Boltz-2\n---\n\nExisting content.\n")
    _make_proposal(tmp_path, action="update", score=0.9)
    execute_proposal_merges(tmp_path, wiki_dir=wiki_dir)
    merged = target.read_text()
    assert "overview" in merged.lower() or "Overview" in merged

def test_find_pending_proposals_create_always_issues(tmp_path):
    """action=create always raises an issue for human review."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True)
    _make_proposal(tmp_path, action="create", score=0.95)
    result = find_pending_proposals(tmp_path, wiki_dir=wiki_dir)
    assert any(i.type == "proposal" for i in result.issues)

def test_find_pending_proposals_low_score_issues(tmp_path):
    """action=update with low verification score raises an issue."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True)
    target = wiki_dir / "boltz-2.md"
    target.write_text("---\ntitle: Boltz-2\n---\n\nContent.\n")
    _make_proposal(tmp_path, action="update", score=0.3)
    result = find_pending_proposals(tmp_path, wiki_dir=wiki_dir)
    assert any(i.type == "proposal-verification-failed" for i in result.issues)
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/test_audit/test_checks.py -k "pending_proposals" -v
```

- [ ] **Step 3: Add `find_pending_proposals` to `src/llm_wiki/audit/checks.py`**

Add imports at top:

```python
from llm_wiki.ingest.proposals import (
    list_pending_proposals,
    read_proposal_meta,
    read_proposal_body,
    update_proposal_status,
    find_wiki_page,
)
```

Add two functions — `find_pending_proposals` (read-only check) and `execute_proposal_merges` (called by the auditor scheduler after `audit()`):

```python
def find_pending_proposals(
    vault_root: Path,
    wiki_dir: Path | None = None,
    auto_merge_threshold: float = 0.75,
    flag_threshold: float = 0.50,
) -> CheckResult:
    """Read-only check: classify pending proposals and return issues.

    This function NEVER mutates wiki pages — it is safe to call from lint.

    Issue types returned:
      - 'merge-ready':                 action=update, all verifiable scores ≥ auto_merge_threshold
      - 'proposal':                    action=create (requires human review), or target missing
      - 'proposal-verification-failed': any verifiable score < flag_threshold
    """
    import json, re as _re
    proposals_dir = vault_root / "inbox" / "proposals"
    if wiki_dir is None:
        wiki_dir = vault_root / "wiki"
    _ev_re = _re.compile(r"```evidence\s*\n(.*?)\n```", _re.DOTALL)

    issues: list[Issue] = []

    for proposal_path in list_pending_proposals(proposals_dir):
        meta = read_proposal_meta(proposal_path)
        if not meta:
            continue

        action = meta.get("action", "update")
        target_page = meta.get("target_page", "")
        source = meta.get("source", "")

        raw = proposal_path.read_text(encoding="utf-8")
        ev_match = _ev_re.search(raw)
        scores: list[float] = []
        if ev_match:
            try:
                evidence = json.loads(ev_match.group(1))
                scores = [e["score"] for e in evidence if e.get("verifiable", True)]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        min_score = min(scores) if scores else 1.0

        if action == "create":
            issues.append(Issue(
                id=Issue.make_id("proposal", target_page, source),
                type="proposal",
                status="open",
                severity="minor",
                title=f"New page proposal: '{target_page}' from {source}",
                page=target_page,
                body=(
                    f"The ingest pipeline proposes creating [[{target_page}]] from "
                    f"`{source}`. Review `{proposal_path.relative_to(vault_root)}` "
                    f"and approve with `llm-wiki proposals approve` or reject with "
                    f"`llm-wiki proposals reject`."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"proposal_path": str(proposal_path), "source": source},
            ))
            continue

        if min_score < flag_threshold:
            issues.append(Issue(
                id=Issue.make_id("proposal-verification-failed", target_page, source),
                type="proposal-verification-failed",
                status="open",
                severity="moderate",
                title=f"Proposal for '{target_page}' has low grounding score ({min_score:.2f})",
                page=target_page,
                body=(
                    f"The proposal to update [[{target_page}]] from `{source}` "
                    f"has a minimum passage verification score of {min_score:.2f} "
                    f"(threshold: {flag_threshold}). Review `{proposal_path.relative_to(vault_root)}`."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"proposal_path": str(proposal_path), "min_score": min_score},
            ))
            continue

        target_path = find_wiki_page(wiki_dir, target_page)
        if target_path is None:
            issues.append(Issue(
                id=Issue.make_id("proposal", target_page, source),
                type="proposal",
                status="open",
                severity="minor",
                title=f"Proposal target page not found: '{target_page}'",
                page=target_page,
                body=f"Proposal at `{proposal_path.relative_to(vault_root)}` targets [[{target_page}]] which does not exist.",
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"proposal_path": str(proposal_path)},
            ))
            continue

        # Clean update above both thresholds — flag as merge-ready
        issues.append(Issue(
            id=Issue.make_id("merge-ready", target_page, source),
            type="merge-ready",
            status="open",
            severity="info",
            title=f"Proposal ready to merge: '{target_page}' (score {min_score:.2f})",
            page=target_page,
            body=f"Proposal at `{proposal_path.relative_to(vault_root)}` is verified and ready to merge.",
            created=Issue.now_iso(),
            detected_by="auditor",
            metadata={"proposal_path": str(proposal_path), "min_score": min_score},
        ))

    return CheckResult(check="pending-proposals", issues=issues)


def execute_proposal_merges(
    vault_root: Path,
    wiki_dir: Path | None = None,
    auto_merge_threshold: float = 0.75,
) -> list[str]:
    """Apply merge-ready proposals to their target wiki pages.

    Called by the auditor scheduler AFTER audit() — NOT called during lint.
    Returns list of target page slugs that were updated.
    """
    if wiki_dir is None:
        wiki_dir = vault_root / "wiki"

    result = find_pending_proposals(
        vault_root, wiki_dir=wiki_dir,
        auto_merge_threshold=auto_merge_threshold,
    )
    merged: list[str] = []

    for issue in result.issues:
        if issue.type != "merge-ready":
            continue
        proposal_path = Path(issue.metadata["proposal_path"])
        meta = read_proposal_meta(proposal_path)
        target_page = issue.page
        action = meta.get("action", "update")
        target_cluster = meta.get("target_cluster") or ""

        from llm_wiki.ingest.page_writer import patch_token_estimates
        body = read_proposal_body(proposal_path)

        if action == "update":
            target_path = find_wiki_page(wiki_dir, target_page)
            if target_path is None:
                continue  # page vanished between check and merge — skip
            existing = target_path.read_text(encoding="utf-8")
            if body and body not in existing:
                target_path.write_text(
                    existing.rstrip() + "\n\n" + body + "\n",
                    encoding="utf-8",
                )
                patch_token_estimates(target_path)
        else:
            # create: place in cluster subdir (or root if no cluster assigned)
            cluster_dir = wiki_dir / target_cluster if target_cluster else wiki_dir
            cluster_dir.mkdir(parents=True, exist_ok=True)
            target_path = cluster_dir / f"{target_page}.md"
            if not target_path.exists() and body:
                target_path.write_text(body + "\n", encoding="utf-8")
                patch_token_estimates(target_path)

        update_proposal_status(proposal_path, "merged")
        merged.append(target_page)

    return merged
```

- [ ] **Step 4: Register in `auditor.py`**

In `src/llm_wiki/audit/auditor.py`, add to the imports:

```python
from llm_wiki.audit.checks import (
    execute_proposal_merges,    # ← add
    find_broken_citations,
    find_broken_wikilinks,
    find_inbox_staleness,
    find_missing_markers,
    find_orphans,
    find_pending_proposals,     # ← add
    find_source_gaps,
    find_stale_resonance,
    find_synthesis_without_resonance,
)
```

In `Auditor.audit()`, add `find_pending_proposals` to the `results` list (read-only):

```python
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_broken_citations(self._vault, self._vault_root),
            find_source_gaps(self._vault_root, self._config),
            find_stale_resonance(self._vault_root, self._config),
            find_synthesis_without_resonance(self._vault_root, self._config),
            find_inbox_staleness(self._vault_root),
            find_pending_proposals(              # ← add (read-only)
                self._vault_root,
                auto_merge_threshold=self._config.ingest.grounding_auto_merge,
                flag_threshold=self._config.ingest.grounding_flag,
            ),
        ]
```

In the auditor's scheduler method (search for the method that calls `audit()` on a timer — likely `run_cycle` or `_run`), add a call to `execute_proposal_merges` AFTER `audit()` completes:

```python
# After: audit_result = await self.audit()  (or however audit() is called)
execute_proposal_merges(
    self._vault_root,
    auto_merge_threshold=self._config.ingest.grounding_auto_merge,
)
```

This keeps `find_pending_proposals` safe to call from `llm-wiki lint` (pure read), while the scheduled auditor run applies the actual merges.

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/test_audit/ -v
```

- [ ] **Step 6: Commit**

```
git add src/llm_wiki/audit/checks.py src/llm_wiki/audit/auditor.py tests/test_audit/test_checks.py
git commit -m "feat: auditor auto-merges clean update proposals, issues creates and failures"
```

---

### Task 13: CLI `proposals` subcommand

**Files:**
- Modify: `src/llm_wiki/cli/main.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_cli/test_ingest_copy.py or new test file
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from llm_wiki.cli.main import cli
from llm_wiki.ingest.proposals import write_proposal, Proposal, ProposalPassage
from llm_wiki.ingest.page_writer import PageSection

def test_proposals_list_shows_pending(tmp_path):
    proposals_dir = tmp_path / "inbox" / "proposals"
    p = Proposal(
        source="raw/paper.pdf", target_page="boltz-2", action="update",
        proposed_by="ingest", created="2026-04-10T00:00:00", extraction_method="pdf",
        sections=[PageSection(name="s", heading="S", content="content.")],
        passages=[],
    )
    write_proposal(proposals_dir, p, source_slug="paper")

    runner = CliRunner()
    mock_client = MagicMock()
    mock_client.is_running.return_value = True
    mock_client.request.return_value = {
        "status": "ok",
        "proposals": [{"path": "inbox/proposals/2026-04-10-paper-boltz-2.md",
                        "target_page": "boltz-2", "action": "update", "status": "pending",
                        "source": "raw/paper.pdf"}]
    }
    with patch("llm_wiki.cli.main._get_client", return_value=mock_client):
        result = runner.invoke(cli, ["proposals", "list", "--vault", str(tmp_path)])
    assert result.exit_code == 0
    assert "boltz-2" in result.output
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/test_cli/ -k "proposals_list" -v
```

- [ ] **Step 3: Add `proposals` group to `src/llm_wiki/cli/main.py`**

Add after the `issues` group:

```python
@cli.group()
def proposals() -> None:
    """List, approve, or reject ingest proposals."""
    pass


@proposals.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_list(vault_path: Path) -> None:
    """List pending ingest proposals."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Failed"))
    items = resp.get("proposals", [])
    if not items:
        click.echo("No pending proposals.")
        return
    click.echo(f"{len(items)} pending proposal(s):\n")
    for item in items:
        click.echo(f"  {item['path']}")
        click.echo(f"    target: {item['target_page']} | action: {item['action']} | source: {item['source']}")


@proposals.command("approve")
@click.argument("proposal_path")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_approve(proposal_path: str, vault_path: Path) -> None:
    """Approve and merge an ingest proposal."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-approve", "path": proposal_path})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Approve failed"))
    click.echo(f"Approved and merged: {proposal_path}")


@proposals.command("reject")
@click.argument("proposal_path")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_reject(proposal_path: str, vault_path: Path) -> None:
    """Reject an ingest proposal."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-reject", "path": proposal_path})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Reject failed"))
    click.echo(f"Rejected: {proposal_path}")
```

Also add daemon routes for `proposals-list`, `proposals-approve`, `proposals-reject` in `server.py`. Find the route dispatch pattern and add:

```python
# proposals-list
elif request_type == "proposals-list":
    from llm_wiki.ingest.proposals import list_pending_proposals, read_proposal_meta
    proposals_dir = self._vault_root / "inbox" / "proposals"
    items = []
    for p in list_pending_proposals(proposals_dir):
        meta = read_proposal_meta(p)
        items.append({
            "path": str(p.relative_to(self._vault_root)),
            "target_page": meta.get("target_page", ""),
            "action": meta.get("action", ""),
            "status": meta.get("status", ""),
            "source": meta.get("source", ""),
        })
    return {"status": "ok", "proposals": items}

# proposals-approve
elif request_type == "proposals-approve":
    from llm_wiki.ingest.proposals import (
        read_proposal_meta, read_proposal_body, update_proposal_status, find_wiki_page,
    )
    from llm_wiki.ingest.page_writer import patch_token_estimates
    p = self._vault_root / request["path"]
    meta = read_proposal_meta(p)
    wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
    target_page = meta["target_page"]
    target_cluster = meta.get("target_cluster") or ""
    body = read_proposal_body(p)
    target = find_wiki_page(wiki_dir, target_page)
    if target is not None and body:
        existing = target.read_text(encoding="utf-8")
        if body not in existing:
            target.write_text(existing.rstrip() + "\n\n" + body + "\n", encoding="utf-8")
            patch_token_estimates(target)
    elif target is None and meta.get("action") == "create":
        # Write a new page from the proposal body, respecting cluster
        cluster_dir = wiki_dir / target_cluster if target_cluster else wiki_dir
        cluster_dir.mkdir(parents=True, exist_ok=True)
        new_path = cluster_dir / f"{target_page}.md"
        fm = {"title": meta["target_page"], "source": f"[[{meta['source']}]]", "created_by": "ingest"}
        import yaml as _yaml
        fm_text = "---\n" + _yaml.dump(fm, default_flow_style=False).strip() + "\n---\n\n"
        new_path.write_text(fm_text + body + "\n", encoding="utf-8")
        patch_token_estimates(new_path)
    update_proposal_status(p, "merged")
    return {"status": "ok", "merged": str(request["path"])}

# proposals-reject
elif request_type == "proposals-reject":
    from llm_wiki.ingest.proposals import update_proposal_status
    p = self._vault_root / request["path"]
    update_proposal_status(p, "rejected")
    return {"status": "ok", "rejected": str(request["path"])}
```

- [ ] **Step 4: Run full test suite — expect PASS**

```
pytest tests/ -q --tb=short
```

- [ ] **Step 5: Commit**

```
git add src/llm_wiki/cli/main.py src/llm_wiki/daemon/server.py
git commit -m "feat: add proposals list/approve/reject CLI subcommand and daemon routes"
```

---

### Task 14: Full test suite + self-check

- [ ] **Step 1: Run all tests**

```
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all 823 original tests pass, plus the new tests added in Tasks 1–13.

- [ ] **Step 2: Smoke-test the pipeline end-to-end**

Move `boltz2.pdf` to `~/wiki/raw/` if not already there:

```bash
cp ~/boltz2.pdf ~/wiki/raw/
```

Run ingest:

```bash
llm-wiki ingest ~/wiki/raw/boltz2.pdf
```

Expected output: `Copied to raw/boltz2.pdf` (if not already there), then concept names printed. Check proposals were written:

```bash
ls ~/wiki/inbox/proposals/
```

Run lint to trigger auditor proposal check:

```bash
llm-wiki lint
```

Check that high-score update proposals auto-merged and new-page proposals appeared as issues:

```bash
llm-wiki issues list
```

- [ ] **Step 3: Verify boltz-2.md was updated**

```bash
llm-wiki read boltz-2
```

Should contain new sections from the boltz2.pdf paper with `[[raw/boltz2.pdf]]` citations and `[[slug]]` wikilinks.

- [ ] **Step 4: Final commit**

```
git add -p  # stage any remaining changes
git commit -m "chore: verify ingest proposals pipeline end-to-end"
```
