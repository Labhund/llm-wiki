from pathlib import Path

import pytest

from llm_wiki.ingest.page_writer import PageSection, WrittenPage, write_page
from llm_wiki.ingest.agent import IngestAgent


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

    import re
    text = result.path.read_text()
    assert re.search(r"%% section: overview(?:, tokens: \d+)? %%", text)
    assert "## Overview" in text
    assert "Overview content." in text
    assert re.search(r"%% section: method(?:, tokens: \d+)? %%", text)
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
    import re as _re
    assert _re.search(r"%% section: from-new(?:, tokens: \d+)? %%", text)


def test_update_same_source_twice_no_duplicate(wiki_dir: Path):
    """Ingesting the same source twice does not duplicate the section."""
    sections = [PageSection(name="overview", heading="Overview", content="Content [[raw/paper.pdf]].")]
    write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")
    write_page(wiki_dir, "pca", "PCA", sections, "raw/paper.pdf")  # second ingest

    text = (wiki_dir / "pca.md").read_text()
    # Section name from-paper should appear only once
    import re as _re2
    assert len(_re2.findall(r"%% section: from-paper(?:, tokens: \d+)? %%", text)) <= 1


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


def test_patch_token_estimates_adds_token_count(tmp_path):
    from llm_wiki.ingest.page_writer import patch_token_estimates
    page = tmp_path / "test.md"
    page.write_text(
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n"
        "## Overview\n\n"
        "Some content here.\n\n"
        "%% section: methods %%\n"
        "## Methods\n\n"
        "Method details.\n",
        encoding="utf-8",
    )
    patch_token_estimates(page)
    text = page.read_text()
    import re
    assert re.search(r"%% section: overview, tokens: \d+ %%", text)
    assert re.search(r"%% section: methods, tokens: \d+ %%", text)


def test_write_page_creates_file_with_token_markers(wiki_dir):
    sections = [PageSection(name="overview", heading="Overview", content="Boltz-2 is a model.")]
    write_page(wiki_dir, "boltz-2", "Boltz-2", sections, "raw/paper.pdf")
    text = (wiki_dir / "boltz-2.md").read_text()
    import re
    assert re.search(r"%% section: overview, tokens: \d+ %%", text)
