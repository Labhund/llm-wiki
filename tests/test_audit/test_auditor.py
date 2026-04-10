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
    assert report.total_checks_run == 9
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
    (tmp_path / "wiki").mkdir()
    queue = IssueQueue(tmp_path)
    auditor = Auditor(Vault.scan(tmp_path), queue, tmp_path)
    report = auditor.audit()
    assert report.total_issues == 0
    assert report.total_checks_run == 9


def test_audit_preserves_wontfix_status(sample_vault: Path):
    """Re-auditing must NOT re-open an issue the user marked wontfix.

    The contract: once a human decides an auditor finding is wontfix
    (e.g. an intentional orphan, a known-missing citation), subsequent
    audit() runs see the same problem, re-derive the same deterministic
    id, and must leave the on-disk file (including its status) alone.
    """
    queue = IssueQueue(sample_vault)
    auditor = Auditor(Vault.scan(sample_vault), queue, sample_vault)

    first = auditor.audit()
    assert first.new_issue_ids, "sample_vault should yield at least one issue"

    # Pick any filed issue and mark it wontfix.
    target_id = first.new_issue_ids[0]
    filed = queue.get(target_id)
    assert filed is not None
    assert filed.status == "open"
    assert queue.update_status(target_id, "wontfix") is True

    # Re-run audit — the same problem should be detected again, but the
    # existing file must be preserved unchanged.
    second = auditor.audit()

    assert target_id not in second.new_issue_ids, (
        f"wontfix issue {target_id} was re-filed as new"
    )
    assert target_id in second.existing_issue_ids, (
        f"wontfix issue {target_id} was not recognized as existing"
    )

    reloaded = queue.get(target_id)
    assert reloaded is not None
    assert reloaded.status == "wontfix", (
        f"expected status wontfix after re-audit, got {reloaded.status}"
    )
