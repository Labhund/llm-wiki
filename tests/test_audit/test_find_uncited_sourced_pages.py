from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.checks import CheckResult, find_uncited_sourced_pages
from llm_wiki.vault import Vault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, pages: dict[str, str]) -> Vault:
    """Write pages into tmp_path/wiki/ and return a scanned Vault."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    for filename, content in pages.items():
        page_path = wiki_dir / filename
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(content, encoding="utf-8")
    return Vault.scan(tmp_path)


def _page(body: str = "Content.", **fm_extra) -> str:
    """Build a minimal page with the given frontmatter extras and body."""
    base = {
        "title": "Test Page",
        "created": "2026-04-10",
        "updated": "2026-04-10",
        "type": "concept",
        "status": "stub",
    }
    base.update(fm_extra)
    lines = "\n".join(f"{k}: {v}" for k, v in base.items())
    return f"---\n{lines}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_returns_check_result(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_uncited_sourced_pages(vault)
    assert isinstance(result, CheckResult)
    assert result.check == "uncited-source"


def test_empty_vault_no_issues(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Pages that SHOULD be flagged
# ---------------------------------------------------------------------------

def test_source_field_no_inline_citations_flagged(tmp_path: Path):
    """Page with source: field and no [[raw/...]] in body → flagged as moderate."""
    content = _page(
        body="This page discusses something.",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"boltz-2.md": content})
    result = find_uncited_sourced_pages(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.type == "uncited-source"
    assert issue.severity == "moderate"
    assert issue.page == "boltz-2"
    assert "boltz-2" in issue.title
    assert "[[raw/...]]" in issue.title


def test_created_by_ingest_no_inline_citations_flagged(tmp_path: Path):
    """created_by: ingest with no [[raw/...]] in body → flagged."""
    content = _page(
        body="Some ingested content without body citations.",
        created_by="ingest",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"ingest-page.md": content})
    result = find_uncited_sourced_pages(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.type == "uncited-source"
    assert issue.severity == "moderate"
    assert issue.page == "ingest-page"


def test_created_by_proposal_no_inline_citations_flagged(tmp_path: Path):
    """created_by: proposal with no [[raw/...]] in body → flagged."""
    content = _page(
        body="Some proposed content.",
        created_by="proposal",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"proposal-page.md": content})
    result = find_uncited_sourced_pages(vault)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "moderate"
    assert result.issues[0].page == "proposal-page"


def test_source_field_only_no_created_by_flagged(tmp_path: Path):
    """source: field alone (no created_by) and no inline citations → still flagged."""
    content = _page(
        body="Hand-edited page with a source but no body citations.",
        source="[[raw/notes.pdf]]",
    )
    vault = _make_vault(tmp_path, {"sourced.md": content})
    result = find_uncited_sourced_pages(vault)
    assert len(result.issues) == 1
    assert result.issues[0].page == "sourced"


def test_bare_filename_citation_not_raw_still_flagged(tmp_path: Path):
    """Body citation [[boltz2.pdf]] (no raw/ prefix) does NOT count → still flagged."""
    content = _page(
        body="This paper found X [[boltz2.pdf]].",
        source="[[raw/boltz2.pdf]]",
    )
    vault = _make_vault(tmp_path, {"boltz2.md": content})
    result = find_uncited_sourced_pages(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.page == "boltz2"
    assert issue.type == "uncited-source"


# ---------------------------------------------------------------------------
# Pages that should NOT be flagged
# ---------------------------------------------------------------------------

def test_page_with_inline_raw_citation_not_flagged(tmp_path: Path):
    """Body contains [[raw/paper.pdf]] → no issue."""
    content = _page(
        body="This paper found X [[raw/paper.pdf]].",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"cited.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


def test_source_and_inline_citations_no_issue(tmp_path: Path):
    """source: field AND inline [[raw/...]] citation → clean."""
    content = _page(
        body="Boltz-2 achieves state-of-the-art accuracy [[raw/boltz2.pdf]].",
        created_by="ingest",
        source="[[raw/boltz2.pdf]]",
    )
    vault = _make_vault(tmp_path, {"boltz2.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


def test_hand_written_page_no_source_no_created_by_exempt(tmp_path: Path):
    """Page with no source: and no created_by → hand-written, exempt."""
    content = _page(body="Hand-written note with no source citation.")
    vault = _make_vault(tmp_path, {"hand-written.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


def test_created_by_manual_exempt(tmp_path: Path):
    """created_by: manual (not ingest/proposal) with no inline citations → exempt."""
    content = _page(
        body="Manual page content.",
        created_by="manual",
    )
    vault = _make_vault(tmp_path, {"manual.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


def test_inline_citation_with_alias_counts(tmp_path: Path):
    """[[raw/paper.pdf|Paper Title]] (aliased) counts as a valid citation."""
    content = _page(
        body="The method outperforms baselines [[raw/boltz2.pdf|Boltz-2]].",
        source="[[raw/boltz2.pdf]]",
    )
    vault = _make_vault(tmp_path, {"boltz2.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Issue shape
# ---------------------------------------------------------------------------

def test_issue_shape(tmp_path: Path):
    """Issue has correct type, status, detected_by, severity, and id prefix."""
    content = _page(
        body="No inline citations.",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"my-page.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues
    issue = result.issues[0]
    assert issue.type == "uncited-source"
    assert issue.status == "open"
    assert issue.severity == "moderate"
    assert issue.detected_by == "auditor"
    assert issue.id.startswith("uncited-source-")
    assert issue.page == "my-page"
    assert isinstance(issue.body, str)
    assert len(issue.body) > 0


def test_issue_body_mentions_adversary(tmp_path: Path):
    """Issue body should explain that the adversary cannot verify claims."""
    content = _page(
        body="Uncited content.",
        source="[[raw/paper.pdf]]",
    )
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_uncited_sourced_pages(vault)
    assert result.issues
    body = result.issues[0].body
    assert "adversary" in body.lower()
    assert "[[raw/" in body


def test_issue_id_is_deterministic(tmp_path: Path):
    """Running the check twice on the same page produces identical ids."""
    content = _page(body="Uncited.", source="[[raw/paper.pdf]]")
    vault1 = _make_vault(tmp_path, {"stable.md": content})
    id1 = find_uncited_sourced_pages(vault1).issues[0].id

    import shutil
    from llm_wiki.vault import _state_dir_for
    state_dir = _state_dir_for(tmp_path)
    if state_dir.exists():
        shutil.rmtree(state_dir)

    vault2 = Vault.scan(tmp_path)
    id2 = find_uncited_sourced_pages(vault2).issues[0].id
    assert id1 == id2


# ---------------------------------------------------------------------------
# Multiple pages
# ---------------------------------------------------------------------------

def test_multiple_pages_each_gets_own_issue(tmp_path: Path):
    pages = {
        "page-a.md": _page(body="No citations.", source="[[raw/a.pdf]]"),
        "page-b.md": _page(body="No citations.", created_by="ingest", source="[[raw/b.pdf]]"),
        "clean.md": _page(body="Cited claim [[raw/c.pdf]].", source="[[raw/c.pdf]]"),
        "exempt.md": _page(body="Hand-written, no source."),
    }
    vault = _make_vault(tmp_path, pages)
    result = find_uncited_sourced_pages(vault)
    flagged = {i.page for i in result.issues}
    assert "page-a" in flagged
    assert "page-b" in flagged
    assert "clean" not in flagged
    assert "exempt" not in flagged
    assert len(result.issues) == 2
