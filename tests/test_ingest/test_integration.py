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
