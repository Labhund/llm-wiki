from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent, LibrarianResult
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    """Async LLM stub matching LLMClient.complete shape."""

    def __init__(self, response_text: str = '{"tags": [], "summary": null}') -> None:
        self.response = response_text
        self.calls: list[list[dict]] = []

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        self.calls.append(messages)
        return LLMResponse(content=self.response, tokens_used=100)


def _seed_log(state_dir: Path, entries: list[dict]) -> None:
    log_dir = state_dir / "traversal_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "traversal_logs.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.mark.asyncio
async def test_recalc_authority_writes_overrides_for_every_page(sample_vault: Path):
    """recalc_authority computes scores for every entry and persists them."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How does k-means work?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "uses k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    vault = Vault.scan(sample_vault)
    queue = IssueQueue(sample_vault / "wiki")  # may not exist; OK for this test
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), queue, WikiConfig())

    count = await agent.recalc_authority()

    assert count == vault.page_count

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    for name in vault.manifest_entries():
        override = overrides.get(name)
        assert override is not None, f"missing override for {name}"
        assert 0.0 <= override.authority <= 1.0


@pytest.mark.asyncio
async def test_recalc_authority_does_not_call_llm(sample_vault: Path):
    """recalc_authority is purely programmatic."""
    vault = Vault.scan(sample_vault)
    stub = _StubLLM()
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    await agent.recalc_authority()

    assert stub.calls == []


@pytest.mark.asyncio
async def test_recalc_authority_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    count = await agent.recalc_authority()
    assert count == 0


@pytest.mark.asyncio
async def test_refresh_page_updates_overrides_with_llm_output(sample_vault: Path):
    """refresh_page calls the LLM and writes the parsed tags/summary."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How are sRNA embeddings validated?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": ["overview"], "salient_points": "PCA + k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM(
        '{"tags": ["embeddings", "validation", "k-means"], "summary": "Validates sRNA embeddings via PCA + k-means."}'
    )
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")

    assert refreshed is True
    assert len(stub.calls) == 1

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = overrides.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["embeddings", "validation", "k-means"]
    assert got.summary_override == "Validates sRNA embeddings via PCA + k-means."
    assert got.last_refreshed_read_count == 1   # one query in the seeded log


@pytest.mark.asyncio
async def test_refresh_page_unknown_page_returns_false(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    assert await agent.refresh_page("nope") is False


@pytest.mark.asyncio
async def test_refresh_page_invalid_llm_response_does_not_corrupt_overrides(sample_vault: Path):
    """If the LLM returns junk, the override is left unchanged."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["original"],
        summary_override="original summary",
        authority=0.5,
    ))
    overrides.save()

    stub = _StubLLM("complete garbage, not JSON")
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")
    assert refreshed is False

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["original"]
    assert got.summary_override == "original summary"


@pytest.mark.asyncio
async def test_recalc_authority_preserves_existing_tags_and_summary(sample_vault: Path):
    """recalc_authority must not clobber tags/summary set by prior refinement."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["preserved-tag"],
        summary_override="preserved summary",
        authority=0.0,
        read_count=12,
        last_refreshed_read_count=12,
    ))
    overrides.save()

    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    await agent.recalc_authority()

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["preserved-tag"]
    assert got.summary_override == "preserved summary"
    assert got.read_count == 12
    assert got.last_refreshed_read_count == 12
