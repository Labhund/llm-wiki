from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.checks import CheckResult, find_index_out_of_sync
from llm_wiki.vault import Vault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, pages: dict[str, str], index_content: str | None = None) -> Vault:
    """Write pages into tmp_path/wiki/ and optionally write an index.md.

    pages: mapping of filename (e.g. "rfdiffusion.md") to content.
    index_content: if given, written to tmp_path/wiki/index.md.
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    for filename, content in pages.items():
        page_path = wiki_dir / filename
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(content, encoding="utf-8")
    if index_content is not None:
        (wiki_dir / "index.md").write_text(index_content, encoding="utf-8")
    return Vault.scan(tmp_path)


def _page(title: str = "Test") -> str:
    return f"---\ntitle: {title}\ncreated: 2026-04-10\nupdated: 2026-04-10\ntype: concept\nstatus: stub\n---\n\nContent.\n"


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_returns_check_result(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_index_out_of_sync(vault)
    assert isinstance(result, CheckResult)
    assert result.check == "index-out-of-sync"


# ---------------------------------------------------------------------------
# Missing index → graceful skip
# ---------------------------------------------------------------------------

def test_no_index_no_issues(tmp_path: Path):
    """If wiki/index.md does not exist, return no issues without raising."""
    vault = _make_vault(tmp_path, {"alpha.md": _page("Alpha")})
    result = find_index_out_of_sync(vault)
    assert result.issues == []


def test_empty_vault_no_index_no_issues(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_index_out_of_sync(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Missing entry → minor issue
# ---------------------------------------------------------------------------

def test_missing_page_slug_in_index_is_minor(tmp_path: Path):
    """A page that exists in the vault but has no [[slug]] in the index."""
    vault = _make_vault(
        tmp_path,
        {"rfdiffusion.md": _page("RFDiffusion")},
        index_content="# Index\n\nNothing here yet.\n",
    )
    result = find_index_out_of_sync(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.type == "index-out-of-sync"
    assert issue.severity == "minor"
    assert issue.page == "rfdiffusion"
    assert "rfdiffusion" in issue.title


def test_missing_multiple_slugs(tmp_path: Path):
    """Two pages absent from the index → two minor issues."""
    vault = _make_vault(
        tmp_path,
        {
            "alpha.md": _page("Alpha"),
            "beta.md": _page("Beta"),
        },
        index_content="# Index\n\nEmpty.\n",
    )
    result = find_index_out_of_sync(vault)
    slugs = {i.page for i in result.issues}
    assert "alpha" in slugs
    assert "beta" in slugs
    for issue in result.issues:
        assert issue.severity == "minor"


# ---------------------------------------------------------------------------
# Broken link format → moderate issue
# ---------------------------------------------------------------------------

def test_broken_link_nonexistent_slug_is_moderate(tmp_path: Path):
    """[[ghost-page]] appears in the index but no such page exists."""
    vault = _make_vault(
        tmp_path,
        {"alpha.md": _page("Alpha")},
        index_content="# Index\n\n- [[alpha]]\n- [[ghost-page]]\n",
    )
    result = find_index_out_of_sync(vault)
    moderate = [i for i in result.issues if i.severity == "moderate"]
    assert len(moderate) == 1
    assert moderate[0].metadata["target"] == "ghost-page"
    assert moderate[0].page == "index"


def test_old_path_format_link_is_moderate(tmp_path: Path):
    """[[wiki/rfdiffusion.md]] is not a slug → moderate broken-link issue."""
    vault = _make_vault(
        tmp_path,
        {"rfdiffusion.md": _page("RFDiffusion")},
        index_content="# Index\n\n- [[wiki/rfdiffusion.md]]\n",
    )
    result = find_index_out_of_sync(vault)
    # "rfdiffusion" is missing from the index (minor) + the old path is a broken link (moderate)
    severities = {i.severity for i in result.issues}
    assert "moderate" in severities
    moderate = [i for i in result.issues if i.severity == "moderate"]
    assert moderate[0].metadata["target"] == "wiki/rfdiffusion.md"


# ---------------------------------------------------------------------------
# Clean state → no issues
# ---------------------------------------------------------------------------

def test_all_pages_in_index_all_links_valid_no_issues(tmp_path: Path):
    """Every page has a [[slug]] in the index and every link resolves."""
    vault = _make_vault(
        tmp_path,
        {
            "alpha.md": _page("Alpha"),
            "beta.md": _page("Beta"),
        },
        index_content="# Index\n\n- [[alpha]]\n- [[beta]]\n",
    )
    result = find_index_out_of_sync(vault)
    assert result.issues == []


def test_empty_vault_with_empty_index_no_issues(tmp_path: Path):
    vault = _make_vault(tmp_path, {}, index_content="# Index\n\nNothing.\n")
    result = find_index_out_of_sync(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Duplicate links not double-reported
# ---------------------------------------------------------------------------

def test_duplicate_broken_link_reported_once(tmp_path: Path):
    """The same broken target appearing twice in the index is reported only once."""
    vault = _make_vault(
        tmp_path,
        {"alpha.md": _page("Alpha")},
        index_content="# Index\n\n- [[ghost]]\n- [[ghost]]\n",
    )
    result = find_index_out_of_sync(vault)
    moderate = [i for i in result.issues if i.severity == "moderate"]
    assert len(moderate) == 1


# ---------------------------------------------------------------------------
# Issue metadata
# ---------------------------------------------------------------------------

def test_missing_entry_issue_metadata(tmp_path: Path):
    vault = _make_vault(
        tmp_path,
        {"alpha.md": _page("Alpha")},
        index_content="# Index\n",
    )
    result = find_index_out_of_sync(vault)
    minor = [i for i in result.issues if i.severity == "minor"]
    assert len(minor) == 1
    issue = minor[0]
    assert issue.status == "open"
    assert issue.detected_by == "auditor"
    assert issue.id.startswith("index-out-of-sync-")
    assert issue.metadata.get("slug") == "alpha"


def test_broken_link_issue_metadata(tmp_path: Path):
    vault = _make_vault(
        tmp_path,
        {},
        index_content="# Index\n\n- [[old-format/page.md]]\n",
    )
    result = find_index_out_of_sync(vault)
    moderate = [i for i in result.issues if i.severity == "moderate"]
    assert len(moderate) == 1
    issue = moderate[0]
    assert issue.status == "open"
    assert issue.detected_by == "auditor"
    assert issue.metadata.get("target") == "old-format/page.md"
