from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.config import WikiConfig
from llm_wiki.page import Page, Section
from llm_wiki.resonance.agent import ResonanceAgent
from llm_wiki.search.backend import SearchResult
from llm_wiki.talk.page import TalkPage


def _make_page_with_claim(slug: str, claim_text: str, citation: str, tmp_path: Path) -> Page:
    """Write a wiki page with one cited claim and return the parsed Page."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    path = wiki_dir / f"{slug}.md"
    content = f"---\ntitle: {slug}\n---\n{claim_text} [[{citation}]].\n"
    path.write_text(content)
    return Page.parse(path)


@pytest.mark.asyncio
async def test_resonance_agent_posts_talk_entry_on_match(tmp_path: Path):
    """When LLM returns YES, a resonance talk entry is posted on the existing page."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "rfdiffusion", "Noise scale controls output diversity", "raw/rfd.pdf", tmp_path
    )
    existing_page = _make_page_with_claim(
        "diffusion-models", "Noise controls generative diversity", "raw/old.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.side_effect = lambda name: {
        "rfdiffusion": new_page,
        "diffusion-models": existing_page,
    }.get(name)
    vault.search.return_value = [
        SearchResult(name="diffusion-models", score=0.9, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content="VERDICT: YES\nRELATION: corroborates\nNOTE: Both discuss noise as diversity control."
    ))

    config = WikiConfig()
    config.maintenance.resonance_matching = True
    config.maintenance.resonance_candidates_per_claim = 1

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["rfdiffusion"])

    assert result.resonance_posts == [("rfdiffusion", "diffusion-models")]

    talk = TalkPage.for_page(wiki_dir / "diffusion-models.md")
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].type == "resonance"
    assert entries[0].severity == "moderate"
    assert "corroborates" in entries[0].body


@pytest.mark.asyncio
async def test_resonance_agent_no_post_on_no_match(tmp_path: Path):
    """When LLM returns NO, no talk entry is posted."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "rfdiffusion", "Noise scale controls output diversity", "raw/rfd.pdf", tmp_path
    )
    existing_page = _make_page_with_claim(
        "other-topic", "Unrelated claim about chemistry", "raw/chem.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.side_effect = lambda name: {
        "rfdiffusion": new_page,
        "other-topic": existing_page,
    }.get(name)
    vault.search.return_value = [
        SearchResult(name="other-topic", score=0.3, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(content="VERDICT: NO"))

    config = WikiConfig()
    config.maintenance.resonance_candidates_per_claim = 1

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["rfdiffusion"])

    assert result.resonance_posts == []
    talk = TalkPage.for_page(wiki_dir / "other-topic.md")
    assert not talk.exists


@pytest.mark.asyncio
async def test_resonance_agent_skips_new_pages_as_candidates(tmp_path: Path):
    """The agent must not compare a new page against itself."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "new-page", "A claim", "raw/src.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.return_value = new_page
    vault.search.return_value = [
        SearchResult(name="new-page", score=0.95, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock()

    config = WikiConfig()
    config.maintenance.resonance_candidates_per_claim = 3

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["new-page"])

    llm.complete.assert_not_called()
    assert result.resonance_posts == []


@pytest.mark.asyncio
async def test_resonance_agent_empty_pages_noop(tmp_path: Path):
    vault = MagicMock()
    llm = MagicMock()
    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=WikiConfig())
    result = await agent.run_for_pages([])
    assert result.resonance_posts == []
    vault.read_page.assert_not_called()
