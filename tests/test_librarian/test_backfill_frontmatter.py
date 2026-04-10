from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent
from llm_wiki.vault import Vault


class _StubLLM:
    """LLM stub that must never be called during backfill tests."""

    async def complete(self, messages, temperature=0.7, priority="query"):
        raise AssertionError("LLM must not be called during frontmatter backfill")


def _make_agent(vault: Vault, vault_root: Path) -> LibrarianAgent:
    return LibrarianAgent(
        vault,
        vault_root,
        _StubLLM(),
        IssueQueue(vault_root / "wiki"),
        WikiConfig(),
    )


def _read_frontmatter(path: Path) -> dict:
    """Parse frontmatter from a markdown file. Returns {} if none."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}
    end = raw.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = raw[3:end].strip()
    try:
        return yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}


def _read_body(path: Path) -> str:
    """Return the body portion of the markdown file (after frontmatter)."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return raw
    end = raw.find("\n---", 3)
    if end == -1:
        return raw
    return raw[end + 4:].strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_vault(tmp_path: Path) -> Path:
    """Create a minimal vault with a few pages for backfill testing."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: missing 'created' gets backfilled
# ---------------------------------------------------------------------------

def test_backfill_writes_created_when_absent(simple_vault: Path):
    """A page without 'created' in frontmatter gets the field backfilled."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "mypage.md"
    page.write_text(
        "---\ntitle: My Page\n---\n\n## Overview\n\nSome content here.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 1
    fm = _read_frontmatter(page)
    assert "created" in fm
    # Should be a YYYY-MM-DD string
    created = fm["created"]
    assert len(str(created)) == 10
    assert str(created).count("-") == 2


def test_backfill_writes_updated_when_absent(simple_vault: Path):
    """A page without 'updated' gets it backfilled alongside 'created'."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "page-no-updated.md"
    page.write_text(
        "---\ntitle: Test\ncreated: '2024-01-01'\n---\n\n## Body\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 1
    fm = _read_frontmatter(page)
    assert "updated" in fm


# ---------------------------------------------------------------------------
# Tests: status backfill for ingest pages
# ---------------------------------------------------------------------------

def test_backfill_status_stub_for_ingest_page(simple_vault: Path):
    """A page with created_by=ingest but no status gets status: stub."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "ingest-page.md"
    page.write_text(
        "---\n"
        "title: Ingested\n"
        "created: '2024-03-01'\n"
        "updated: '2024-03-01'\n"
        "type: concept\n"
        "created_by: ingest\n"
        "---\n\n## Overview\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 1
    fm = _read_frontmatter(page)
    assert fm["status"] == "stub"


def test_backfill_no_status_for_non_ingest_page(simple_vault: Path):
    """A page without created_by=ingest does NOT get status backfilled."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "manual-page.md"
    page.write_text(
        "---\n"
        "title: Manual\n"
        "created: '2024-03-01'\n"
        "updated: '2024-03-01'\n"
        "type: concept\n"
        "---\n\n## Overview\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    # no missing fields that apply — should not touch this page
    assert count == 0
    fm = _read_frontmatter(page)
    assert "status" not in fm


# ---------------------------------------------------------------------------
# Tests: ingested backfill for ingest pages
# ---------------------------------------------------------------------------

def test_backfill_ingested_for_ingest_page(simple_vault: Path):
    """A page with created_by=ingest but no ingested gets it backfilled."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "ingested-no-field.md"
    page.write_text(
        "---\n"
        "title: Ingested\n"
        "created: '2024-03-01'\n"
        "updated: '2024-03-01'\n"
        "type: concept\n"
        "status: stub\n"
        "created_by: ingest\n"
        "---\n\n## Overview\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 1
    fm = _read_frontmatter(page)
    assert "ingested" in fm


# ---------------------------------------------------------------------------
# Tests: complete pages are NOT modified
# ---------------------------------------------------------------------------

def test_backfill_does_not_touch_complete_page(simple_vault: Path):
    """A page that already has all fields is not modified."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "complete.md"
    content = (
        "---\n"
        "title: Complete\n"
        "created: '2024-01-01'\n"
        "updated: '2024-01-01'\n"
        "type: concept\n"
        "status: stub\n"
        "ingested: '2024-01-01'\n"
        "created_by: ingest\n"
        "---\n\n## Overview\n\nFull content here.\n"
    )
    page.write_text(content)
    original_mtime = page.stat().st_mtime

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 0
    # File should be byte-for-byte unchanged
    assert page.read_text(encoding="utf-8") == content


def test_backfill_does_not_modify_existing_fields(simple_vault: Path):
    """Fields that are already present are not overwritten."""
    wiki_dir = simple_vault / "wiki"
    page = wiki_dir / "partial.md"
    page.write_text(
        "---\n"
        "title: Partial\n"
        "created: '2020-06-15'\n"
        "updated: '2021-12-01'\n"
        "type: reference\n"
        "---\n\n## Overview\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    agent._backfill_frontmatter()

    fm = _read_frontmatter(page)
    # Existing fields must not be changed
    assert fm["created"] == "2020-06-15"
    assert fm["updated"] == "2021-12-01"
    assert fm["type"] == "reference"


# ---------------------------------------------------------------------------
# Tests: body content is NOT modified
# ---------------------------------------------------------------------------

def test_backfill_does_not_modify_body(simple_vault: Path):
    """Backfill only touches frontmatter — the body is preserved exactly."""
    wiki_dir = simple_vault / "wiki"
    body_text = (
        "%% section: overview, tokens: 5 %%\n"
        "## Overview\n\n"
        "This is a very specific body with [[wikilinks]] and special chars: &<>.\n"
    )
    page = wiki_dir / "body-check.md"
    page.write_text("---\ntitle: Body Check\n---\n\n" + body_text)

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    agent._backfill_frontmatter()

    assert _read_body(page) == body_text.strip()


# ---------------------------------------------------------------------------
# Tests: pages_backfilled count
# ---------------------------------------------------------------------------

def test_pages_backfilled_count_correct(simple_vault: Path):
    """pages_backfilled reflects exactly how many pages were modified."""
    wiki_dir = simple_vault / "wiki"

    # Page 1: missing created (needs backfill)
    (wiki_dir / "needs-backfill.md").write_text(
        "---\ntitle: Needs\n---\n\n## Body\n\nContent.\n"
    )
    # Page 2: already complete (no backfill needed)
    (wiki_dir / "already-complete.md").write_text(
        "---\n"
        "title: Complete\n"
        "created: '2024-01-01'\n"
        "updated: '2024-01-01'\n"
        "type: concept\n"
        "---\n\n## Body\n\nContent.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    count = agent._backfill_frontmatter()

    assert count == 1


# ---------------------------------------------------------------------------
# Integration: run() exposes pages_backfilled on LibrarianResult
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_exposes_pages_backfilled(simple_vault: Path):
    """LibrarianResult.pages_backfilled is set after run()."""
    wiki_dir = simple_vault / "wiki"
    # One page missing created
    (wiki_dir / "run-test.md").write_text(
        "---\ntitle: Run Test\n---\n\n## Overview\n\nSome content.\n"
    )

    vault = Vault.scan(simple_vault)
    agent = _make_agent(vault, simple_vault)
    result = await agent.run()

    assert result.pages_backfilled == 1


@pytest.mark.asyncio
async def test_run_empty_vault_pages_backfilled_zero(tmp_path: Path):
    """run() on an empty vault sets pages_backfilled to 0."""
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    agent = _make_agent(vault, tmp_path)
    result = await agent.run()

    assert result.pages_backfilled == 0
