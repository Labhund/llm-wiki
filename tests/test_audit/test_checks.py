from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.checks import CheckResult, find_orphans
from llm_wiki.vault import Vault


def test_find_orphans_finds_unreferenced_top_level_page(sample_vault: Path):
    """no-structure.md sits at vault root, has zero inlinks → orphan."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)

    assert isinstance(result, CheckResult)
    assert result.check == "orphans"
    orphan_pages = {issue.page for issue in result.issues}
    assert "no-structure" in orphan_pages


def test_find_orphans_does_not_flag_referenced_pages(sample_vault: Path):
    """srna-embeddings has multiple inlinks — must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "srna-embeddings" not in orphan_pages
    assert "clustering-metrics" not in orphan_pages
    assert "inter-rep-variant-analysis" not in orphan_pages


def test_find_orphans_skips_index_readme_home(tmp_path: Path):
    """Pages named index/readme/home are entry points, not orphans."""
    (tmp_path / "index.md").write_text("# Index\n\nEntry point.\n")
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / "home.md").write_text("# Home\n")

    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "index" not in orphan_pages
    assert "readme" not in orphan_pages
    assert "home" not in orphan_pages


def test_find_orphans_empty_vault(tmp_path: Path):
    """Empty vault produces no orphans without raising."""
    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    assert result.issues == []


def test_find_orphans_issue_metadata(sample_vault: Path):
    """Each orphan issue has type=orphan, status=open, detected_by=auditor."""
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    for issue in result.issues:
        assert issue.type == "orphan"
        assert issue.status == "open"
        assert issue.detected_by == "auditor"
        assert issue.id.startswith("orphan-")
