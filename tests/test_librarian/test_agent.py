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
async def test_recalc_authority_with_passed_usage_matches_self_loaded(sample_vault: Path):
    """Passing a pre-aggregated usage dict yields the same overrides as loading logs internally."""
    from llm_wiki.librarian.log_reader import aggregate_logs

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How does k-means work?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
        {
            "query": "What is clustering?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "clustering-metrics", "sections_read": [], "salient_points": "silhouette", "relevance": 0.7}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    overrides_path = state_dir / "manifest_overrides.json"

    # Run 1: recalc_authority() loads logs internally (default path)
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    await agent.recalc_authority()
    baseline = ManifestOverrides.load(overrides_path)
    baseline_scores = {name: baseline.get(name).authority for name in vault.manifest_entries()}

    # Wipe the overrides file so the second run writes from scratch
    overrides_path.unlink()

    # Run 2: recalc_authority(usage=...) with the same logs, loaded once externally
    log_path = state_dir / "traversal_logs" / "traversal_logs.jsonl"
    usage = aggregate_logs(log_path)
    await agent.recalc_authority(usage=usage)
    passed = ManifestOverrides.load(overrides_path)
    passed_scores = {name: passed.get(name).authority for name in vault.manifest_entries()}

    assert passed_scores == baseline_scores


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


@pytest.mark.asyncio
async def test_run_refreshes_pages_above_threshold(sample_vault: Path):
    """A page with accumulated reads ≥ threshold gets refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Threshold is 3 in our test config
    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 3

    # 4 distinct queries reading srna-embeddings
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"point {i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(4)
    ])

    stub = _StubLLM('{"tags": ["validation"], "summary": "Refined."}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()

    assert isinstance(result, LibrarianResult)
    assert "srna-embeddings" in result.pages_refined
    assert result.authorities_updated == vault.page_count
    # The other fixture pages have zero reads, so they should NOT be refreshed
    assert "clustering-metrics" not in result.pages_refined


@pytest.mark.asyncio
async def test_run_skips_pages_below_threshold(sample_vault: Path):
    """A page with reads < threshold is not refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 10

    _seed_log(state_dir, [
        {
            "query": "q",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "x", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM('{"tags": ["x"], "summary": "y"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert result.pages_refined == []
    assert stub.calls == []  # no LLM calls
    assert result.authorities_updated == vault.page_count   # authority still recalculated


@pytest.mark.asyncio
async def test_run_uses_delta_since_last_refresh(sample_vault: Path):
    """A page already refreshed at read_count=10 is not re-refreshed at read_count=12 with threshold=5."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["existing"],
        last_refreshed_read_count=10,
    ))
    overrides.save()

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 5

    # Seed 12 distinct queries reading srna-embeddings (delta since last refresh = 2)
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"p{i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(12)
    ])

    stub = _StubLLM('{"tags": ["new"], "summary": "new summary"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert "srna-embeddings" not in result.pages_refined
    assert stub.calls == []


@pytest.mark.asyncio
async def test_run_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    result = await agent.run()
    assert result.pages_refined == []
    assert result.authorities_updated == 0
