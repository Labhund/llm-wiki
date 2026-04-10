"""End-to-end: traversal log → librarian.run() → vault rescan → entries reflect refinement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    def __init__(self, response_text: str) -> None:
        self.response = response_text

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=0)


@pytest.mark.asyncio
async def test_librarian_full_lifecycle(sample_vault: Path):
    """Run librarian → rescan vault → assert entry has the refined fields."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Seed 5 distinct queries that read srna-embeddings
    log_dir = state_dir / "traversal_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "traversal_logs.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for i in range(5):
            f.write(json.dumps({
                "query": f"How are sRNA embeddings validated? variant {i}",
                "turns": [{"turn": 0, "pages_read": [
                    {"name": "srna-embeddings", "sections_read": ["overview"],
                     "salient_points": f"PCA k=10 (sample {i})", "relevance": 0.85}
                ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
            }) + "\n")

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 3

    stub = _StubLLM(
        '{"tags": ["validation", "embeddings", "k-means"], '
        '"summary": "Validates sRNA embeddings via PCA + k-means."}'
    )

    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()

    assert "srna-embeddings" in result.pages_refined
    assert result.authorities_updated >= 1

    # Rescan to verify the override survives + is applied to the manifest entry
    rescanned = Vault.scan(sample_vault)
    entry = rescanned.manifest_entries()["srna-embeddings"]

    assert entry.tags == ["validation", "embeddings", "k-means"]
    assert "PCA + k-means" in entry.summary
    assert entry.authority > 0.0
    assert entry.read_count == 5
