from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.adversary.agent import AdversaryAgent
from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.page import Page


def _make_page(slug: str, type_: str | None, has_claim: bool, tmp_path: Path) -> Page:
    body = ""
    if has_claim:
        body = "A verifiable claim [[raw/source.pdf]].\n"
    fm = f"---\ntype: {type_}\n---\n" if type_ else "---\ntitle: Normal\n---\n"
    path = tmp_path / "wiki" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(fm + body)
    return Page.parse(path)


@pytest.mark.asyncio
async def test_adversary_skips_synthesis_page_claims(tmp_path: Path):
    """Claims from type:synthesis pages must never be processed."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    synthesis_page = _make_page("syn-page", "synthesis", has_claim=True, tmp_path=tmp_path)
    normal_page = _make_page("normal-page", None, has_claim=True, tmp_path=tmp_path)

    vault = MagicMock()
    vault.manifest_entries.return_value = {
        "syn-page": MagicMock(authority=0.5, last_corroborated=None),
        "normal-page": MagicMock(authority=0.5, last_corroborated=None),
    }
    vault.read_page.side_effect = lambda name: {
        "syn-page": synthesis_page,
        "normal-page": normal_page,
    }.get(name)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content="VERDICT: validated\nCONFIDENCE: 0.9\nEXPLANATION: ok"
    ))

    queue = IssueQueue(tmp_path / ".issues")
    config = WikiConfig()
    config.maintenance.adversary_claims_per_run = 10

    agent = AdversaryAgent(
        vault=vault,
        vault_root=tmp_path,
        llm=llm,
        queue=queue,
        config=config,
    )

    processed_pages: list[str] = []
    original_extract = __import__(
        "llm_wiki.adversary.claim_extractor", fromlist=["extract_claims"]
    ).extract_claims

    def tracking_extract(page: Page, raw_dir: str = "raw") -> list[Claim]:
        processed_pages.append(page.path.stem)
        return original_extract(page, raw_dir=raw_dir)

    with patch("llm_wiki.adversary.agent.extract_claims", side_effect=tracking_extract):
        await agent.run()

    assert "syn-page" not in processed_pages
    assert "normal-page" in processed_pages
