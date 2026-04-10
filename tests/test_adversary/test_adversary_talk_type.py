from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.adversary.agent import AdversaryAgent
from llm_wiki.config import MaintenanceConfig, WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.talk.page import TalkPage
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    def __init__(self, response_text: str) -> None:
        self.response = response_text

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        return LLMResponse(content=self.response, input_tokens=50, output_tokens=0)


def _build_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Minimal vault with one page citing one raw source."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "src.md").write_text(
        "# Source\n\nSome content that is not clearly supportive.\n"
    )
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "test-page.md"
    page.write_text(
        "---\ntitle: Test Page\n---\n\n"
        "%% section: body %%\n## Body\n\n"
        "Some claim about things [[raw/src.md]].\n"
    )
    return tmp_path, page


@pytest.mark.asyncio
async def test_adversary_ambiguous_talk_entry_type(tmp_path: Path):
    """_handle_ambiguous must post a TalkEntry with type='adversary-finding'."""
    vault_root, page_path = _build_vault(tmp_path)
    state_dir = _state_dir_for(vault_root)

    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    stub = _StubLLM(
        '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Unclear."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()

    assert len(result.talk_posts) == 1, "expected one talk post for ambiguous verdict"

    talk = TalkPage.for_page(page_path)
    entries = talk.load()
    assert len(entries) >= 1
    adversary_entries = [e for e in entries if e.author == "@adversary"]
    assert adversary_entries, "expected at least one @adversary entry"
    for entry in adversary_entries:
        assert entry.type == "adversary-finding", (
            f"expected type='adversary-finding', got {entry.type!r}"
        )

    import shutil
    shutil.rmtree(state_dir, ignore_errors=True)
