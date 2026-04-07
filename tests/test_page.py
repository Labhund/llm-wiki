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
