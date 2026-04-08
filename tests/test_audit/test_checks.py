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


from llm_wiki.audit.checks import find_broken_wikilinks


def test_find_broken_wikilinks_detects_missing_target(sample_vault: Path):
    """no-structure.md links to [[some-other-page]] which does not exist."""
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)

    assert result.check == "broken-wikilinks"
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "some-other-page" in targets


def test_find_broken_wikilinks_does_not_flag_existing_targets(sample_vault: Path):
    """Wikilinks to pages that exist must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "srna-embeddings" not in targets
    assert "clustering-metrics" not in targets


def test_find_broken_wikilinks_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    result = find_broken_wikilinks(vault)
    assert result.issues == []


def test_find_broken_wikilinks_issue_shape(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    assert result.issues, "expected at least one broken-wikilink issue in fixture"
    issue = next(i for i in result.issues if i.metadata.get("target") == "some-other-page")
    assert issue.type == "broken-link"
    assert issue.status == "open"
    assert issue.page == "no-structure"
    assert issue.detected_by == "auditor"
    assert "some-other-page" in issue.body


from llm_wiki.audit.checks import find_missing_markers


def test_find_missing_markers_flags_pages_with_headings_no_markers(sample_vault: Path):
    """clustering-metrics uses ## headings but has no %% markers."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)

    assert result.check == "missing-markers"
    affected = {issue.page for issue in result.issues}
    assert "clustering-metrics" in affected


def test_find_missing_markers_does_not_flag_pages_with_markers(sample_vault: Path):
    """srna-embeddings has %% markers — must not be flagged."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    affected = {issue.page for issue in result.issues}
    assert "srna-embeddings" not in affected
    assert "inter-rep-variant-analysis" not in affected


def test_find_missing_markers_does_not_flag_pages_without_headings(sample_vault: Path):
    """no-structure.md has no headings at all — also not flagged."""
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    affected = {issue.page for issue in result.issues}
    assert "no-structure" not in affected


def test_find_missing_markers_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    result = find_missing_markers(vault)
    assert result.issues == []
