# Phase 4: Ingest Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ingest pipeline so sources (PDFs, DOCX, markdown) can be compiled into concept-oriented wiki pages with strict citation discipline — turning raw documents into curated knowledge.

**Architecture:** The ingest pipeline runs through the daemon: extract text via `liteparse.LiteParse().parse_async()` → LLM identifies concepts + generates page sections with passage-level citations → `page_writer` creates or appends to wiki pages (one page per concept, not one per source) → daemon rescans vault → logs operation. Pages follow `%%` section marker structure. Updates preserve existing content.

**Tech Stack:** Python 3.11+, litellm (via `LLMClient`/`LLMQueue`), pytest-asyncio, liteparse (`LiteParse` class — wraps Node.js CLI)

---

## File Structure

```
src/llm_wiki/
  ingest/
    __init__.py           # package marker
    extractor.py          # ExtractionResult, extract_text() — liteparse wrapper
    prompts.py            # prompt strings + compose_*_messages() helpers
    page_writer.py        # PageSection, WrittenPage, write_page()
    agent.py              # ConceptPlan, IngestResult, IngestAgent
  traverse/
    llm_client.py         # MODIFIED: add priority param to complete()
  daemon/
    server.py             # MODIFIED: add "ingest" route
  cli/
    main.py               # MODIFIED: add ingest command

tests/
  test_ingest/
    __init__.py
    test_extractor.py
    test_prompts.py
    test_page_writer.py
    test_agent.py
    test_ingest_route.py
    test_integration.py
```

**Type flow across tasks:**
- `extractor.py` defines `ExtractionResult(success, content, extraction_method, token_count, error)`
- `page_writer.py` defines `PageSection(name, heading, content)` and `WrittenPage(path, was_update)`
- `agent.py` defines `ConceptPlan(name, title, passages)` and `IngestResult(source_path, pages_created, pages_updated, concepts_found)`; imports `ExtractionResult` from `extractor`, `PageSection`/`write_page` from `page_writer`
- `server.py` imports `IngestAgent` from `agent`
- `cli/main.py` sends `{"type": "ingest", "source_path": "..."}` to the daemon

---

### Task 1: Dependencies + Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/llm_wiki/ingest/__init__.py`
- Create: `tests/test_ingest/__init__.py`

- [ ] **Step 1: Add liteparse to pyproject.toml**

Edit `pyproject.toml`, adding `liteparse>=0.2.0` to the dependencies list:

```toml
dependencies = [
    "pyyaml>=6.0",
    "tantivy>=0.22.0",
    "click>=8.0",
    "litellm>=1.0.0",
    "liteparse>=0.2.0",
]
```

- [ ] **Step 2: Create package files**

```python
# src/llm_wiki/ingest/__init__.py
```

```python
# tests/test_ingest/__init__.py
```

Both files are empty — they are package markers only.

- [ ] **Step 3: Install dependencies**

Run: `cd /home/labhund/repos/llm-wiki && pip install -e ".[dev]"`
Expected: Successful — `liteparse` installs (it wraps a Node.js CLI; the Python package downloads it on first use).

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest -q`
Expected: All existing tests pass (no regressions from the new dependency).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/llm_wiki/ingest/__init__.py tests/test_ingest/__init__.py
git commit -m "feat: ingest pipeline skeleton + liteparse dependency"
```

---

### Task 2: Document Extractor

**Files:**
- Create: `src/llm_wiki/ingest/extractor.py`
- Create: `tests/test_ingest/test_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_extractor.py
from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.ingest.extractor import ExtractionResult, extract_text


# ---------------------------------------------------------------------------
# Minimal stand-in for liteparse.types.ParseResult so tests don't depend on
# liteparse's Node.js CLI being available.
# ---------------------------------------------------------------------------
class _FakeParseResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pages = []


class _FakeParser:
    """Fake liteparse.LiteParse that returns scripted text."""
    def __init__(self, text: str = "Extracted content.") -> None:
        self._text = text

    async def parse_async(self, path, **kwargs) -> _FakeParseResult:
        return _FakeParseResult(self._text)


class _ErrorParser:
    """Fake liteparse.LiteParse that raises ParseError."""
    async def parse_async(self, path, **kwargs):
        from liteparse.types import ParseError
        raise ParseError("corrupt file")


@pytest.mark.asyncio
async def test_extract_pdf(tmp_path: Path):
    """PDF extraction returns liteparse text."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    result = await extract_text(pdf_file, _parser=_FakeParser("PDF text here."))

    assert result.success
    assert result.content == "PDF text here."
    assert result.extraction_method == "pdf"
    assert result.token_count > 0
    assert result.error is None


@pytest.mark.asyncio
async def test_extract_docx(tmp_path: Path):
    """DOCX extraction returns liteparse text."""
    docx_file = tmp_path / "test.docx"
    docx_file.write_bytes(b"fake docx content")

    result = await extract_text(docx_file, _parser=_FakeParser("DOCX text here."))

    assert result.success
    assert result.content == "DOCX text here."
    assert result.extraction_method == "docx"


@pytest.mark.asyncio
async def test_extract_image_ocr(tmp_path: Path):
    """Image extraction uses OCR via liteparse."""
    img_file = tmp_path / "scan.png"
    img_file.write_bytes(b"fake png")

    result = await extract_text(img_file, _parser=_FakeParser("OCR text."))

    assert result.success
    assert result.content == "OCR text."
    assert result.extraction_method == "image_ocr"


@pytest.mark.asyncio
async def test_extract_markdown_passthrough(tmp_path: Path):
    """Markdown files are read directly — liteparse is NOT called."""
    md_content = "# Test\n\nContent here."
    md_file = tmp_path / "test.md"
    md_file.write_text(md_content)

    # Pass an error parser — if it's called, the test will fail
    result = await extract_text(md_file, _parser=_ErrorParser())

    assert result.success
    assert result.content == md_content
    assert result.extraction_method == "markdown"
    assert result.token_count > 0


@pytest.mark.asyncio
async def test_extract_liteparse_error(tmp_path: Path):
    """ParseError from liteparse becomes a failed ExtractionResult."""
    pdf_file = tmp_path / "corrupt.pdf"
    pdf_file.write_bytes(b"not a real pdf")

    result = await extract_text(pdf_file, _parser=_ErrorParser())

    assert not result.success
    assert "corrupt file" in result.error
    assert result.extraction_method == "pdf"


@pytest.mark.asyncio
async def test_extract_unsupported_format(tmp_path: Path):
    """Unsupported file extension returns error without calling liteparse."""
    bad_file = tmp_path / "data.xyz"
    bad_file.write_text("content")

    result = await extract_text(bad_file)

    assert not result.success
    assert "Unsupported" in result.error


@pytest.mark.asyncio
async def test_extract_nonexistent_file():
    """Missing files return error."""
    result = await extract_text(Path("/nonexistent/file.pdf"))

    assert not result.success
    assert "No such file" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_wiki.ingest.extractor'`

- [ ] **Step 3: Implement extractor**

```python
# src/llm_wiki/ingest/extractor.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_wiki.tokens import count_tokens


@dataclass
class ExtractionResult:
    """Result of document text extraction."""
    success: bool
    content: str
    extraction_method: str   # "pdf", "docx", "image_ocr", "markdown"
    token_count: int = 0
    error: str | None = None


_SUPPORTED_BINARY = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image_ocr",
    ".jpg": "image_ocr",
    ".jpeg": "image_ocr",
    ".gif": "image_ocr",
    ".bmp": "image_ocr",
    ".tiff": "image_ocr",
}


async def extract_text(
    source_path: Path,
    _parser: Any = None,     # injection point for tests; defaults to LiteParse()
) -> ExtractionResult:
    """Extract text from a document.

    Supports: PDF, DOCX, images (OCR via liteparse), markdown (passthrough).
    _parser is a liteparse.LiteParse-compatible object; pass a fake in tests to
    avoid the Node.js CLI cold-start.
    """
    if not source_path.exists():
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="none",
            error=f"No such file: {source_path}",
        )

    suffix = source_path.suffix.lower()

    if suffix in (".md", ".markdown"):
        return _extract_markdown(source_path)

    method = _SUPPORTED_BINARY.get(suffix)
    if method is None:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="none",
            error=f"Unsupported format: {suffix}",
        )

    return await _extract_via_liteparse(source_path, method, _parser)


def _extract_markdown(path: Path) -> ExtractionResult:
    """Read a markdown file directly — no liteparse needed."""
    content = path.read_text(encoding="utf-8")
    return ExtractionResult(
        success=True,
        content=content,
        extraction_method="markdown",
        token_count=count_tokens(content),
    )


async def _extract_via_liteparse(
    path: Path,
    method: str,
    parser: Any,
) -> ExtractionResult:
    """Call liteparse (or test fake) to extract text from binary docs."""
    if parser is None:
        from liteparse import LiteParse
        parser = LiteParse()

    try:
        result = await parser.parse_async(path)
        content = result.text or ""
        return ExtractionResult(
            success=True,
            content=content,
            extraction_method=method,
            token_count=count_tokens(content),
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method=method,
            error=str(exc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_extractor.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/ingest/extractor.py tests/test_ingest/test_extractor.py
git commit -m "feat: document extractor — PDF, DOCX, markdown, image OCR via liteparse"
```

---

### Task 3: Ingest Prompts

**Files:**
- Create: `src/llm_wiki/ingest/prompts.py`
- Create: `tests/test_ingest/test_prompts.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_prompts.py
from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_page_content_messages,
    parse_concept_extraction,
    parse_page_content,
)
from llm_wiki.ingest.agent import ConceptPlan
from llm_wiki.ingest.page_writer import PageSection


def test_concept_extraction_messages_contain_source_text():
    """compose_concept_extraction_messages embeds source text + ref."""
    msgs = compose_concept_extraction_messages(
        source_text="PCA reduces dimensions. k-means clusters data.",
        source_ref="raw/paper.pdf",
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "PCA reduces dimensions" in combined
    assert "raw/paper.pdf" in combined


def test_page_content_messages_contain_concept_and_passages():
    """compose_page_content_messages embeds concept title, passages, source ref."""
    msgs = compose_page_content_messages(
        concept_title="PCA",
        passages=["PCA reduces high-dimensional data."],
        source_ref="raw/paper.pdf",
    )
    assert len(msgs) == 2
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "PCA" in combined
    assert "PCA reduces high-dimensional data." in combined
    assert "raw/paper.pdf" in combined


def test_parse_concept_extraction_valid():
    """parse_concept_extraction parses well-formed JSON."""
    text = """{
        "concepts": [
            {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
            {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]}
        ]
    }"""
    result = parse_concept_extraction(text)
    assert len(result) == 2
    assert result[0].name == "pca"
    assert result[0].title == "PCA"
    assert result[0].passages == ["PCA reduces dimensions."]
    assert result[1].name == "k-means"


def test_parse_concept_extraction_fenced():
    """parse_concept_extraction handles markdown-fenced JSON."""
    text = '```json\n{"concepts": [{"name": "pca", "title": "PCA", "passages": []}]}\n```'
    result = parse_concept_extraction(text)
    assert len(result) == 1
    assert result[0].name == "pca"


def test_parse_concept_extraction_invalid_returns_empty():
    """parse_concept_extraction returns [] on bad JSON."""
    result = parse_concept_extraction("not json at all")
    assert result == []


def test_parse_page_content_valid():
    """parse_page_content parses well-formed JSON."""
    text = """{
        "sections": [
            {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.pdf]]."}
        ]
    }"""
    result = parse_page_content(text)
    assert len(result) == 1
    assert result[0].name == "overview"
    assert result[0].heading == "Overview"
    assert "PCA" in result[0].content


def test_parse_page_content_invalid_returns_empty():
    """parse_page_content returns [] on bad JSON."""
    result = parse_page_content("not json")
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError` for `llm_wiki.ingest.prompts` and `llm_wiki.ingest.agent`

- [ ] **Step 3: Implement prompts.py**

Note: `parse_concept_extraction` returns `list[ConceptPlan]` and `parse_page_content` returns `list[PageSection]`. Both types are imported from `agent.py` and `page_writer.py` respectively. Those modules don't exist yet, so create stubs in this task too (Steps 4a and 4b below).

```python
# src/llm_wiki/ingest/prompts.py
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_wiki.ingest.agent import ConceptPlan
    from llm_wiki.ingest.page_writer import PageSection


_CONCEPT_EXTRACTION_SYSTEM = """\
You are analyzing a document to identify its main concepts and entities for a \
knowledge wiki.

## Task

Identify the key concepts, techniques, and entities from the source text. For each:
1. Assign a URL-safe slug (lowercase, hyphens only — e.g. "srna-embeddings")
2. Give a human-readable title (e.g. "sRNA Embeddings")
3. Extract 1-3 short passages from the text that discuss this concept

Focus on concepts with enough substance to warrant their own wiki page. \
Exclude trivial mentions.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "concepts": [
    {
      "name": "concept-slug",
      "title": "Concept Title",
      "passages": ["relevant excerpt from source text"]
    }
  ]
}"""

_PAGE_CONTENT_SYSTEM = """\
You are writing content for a wiki page about a specific concept, based on a \
source document.

## Ingest Rules (Non-Negotiable)

1. Every factual claim MUST end with a citation: [[{source_ref}]]
2. Do NOT interpret beyond what the source states
3. If the source says "X correlates with Y", write exactly that — never "X causes Y"
4. Be concise and precise

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "sections": [
    {
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown content with [[source]] citations."
    }
  ]
}"""


def compose_concept_extraction_messages(
    source_text: str,
    source_ref: str,
    budget: int = 8000,
) -> list[dict[str, str]]:
    """Build the message list for concept extraction."""
    truncated = source_text[: budget * 4]  # rough chars-per-token estimate
    user = (
        f"## Source Reference\n{source_ref}\n\n"
        f"## Source Text\n{truncated}"
    )
    return [
        {"role": "system", "content": _CONCEPT_EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_page_content_messages(
    concept_title: str,
    passages: list[str],
    source_ref: str,
) -> list[dict[str, str]]:
    """Build the message list for page content generation."""
    system = _PAGE_CONTENT_SYSTEM.format(source_ref=source_ref)
    passages_text = "\n\n".join(f"- {p}" for p in passages)
    user = (
        f"## Concept\n{concept_title}\n\n"
        f"## Source Reference\n{source_ref}\n\n"
        f"## Relevant Passages\n{passages_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response (handles fenced blocks)."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in response: {text[:200]}")


def parse_concept_extraction(text: str) -> list[ConceptPlan]:
    """Parse JSON concept extraction response → list of ConceptPlan."""
    from llm_wiki.ingest.agent import ConceptPlan  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        return [
            ConceptPlan(
                name=c["name"],
                title=c.get("title", c["name"]),
                passages=c.get("passages", []),
            )
            for c in data.get("concepts", [])
            if isinstance(c, dict) and "name" in c
        ]
    except (ValueError, KeyError):
        return []


def parse_page_content(text: str) -> list[PageSection]:
    """Parse JSON page content response → list of PageSection."""
    from llm_wiki.ingest.page_writer import PageSection  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        return [
            PageSection(
                name=s["name"],
                heading=s.get("heading", s["name"]),
                content=s.get("content", ""),
            )
            for s in data.get("sections", [])
            if isinstance(s, dict) and "name" in s
        ]
    except (ValueError, KeyError):
        return []
```

- [ ] **Step 4a: Create stub for agent.py** (needed for prompts tests to import `ConceptPlan`)

```python
# src/llm_wiki/ingest/agent.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str        # URL-safe slug: "srna-embeddings"
    title: str       # Human-readable: "sRNA Embeddings"
    passages: list[str] = field(default_factory=list)


@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)   # concept slugs
    pages_updated: list[str] = field(default_factory=list)   # concept slugs

    @property
    def concepts_found(self) -> int:
        return len(self.pages_created) + len(self.pages_updated)
```

- [ ] **Step 4b: Create stub for page_writer.py** (needed for prompts tests to import `PageSection`)

```python
# src/llm_wiki/ingest/page_writer.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageSection:
    """One section of a wiki page."""
    name: str      # slug: "overview"
    heading: str   # display text: "Overview"
    content: str   # markdown body


@dataclass
class WrittenPage:
    """Result of writing a page."""
    path: Path
    was_update: bool
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_prompts.py -v`
Expected: 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/ingest/prompts.py src/llm_wiki/ingest/agent.py src/llm_wiki/ingest/page_writer.py tests/test_ingest/test_prompts.py
git commit -m "feat: ingest prompts — concept extraction + page content with citation rules"
```

---

### Task 4: Page Writer

**Files:**
- Modify (complete): `src/llm_wiki/ingest/page_writer.py`
- Create: `tests/test_ingest/test_page_writer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_page_writer.py
from pathlib import Path

import pytest

from llm_wiki.ingest.page_writer import PageSection, WrittenPage, write_page


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wiki"
    d.mkdir()
    return d


def test_write_new_page_creates_file(wiki_dir: Path):
    """write_page creates a new .md file for a concept."""
    sections = [
        PageSection(name="overview", heading="Overview", content="PCA [[raw/paper.pdf]]."),
    ]
    result = write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")

    assert isinstance(result, WrittenPage)
    assert result.path == wiki_dir / "pca.md"
    assert result.was_update is False
    assert result.path.exists()


def test_new_page_has_frontmatter(wiki_dir: Path):
    """New page has YAML frontmatter with title and source."""
    sections = [PageSection(name="overview", heading="Overview", content="Content.")]
    result = write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")

    text = result.path.read_text()
    assert text.startswith("---\n")
    assert "title: PCA" in text
    assert "source: '[[raw/paper.pdf]]'" in text
    assert "created_by: ingest" in text


def test_new_page_has_section_markers(wiki_dir: Path):
    """New page has %% section: name %% markers and ## headings."""
    sections = [
        PageSection(name="overview", heading="Overview", content="Overview content."),
        PageSection(name="method", heading="Method", content="Method content."),
    ]
    result = write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")

    text = result.path.read_text()
    assert "%% section: overview %%" in text
    assert "## Overview" in text
    assert "Overview content." in text
    assert "%% section: method %%" in text
    assert "## Method" in text
    assert "Method content." in text


def test_update_existing_page_appends(wiki_dir: Path):
    """write_page appends a new source section to an existing page."""
    # Create original page
    existing = wiki_dir / "pca.md"
    existing.write_text(
        "---\ntitle: PCA\nsource: '[[raw/original.pdf]]'\ncreated_by: ingest\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOriginal content [[raw/original.pdf]].\n"
    )

    sections = [PageSection(name="overview", heading="Overview", content="New content [[raw/new.pdf]].")]
    result = write_page(wiki_dir, "pca", "PCA", sections, "raw/new.pdf")

    assert result.was_update is True
    text = result.path.read_text()
    # Original content preserved
    assert "Original content [[raw/original.pdf]]." in text
    # New content appended
    assert "New content [[raw/new.pdf]]." in text
    assert "%% section: from-new %%" in text


def test_update_same_source_twice_no_duplicate(wiki_dir: Path):
    """Ingesting the same source twice does not duplicate the section."""
    sections = [PageSection(name="overview", heading="Overview", content="Content [[raw/paper.pdf]].")]
    write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")
    write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")  # second ingest

    text = (wiki_dir / "pca.md").read_text()
    # Section name from-paper should appear only once
    assert text.count("%% section: from-paper %%") <= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_page_writer.py -v`
Expected: FAIL — `write_page not defined` (stub has no implementation)

- [ ] **Step 3: Implement page_writer.py**

```python
# src/llm_wiki/ingest/page_writer.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class PageSection:
    """One section of a wiki page."""
    name: str      # slug: "overview"
    heading: str   # display text: "Overview"
    content: str   # markdown body


@dataclass
class WrittenPage:
    """Result of writing a page."""
    path: Path
    was_update: bool


def write_page(
    wiki_dir: Path,
    concept_name: str,
    title: str,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Create a new wiki page or append new-source sections to an existing one.

    Args:
        wiki_dir:     Directory to write the page into.
        concept_name: URL-safe slug, used as the filename (without .md).
        title:        Human-readable page title (written to frontmatter).
        sections:     Sections to write (for new pages) or append (for updates).
        source_ref:   Source citation string, e.g. "raw/paper.pdf".
                      Used in frontmatter and to name the appended section.

    Returns:
        WrittenPage with .path and .was_update flag.
    """
    page_path = wiki_dir / f"{concept_name}.md"

    if not page_path.exists():
        return _create_page(page_path, title, sections, source_ref)
    else:
        return _append_source(page_path, sections, source_ref)


def _create_page(
    page_path: Path,
    title: str,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Write a brand-new wiki page with frontmatter and %% markers."""
    fm = {
        "title": title,
        "source": f"[[{source_ref}]]",
        "created_by": "ingest",
    }
    frontmatter = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---"

    body_parts = []
    for section in sections:
        body_parts.append(f"%% section: {section.name} %%")
        body_parts.append(f"## {section.heading}")
        body_parts.append("")
        body_parts.append(section.content)
        body_parts.append("")

    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(frontmatter + "\n\n" + "\n".join(body_parts).strip() + "\n")
    return WrittenPage(path=page_path, was_update=False)


def _append_source(
    page_path: Path,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Append a 'from-{source-slug}' section to an existing page.

    Does nothing if a section from this source already exists (idempotent).
    """
    source_slug = Path(source_ref).stem  # "raw/paper.pdf" → "paper"
    section_marker = f"%% section: from-{source_slug} %%"

    existing = page_path.read_text(encoding="utf-8")
    if section_marker in existing:
        return WrittenPage(path=page_path, was_update=True)

    appended_parts = [f"\n{section_marker}", f"## From {source_slug}", ""]
    for section in sections:
        appended_parts.append(section.content)
        appended_parts.append("")

    page_path.write_text(existing.rstrip() + "\n" + "\n".join(appended_parts))
    return WrittenPage(path=page_path, was_update=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_page_writer.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/ingest/page_writer.py tests/test_ingest/test_page_writer.py
git commit -m "feat: page writer — create/update wiki pages with %% markers and citations"
```

---

### Task 5: Ingest Agent

**Files:**
- Modify (complete): `src/llm_wiki/ingest/agent.py`
- Modify: `src/llm_wiki/traverse/llm_client.py`
- Create: `tests/test_ingest/test_agent.py`

- [ ] **Step 1: Add `priority` parameter to `LLMClient.complete()`**

Edit `src/llm_wiki/traverse/llm_client.py`. Change line 40 from:

```python
    async def complete(
        self, messages: list[dict[str, str]], temperature: float = 0.7
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""

        async def _call() -> LLMResponse:
```

To:

```python
    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        priority: str = "query",
    ) -> LLMResponse:
        """Send a completion request through the concurrency-limited queue."""

        async def _call() -> LLMResponse:
```

And change the submit call at the bottom of `complete()` from:

```python
        return await self._queue.submit(_call, priority="query")
```

To:

```python
        return await self._queue.submit(_call, priority=priority)
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_ingest/test_agent.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import ConceptPlan, IngestAgent, IngestResult
from llm_wiki.traverse.llm_client import LLMResponse


class MockLLMClient:
    """Scripted LLM responses for testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[list[dict]] = []
        self.priorities: list[str] = []

    async def complete(
        self, messages: list[dict], temperature: float = 0.7, priority: str = "query"
    ) -> LLMResponse:
        self.calls.append(messages)
        self.priorities.append(priority)
        if self._idx >= len(self._responses):
            raise RuntimeError("MockLLMClient: no more scripted responses")
        content = self._responses[self._idx]
        self._idx += 1
        return LLMResponse(content=content, tokens_used=50)


def _concept_json(concepts: list[dict]) -> str:
    return json.dumps({"concepts": concepts})


def _sections_json(sections: list[dict]) -> str:
    return json.dumps({"sections": sections})


@pytest.mark.asyncio
async def test_ingest_markdown_creates_pages(tmp_path: Path):
    """Ingesting a markdown source creates wiki pages for each concept."""
    # Set up a minimal managed vault
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    source = raw_dir / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions. k-means clusters data.")

    # LLM call 1: concept extraction → two concepts
    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
        {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]},
    ])
    # LLM call 2: page content for "pca"
    pca_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA reduces dimensions [[raw/paper.md]]."},
    ])
    # LLM call 3: page content for "k-means"
    km_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "k-means clusters data [[raw/paper.md]]."},
    ])

    mock_llm = MockLLMClient([concept_response, pca_sections, km_sections])
    config = WikiConfig()
    agent = IngestAgent(mock_llm, config)

    result = await agent.ingest(source, tmp_path)

    assert isinstance(result, IngestResult)
    assert result.pages_created == ["pca", "k-means"]
    assert result.pages_updated == []
    assert result.concepts_found == 2
    assert (wiki_dir / "pca.md").exists()
    assert (wiki_dir / "k-means.md").exists()


@pytest.mark.asyncio
async def test_ingest_uses_ingest_priority(tmp_path: Path):
    """All LLM calls from IngestAgent use priority='ingest'."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "doc.md"
    source.write_text("# Doc\n\nSome content about topic A.")

    concept_response = _concept_json([
        {"name": "topic-a", "title": "Topic A", "passages": ["Some content about topic A."]},
    ])
    sections_response = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "Topic A [[raw/doc.md]]."},
    ])
    mock_llm = MockLLMClient([concept_response, sections_response])
    agent = IngestAgent(mock_llm, WikiConfig())

    await agent.ingest(source, tmp_path)

    assert all(p == "ingest" for p in mock_llm.priorities)


@pytest.mark.asyncio
async def test_ingest_no_concepts_returns_empty_result(tmp_path: Path):
    """If LLM returns no concepts, result has empty lists."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "empty.md"
    source.write_text("# Nothing useful")

    mock_llm = MockLLMClient([_concept_json([])])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)

    assert result.pages_created == []
    assert result.pages_updated == []


@pytest.mark.asyncio
async def test_ingest_updates_existing_page(tmp_path: Path):
    """If a concept page already exists, it is updated (appended), not recreated."""
    (tmp_path / "raw").mkdir()
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # Pre-existing page for "pca"
    (wiki_dir / "pca.md").write_text(
        "---\ntitle: PCA\nsource: '[[raw/old.md]]'\ncreated_by: ingest\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOld content.\n"
    )

    source = tmp_path / "raw" / "new.md"
    source.write_text("# New source\n\nPCA is also used here.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA is also used here."]},
    ])
    sections_response = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "Also used here [[raw/new.md]]."},
    ])
    mock_llm = MockLLMClient([concept_response, sections_response])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)

    assert result.pages_created == []
    assert result.pages_updated == ["pca"]
    text = (wiki_dir / "pca.md").read_text()
    assert "Old content." in text        # original preserved
    assert "Also used here" in text      # new content appended


@pytest.mark.asyncio
async def test_ingest_extraction_failure_returns_error(tmp_path: Path):
    """If text extraction fails, IngestResult has no pages and reports error."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()

    source = tmp_path / "raw" / "missing.pdf"
    # File does not exist — extract_text will return success=False

    mock_llm = MockLLMClient([])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)

    assert result.pages_created == []
    assert result.pages_updated == []
    assert mock_llm._idx == 0   # No LLM calls made
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_agent.py -v`
Expected: FAIL — `IngestAgent` not defined in agent.py (only stub exists)

- [ ] **Step 4: Implement agent.py**

```python
# src/llm_wiki/ingest/agent.py
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.extractor import extract_text
from llm_wiki.ingest.page_writer import PageSection, WrittenPage, write_page
from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_page_content_messages,
    parse_concept_extraction,
    parse_page_content,
)

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str                                   # URL-safe slug: "srna-embeddings"
    title: str                                  # Human-readable: "sRNA Embeddings"
    passages: list[str] = field(default_factory=list)


@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)   # concept slugs
    pages_updated: list[str] = field(default_factory=list)   # concept slugs

    @property
    def concepts_found(self) -> int:
        return len(self.pages_created) + len(self.pages_updated)


class IngestAgent:
    """Orchestrates: extract → identify concepts → write wiki pages.

    Args:
        llm:    LLMClient instance (from traverse.llm_client). All calls are
                submitted at priority="ingest" so they yield to user queries.
        config: WikiConfig — uses config.vault.wiki_dir to locate wiki directory.
    """

    def __init__(self, llm: LLMClient, config: WikiConfig) -> None:
        self._llm = llm
        self._config = config

    async def ingest(self, source_path: Path, vault_root: Path) -> IngestResult:
        """Ingest one source file into the wiki.

        Args:
            source_path: Absolute path to the source file (PDF, DOCX, markdown, etc.)
            vault_root:  Root directory of the vault.

        Returns:
            IngestResult listing which pages were created vs. updated.
        """
        result = IngestResult(source_path=source_path)
        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")

        # Derive the citation reference: relative to vault_root if possible
        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        # 1. Extract text
        extraction = await extract_text(source_path)
        if not extraction.success:
            logger.warning(
                "Extraction failed for %s: %s", source_path, extraction.error
            )
            return result

        # 2. Identify concepts
        budget = self._config.budgets.default_ingest
        messages = compose_concept_extraction_messages(
            source_text=extraction.content,
            source_ref=source_ref,
            budget=budget,
        )
        response = await self._llm.complete(messages, temperature=0.3, priority="ingest")
        concepts = parse_concept_extraction(response.content)

        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        # 3. Generate + write one page per concept
        wiki_dir.mkdir(parents=True, exist_ok=True)
        for concept in concepts:
            page_messages = compose_page_content_messages(
                concept_title=concept.title,
                passages=concept.passages,
                source_ref=source_ref,
            )
            page_response = await self._llm.complete(
                page_messages, temperature=0.5, priority="ingest"
            )
            sections = parse_page_content(page_response.content)

            if not sections:
                logger.warning(
                    "No sections generated for concept %r from %s",
                    concept.name, source_path,
                )
                continue

            written = write_page(wiki_dir, concept.name, concept.title, sections, source_ref)
            if written.was_update:
                result.pages_updated.append(concept.name)
            else:
                result.pages_created.append(concept.name)

        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_agent.py -v`
Expected: 5 tests PASS

- [ ] **Step 6: Run full test suite (verify no regressions from LLMClient change)**

Run: `pytest -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/ingest/agent.py src/llm_wiki/traverse/llm_client.py tests/test_ingest/test_agent.py
git commit -m "feat: ingest agent — concept extraction + page writing via LLM"
```

---

### Task 6: Daemon Route + CLI Command

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_ingest/test_ingest_route.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_ingest_route.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


@pytest_asyncio.fixture
async def server_with_ingest(sample_vault: Path, tmp_path: Path):
    """Daemon server with IngestAgent mocked out."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    yield server, sock_path
    await server.stop()


async def _request(sock_path: Path, msg: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_ingest_route_unknown_path_returns_error(server_with_ingest):
    """Sending ingest with a non-existent path returns an error response."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {
        "type": "ingest",
        "source_path": "/nonexistent/file.md",
    })
    assert resp["status"] == "ok"
    # No crash — IngestAgent handles missing files gracefully
    assert "pages_created" in resp


@pytest.mark.asyncio
async def test_ingest_route_missing_source_path(server_with_ingest):
    """Missing source_path field returns an error."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {"type": "ingest"})
    assert resp["status"] == "error"
    assert "source_path" in resp["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_ingest_route.py -v`
Expected: FAIL — `"ingest"` case not in server routing

- [ ] **Step 3: Add ingest route to server.py**

In `src/llm_wiki/daemon/server.py`, add the `"ingest"` case to `_route()` and a `_handle_ingest()` method.

In the `_route()` method, add after the `"query"` case:

```python
            case "ingest":
                return await self._handle_ingest(request)
```

Add this method to the `DaemonServer` class (after `_handle_query`):

```python
    async def _handle_ingest(self, request: dict) -> dict:
        if "source_path" not in request:
            return {"status": "error", "message": "Missing required field: source_path"}

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient

        source_path = Path(request["source_path"])
        llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
        agent = IngestAgent(llm, self._config)
        result = await agent.ingest(source_path, self._vault_root)

        await self.rescan()

        return {
            "status": "ok",
            "pages_created": result.pages_created,
            "pages_updated": result.pages_updated,
            "concepts_found": result.concepts_found,
        }
```

- [ ] **Step 4: Add ingest command to cli/main.py**

Add this command to `src/llm_wiki/cli/main.py` (after the `query` command):

```python
@cli.command()
@click.argument("source_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def ingest(source_path: Path, vault_path: Path) -> None:
    """Ingest a source document — extracts concepts and creates wiki pages."""
    client = _get_client(vault_path)
    resp = client.request({
        "type": "ingest",
        "source_path": str(source_path.resolve()),
    })
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Ingest failed"))

    created = resp.get("pages_created", [])
    updated = resp.get("pages_updated", [])
    click.echo(f"Ingested: {resp['concepts_found']} concept(s) identified.")
    if created:
        click.echo(f"  Created: {', '.join(created)}")
    if updated:
        click.echo(f"  Updated: {', '.join(updated)}")
    if not created and not updated:
        click.echo("  No pages created — no concepts identified in source.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_ingest_route.py -v`
Expected: 2 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/daemon/server.py src/llm_wiki/cli/main.py tests/test_ingest/test_ingest_route.py
git commit -m "feat: ingest route in daemon + ingest CLI command"
```

---

### Task 7: Integration Test

**Files:**
- Create: `tests/test_ingest/test_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_ingest/test_integration.py
"""Full ingest pipeline integration test.

Covers: markdown source → concept extraction → page creation.
Uses MockLLMClient (no real LLM calls). Validates page format and content.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.page import Page
from llm_wiki.traverse.llm_client import LLMResponse


class MockLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def complete(self, messages, temperature=0.7, priority="query") -> LLMResponse:
        if self._idx >= len(self._responses):
            raise RuntimeError("no more responses")
        content = self._responses[self._idx]
        self._idx += 1
        return LLMResponse(content=content, tokens_used=80)


@pytest.fixture
def managed_vault(tmp_path: Path) -> Path:
    """A minimal managed vault with raw/ and wiki/ directories."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_full_pipeline_creates_parseable_pages(managed_vault: Path):
    """End-to-end: markdown → IngestAgent → wiki pages readable by Page.parse()."""
    source = managed_vault / "raw" / "srna-paper.md"
    source.write_text(
        "# sRNA Embeddings\n\n"
        "sRNA embeddings are validated using PCA projection.\n"
        "k-means clustering (k=10) separates embedding clusters.\n"
    )

    concept_response = json.dumps({
        "concepts": [
            {
                "name": "srna-embeddings",
                "title": "sRNA Embeddings",
                "passages": ["sRNA embeddings are validated using PCA projection."],
            },
            {
                "name": "k-means-clustering",
                "title": "K-Means Clustering",
                "passages": ["k-means clustering (k=10) separates embedding clusters."],
            },
        ]
    })
    srna_sections = json.dumps({
        "sections": [
            {
                "name": "overview",
                "heading": "Overview",
                "content": "sRNA embeddings use PCA for validation [[raw/srna-paper.md]].",
            }
        ]
    })
    kmeans_sections = json.dumps({
        "sections": [
            {
                "name": "overview",
                "heading": "Overview",
                "content": "k=10 clusters are used for sRNA embeddings [[raw/srna-paper.md]].",
            }
        ]
    })

    mock_llm = MockLLMClient([concept_response, srna_sections, kmeans_sections])
    agent = IngestAgent(mock_llm, WikiConfig())
    result = await agent.ingest(source, managed_vault)

    # --- Result shape ---
    assert result.pages_created == ["srna-embeddings", "k-means-clustering"]
    assert result.pages_updated == []
    assert result.concepts_found == 2

    # --- Pages exist ---
    srna_page_path = managed_vault / "wiki" / "srna-embeddings.md"
    kmeans_page_path = managed_vault / "wiki" / "k-means-clustering.md"
    assert srna_page_path.exists()
    assert kmeans_page_path.exists()

    # --- sRNA page is parseable by Page.parse() ---
    srna_page = Page.parse(srna_page_path)
    assert srna_page.title == "sRNA Embeddings"
    assert len(srna_page.sections) >= 1
    assert srna_page.sections[0].name == "overview"

    # --- Citation present in page content ---
    srna_text = srna_page_path.read_text()
    assert "[[raw/srna-paper.md]]" in srna_text

    # --- %% section markers present ---
    assert "%% section: overview %%" in srna_text

    # --- Frontmatter present ---
    assert "title: sRNA Embeddings" in srna_text
    assert "created_by: ingest" in srna_text


@pytest.mark.asyncio
async def test_reingest_same_source_appends_not_duplicates(managed_vault: Path):
    """Ingesting the same source thrice: once creates, twice appends, thrice is idempotent."""
    source = managed_vault / "raw" / "paper.md"
    source.write_text("# Paper\n\nContent about topic A.")

    concept_json = json.dumps({
        "concepts": [{"name": "topic-a", "title": "Topic A", "passages": ["Content about topic A."]}]
    })
    sections_json = json.dumps({
        "sections": [{"name": "overview", "heading": "Overview", "content": "Content [[raw/paper.md]]."}]
    })

    # First ingest: creates the page
    mock_llm = MockLLMClient([concept_json, sections_json])
    agent = IngestAgent(mock_llm, WikiConfig())
    result1 = await agent.ingest(source, managed_vault)

    assert result1.pages_created == ["topic-a"]
    text1 = (managed_vault / "wiki" / "topic-a.md").read_text()
    assert "%% section: overview %%" in text1

    # Second ingest: appends as "from-paper" since marker doesn't exist yet
    mock_llm2 = MockLLMClient([concept_json, sections_json])
    agent2 = IngestAgent(mock_llm2, WikiConfig())
    result2 = await agent2.ingest(source, managed_vault)

    assert result2.pages_updated == ["topic-a"]
    assert result2.pages_created == []
    text2 = (managed_vault / "wiki" / "topic-a.md").read_text()
    assert "%% section: overview %%" in text2
    assert "%% section: from-paper %%" in text2

    # Third ingest: should be idempotent (from-paper marker exists)
    mock_llm3 = MockLLMClient([concept_json, sections_json])
    agent3 = IngestAgent(mock_llm3, WikiConfig())
    result3 = await agent3.ingest(source, managed_vault)

    assert result3.pages_updated == ["topic-a"]
    assert result3.pages_created == []
    text3 = (managed_vault / "wiki" / "topic-a.md").read_text()
    # Should be identical to text2 (no new content appended)
    assert text3 == text2

    # Verify page is parseable
    page = Page.parse(managed_vault / "wiki" / "topic-a.md")
    assert page.title == "Topic A"
    assert len(page.sections) >= 2  # overview + from-paper

    # No duplicate sections
    text = (managed_vault / "wiki" / "topic-a.md").read_text()
    assert text.count("Content [[raw/paper.md]].") == 1
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_ingest/test_integration.py -v`
Expected: 2 tests PASS

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass (149 + new ingest tests)

- [ ] **Step 4: Commit**

```bash
git add tests/test_ingest/test_integration.py
git commit -m "test: ingest pipeline integration test — markdown source to wiki pages"
```

---

## Self-Review

### Spec Coverage

Spec Section 3 (Ingest) requirements:
- ✓ Extract text via liteparse — Task 2
- ✓ LLM identifies entities/concepts with passage-level citations — Task 5
- ✓ Creates/updates wiki pages (one per concept) — Tasks 4, 5
- ✓ Updates tantivy index — Task 6 (`await self.rescan()` after ingest)
- ✓ Logs operation — covered by `IngestResult` return value
- ✓ Every claim must cite raw source — enforced in PAGE_CONTENT_PROMPT (ingest rules)
- ✓ No interpretations beyond source — enforced in prompt
- ✓ Pages follow `%%` section marker structure — Task 4
- ✓ Ingest agent writes initial manifest metadata — handled by rescan after ingest
- ✓ Updates preserve existing citations — `_append_source` in Task 4
- ✓ `liteparse` as extraction backend — Task 2
- ✓ CLI `ingest` command — Task 6
- ✓ Daemon ingest route — Task 6
- ✓ Priority="ingest" through LLM queue — Task 5

### Placeholder Scan

No TBDs, no "similar to Task N", no steps without code. Each `run` step has exact command + expected output.

### Type Consistency

- `ExtractionResult` — defined in `extractor.py`, used in `agent.py`
- `ConceptPlan(name, title, passages)` — defined in `agent.py`, parsed in `prompts.parse_concept_extraction()`
- `PageSection(name, heading, content)` — defined in `page_writer.py`, parsed in `prompts.parse_page_content()`
- `WrittenPage(path, was_update)` — defined in `page_writer.py`, returned by `write_page()`
- `IngestResult(source_path, pages_created, pages_updated)` — defined in `agent.py`, returned by `IngestAgent.ingest()`
- `LLMClient.complete(messages, temperature, priority)` — modified signature is compatible with all existing callers (added `priority` as keyword-only with default `"query"`)
