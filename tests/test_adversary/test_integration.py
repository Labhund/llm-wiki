"""End-to-end: vault → adversary.run() → verdict dispatch → state assertions."""
from __future__ import annotations

from pathlib import Path
from random import Random

import pytest

from llm_wiki.adversary.agent import AdversaryAgent
from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides
from llm_wiki.talk.page import TalkPage
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    def __init__(self, response_text: str) -> None:
        self.response = response_text

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query", **kwargs):
        from llm_wiki.traverse.llm_client import LLMResponse
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=0)


def _build_vault_with_three_claims(tmp_path: Path) -> tuple[Path, list[Path]]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for slug in ("a", "b", "c"):
        (raw_dir / f"src-{slug}.md").write_text(f"# Source {slug}\n\nClaim {slug} is true.\n")

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    pages: list[Path] = []
    for slug in ("a", "b", "c"):
        page = wiki_dir / f"page-{slug}.md"
        page.write_text(
            f"---\ntitle: Page {slug}\n---\n\n"
            f"%% section: claim %%\n## Claim\n\n"
            f"Claim {slug} is true [[raw/src-{slug}.md]].\n"
        )
        pages.append(page)
    return tmp_path, pages


@pytest.fixture
def _clean_state():
    """Clean up vault state dirs created during integration tests."""
    created: list[Path] = []
    yield created
    import shutil
    for d in created:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_adversary_full_lifecycle_validated(tmp_path: Path, _clean_state):
    vault_root, _ = _build_vault_with_three_claims(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=10),
        vault=VaultConfig(wiki_dir="wiki/"),
    )
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.95, "explanation": "Source matches."}'
    )

    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config, rng=Random(42))

    result = await agent.run()
    assert result.claims_checked == 3
    assert len(result.validated) == 3
    assert result.failed == []
    assert result.talk_posts == []

    overrides = ManifestOverrides.load(_state_dir_for(vault_root) / "manifest_overrides.json")
    for slug in ("a", "b", "c"):
        po = overrides.get(f"page-{slug}")
        assert po is not None
        assert po.last_corroborated is not None


@pytest.mark.asyncio
async def test_adversary_full_lifecycle_mixed_verdicts(tmp_path: Path, _clean_state):
    """Different verdicts dispatched correctly across multiple claims."""
    vault_root, pages = _build_vault_with_three_claims(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=10),
        vault=VaultConfig(wiki_dir="wiki/"),
    )

    # Cycle through verdicts using a counter on the stub
    class _CyclingLLM:
        verdicts = [
            '{"verdict": "validated", "confidence": 0.95, "explanation": "ok"}',
            '{"verdict": "contradicted", "confidence": 0.85, "explanation": "bad"}',
            '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "unclear"}',
        ]

        def __init__(self) -> None:
            self.i = 0

        async def complete(self, messages, temperature: float = 0.7, priority: str = "query", **kwargs):
            from llm_wiki.traverse.llm_client import LLMResponse
            response = self.verdicts[self.i % 3]
            self.i += 1
            return LLMResponse(content=response, input_tokens=100, output_tokens=0)

    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, _CyclingLLM(), queue, config, rng=Random(0))

    result = await agent.run()
    assert result.claims_checked == 3
    assert len(result.validated) == 1
    assert len(result.failed) == 1
    assert len(result.talk_posts) == 1

    # The talk post page should have a real talk file with one entry
    talk_pages = result.talk_posts
    talk_page_slug = talk_pages[0]
    talk_path = vault_root / "wiki" / f"{talk_page_slug}.talk.md"
    assert talk_path.exists()
    talk = TalkPage(talk_path)
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].author == "@adversary"

    # The parent page should have the talk discovery marker
    parent_path = vault_root / "wiki" / f"{talk_page_slug}.md"
    parent_text = parent_path.read_text(encoding="utf-8")
    assert f"%% talk: [[{talk_page_slug}.talk]] %%" in parent_text

    # The contradicted verdict should have filed an issue
    issues = queue.list(type="claim-failed")
    assert len(issues) == 1
