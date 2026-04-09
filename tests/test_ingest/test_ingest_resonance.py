from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.traverse.llm_client import LLMResponse


class MockLLMClient:
    """Scripted LLM responses for testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def complete(
        self, messages: list[dict], temperature: float = 0.7, priority: str = "query"
    ) -> LLMResponse:
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
async def test_resonance_agent_called_when_enabled(tmp_path: Path):
    """When resonance_matching is enabled, ResonanceAgent.run_for_pages is called
    with the slugs of newly created pages."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()

    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
    ])
    sections_response = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA reduces dimensions."},
    ])

    mock_llm = MockLLMClient([concept_response, sections_response])

    config = WikiConfig()
    config.maintenance.resonance_matching = True

    with patch("llm_wiki.resonance.agent.ResonanceAgent") as MockResonanceAgent:
        mock_instance = MagicMock()
        mock_instance.run_for_pages = AsyncMock(return_value=MagicMock(resonance_posts=[]))
        MockResonanceAgent.return_value = mock_instance

        agent = IngestAgent(mock_llm, config)
        result = await agent.ingest(source, tmp_path)

    assert result.pages_created == ["pca"]
    MockResonanceAgent.assert_called_once()
    mock_instance.run_for_pages.assert_awaited_once_with(["pca"])


@pytest.mark.asyncio
async def test_resonance_agent_not_called_when_disabled(tmp_path: Path):
    """When resonance_matching is False (default), ResonanceAgent is never instantiated."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()

    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
    ])
    sections_response = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA reduces dimensions."},
    ])

    mock_llm = MockLLMClient([concept_response, sections_response])

    config = WikiConfig()
    # resonance_matching defaults to False

    with patch("llm_wiki.resonance.agent.ResonanceAgent") as MockResonanceAgent:
        agent = IngestAgent(mock_llm, config)
        result = await agent.ingest(source, tmp_path)

    assert result.pages_created == ["pca"]
    MockResonanceAgent.assert_not_called()


@pytest.mark.asyncio
async def test_resonance_agent_not_called_when_no_pages_created(tmp_path: Path):
    """When resonance_matching is enabled but no pages were created, ResonanceAgent
    is not invoked (avoids pointless LLM spend)."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()

    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nNothing useful.")

    # LLM returns no concepts
    mock_llm = MockLLMClient([_concept_json([])])

    config = WikiConfig()
    config.maintenance.resonance_matching = True

    with patch("llm_wiki.resonance.agent.ResonanceAgent") as MockResonanceAgent:
        agent = IngestAgent(mock_llm, config)
        result = await agent.ingest(source, tmp_path)

    assert result.pages_created == []
    MockResonanceAgent.assert_not_called()
