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
        self, messages: list[dict], temperature: float = 0.7, priority: str = "query", **kwargs
    ) -> LLMResponse:
        self.calls.append(messages)
        self.priorities.append(priority)
        if self._idx >= len(self._responses):
            raise RuntimeError("MockLLMClient: no more scripted responses")
        content = self._responses[self._idx]
        self._idx += 1
        return LLMResponse(content=content, input_tokens=50, output_tokens=0)


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


# ---------------------------------------------------------------------------
# extraction_warning threading
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_result_includes_extraction_warning(tmp_path):
    """When extraction returns a quality_warning, IngestResult carries it through."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from llm_wiki.config import WikiConfig
    from llm_wiki.ingest.agent import IngestAgent
    from llm_wiki.ingest.extractor import ExtractionResult

    # Source file
    source = tmp_path / "raw" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake pdf")
    (tmp_path / "wiki").mkdir()

    config = WikiConfig()

    # Patched extract_text that returns a result with a quality_warning
    warned_result = ExtractionResult(
        success=True,
        content="x\n" * 50,
        extraction_method="pdf",
        token_count=50,
        quality_warning="low word/line ratio (1.0) — extraction may be mangled",
    )

    fake_llm = AsyncMock()
    fake_llm.complete = AsyncMock(return_value=MagicMock(content='{"concepts": []}'))

    with patch("llm_wiki.ingest.agent.extract_text", return_value=warned_result):
        agent = IngestAgent(config=config, llm=fake_llm)
        result = await agent.ingest(
            source_path=source,
            vault_root=tmp_path,
            dry_run=True,
        )

    assert result.extraction_warning == "low word/line ratio (1.0) — extraction may be mangled"


@pytest.mark.asyncio
async def test_ingest_result_extraction_warning_absent_on_clean_extraction(tmp_path):
    """Clean extraction produces IngestResult with extraction_warning = None."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from llm_wiki.config import WikiConfig
    from llm_wiki.ingest.agent import IngestAgent
    from llm_wiki.ingest.extractor import ExtractionResult

    source = tmp_path / "raw" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake pdf")
    (tmp_path / "wiki").mkdir()

    config = WikiConfig()

    clean_result = ExtractionResult(
        success=True,
        content="Normal text content with good word line ratio.\n" * 5,
        extraction_method="pdf",
        token_count=100,
        quality_warning=None,
    )

    fake_llm = AsyncMock()
    fake_llm.complete = AsyncMock(return_value=MagicMock(content='{"concepts": []}'))

    with patch("llm_wiki.ingest.agent.extract_text", return_value=clean_result):
        agent = IngestAgent(config=config, llm=fake_llm)
        result = await agent.ingest(
            source_path=source,
            vault_root=tmp_path,
            dry_run=True,
        )

    assert result.extraction_warning is None


@pytest.mark.asyncio
async def test_dry_run_makes_only_one_llm_call(tmp_path: Path):
    """Dry-run stops after concept extraction — no page-content LLM calls."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions. k-means clusters data.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
        {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]},
    ])
    mock_llm = MockLLMClient([concept_response])  # only 1 response scripted
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path, dry_run=True)

    assert len(mock_llm.calls) == 1   # concept extraction only
    assert result.concepts_found == 2


@pytest.mark.asyncio
async def test_dry_run_returns_previews_without_sections(tmp_path: Path):
    """Dry-run ConceptPreview has name/title/is_update/passages but no sections."""
    (tmp_path / "raw").mkdir()
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # One existing page, one new
    (wiki_dir / "pca.md").write_text("---\ntitle: PCA\n---\n\nExisting.")

    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA content. K-Means content.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA content."]},
        {"name": "k-means", "title": "K-Means", "passages": ["K-Means content."]},
    ])
    mock_llm = MockLLMClient([concept_response])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path, dry_run=True)

    assert len(result.concepts_planned) == 2
    pca = next(c for c in result.concepts_planned if c.name == "pca")
    km = next(c for c in result.concepts_planned if c.name == "k-means")

    assert pca.is_update is True
    assert km.is_update is False
    assert pca.passages == ["PCA content."]
    assert pca.sections == []   # no section generation in dry-run
    assert km.sections == []


@pytest.mark.asyncio
async def test_on_progress_callback_receives_correct_frames(tmp_path: Path):
    """on_progress receives extracting → concepts_found → concept_done frames in order."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions. k-means clusters data.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
        {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]},
    ])
    pca_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.md]]."},
    ])
    km_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "k-means [[raw/paper.md]]."},
    ])

    mock_llm = MockLLMClient([concept_response, pca_sections, km_sections])
    agent = IngestAgent(mock_llm, WikiConfig())

    frames: list[dict] = []

    async def capture(frame: dict) -> None:
        frames.append(frame)

    await agent.ingest(source, tmp_path, on_progress=capture)

    stages = [f["stage"] for f in frames]
    assert stages[0] == "extracting"
    assert stages[1] == "concepts_found"
    assert frames[1]["count"] == 2
    assert stages[2] == "concept_done"
    assert frames[2]["name"] == "pca"
    assert frames[2]["action"] in ("created", "updated")
    assert frames[2]["num"] == 1
    assert frames[2]["total"] == 2
    assert stages[3] == "concept_done"
    assert frames[3]["name"] == "k-means"
    assert frames[3]["num"] == 2


@pytest.mark.asyncio
async def test_on_progress_none_is_safe(tmp_path: Path):
    """on_progress=None (default) works — no errors, result is correct."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
    ])
    pca_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.md]]."},
    ])
    mock_llm = MockLLMClient([concept_response, pca_sections])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)  # no on_progress kwarg

    assert result.pages_created == ["pca"]
