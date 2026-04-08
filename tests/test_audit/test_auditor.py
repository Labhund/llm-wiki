from __future__ import annotations

from pathlib import Path

from llm_wiki.audit.auditor import Auditor, AuditReport
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault


def test_audit_runs_all_checks_on_sample_vault(sample_vault: Path):
    """A first audit run finds the four expected issues from the fixture."""
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)

    report = auditor.audit()

    assert isinstance(report, AuditReport)
    # The fixture should produce: at least 1 orphan (no-structure),
    # 1 broken-wikilink (some-other-page), 1 missing-markers (clustering-metrics),
    # 1 broken-citation (raw/smith-2026-srna.pdf).
    assert report.by_check["orphans"] >= 1
    assert report.by_check["broken-wikilinks"] >= 1
    assert report.by_check["missing-markers"] >= 1
    assert report.by_check["broken-citations"] >= 1
    assert report.total_checks_run == 4
    assert len(report.new_issue_ids) == report.total_issues
    assert report.existing_issue_ids == []


def test_audit_is_idempotent(sample_vault: Path):
    """Re-running audit() produces zero new issues — all are existing."""
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)

    first = auditor.audit()
    second = auditor.audit()

    assert second.total_issues == first.total_issues
    assert second.new_issue_ids == []
    assert sorted(second.existing_issue_ids) == sorted(first.new_issue_ids)


def test_audit_writes_files_to_issues_dir(sample_vault: Path):
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)
    auditor.audit()

    files = list(queue.issues_dir.glob("*.md"))
    assert len(files) >= 4


def test_audit_empty_vault(tmp_path: Path):
    """An empty vault produces an empty report without raising."""
    queue = IssueQueue(tmp_path)
    auditor = Auditor(Vault.scan(tmp_path), queue, tmp_path)
    report = auditor.audit()
    assert report.total_issues == 0
    assert report.total_checks_run == 4
