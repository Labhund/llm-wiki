"""Tests for complete frontmatter writing and summary extraction.

TDD tests for:
- _create_page writes all required frontmatter fields
- cluster is threaded through write_page → _create_page
- summary is extracted from synthesis prompt response
- synthesis prompt includes summary field in JSON schema
- edge cases (no cluster, no summary)
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
import yaml

from llm_wiki.ingest.page_writer import PageSection, WrittenPage, write_page
from llm_wiki.ingest.prompts import (
    parse_content_synthesis,
    compose_content_synthesis_messages,
)
from llm_wiki.ingest.agent import ConceptPlan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wiki"
    d.mkdir()
    return d


def _sections() -> list[PageSection]:
    return [PageSection(name="overview", heading="Overview", content="Content [[raw/paper.pdf]].")]


def _parse_frontmatter(text: str) -> dict:
    """Extract and parse the YAML frontmatter block from a page."""
    assert text.startswith("---\n"), "Page must start with frontmatter"
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end])


# ---------------------------------------------------------------------------
# _create_page / write_page — frontmatter completeness
# ---------------------------------------------------------------------------

class TestCreatePageFrontmatter:
    """write_page (new page) must write all required frontmatter fields."""

    def test_title_present(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality by projecting data.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["title"] == "PCA"

    def test_created_is_today(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["created"] == datetime.date.today().isoformat()

    def test_updated_equals_created_for_new_page(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["updated"] == fm["created"]

    def test_type_is_concept(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["type"] == "concept"

    def test_status_is_stub(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["status"] == "stub"

    def test_ingested_equals_created(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["ingested"] == fm["created"]

    def test_cluster_written(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="structural-bio", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["cluster"] == "structural-bio"

    def test_summary_written(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA projects high-dimensional data to lower dimensions.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["summary"] == "PCA projects high-dimensional data to lower dimensions."

    def test_source_written(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["source"] == "[[raw/paper.pdf]]"

    def test_created_by_ingest(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["created_by"] == "ingest"

    def test_tags_is_empty_list(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert fm["tags"] == []

    def test_all_required_fields_present(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        required = ["title", "created", "updated", "type", "status", "ingested",
                    "cluster", "summary", "source", "created_by", "tags"]
        for field in required:
            assert field in fm, f"Missing frontmatter field: {field!r}"


class TestCreatePageEdgeCases:
    """Edge cases: no cluster, empty summary."""

    def test_no_cluster_defaults_to_empty_string(self, wiki_dir: Path):
        """write_page without cluster argument still produces valid frontmatter."""
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   summary="PCA reduces dimensionality.")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        # cluster field should be present; empty string or omitted — either way parseable
        assert "cluster" in fm
        assert fm["cluster"] == "" or fm["cluster"] is None or fm["cluster"] == ""

    def test_no_summary_defaults_to_empty_string(self, wiki_dir: Path):
        """write_page without summary argument still produces valid frontmatter."""
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods")
        fm = _parse_frontmatter((wiki_dir / "pca.md").read_text())
        assert "summary" in fm

    def test_update_path_unaffected(self, wiki_dir: Path):
        """Appending to an existing page still works (was_update=True)."""
        # Create original
        existing = wiki_dir / "pca.md"
        existing.write_text(
            "---\ntitle: PCA\nsource: '[[raw/orig.pdf]]'\ncreated_by: ingest\n---\n\n"
            "%% section: overview %%\n## Overview\n\nOriginal [[raw/orig.pdf]].\n"
        )
        result = write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                            cluster="ml-methods", summary="PCA reduces dimensionality.")
        assert result.was_update is True


# ---------------------------------------------------------------------------
# Frontmatter field order
# ---------------------------------------------------------------------------

class TestFrontmatterFieldOrder:
    """Fields should appear in the specified order for readability."""

    EXPECTED_ORDER = [
        "title", "created", "updated", "type", "status", "ingested",
        "cluster", "summary", "source", "created_by", "tags",
    ]

    def test_field_order(self, wiki_dir: Path):
        write_page(wiki_dir, "pca", "PCA", _sections(), "raw/paper.pdf",
                   cluster="ml-methods", summary="PCA reduces dimensionality.")
        text = (wiki_dir / "pca.md").read_text()
        # Extract frontmatter block lines
        fm_end = text.index("\n---\n", 4)
        fm_block = text[4:fm_end]
        fm_lines = [l for l in fm_block.splitlines() if l and not l.startswith(" ")]
        keys_in_order = [l.split(":")[0].strip() for l in fm_lines if ":" in l]

        present = [k for k in self.EXPECTED_ORDER if k in keys_in_order]
        actual_present_order = [k for k in keys_in_order if k in self.EXPECTED_ORDER]
        assert actual_present_order == present, (
            f"Fields out of order.\nExpected order: {present}\nActual order: {actual_present_order}"
        )


# ---------------------------------------------------------------------------
# parse_content_synthesis — summary extraction
# ---------------------------------------------------------------------------

class TestParseContentSynthesisSummary:
    """parse_content_synthesis must extract summary alongside sections."""

    def test_summary_extracted(self):
        text = json.dumps({
            "summary": "Boltz-2 predicts protein structures using diffusion.",
            "sections": [
                {"name": "overview", "heading": "Overview",
                 "content": "Boltz-2 is a model [[raw/boltz2.pdf]]."}
            ],
        })
        result = parse_content_synthesis(text)
        assert result.summary == "Boltz-2 predicts protein structures using diffusion."

    def test_sections_still_extracted(self):
        text = json.dumps({
            "summary": "A short description.",
            "sections": [
                {"name": "overview", "heading": "Overview", "content": "Content."}
            ],
        })
        result = parse_content_synthesis(text)
        assert len(result.sections) == 1
        assert result.sections[0].name == "overview"

    def test_missing_summary_defaults_to_empty_string(self):
        """Backward compatibility: old responses without summary field."""
        text = json.dumps({
            "sections": [
                {"name": "overview", "heading": "Overview", "content": "Content."}
            ],
        })
        result = parse_content_synthesis(text)
        assert result.summary == ""

    def test_invalid_json_returns_empty_result(self):
        result = parse_content_synthesis("not json")
        assert result.sections == []
        assert result.summary == ""

    def test_summary_attribute_accessible(self):
        """result.summary is accessible (not just result[0] or similar)."""
        text = json.dumps({
            "summary": "Test summary.",
            "sections": [],
        })
        result = parse_content_synthesis(text)
        assert hasattr(result, "summary")
        assert hasattr(result, "sections")


# ---------------------------------------------------------------------------
# Synthesis prompt includes summary field
# ---------------------------------------------------------------------------

class TestSynthesisPromptIncludesSummary:
    """The synthesis prompt system message must mention summary in its schema."""

    def test_prompt_schema_mentions_summary(self):
        from llm_wiki.ingest.prompts import _CONTENT_SYNTHESIS_SYSTEM
        assert "summary" in _CONTENT_SYNTHESIS_SYSTEM.lower()

    def test_compose_synthesis_messages_system_mentions_summary(self):
        concept = ConceptPlan(
            name="boltz-2", title="Boltz-2",
            section_names=["overview"], cluster="structural-bio",
        )
        msgs = compose_content_synthesis_messages(
            concept=concept,
            passages=["Boltz-2 achieves high accuracy."],
            source_ref="raw/boltz2.pdf",
            manifest_lines=[],
            batch_concepts=[concept],
        )
        system_content = msgs[0]["content"]
        assert "summary" in system_content.lower()
