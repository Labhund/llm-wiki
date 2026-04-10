from pathlib import Path

import pytest

from llm_wiki.audit.compliance import ComplianceReviewer
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue


def _make_reviewer(tmp_path: Path) -> ComplianceReviewer:
    (tmp_path / "wiki").mkdir(exist_ok=True)
    queue = IssueQueue(tmp_path / "wiki" / ".issues")
    return ComplianceReviewer(tmp_path, queue, WikiConfig())


def test_synthesis_page_skips_citation_check(tmp_path: Path):
    """A page with type: synthesis must not get a missing-citation issue."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\ntype: synthesis\n---\nA claim with absolutely no citation here.\n"
    page = tmp_path / "wiki" / "syn-page.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert result.issues_filed == []


def test_synthesis_page_still_gets_structural_drift_check(tmp_path: Path):
    """Synthesis pages still get %% markers auto-inserted on structural drift."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\ntype: synthesis\n---\n## My Heading\n\nSome uncited content.\n"
    page = tmp_path / "wiki" / "syn-drift.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert any("inserted-marker" in fix for fix in result.auto_fixed)


def test_non_synthesis_page_still_gets_citation_check(tmp_path: Path):
    """Normal pages (no status field) still get the citation compliance check."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\ntitle: Normal\n---\nA claim with no citation at all.\n"
    page = tmp_path / "wiki" / "normal-page.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert len(result.issues_filed) > 0
