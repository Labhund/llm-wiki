from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import IngestAgent, IngestResult
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
    """If text extraction fails (file missing), IngestResult has no pages."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()

    source = tmp_path / "raw" / "missing.pdf"
    # File does not exist — extract_text will return success=False

    mock_llm = MockLLMClient([])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)

    assert result.pages_created == []
    assert result.pages_updated == []
    assert mock_llm.calls == []
