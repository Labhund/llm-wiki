from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.checks import CheckResult, find_missing_frontmatter
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


def _full_fm(**extra) -> str:
    """Return a page with all required frontmatter fields present."""
    fm = {
        "title": "Test Page",
        "created": "2026-04-10",
        "updated": "2026-04-10",
        "type": "concept",
        "status": "stub",
        **extra,
    }
    lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{lines}\n---\n\nContent.\n"


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_returns_check_result(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_missing_frontmatter(vault)
    assert isinstance(result, CheckResult)
    assert result.check == "missing-frontmatter"


def test_empty_vault_no_issues(tmp_path: Path):
    vault = _make_vault(tmp_path, {})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Pages with all required fields → no issue
# ---------------------------------------------------------------------------

def test_page_with_all_fields_no_issue(tmp_path: Path):
    vault = _make_vault(tmp_path, {"complete.md": _full_fm()})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


def test_ingest_page_with_source_no_issue(tmp_path: Path):
    """created_by: ingest + source present → no issue."""
    content = _full_fm(created_by="ingest", source="[[raw/paper.pdf]]")
    vault = _make_vault(tmp_path, {"ingested.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


def test_proposal_page_with_source_no_issue(tmp_path: Path):
    """created_by: proposal + source present → no issue."""
    content = _full_fm(created_by="proposal", source="[[raw/paper.pdf]]")
    vault = _make_vault(tmp_path, {"proposed.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Missing minor-severity fields
# ---------------------------------------------------------------------------

def test_missing_created_raises_minor_issue(tmp_path: Path):
    content = "---\ntitle: P\nupdated: 2026-04-10\ntype: concept\nstatus: stub\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.type == "missing-frontmatter"
    assert issue.severity == "minor"
    assert issue.page == "p"
    assert "created" in issue.title
    assert "created" in issue.metadata["missing_fields"]


def test_missing_updated_raises_minor_issue(tmp_path: Path):
    content = "---\ntitle: P\ncreated: 2026-04-10\ntype: concept\nstatus: stub\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "minor"
    assert "updated" in result.issues[0].metadata["missing_fields"]


def test_missing_type_raises_minor_issue(tmp_path: Path):
    content = "---\ntitle: P\ncreated: 2026-04-10\nupdated: 2026-04-10\nstatus: stub\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "minor"
    assert "type" in result.issues[0].metadata["missing_fields"]


def test_missing_status_raises_minor_issue(tmp_path: Path):
    content = "---\ntitle: P\ncreated: 2026-04-10\nupdated: 2026-04-10\ntype: concept\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "minor"
    assert "status" in result.issues[0].metadata["missing_fields"]


def test_multiple_missing_minor_fields_single_issue(tmp_path: Path):
    """All four minor fields absent → exactly one issue listing all four."""
    content = "---\ntitle: Sparse\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"sparse.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "minor"
    missing = issue.metadata["missing_fields"]
    for f in ("created", "updated", "type", "status"):
        assert f in missing, f"expected {f!r} in missing_fields"
    # Title should mention all missing fields
    for f in ("created", "updated", "type", "status"):
        assert f in issue.title


# ---------------------------------------------------------------------------
# Missing source on ingest/proposal pages → moderate
# ---------------------------------------------------------------------------

def test_missing_source_on_ingest_page_raises_moderate_issue(tmp_path: Path):
    """created_by: ingest, source absent → moderate issue."""
    content = _full_fm(created_by="ingest")  # no source
    vault = _make_vault(tmp_path, {"ingest-page.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.type == "missing-frontmatter"
    assert issue.severity == "moderate"
    assert "source" in issue.metadata["missing_fields"]


def test_missing_source_on_proposal_page_raises_moderate_issue(tmp_path: Path):
    """created_by: proposal, source absent → moderate issue."""
    content = _full_fm(created_by="proposal")  # no source
    vault = _make_vault(tmp_path, {"proposal-page.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "moderate"
    assert "source" in result.issues[0].metadata["missing_fields"]


def test_missing_source_without_created_by_no_issue(tmp_path: Path):
    """Hand-written page (no created_by) with no source → NOT flagged."""
    content = _full_fm()  # no created_by, no source
    vault = _make_vault(tmp_path, {"hand-written.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


def test_missing_source_with_manual_created_by_no_issue(tmp_path: Path):
    """created_by: manual (not ingest/proposal) with no source → NOT flagged."""
    content = _full_fm(created_by="manual")
    vault = _make_vault(tmp_path, {"manual-page.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues == []


# ---------------------------------------------------------------------------
# Combined: minor + moderate fields missing in same page
# ---------------------------------------------------------------------------

def test_missing_minor_and_moderate_fields_single_issue_moderate_severity(tmp_path: Path):
    """When both minor and moderate fields missing, severity is moderate."""
    # created_by: ingest but missing created + source
    content = "---\ntitle: P\ncreated_by: ingest\nupdated: 2026-04-10\ntype: concept\nstatus: stub\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "moderate"
    assert "created" in issue.metadata["missing_fields"]
    assert "source" in issue.metadata["missing_fields"]


# ---------------------------------------------------------------------------
# Issue shape
# ---------------------------------------------------------------------------

def test_issue_shape(tmp_path: Path):
    """Issue has correct type, status, detected_by, and id prefix."""
    content = "---\ntitle: P\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"my-page.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues, "expected at least one issue"
    issue = result.issues[0]
    assert issue.type == "missing-frontmatter"
    assert issue.status == "open"
    assert issue.detected_by == "auditor"
    assert issue.id.startswith("missing-frontmatter-")
    assert issue.page == "my-page"
    assert isinstance(issue.body, str)
    assert len(issue.body) > 0


def test_issue_body_mentions_missing_field_purpose(tmp_path: Path):
    """The issue body should explain why each missing field matters."""
    content = "---\ntitle: P\n---\n\nBody.\n"
    vault = _make_vault(tmp_path, {"p.md": content})
    result = find_missing_frontmatter(vault)
    assert result.issues
    body = result.issues[0].body
    # Body should describe field purposes, not just list names
    assert "created" in body
    assert "updated" in body


# ---------------------------------------------------------------------------
# Multiple pages: each gets its own issue
# ---------------------------------------------------------------------------

def test_multiple_pages_each_gets_own_issue(tmp_path: Path):
    pages = {
        "page-a.md": "---\ntitle: A\n---\n\nBody.\n",
        "page-b.md": "---\ntitle: B\n---\n\nBody.\n",
        "complete.md": _full_fm(),
    }
    vault = _make_vault(tmp_path, pages)
    result = find_missing_frontmatter(vault)
    pages_with_issues = {i.page for i in result.issues}
    assert "page-a" in pages_with_issues
    assert "page-b" in pages_with_issues
    assert "complete" not in pages_with_issues
    assert len(result.issues) == 2


# ---------------------------------------------------------------------------
# Idempotency: same page produces same issue id
# ---------------------------------------------------------------------------

def test_issue_id_is_deterministic(tmp_path: Path):
    """Running the check twice on the same page produces identical ids."""
    content = "---\ntitle: P\n---\n\nBody.\n"
    vault1 = _make_vault(tmp_path, {"stable.md": content})
    id1 = find_missing_frontmatter(vault1).issues[0].id

    import shutil
    from llm_wiki.vault import _state_dir_for
    state_dir = _state_dir_for(tmp_path)
    if state_dir.exists():
        shutil.rmtree(state_dir)

    vault2 = Vault.scan(tmp_path)
    id2 = find_missing_frontmatter(vault2).issues[0].id
    assert id1 == id2
