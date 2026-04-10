from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.audit.checks import (
    execute_proposal_merges,
    find_broken_citations,
    find_broken_wikilinks,
    find_inbox_staleness,
    find_missing_frontmatter,
    find_missing_markers,
    find_orphans,
    find_pending_proposals,
    find_source_gaps,
    find_stale_resonance,
    find_synthesis_without_resonance,
    find_uncited_sourced_pages,
)
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.vault import Vault


@dataclass
class AuditReport:
    """Aggregate result of one audit run."""
    total_checks_run: int
    by_check: dict[str, int] = field(default_factory=dict)
    new_issue_ids: list[str] = field(default_factory=list)
    existing_issue_ids: list[str] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(self.by_check.values())

    def to_dict(self) -> dict:
        return {
            "total_checks_run": self.total_checks_run,
            "total_issues": self.total_issues,
            "by_check": self.by_check,
            "new_issue_ids": self.new_issue_ids,
            "existing_issue_ids": self.existing_issue_ids,
        }


class Auditor:
    """Runs all structural checks and routes results through the issue queue."""

    def __init__(
        self,
        vault: Vault,
        queue: IssueQueue,
        vault_root: Path,
        config: WikiConfig | None = None,
    ) -> None:
        self._vault = vault
        self._queue = queue
        self._vault_root = vault_root
        self._config = config or WikiConfig()

    def audit(self) -> AuditReport:
        """Run every check and file each issue idempotently."""
        results = [
            find_orphans(self._vault),
            find_broken_wikilinks(self._vault),
            find_missing_markers(self._vault),
            find_missing_frontmatter(self._vault),
            find_uncited_sourced_pages(self._vault),
            find_broken_citations(self._vault, self._vault_root),
            find_source_gaps(self._vault_root, self._config),
            find_stale_resonance(self._vault_root, self._config),
            find_synthesis_without_resonance(self._vault_root, self._config),
            find_inbox_staleness(self._vault_root),
            find_pending_proposals(
                self._vault_root,
                auto_merge_threshold=self._config.ingest.grounding_auto_merge,
                flag_threshold=self._config.ingest.grounding_flag,
            ),
        ]

        by_check: dict[str, int] = {}
        new_ids: list[str] = []
        existing_ids: list[str] = []

        for result in results:
            by_check[result.check] = len(result.issues)
            for issue in result.issues:
                _, was_new = self._queue.add(issue)
                if was_new:
                    new_ids.append(issue.id)
                else:
                    existing_ids.append(issue.id)

        return AuditReport(
            total_checks_run=len(results),
            by_check=by_check,
            new_issue_ids=new_ids,
            existing_issue_ids=existing_ids,
        )
