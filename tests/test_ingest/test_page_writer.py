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
