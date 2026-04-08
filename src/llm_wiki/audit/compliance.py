from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue

# Threshold for the minor-edit shortcut. Spec section 5 Compliance Review uses 50 chars.
_MINOR_EDIT_CHARS = 50

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(?:##|###)\s+(\S.*)$", re.MULTILINE)


@dataclass
class ComplianceResult:
    """Outcome of one compliance review pass over a single page edit."""
    page: str
    auto_approved: bool = False
    auto_fixed: list[str] = field(default_factory=list)
    issues_filed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class ComplianceReviewer:
    """Heuristic compliance checks for human edits to wiki pages.

    No LLM calls. Each check is independent and may fire multiple reasons
    on the same edit. The reviewer may modify the file on disk to insert
    missing %% section: ... %% markers (markers are invisible in Obsidian's
    preview, so this respects "human prose is sacred").

    Construction:
        ComplianceReviewer(vault_root, queue, config)
    """

    def __init__(
        self,
        vault_root: Path,
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None:
        self._vault_root = vault_root
        self._queue = queue
        self._config = config

    def review_change(
        self,
        page_path: Path,
        old_content: str | None,
        new_content: str,
    ) -> ComplianceResult:
        result = ComplianceResult(page=page_path.stem)

        # Minor-edit shortcut only applies when we have a prior snapshot.
        if old_content is not None and self._is_minor_edit(old_content, new_content):
            result.auto_approved = True
            result.reasons.append("minor-edit")
            return result

        # Other heuristics fire here in subsequent tasks (Task 6, 7, 8).
        return result

    @staticmethod
    def _is_minor_edit(old: str, new: str) -> bool:
        """True iff diff size < threshold AND no new wikilinks AND no new headings."""
        if abs(len(new) - len(old)) >= _MINOR_EDIT_CHARS:
            return False

        old_links = set(_WIKILINK_RE.findall(old))
        new_links = set(_WIKILINK_RE.findall(new))
        if new_links - old_links:
            return False

        old_headings = set(_HEADING_RE.findall(old))
        new_headings = set(_HEADING_RE.findall(new))
        if new_headings - old_headings:
            return False

        return True
