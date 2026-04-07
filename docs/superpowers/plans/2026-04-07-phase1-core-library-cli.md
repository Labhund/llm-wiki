# Phase 1: Core Library + CLI (Vault Mode) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundational core library and CLI so users can point at an Obsidian vault, index it, search it, and read pages with viewport support — no LLM or daemon required.

**Architecture:** A Python package (`llm_wiki`) with a page parser (supports `%%` section markers + heading fallback), a pluggable search backend (tantivy default), a hierarchical manifest store with token-budget-aware pagination, and a Click CLI. State stored in `.llm-wiki/` inside the vault. Everything is synchronous in Phase 1 — async comes with the daemon in Phase 2.

**Tech Stack:** Python 3.11+, PyYAML, tantivy (tantivy-py), Click, pytest

**Design note for Phase 3:** The traversal engine will emit structured logs. Design the manifest store and search results to include fields the librarian will later consume (read_count, usefulness). Initialize them to defaults now.

---

## File Structure

```
src/
  llm_wiki/
    __init__.py              # Package version
    config.py                # Config dataclasses + YAML loading
    tokens.py                # Token counting heuristic
    page.py                  # Page parser: frontmatter, sections, wikilinks
    manifest.py              # ManifestEntry, ClusterSummary, ManifestStore
    search/
      __init__.py
      backend.py             # SearchBackend protocol + SearchResult
      tantivy_backend.py     # Tantivy implementation
    vault.py                 # Vault scanner + initialization
    cli/
      __init__.py
      main.py                # Click group + entry point
tests/
  __init__.py
  conftest.py               # Shared fixtures (tmp vault with sample pages)
  test_config.py
  test_tokens.py
  test_page.py
  test_manifest.py
  test_search/
    __init__.py
    test_tantivy.py
  test_vault.py
  test_cli/
    __init__.py
    test_commands.py
  test_integration.py
pyproject.toml
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/llm_wiki/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "llm-wiki"
version = "0.1.0"
description = "Agent-first knowledge base tool — wiki over RAG"
requires-python = ">=3.11"
dependencies = [
    "pyyaml>=6.0",
    "tantivy>=0.22.0",
    "click>=8.0",
]

[project.scripts]
llm-wiki = "llm_wiki.cli.main:cli"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-tmp-files>=0.0.2",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.hatch.build.targets.wheel]
packages = ["src/llm_wiki"]
```

- [ ] **Step 2: Create package init**

```python
# src/llm_wiki/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 3: Create test init and conftest with sample vault fixture**

```python
# tests/__init__.py
```

```python
# tests/conftest.py
import pytest
from pathlib import Path


SAMPLE_PAGE_WITH_MARKERS = """\
---
title: sRNA Embeddings Validation
source: "[[raw/smith-2026-srna.pdf]]"
---

%% section: overview, tokens: 45 %%
## Overview

sRNA embeddings are validated via PCA projection and k-means clustering.

%% section: method, tokens: 38 %%
## Method

We use PCA analysis to reduce dimensionality of embeddings before clustering.

%% section: clustering, tokens: 32 %%
## Clustering

Clustering is performed using k-means with k=10 clusters.

%% section: related, tokens: 52 %%
## Related Pages

For clustering metrics, see [[clustering-metrics]].
For variant analysis, see [[inter-rep-variant-analysis]].
"""

SAMPLE_PAGE_NO_MARKERS = """\
---
title: Clustering Metrics
---

# Clustering Metrics

Evaluation metrics for clustering algorithms.

## Silhouette Score

Silhouette score ranges from -1 to 1:
- > 0.5: Well-separated clusters
- 0.2 - 0.5: Moderate structure
- < 0.2: Poor or no structure

## Related Pages

For application to sRNA embeddings, see [[srna-embeddings]].
"""

SAMPLE_PAGE_NO_STRUCTURE = """\
A simple page with no headings and no markers.
Just plain text content that should be treated as one section.
It references [[some-other-page]] in passing.
"""


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample pages."""
    bio = tmp_path / "bioinformatics"
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

    ml = tmp_path / "machine-learning"
    ml.mkdir()
    (ml / "clustering-metrics.md").write_text(SAMPLE_PAGE_NO_MARKERS)

    (tmp_path / "no-structure.md").write_text(SAMPLE_PAGE_NO_STRUCTURE)

    return tmp_path
```

- [ ] **Step 4: Install in dev mode and verify**

Run: `cd /home/labhund/repos/llm-wiki && pip install -e ".[dev]"`
Expected: Successful install

Run: `python -c "import llm_wiki; print(llm_wiki.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: project scaffolding with dev dependencies and test fixtures"
```

---

### Task 2: Config Model

**Files:**
- Create: `src/llm_wiki/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
from pathlib import Path
from llm_wiki.config import WikiConfig


def test_default_config():
    config = WikiConfig()
    assert config.llm.default == "litellm/gemma4"
    assert config.llm.embeddings == "ollama/nomic-embed-text"
    assert config.search.backend == "tantivy"
    assert config.budgets.default_query == 16000
    assert config.budgets.hard_ceiling_pct == 0.8
    assert config.vault.mode == "vault"


def test_load_from_yaml(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "llm:\n"
        "  default: ollama/llama3\n"
        "budgets:\n"
        "  default_query: 8192\n"
        "vault:\n"
        "  mode: managed\n"
    )
    config = WikiConfig.load(config_file)
    assert config.llm.default == "ollama/llama3"
    assert config.budgets.default_query == 8192
    assert config.vault.mode == "managed"
    # Non-specified fields keep defaults
    assert config.llm.embeddings == "ollama/nomic-embed-text"
    assert config.search.backend == "tantivy"


def test_load_missing_file():
    config = WikiConfig.load(Path("/nonexistent/config.yaml"))
    assert config.llm.default == "litellm/gemma4"


def test_load_empty_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    config = WikiConfig.load(config_file)
    assert config.llm.default == "litellm/gemma4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_wiki.config'`

- [ ] **Step 3: Implement config model**

```python
# src/llm_wiki/config.py
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml


def _merge(dc_class, data: dict):
    """Create a dataclass instance, merging dict values over defaults."""
    kwargs = {}
    for f in fields(dc_class):
        if f.name in data:
            val = data[f.name]
            # Recurse into nested dataclasses
            if hasattr(f.type, "__dataclass_fields__") and isinstance(val, dict):
                kwargs[f.name] = _merge(f.type, val)
            else:
                kwargs[f.name] = val
    return dc_class(**kwargs)


@dataclass
class LLMConfig:
    default: str = "litellm/gemma4"
    embeddings: str = "ollama/nomic-embed-text"
    api_key: Optional[str] = None


@dataclass
class LLMQueueConfig:
    max_concurrent: int = 2
    priority_order: list[str] = field(
        default_factory=lambda: ["query", "ingest", "maintenance"]
    )
    cloud_daily_limit: Optional[int] = None
    cloud_hourly_limit: Optional[int] = None


@dataclass
class SearchConfig:
    backend: str = "tantivy"
    embeddings_enabled: bool = True
    hybrid_weight: float = 0.6


@dataclass
class BudgetConfig:
    default_query: int = 16000
    default_ingest: int = 32000
    manifest_page_size: int = 20
    manifest_refresh_after_traversals: int = 10
    page_viewport_default: str = "top"
    hard_ceiling_pct: float = 0.8
    max_traversal_turns: int = 10


@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    auditor_interval: str = "24h"
    authority_recalc: str = "12h"
    compliance_debounce_secs: int = 30
    talk_pages_enabled: bool = True


@dataclass
class VaultConfig:
    mode: str = "vault"
    raw_dir: str = "raw/"
    wiki_dir: str = "wiki/"
    watch: bool = True


@dataclass
class HonchoConfig:
    enabled: bool = False
    endpoint: str = "http://localhost:8000"


@dataclass
class WikiConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_queue: LLMQueueConfig = field(default_factory=LLMQueueConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    honcho: HonchoConfig = field(default_factory=HonchoConfig)

    @classmethod
    def load(cls, path: Path) -> "WikiConfig":
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data:
            return cls()
        return _merge(cls, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: config model with YAML loading and defaults"
```

---

### Task 3: Token Counting

**Files:**
- Create: `src/llm_wiki/tokens.py`
- Create: `tests/test_tokens.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tokens.py
from llm_wiki.tokens import count_tokens, fits_budget


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_short():
    # ~4 chars per token heuristic
    result = count_tokens("hello world")
    assert 2 <= result <= 4


def test_count_tokens_longer():
    text = "The quick brown fox jumps over the lazy dog. " * 10
    result = count_tokens(text)
    assert 90 <= result <= 130


def test_fits_budget():
    assert fits_budget("hello", budget=100)
    long_text = "word " * 10000
    assert not fits_budget(long_text, budget=100)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tokens.py -v`
Expected: FAIL

- [ ] **Step 3: Implement token counting**

```python
# src/llm_wiki/tokens.py


def count_tokens(text: str) -> int:
    """Estimate token count. Roughly 4 characters per token for English text.

    This is a fast heuristic. Swap for tiktoken or tokenizers if precision
    matters for your model.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def fits_budget(text: str, budget: int) -> bool:
    """Check if text fits within a token budget."""
    return count_tokens(text) <= budget
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tokens.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/tokens.py tests/test_tokens.py
git commit -m "feat: token counting heuristic"
```

---

### Task 4: Page Parser

**Files:**
- Create: `src/llm_wiki/page.py`
- Create: `tests/test_page.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_page.py
from pathlib import Path
from llm_wiki.page import Page, Section
from conftest import (
    SAMPLE_PAGE_WITH_MARKERS,
    SAMPLE_PAGE_NO_MARKERS,
    SAMPLE_PAGE_NO_STRUCTURE,
)


def test_parse_frontmatter(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    assert page.title == "sRNA Embeddings Validation"
    assert page.frontmatter["source"] == "[[raw/smith-2026-srna.pdf]]"


def test_parse_sections_with_markers(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    names = [s.name for s in page.sections]
    assert names == ["overview", "method", "clustering", "related"]
    assert "PCA projection" in page.sections[0].content
    assert "k-means" in page.sections[2].content


def test_parse_sections_heading_fallback(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_NO_MARKERS)
    page = Page.parse(p)
    names = [s.name for s in page.sections]
    assert "clustering-metrics" in names or "Clustering Metrics" in names
    assert any("silhouette" in s.name.lower() for s in page.sections)


def test_parse_no_structure(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_NO_STRUCTURE)
    page = Page.parse(p)
    assert len(page.sections) == 1
    assert page.sections[0].name == "content"
    assert "plain text" in page.sections[0].content


def test_extract_wikilinks(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    assert "clustering-metrics" in page.wikilinks
    assert "inter-rep-variant-analysis" in page.wikilinks


def test_wikilinks_from_unstructured(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_NO_STRUCTURE)
    page = Page.parse(p)
    assert "some-other-page" in page.wikilinks


def test_token_counts(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    assert page.total_tokens > 0
    assert all(s.tokens > 0 for s in page.sections)


def test_title_fallback_to_heading(tmp_path: Path):
    p = tmp_path / "test.md"
    p.write_text("# My Title\n\nSome content.\n")
    page = Page.parse(p)
    assert page.title == "My Title"


def test_title_fallback_to_filename(tmp_path: Path):
    p = tmp_path / "my-page.md"
    p.write_text("No frontmatter, no heading.\n")
    page = Page.parse(p)
    assert page.title == "my-page"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_page.py -v`
Expected: FAIL

- [ ] **Step 3: Implement page parser**

```python
# src/llm_wiki/page.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from llm_wiki.tokens import count_tokens

# Matches: %% section: name, tokens: 123 %%
# or just: %% section: name %%
_MARKER_RE = re.compile(
    r"^%%\s*section:\s*(?P<name>[^,]+?)(?:\s*,\s*tokens:\s*\d+)?\s*%%$"
)

# Matches: ## Heading or ### Heading
_HEADING_RE = re.compile(r"^(?P<level>#{2,3})\s+(?P<title>.+)$")

# Matches: [[target]] or [[path/to/target]] or [[target|alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")

# Matches: # Top Heading (h1 only)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class Section:
    name: str
    content: str
    tokens: int


@dataclass
class Page:
    path: Path
    title: str
    frontmatter: dict
    sections: list[Section]
    wikilinks: list[str]
    raw_content: str
    total_tokens: int

    @classmethod
    def parse(cls, path: Path) -> Page:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)

        title = (
            frontmatter.get("title")
            or _extract_h1(body)
            or path.stem
        )

        lines = body.splitlines(keepends=True)
        sections = _parse_sections_markers(lines)
        if not sections:
            sections = _parse_sections_headings(lines)
        if not sections:
            sections = [Section(
                name="content",
                content=body.strip(),
                tokens=count_tokens(body),
            )]

        wikilinks = _extract_wikilinks(raw)
        total_tokens = sum(s.tokens for s in sections)

        return cls(
            path=path,
            title=title,
            frontmatter=frontmatter,
            sections=sections,
            wikilinks=wikilinks,
            raw_content=raw,
            total_tokens=total_tokens,
        )


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns ({}, full_text) if none."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, text
    return fm, body


def _extract_h1(text: str) -> str | None:
    m = _H1_RE.search(text)
    return m.group(1).strip() if m else None


def _parse_sections_markers(lines: list[str]) -> list[Section]:
    """Parse sections delimited by %% section: name %% markers."""
    boundaries: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _MARKER_RE.match(line.strip())
        if m:
            boundaries.append((m.group("name").strip(), i))

    if not boundaries:
        return []

    sections = []
    for idx, (name, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(lines)
        content_lines = lines[start + 1 : end]
        content = "".join(content_lines).strip()
        sections.append(Section(
            name=name,
            content=content,
            tokens=count_tokens(content),
        ))
    return sections


def _parse_sections_headings(lines: list[str]) -> list[Section]:
    """Fallback: parse sections from ## and ### headings."""
    boundaries: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if m:
            name = _slugify(m.group("title"))
            boundaries.append((name, i))

    if not boundaries:
        return []

    sections = []
    for idx, (name, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(lines)
        content_lines = lines[start + 1 : end]
        content = "".join(content_lines).strip()
        if content:
            sections.append(Section(
                name=name,
                content=content,
                tokens=count_tokens(content),
            ))
    return sections


def _slugify(text: str) -> str:
    """Convert heading text to a slug: 'Silhouette Score' -> 'silhouette-score'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _extract_wikilinks(text: str) -> list[str]:
    """Extract wikilink targets, normalized to page name only."""
    raw_links = _WIKILINK_RE.findall(text)
    result = []
    for link in raw_links:
        # Normalize: strip path prefixes and .md suffix
        name = link.split("/")[-1]
        if name.endswith(".md"):
            name = name[:-3]
        if name not in result:
            result.append(name)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_page.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/page.py tests/test_page.py
git commit -m "feat: page parser with section markers, heading fallback, wikilinks"
```

---

### Task 5: Manifest Entry Model

**Files:**
- Create: `src/llm_wiki/manifest.py`
- Create: `tests/test_manifest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_manifest.py
from pathlib import Path
from llm_wiki.manifest import ManifestEntry, ClusterSummary, build_entry
from llm_wiki.page import Page
from conftest import SAMPLE_PAGE_WITH_MARKERS


def test_build_entry_from_page(tmp_path: Path):
    p = tmp_path / "srna-embeddings.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    entry = build_entry(page, cluster="bioinformatics")
    assert entry.name == "srna-embeddings"
    assert entry.title == "sRNA Embeddings Validation"
    assert entry.cluster == "bioinformatics"
    assert entry.tokens == page.total_tokens
    assert len(entry.sections) == 4
    assert entry.sections[0].name == "overview"
    assert entry.links_to == ["clustering-metrics", "inter-rep-variant-analysis"]
    assert entry.read_count == 0
    assert entry.usefulness == 0.0
    assert entry.authority == 0.0


def test_entry_summary_tokens():
    """Manifest entry itself should serialize to predictable size."""
    entry = ManifestEntry(
        name="test",
        title="Test Page",
        summary="A short summary.",
        tags=["a", "b"],
        cluster="test-cluster",
        tokens=500,
        sections=[],
        links_to=["other"],
        links_from=[],
        read_count=0,
        usefulness=0.0,
        authority=0.0,
    )
    text = entry.to_manifest_text()
    assert "test" in text
    assert "A short summary" in text
    assert "500" in text


def test_cluster_summary():
    entries = [
        ManifestEntry(
            name=f"page-{i}", title=f"Page {i}", summary=f"Summary {i}",
            tags=[], cluster="bio", tokens=100 * (i + 1), sections=[],
            links_to=[], links_from=[], read_count=0,
            usefulness=0.0, authority=0.0,
        )
        for i in range(5)
    ]
    cluster = ClusterSummary.from_entries("bio", entries)
    assert cluster.name == "bio"
    assert cluster.page_count == 5
    assert cluster.total_tokens == 100 + 200 + 300 + 400 + 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manifest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement manifest model**

```python
# src/llm_wiki/manifest.py
from __future__ import annotations

from dataclasses import dataclass, field

from llm_wiki.page import Page, Section as PageSection
from llm_wiki.tokens import count_tokens


@dataclass
class SectionInfo:
    name: str
    tokens: int


@dataclass
class ManifestEntry:
    name: str
    title: str
    summary: str
    tags: list[str]
    cluster: str
    tokens: int
    sections: list[SectionInfo]
    links_to: list[str]
    links_from: list[str]
    # Usage stats — initialized to defaults, updated by librarian (Phase 5)
    read_count: int = 0
    usefulness: float = 0.0
    authority: float = 0.0
    last_corroborated: str | None = None

    def to_manifest_text(self) -> str:
        """Compact text representation for agent consumption."""
        sec_info = ", ".join(
            f"{s.name}({s.tokens}t)" for s in self.sections
        )
        tags_str = ", ".join(self.tags) if self.tags else "none"
        links_str = ", ".join(self.links_to) if self.links_to else "none"
        return (
            f"{self.name}: {self.summary}\n"
            f"  tags: [{tags_str}] | tokens: {self.tokens} | "
            f"authority: {self.authority:.2f}\n"
            f"  sections: [{sec_info}]\n"
            f"  links: [{links_str}]"
        )


@dataclass
class ClusterSummary:
    name: str
    page_count: int
    total_tokens: int
    page_names: list[str]

    @classmethod
    def from_entries(cls, name: str, entries: list[ManifestEntry]) -> ClusterSummary:
        return cls(
            name=name,
            page_count=len(entries),
            total_tokens=sum(e.tokens for e in entries),
            page_names=[e.name for e in entries],
        )

    def to_summary_text(self) -> str:
        return f"{self.name} ({self.page_count} pages, {self.total_tokens} tokens)"


def build_entry(page: Page, cluster: str) -> ManifestEntry:
    """Build a manifest entry from a parsed page."""
    # Summary: first section content, truncated
    summary = ""
    if page.sections:
        first_content = page.sections[0].content
        # Take first sentence or first 120 chars
        dot = first_content.find(".")
        if 0 < dot < 120:
            summary = first_content[: dot + 1]
        else:
            summary = first_content[:120].rsplit(" ", 1)[0] + "..."

    sections = [
        SectionInfo(name=s.name, tokens=s.tokens)
        for s in page.sections
    ]

    return ManifestEntry(
        name=page.path.stem,
        title=page.title,
        summary=summary,
        tags=[],  # Tags added by librarian in Phase 5
        cluster=cluster,
        tokens=page.total_tokens,
        sections=sections,
        links_to=page.wikilinks,
        links_from=[],  # Computed after all pages indexed
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manifest.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/manifest.py tests/test_manifest.py
git commit -m "feat: manifest entry model with cluster summaries"
```

---

### Task 6: Search Backend Protocol + Tantivy Indexing

**Files:**
- Create: `src/llm_wiki/search/__init__.py`
- Create: `src/llm_wiki/search/backend.py`
- Create: `src/llm_wiki/search/tantivy_backend.py`
- Create: `tests/test_search/__init__.py`
- Create: `tests/test_search/test_tantivy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_search/__init__.py
```

```python
# tests/test_search/test_tantivy.py
from pathlib import Path
from llm_wiki.search.tantivy_backend import TantivyBackend
from llm_wiki.page import Page
from llm_wiki.manifest import build_entry
from conftest import SAMPLE_PAGE_WITH_MARKERS, SAMPLE_PAGE_NO_MARKERS


def test_index_and_search(tmp_path: Path):
    index_dir = tmp_path / "index"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    # Create pages
    (vault_dir / "srna.md").write_text(SAMPLE_PAGE_WITH_MARKERS)
    (vault_dir / "clustering.md").write_text(SAMPLE_PAGE_NO_MARKERS)

    page1 = Page.parse(vault_dir / "srna.md")
    page2 = Page.parse(vault_dir / "clustering.md")
    entry1 = build_entry(page1, cluster="bio")
    entry2 = build_entry(page2, cluster="ml")

    backend = TantivyBackend(index_dir)
    backend.index_entries([entry1, entry2])

    results = backend.search("sRNA embeddings", limit=5)
    assert len(results) >= 1
    assert results[0].name == "srna"


def test_search_no_results(tmp_path: Path):
    index_dir = tmp_path / "index"
    backend = TantivyBackend(index_dir)
    backend.index_entries([])
    results = backend.search("nonexistent topic", limit=5)
    assert results == []


def test_search_returns_scores(tmp_path: Path):
    index_dir = tmp_path / "index"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "srna.md").write_text(SAMPLE_PAGE_WITH_MARKERS)

    page = Page.parse(vault_dir / "srna.md")
    entry = build_entry(page, cluster="bio")

    backend = TantivyBackend(index_dir)
    backend.index_entries([entry])

    results = backend.search("PCA clustering", limit=5)
    assert len(results) >= 1
    assert results[0].score > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_search/test_tantivy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement search backend protocol**

```python
# src/llm_wiki/search/__init__.py
```

```python
# src/llm_wiki/search/backend.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_wiki.manifest import ManifestEntry


@dataclass
class SearchResult:
    name: str
    score: float
    entry: ManifestEntry


class SearchBackend(Protocol):
    def index_entries(self, entries: list[ManifestEntry]) -> None: ...
    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...
    def entry_count(self) -> int: ...
```

- [ ] **Step 4: Implement tantivy backend**

```python
# src/llm_wiki/search/tantivy_backend.py
from __future__ import annotations

import json
from pathlib import Path

import tantivy

from llm_wiki.manifest import ManifestEntry, SectionInfo
from llm_wiki.search.backend import SearchResult


class TantivyBackend:
    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self._schema = self._build_schema()
        self._path.mkdir(parents=True, exist_ok=True)
        self._index = tantivy.Index(self._schema, path=str(self._path))
        self._entries: dict[str, ManifestEntry] = {}

    @staticmethod
    def _build_schema() -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("name", stored=True, tokenizer_name="default")
        builder.add_text_field("title", stored=True, tokenizer_name="default")
        builder.add_text_field("summary", stored=True, tokenizer_name="default")
        builder.add_text_field("body", stored=False, tokenizer_name="default")
        builder.add_text_field("tags", stored=True, tokenizer_name="default")
        builder.add_text_field("entry_json", stored=True, tokenizer_name="raw")
        return builder.build()

    def index_entries(self, entries: list[ManifestEntry]) -> None:
        writer = self._index.writer(heap_size=50_000_000)

        # Clear existing documents
        writer.delete_all_documents()

        for entry in entries:
            self._entries[entry.name] = entry
            body = f"{entry.title} {entry.summary} {' '.join(entry.tags)}"
            writer.add_document(tantivy.Document(
                name=entry.name,
                title=entry.title,
                summary=entry.summary,
                body=body,
                tags=" ".join(entry.tags),
                entry_json=json.dumps(_entry_to_dict(entry)),
            ))

        writer.commit()
        self._index.reload()

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if self.entry_count() == 0:
            return []

        self._index.reload()
        searcher = self._index.searcher()
        parsed = self._index.parse_query(query, ["name", "title", "summary", "body"])

        results = []
        search_result = searcher.search(parsed, limit)
        for score, doc_address in search_result.hits:
            doc = searcher.doc(doc_address)
            entry_data = json.loads(doc["entry_json"][0])
            entry = _entry_from_dict(entry_data)
            results.append(SearchResult(
                name=entry.name,
                score=score,
                entry=entry,
            ))
        return results

    def entry_count(self) -> int:
        self._index.reload()
        return self._index.searcher().num_docs


def _entry_to_dict(entry: ManifestEntry) -> dict:
    return {
        "name": entry.name,
        "title": entry.title,
        "summary": entry.summary,
        "tags": entry.tags,
        "cluster": entry.cluster,
        "tokens": entry.tokens,
        "sections": [{"name": s.name, "tokens": s.tokens} for s in entry.sections],
        "links_to": entry.links_to,
        "links_from": entry.links_from,
        "read_count": entry.read_count,
        "usefulness": entry.usefulness,
        "authority": entry.authority,
        "last_corroborated": entry.last_corroborated,
    }


def _entry_from_dict(data: dict) -> ManifestEntry:
    return ManifestEntry(
        name=data["name"],
        title=data["title"],
        summary=data["summary"],
        tags=data["tags"],
        cluster=data["cluster"],
        tokens=data["tokens"],
        sections=[SectionInfo(**s) for s in data["sections"]],
        links_to=data["links_to"],
        links_from=data["links_from"],
        read_count=data.get("read_count", 0),
        usefulness=data.get("usefulness", 0.0),
        authority=data.get("authority", 0.0),
        last_corroborated=data.get("last_corroborated"),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_search/test_tantivy.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/search/ tests/test_search/
git commit -m "feat: search backend protocol and tantivy implementation"
```

---

### Task 7: Manifest Store (Hierarchical, Budget-Aware)

**Files:**
- Modify: `src/llm_wiki/manifest.py`
- Create: `tests/test_manifest_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_manifest_store.py
from llm_wiki.manifest import ManifestEntry, ManifestStore, SectionInfo


def _make_entries(cluster: str, count: int) -> list[ManifestEntry]:
    return [
        ManifestEntry(
            name=f"{cluster}-page-{i}",
            title=f"Page {i} in {cluster}",
            summary=f"Summary for page {i}.",
            tags=["tag-a"],
            cluster=cluster,
            tokens=200,
            sections=[SectionInfo(name="content", tokens=200)],
            links_to=[],
            links_from=[],
        )
        for i in range(count)
    ]


def test_store_level0():
    entries = _make_entries("bio", 5) + _make_entries("ml", 3)
    store = ManifestStore(entries)
    level0 = store.level0()
    assert len(level0) == 2
    names = [c.name for c in level0]
    assert "bio" in names
    assert "ml" in names


def test_store_level1():
    entries = _make_entries("bio", 5)
    store = ManifestStore(entries)
    page = store.level1("bio", page_size=3, cursor=0)
    assert len(page.entries) == 3
    assert page.has_more is True
    assert page.next_cursor == 3

    page2 = store.level1("bio", page_size=3, cursor=3)
    assert len(page2.entries) == 2
    assert page2.has_more is False


def test_store_level2():
    entries = _make_entries("bio", 3)
    store = ManifestStore(entries)
    entry = store.level2("bio-page-1")
    assert entry is not None
    assert entry.name == "bio-page-1"


def test_store_level2_missing():
    store = ManifestStore([])
    assert store.level2("nonexistent") is None


def test_store_budget_aware_manifest():
    entries = _make_entries("bio", 10)
    store = ManifestStore(entries)
    # Small budget: should return fewer entries
    text = store.manifest_text(budget=200)
    # Large budget: should return more
    text_large = store.manifest_text(budget=5000)
    assert len(text_large) >= len(text)


def test_links_from_computed():
    entries = [
        ManifestEntry(
            name="a", title="A", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=["b", "c"], links_from=[],
        ),
        ManifestEntry(
            name="b", title="B", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=["a"], links_from=[],
        ),
        ManifestEntry(
            name="c", title="C", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=[], links_from=[],
        ),
    ]
    store = ManifestStore(entries)
    b_entry = store.level2("b")
    assert "a" in b_entry.links_from
    c_entry = store.level2("c")
    assert "a" in c_entry.links_from
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manifest_store.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ManifestStore**

Add to `src/llm_wiki/manifest.py`:

```python
# Append to existing manifest.py

@dataclass
class ManifestPage:
    """A paginated slice of manifest entries."""
    entries: list[ManifestEntry]
    has_more: bool
    next_cursor: int | None


class ManifestStore:
    """Hierarchical manifest with budget-aware pagination."""

    def __init__(self, entries: list[ManifestEntry]) -> None:
        self._entries: dict[str, ManifestEntry] = {e.name: e for e in entries}
        self._clusters: dict[str, list[ManifestEntry]] = {}
        for entry in entries:
            self._clusters.setdefault(entry.cluster, []).append(entry)
        self._compute_links_from()

    def _compute_links_from(self) -> None:
        """Compute reverse links (links_from) across all entries."""
        for entry in self._entries.values():
            entry.links_from = []
        for entry in self._entries.values():
            for target in entry.links_to:
                if target in self._entries:
                    if entry.name not in self._entries[target].links_from:
                        self._entries[target].links_from.append(entry.name)

    def level0(self) -> list[ClusterSummary]:
        """Level 0: cluster summaries."""
        return [
            ClusterSummary.from_entries(name, entries)
            for name, entries in sorted(self._clusters.items())
        ]

    def level1(
        self, cluster: str, page_size: int = 20, cursor: int = 0
    ) -> ManifestPage:
        """Level 1: paginated entries within a cluster."""
        entries = self._clusters.get(cluster, [])
        page = entries[cursor : cursor + page_size]
        has_more = cursor + page_size < len(entries)
        next_cursor = cursor + page_size if has_more else None
        return ManifestPage(entries=page, has_more=has_more, next_cursor=next_cursor)

    def level2(self, name: str) -> ManifestEntry | None:
        """Level 2: single entry by name."""
        return self._entries.get(name)

    def manifest_text(self, budget: int = 16000) -> str:
        """Budget-aware text representation of the manifest.

        Starts with level 0 (clusters). If budget allows, adds level 1 entries
        for each cluster until budget is exhausted.
        """
        lines: list[str] = []
        running_tokens = 0

        # Always include level 0
        for cluster in self.level0():
            line = cluster.to_summary_text()
            running_tokens += count_tokens(line)
            lines.append(line)

        if running_tokens >= budget:
            return "\n".join(lines)

        # Add level 1 entries until budget exhausted
        for cluster_name in sorted(self._clusters):
            page = self.level1(cluster_name, page_size=100)
            for entry in page.entries:
                entry_text = entry.to_manifest_text()
                entry_tokens = count_tokens(entry_text)
                if running_tokens + entry_tokens > budget:
                    lines.append(f"  ... ({cluster_name}: more pages available)")
                    return "\n".join(lines)
                running_tokens += entry_tokens
                lines.append(entry_text)

        return "\n".join(lines)

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def total_clusters(self) -> int:
        return len(self._clusters)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manifest_store.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/manifest.py tests/test_manifest_store.py
git commit -m "feat: hierarchical manifest store with budget-aware pagination"
```

---

### Task 8: Vault Scanner

**Files:**
- Create: `src/llm_wiki/vault.py`
- Create: `tests/test_vault.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_vault.py
from pathlib import Path
from llm_wiki.vault import Vault


def test_scan_vault(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    assert vault.page_count == 4  # 3 in subdirs + 1 no-structure.md
    assert vault.cluster_count >= 2  # bioinformatics, machine-learning


def test_search(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    results = vault.search("sRNA embeddings", limit=3)
    assert len(results) >= 1
    assert results[0].name in ("srna-embeddings", "inter-rep-variant-analysis")


def test_read_page(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    page = vault.read_page("srna-embeddings")
    assert page is not None
    assert page.title == "sRNA Embeddings Validation"


def test_read_page_missing(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    assert vault.read_page("nonexistent") is None


def test_read_viewport_top(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", viewport="top", budget=500)
    assert content is not None
    assert "overview" in content.lower() or "sRNA" in content
    # Should include table of contents of remaining sections
    assert "method" in content.lower()


def test_read_viewport_section(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", section="method")
    assert content is not None
    assert "PCA" in content


def test_read_viewport_grep(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", grep="k-means")
    assert content is not None
    assert "k-means" in content


def test_read_viewport_full(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", viewport="full")
    assert content is not None
    assert "PCA" in content
    assert "k-means" in content


def test_manifest_text(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    text = vault.manifest_text(budget=5000)
    assert "bioinformatics" in text.lower() or "srna" in text.lower()


def test_status(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    status = vault.status()
    assert status["page_count"] == 4
    assert status["cluster_count"] >= 2
    assert "index_path" in status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vault.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Vault**

```python
# src/llm_wiki/vault.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.manifest import ManifestEntry, ManifestStore, build_entry
from llm_wiki.page import Page
from llm_wiki.search.backend import SearchResult
from llm_wiki.search.tantivy_backend import TantivyBackend
from llm_wiki.tokens import count_tokens


_STATE_DIR = ".llm-wiki"


class Vault:
    """A scanned and indexed wiki vault."""

    def __init__(
        self,
        root: Path,
        pages: dict[str, Page],
        store: ManifestStore,
        backend: TantivyBackend,
    ) -> None:
        self._root = root
        self._pages = pages
        self._store = store
        self._backend = backend

    @classmethod
    def scan(cls, root: Path, config: WikiConfig | None = None) -> Vault:
        """Scan a vault directory, parse all pages, build index."""
        config = config or WikiConfig()
        state_dir = root / _STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)

        # Find all markdown files
        md_files = sorted(root.rglob("*.md"))
        # Exclude state dir and hidden directories
        md_files = [
            f for f in md_files
            if _STATE_DIR not in f.relative_to(root).parts
            and not any(p.startswith(".") for p in f.relative_to(root).parts)
        ]

        # Parse pages
        pages: dict[str, Page] = {}
        entries: list[ManifestEntry] = []
        for md_file in md_files:
            page = Page.parse(md_file)
            pages[page.path.stem] = page

            # Cluster from parent directory name, or "root" if top-level
            rel = md_file.relative_to(root)
            cluster = rel.parts[0] if len(rel.parts) > 1 else "root"

            entry = build_entry(page, cluster=cluster)
            entries.append(entry)

        # Build search index
        index_path = state_dir / "index"
        backend = TantivyBackend(index_path)
        backend.index_entries(entries)

        # Build manifest store
        store = ManifestStore(entries)

        return cls(root=root, pages=pages, store=store, backend=backend)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._backend.search(query, limit=limit)

    def read_page(self, name: str) -> Page | None:
        return self._pages.get(name)

    def read_viewport(
        self,
        name: str,
        viewport: str = "top",
        section: str | None = None,
        grep: str | None = None,
        budget: int | None = None,
    ) -> str | None:
        """Read a page with viewport support."""
        page = self._pages.get(name)
        if page is None:
            return None

        if grep:
            return self._viewport_grep(page, grep, budget)
        if section:
            return self._viewport_section(page, section)
        if viewport == "full":
            return self._viewport_full(page, budget)
        # Default: "top"
        return self._viewport_top(page, budget)

    def manifest_text(self, budget: int = 16000) -> str:
        return self._store.manifest_text(budget=budget)

    def status(self) -> dict:
        return {
            "vault_root": str(self._root),
            "page_count": self.page_count,
            "cluster_count": self._store.total_clusters,
            "clusters": [c.to_summary_text() for c in self._store.level0()],
            "index_path": str(self._root / _STATE_DIR / "index"),
            "index_entries": self._backend.entry_count(),
        }

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def cluster_count(self) -> int:
        return self._store.total_clusters

    # -- Viewport implementations --

    @staticmethod
    def _viewport_top(page: Page, budget: int | None) -> str:
        if not page.sections:
            return page.raw_content

        # First section content
        first = page.sections[0]
        lines = [f"## {first.name}\n", first.content, ""]

        # Table of contents for remaining sections
        if len(page.sections) > 1:
            lines.append("**Remaining sections:**")
            for s in page.sections[1:]:
                lines.append(f"  - {s.name} ({s.tokens} tokens)")

        text = "\n".join(lines)
        if budget and count_tokens(text) > budget:
            # Truncate first section to fit
            truncated = text[: budget * 4]  # rough char estimate
            return truncated.rsplit("\n", 1)[0] + "\n... (truncated)"
        return text

    @staticmethod
    def _viewport_section(page: Page, section_name: str) -> str | None:
        for s in page.sections:
            if s.name == section_name or s.name == section_name.lower():
                return f"## {s.name}\n\n{s.content}"
        return None

    @staticmethod
    def _viewport_grep(page: Page, pattern: str, budget: int | None) -> str:
        matches = []
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
        for s in page.sections:
            if regex.search(s.content):
                matches.append(f"## {s.name}\n\n{s.content}")
        if not matches:
            return f"No matches for '{pattern}' in {page.path.stem}"
        text = "\n\n---\n\n".join(matches)
        if budget and count_tokens(text) > budget:
            return text[: budget * 4].rsplit("\n", 1)[0] + "\n... (truncated)"
        return text

    @staticmethod
    def _viewport_full(page: Page, budget: int | None) -> str:
        # Return full body (strip frontmatter)
        body = page.raw_content
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + 4:].strip()
        if budget and count_tokens(body) > budget:
            return body[: budget * 4].rsplit("\n", 1)[0] + "\n... (truncated)"
        return body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vault.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py
git commit -m "feat: vault scanner with search, viewports, and manifest"
```

---

### Task 9: CLI — Init and Status Commands

**Files:**
- Create: `src/llm_wiki/cli/__init__.py`
- Create: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/__init__.py`
- Create: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli/__init__.py
```

```python
# tests/test_cli/test_commands.py
from pathlib import Path
from click.testing import CliRunner
from llm_wiki.cli.main import cli


def test_init_command(sample_vault: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output
    assert (sample_vault / ".llm-wiki" / "index").exists()


def test_init_nonexistent():
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "/nonexistent/path"])
    assert result.exit_code != 0


def test_status_command(sample_vault: Path):
    runner = CliRunner()
    # Init first
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(cli, ["status", "--vault", str(sample_vault)])
    assert result.exit_code == 0
    assert "page" in result.output.lower()
    assert "cluster" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_commands.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CLI**

```python
# src/llm_wiki/cli/__init__.py
```

```python
# src/llm_wiki/cli/main.py
from pathlib import Path

import click

from llm_wiki.vault import Vault


@click.group()
def cli() -> None:
    """llm-wiki — Agent-first knowledge base tool."""
    pass


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path))
def init(vault_path: Path) -> None:
    """Scan and index a vault directory."""
    vault = Vault.scan(vault_path)
    click.echo(
        f"Indexed {vault.page_count} pages "
        f"in {vault.cluster_count} clusters."
    )
    click.echo(f"Index stored in {vault_path / '.llm-wiki' / 'index'}")


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault (default: current directory)",
)
def status(vault_path: Path) -> None:
    """Show vault status."""
    vault = Vault.scan(vault_path)
    info = vault.status()
    click.echo(f"Vault: {info['vault_root']}")
    click.echo(f"Pages: {info['page_count']}")
    click.echo(f"Clusters: {info['cluster_count']}")
    for cluster_text in info["clusters"]:
        click.echo(f"  {cluster_text}")
    click.echo(f"Index: {info['index_path']}")
    click.echo(f"Index entries: {info['index_entries']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_commands.py::test_init_command tests/test_cli/test_commands.py::test_init_nonexistent tests/test_cli/test_commands.py::test_status_command -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Verify CLI works from terminal**

Run: `llm-wiki init /home/labhund/repos/llm-wiki/wiki`
Expected: `Indexed 3 pages in 2 clusters.`

Run: `llm-wiki status --vault /home/labhund/repos/llm-wiki/wiki`
Expected: Status output showing pages, clusters, index path

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/cli/ tests/test_cli/
git commit -m "feat: CLI init and status commands"
```

---

### Task 10: CLI — Search Command

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli/test_commands.py`:

```python
def test_search_command(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "sRNA embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()


def test_search_with_limit(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "clustering", "--vault", str(sample_vault), "--limit", "1"]
    )
    assert result.exit_code == 0


def test_search_no_results(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "quantum physics", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "no results" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_commands.py::test_search_command -v`
Expected: FAIL — no search command

- [ ] **Step 3: Add search command**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.command()
@click.argument("query")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--limit", default=10, help="Max results")
@click.option("--budget", default=16000, help="Token budget for manifest output")
def search(query: str, vault_path: Path, limit: int, budget: int) -> None:
    """Search the wiki index."""
    vault = Vault.scan(vault_path)
    results = vault.search(query, limit=limit)

    if not results:
        click.echo("No results found.")
        return

    click.echo(f"Found {len(results)} result(s):\n")
    for r in results:
        entry = r.entry
        click.echo(entry.to_manifest_text())
        click.echo(f"  score: {r.score:.3f}")
        click.echo()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_commands.py -v -k search`
Expected: All 3 search tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "feat: CLI search command with manifest output"
```

---

### Task 11: CLI — Read Command (Viewports)

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli/test_commands.py`:

```python
def test_read_top(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()
    assert "Remaining sections" in result.output or "method" in result.output.lower()


def test_read_section(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output


def test_read_grep(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output


def test_read_full(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--viewport", "full",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output
    assert "k-means" in result.output


def test_read_missing_page(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "nonexistent", "--vault", str(sample_vault)]
    )
    assert result.exit_code != 0 or "not found" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_commands.py -v -k "test_read"`
Expected: FAIL

- [ ] **Step 3: Add read command**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.command()
@click.argument("page_name")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--viewport", default="top", type=click.Choice(["top", "full"]))
@click.option("--section", default=None, help="Read specific section by name")
@click.option("--grep", default=None, help="Search within page")
@click.option("--budget", default=None, type=int, help="Token budget")
def read(
    page_name: str,
    vault_path: Path,
    viewport: str,
    section: str | None,
    grep: str | None,
    budget: int | None,
) -> None:
    """Read a wiki page with viewport support."""
    vault = Vault.scan(vault_path)

    content = vault.read_viewport(
        page_name,
        viewport=viewport,
        section=section,
        grep=grep,
        budget=budget,
    )

    if content is None:
        click.echo(f"Page not found: {page_name}", err=True)
        raise SystemExit(1)

    click.echo(content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_commands.py -v -k "test_read"`
Expected: All 5 read tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "feat: CLI read command with viewport, section, and grep support"
```

---

### Task 12: CLI — Manifest Command

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli/test_commands.py`:

```python
def test_manifest_command(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    # Should show cluster info and page entries
    assert "bioinformatics" in result.output.lower() or "srna" in result.output.lower()


def test_manifest_with_budget(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    small = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault), "--budget", "50"]
    )
    large = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault), "--budget", "5000"]
    )
    assert small.exit_code == 0
    assert large.exit_code == 0
    # Larger budget should produce more output
    assert len(large.output) >= len(small.output)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_commands.py -v -k "test_manifest"`
Expected: FAIL

- [ ] **Step 3: Add manifest command**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--budget", default=16000, help="Token budget for manifest output")
def manifest(vault_path: Path, budget: int) -> None:
    """Show the hierarchical manifest (budget-aware)."""
    vault = Vault.scan(vault_path)
    click.echo(vault.manifest_text(budget=budget))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_commands.py -v -k "test_manifest"`
Expected: Both tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "feat: CLI manifest command with budget-aware output"
```

---

### Task 13: End-to-End Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""End-to-end test: init vault → search → read viewports → manifest."""
from pathlib import Path
from click.testing import CliRunner
from llm_wiki.cli.main import cli
from llm_wiki.vault import Vault


def test_full_workflow(sample_vault: Path):
    """Simulate a user's first experience with llm-wiki."""
    runner = CliRunner()

    # Step 1: Init
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output

    # Step 2: Status
    result = runner.invoke(cli, ["status", "--vault", str(sample_vault)])
    assert result.exit_code == 0
    assert "4" in result.output  # 4 pages

    # Step 3: Search
    result = runner.invoke(
        cli, ["search", "sRNA embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()

    # Step 4: Read top viewport
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()

    # Step 5: Read specific section
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output

    # Step 6: Grep within page
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output

    # Step 7: Manifest
    result = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0


def test_vault_api_directly(sample_vault: Path):
    """Test the Vault API for programmatic use (library mode)."""
    vault = Vault.scan(sample_vault)

    # Search
    results = vault.search("silhouette score")
    assert len(results) >= 1

    # Read viewport
    content = vault.read_viewport("clustering-metrics", viewport="top")
    assert content is not None
    assert "silhouette" in content.lower()

    # Manifest with tight budget
    manifest_small = vault.manifest_text(budget=100)
    manifest_large = vault.manifest_text(budget=10000)
    assert len(manifest_large) >= len(manifest_small)

    # Page not found
    assert vault.read_viewport("nonexistent") is None

    # Status
    status = vault.status()
    assert status["page_count"] == 4


def test_existing_wiki_directory():
    """Test against the actual wiki/ directory in the repo."""
    wiki_path = Path("/home/labhund/repos/llm-wiki/wiki")
    if not wiki_path.exists():
        return  # Skip if not in the expected location

    vault = Vault.scan(wiki_path)
    assert vault.page_count >= 3

    results = vault.search("sRNA")
    assert len(results) >= 1

    content = vault.read_viewport("srna-embeddings", viewport="full")
    assert content is not None
    assert "PCA" in content
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Run the CLI against the real wiki directory**

Run: `llm-wiki init /home/labhund/repos/llm-wiki/wiki`
Expected: `Indexed 3 pages in 2 clusters.`

Run: `llm-wiki search "sRNA embeddings" --vault /home/labhund/repos/llm-wiki/wiki`
Expected: Search results with manifest entries

Run: `llm-wiki read srna-embeddings --vault /home/labhund/repos/llm-wiki/wiki`
Expected: Top viewport with overview + remaining sections listed

Run: `llm-wiki read srna-embeddings --grep "k-means" --vault /home/labhund/repos/llm-wiki/wiki`
Expected: Sections containing "k-means"

Run: `llm-wiki manifest --vault /home/labhund/repos/llm-wiki/wiki`
Expected: Hierarchical manifest output

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: end-to-end integration tests for vault mode"
```

- [ ] **Step 5: Final commit — Phase 1 complete**

```bash
git add -A
git commit -m "Phase 1 complete: core library + CLI with vault mode

Implements: page parser (%% markers + heading fallback), tantivy search,
hierarchical manifest with budget-aware pagination, intra-page viewports
(top/section/grep/full), and CLI (init/status/search/read/manifest)."
```

---

## Phase 1 Deliverables

After completing all tasks:

| Command | What it does |
|---------|-------------|
| `llm-wiki init /path/to/vault` | Scan markdown files, build search index |
| `llm-wiki status --vault /path` | Show page count, clusters, index info |
| `llm-wiki search "query" --vault /path` | BM25 search, returns manifest entries |
| `llm-wiki read page-name --vault /path` | Read with viewport (top/full/section/grep) |
| `llm-wiki manifest --vault /path --budget N` | Budget-aware hierarchical manifest |

Library API (`from llm_wiki.vault import Vault`) provides the same capabilities for programmatic use — this is what the daemon (Phase 2) and MCP server (Phase 6) will import.

## What's Next

- **Phase 2: Daemon** — wraps the Vault in a persistent process with Unix socket IPC, file watcher, LLM queue, write coordination
- **Phase 3: Traversal Engine** — multi-turn traversal with working memory, budget management, litellm integration
