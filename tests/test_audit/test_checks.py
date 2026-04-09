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
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "index.md").write_text("# Index\n\nEntry point.\n")
    (wiki_dir / "README.md").write_text("# Readme\n")
    (wiki_dir / "home.md").write_text("# Home\n")

    vault = Vault.scan(tmp_path)
    result = find_orphans(vault)
    orphan_pages = {issue.page for issue in result.issues}
    assert "index" not in orphan_pages
    assert "readme" not in orphan_pages
    assert "home" not in orphan_pages


def test_find_orphans_empty_vault(tmp_path: Path):
    """Empty vault produces no orphans without raising."""
    (tmp_path / "wiki").mkdir()
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
    (tmp_path / "wiki").mkdir()
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
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    result = find_missing_markers(vault)
    assert result.issues == []


from llm_wiki.audit.checks import find_broken_citations


def test_find_broken_citations_detects_missing_source(sample_vault: Path):
    """srna-embeddings has frontmatter source [[raw/smith-2026-srna.pdf]] which doesn't exist."""
    vault = Vault.scan(sample_vault)
    result = find_broken_citations(vault, sample_vault)

    assert result.check == "broken-citations"
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/smith-2026-srna.pdf" in targets


def test_find_broken_citations_passes_when_source_exists(sample_vault: Path):
    """Create the missing raw file → re-running the check finds no issue for it."""
    raw_dir = sample_vault / "raw"
    raw_dir.mkdir()
    (raw_dir / "smith-2026-srna.pdf").write_bytes(b"%PDF-1.4 fake")

    vault = Vault.scan(sample_vault)
    result = find_broken_citations(vault, sample_vault)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/smith-2026-srna.pdf" not in targets


def test_find_broken_citations_detects_inline_raw_reference(tmp_path: Path):
    """A [[raw/missing.pdf]] reference in page body is also flagged."""
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "doc.md"
    page.write_text(
        "---\ntitle: Doc\n---\n\nSee [[raw/missing.pdf]] for details.\n"
    )

    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    targets = {issue.metadata.get("target") for issue in result.issues}
    assert "raw/missing.pdf" in targets


def test_find_broken_citations_empty_vault(tmp_path: Path):
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    assert result.issues == []


def test_find_orphans_severity_is_minor(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    result = find_orphans(vault)
    assert result.issues, "expected at least one orphan in fixture (no-structure)"
    for issue in result.issues:
        assert issue.severity == "minor"


def test_find_broken_wikilinks_severity_is_moderate(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    result = find_broken_wikilinks(vault)
    assert result.issues, "expected at least one broken-wikilink in fixture"
    for issue in result.issues:
        assert issue.severity == "moderate"


def test_find_missing_markers_severity_is_minor(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    result = find_missing_markers(vault)
    assert result.issues, "expected at least one missing-markers issue in fixture (clustering-metrics)"
    for issue in result.issues:
        assert issue.severity == "minor"


def test_find_broken_citations_severity_is_critical(tmp_path: Path):
    """Construct a vault with a broken raw citation; severity should be critical."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "p.md").write_text(
        "---\ntitle: P\nsource: \"[[raw/missing.pdf]]\"\n---\n\n"
        "## Body\n\nHas a citation [[raw/missing.pdf]].\n"
    )
    vault = Vault.scan(tmp_path)
    result = find_broken_citations(vault, tmp_path)
    assert result.issues, "expected a broken-citation issue"
    for issue in result.issues:
        assert issue.severity == "critical"


from llm_wiki.audit.checks import find_source_gaps
from llm_wiki.config import WikiConfig
import datetime


def _write_companion(path: Path, reading_status: str, ingested: str, source_type: str = "paper") -> None:
    path.write_text(
        f"---\nreading_status: {reading_status}\ningested: {ingested}\nsource_type: {source_type}\n---\n"
    )


def test_find_source_gaps_bare_source(tmp_path: Path):
    """A PDF in raw/ with no companion .md raises a bare-source issue."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.check == "source-gaps"
    types = {i.type for i in result.issues}
    assert "bare-source" in types


def test_find_source_gaps_no_issue_when_companion_exists(tmp_path: Path):
    """A PDF with a companion .md does not trigger bare-source."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    _write_companion(raw_dir / "paper.md", "unread", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "bare-source" not in types


def test_find_source_gaps_missing_reading_status(tmp_path: Path):
    """A .md in raw/ without reading_status raises missing-reading-status."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "article.md").write_text("---\ntitle: Article\n---\nContent.\n")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "missing-reading-status" in types


def test_find_source_gaps_unread_source_over_threshold(tmp_path: Path):
    """reading_status: unread older than threshold raises unread-source."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    old_date = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    _write_companion(raw_dir / "old-paper.md", "unread", old_date)
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "unread-source" in types


def test_find_source_gaps_unread_source_within_threshold(tmp_path: Path):
    """reading_status: unread within threshold is NOT flagged."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    recent = datetime.date.today().isoformat()
    _write_companion(raw_dir / "recent-paper.md", "unread", recent)
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "unread-source" not in types


def test_find_source_gaps_in_progress_no_plan_with_inbox(tmp_path: Path):
    """in_progress source with no matching plan file raises in-progress-no-plan."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" in types


def test_find_source_gaps_in_progress_with_matching_plan(tmp_path: Path):
    """in_progress source WITH a matching plan is not flagged."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    (inbox_dir / "2026-04-10-paper-plan.md").write_text(
        "---\nsource: raw/paper.md\nstatus: in-progress\n---\n"
    )
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" not in types


def test_find_source_gaps_in_progress_skips_if_no_inbox(tmp_path: Path):
    """in-progress-no-plan check is silently skipped if inbox/ doesn't exist."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _write_companion(raw_dir / "paper.md", "in_progress", "2026-04-10")
    # No inbox/ directory
    result = find_source_gaps(tmp_path, WikiConfig())
    types = {i.type for i in result.issues}
    assert "in-progress-no-plan" not in types


def test_find_source_gaps_empty_raw_dir(tmp_path: Path):
    """Empty raw/ produces no issues."""
    (tmp_path / "raw").mkdir()
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.issues == []


def test_find_source_gaps_no_raw_dir(tmp_path: Path):
    """Missing raw/ produces no issues (vault not yet initialized)."""
    result = find_source_gaps(tmp_path, WikiConfig())
    assert result.issues == []


def test_find_source_gaps_severity(tmp_path: Path):
    """bare-source, missing-reading-status, unread-source are minor; in-progress-no-plan is moderate."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (raw_dir / "bare.pdf").write_bytes(b"%PDF-1.4")
    old_date = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    _write_companion(raw_dir / "unread.md", "unread", old_date)
    _write_companion(raw_dir / "inprog.md", "in_progress", "2026-04-10")
    result = find_source_gaps(tmp_path, WikiConfig())
    by_type = {i.type: i.severity for i in result.issues}
    assert by_type["bare-source"] == "minor"
    assert by_type["unread-source"] == "minor"
    assert by_type["in-progress-no-plan"] == "moderate"
