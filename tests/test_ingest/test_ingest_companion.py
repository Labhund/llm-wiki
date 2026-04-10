from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.agent import IngestAgent
from llm_wiki.traverse.llm_client import LLMResponse


class MockLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def complete(self, messages, temperature=0.7, priority="query") -> LLMResponse:
        content = self._responses[self._idx]
        self._idx += 1
        return LLMResponse(content=content, input_tokens=10, output_tokens=0)


def _concept_json(concepts):
    return json.dumps({"concepts": concepts})

def _sections_json(sections):
    return json.dumps({"sections": sections})


@pytest.mark.asyncio
async def test_ingest_creates_companion_for_pdf_in_raw(tmp_path: Path):
    """wiki_ingest on a PDF in raw/ creates a companion .md with reading_status: unread."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = raw_dir / "2026-04-10-paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    llm = MockLLMClient([
        _concept_json([{"name": "pca", "title": "PCA", "passages": ["PCA reduces dims."]}]),
        _sections_json([{"heading": "Overview", "content": "PCA overview."}]),
    ])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path, source_type="paper")

    companion = raw_dir / "2026-04-10-paper.md"
    assert companion.exists()
    from llm_wiki.ingest.source_meta import read_frontmatter
    fm = read_frontmatter(companion)
    assert fm["reading_status"] == "unread"
    assert fm["source_type"] == "paper"
    # Body should contain extracted text (even if liteparse fake returns "")
    content = companion.read_text()
    assert "---" in content


@pytest.mark.asyncio
async def test_ingest_does_not_create_companion_outside_raw(tmp_path: Path):
    """wiki_ingest on a file outside raw/ never creates a companion."""
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = other / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    llm = MockLLMClient([
        _concept_json([]),
    ])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path)

    assert not (other / "paper.md").exists()


@pytest.mark.asyncio
async def test_ingest_does_not_overwrite_existing_companion(tmp_path: Path):
    """If companion already exists, ingest must not touch it."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (tmp_path / "wiki").mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    companion = raw_dir / "paper.md"
    companion.write_text("---\nreading_status: in_progress\n---\nPrior body.\n")

    llm = MockLLMClient([_concept_json([])])
    agent = IngestAgent(llm, WikiConfig())
    await agent.ingest(pdf, tmp_path)

    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"
    assert "Prior body." in companion.read_text()
