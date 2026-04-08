from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import Issue, IssueQueue

# Threshold for the minor-edit shortcut. Spec section 5 Compliance Review uses 50 chars.
_MINOR_EDIT_CHARS = 50

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(?:##|###)\s+(\S.*)$", re.MULTILINE)

# Sentence splitter — naive but adequate for v1. Splits on sentence-final
# punctuation followed by whitespace or end-of-string, or on blank lines.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# A line that opens or closes a fenced code block.
_CODE_FENCE_RE = re.compile(r"^```")
# A line that is a %% marker (not body content).
_MARKER_LINE_RE = re.compile(r"^%%\s*section:")


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

        self._check_missing_citation(result, old_content, new_content)
        # Tasks 7 + 8 add structural-drift auto-fix and new-idea detection here.

        return result

    def _check_missing_citation(
        self,
        result: ComplianceResult,
        old_content: str | None,
        new_content: str,
    ) -> None:
        """File a compliance issue for any uncited sentence introduced by the edit.

        For first-time-seen pages (old_content is None), every body sentence is
        considered "new" and checked. For edits, only sentences present in the
        new body but not in the old body are checked.
        """
        new_body = self._strip_frontmatter(new_content)
        new_sentences = set(self._extract_body_sentences(new_body))
        if old_content is None:
            uncited_new = [s for s in new_sentences if not self._has_citation(s)]
        else:
            old_body = self._strip_frontmatter(old_content)
            old_sentences = set(self._extract_body_sentences(old_body))
            added = new_sentences - old_sentences
            uncited_new = [s for s in added if not self._has_citation(s)]

        if not uncited_new:
            return

        result.reasons.append("missing-citation")
        for sentence in uncited_new:
            preview = sentence.strip()[:80]
            issue = Issue(
                id=Issue.make_id("compliance", result.page, f"missing-citation:{preview}"),
                type="compliance",
                status="open",
                title=f"Uncited sentence on '{result.page}'",
                page=result.page,
                body=(
                    f"The page [[{result.page}]] received a new sentence without a "
                    f"`[[...]]` citation:\n\n> {preview}\n\n"
                    f"Either add a citation or revise the sentence."
                ),
                created=Issue.now_iso(),
                detected_by="compliance",
                metadata={"sentence_preview": preview, "subtype": "missing-citation"},
            )
            _, was_new = self._queue.add(issue)
            if was_new:
                result.issues_filed.append(issue.id)

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return text
        return text[end + 4:].lstrip()

    @staticmethod
    def _extract_body_sentences(body: str) -> list[str]:
        """Sentences from non-code, non-marker, non-heading lines."""
        keep_lines: list[str] = []
        in_code = False
        for line in body.splitlines():
            stripped = line.strip()
            if _CODE_FENCE_RE.match(stripped):
                in_code = not in_code
                continue
            if in_code:
                continue
            if _MARKER_LINE_RE.match(stripped):
                continue
            if stripped.startswith("#"):
                continue
            keep_lines.append(line)
        joined = "\n".join(keep_lines)
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(joined) if s.strip()]
        return sentences

    @staticmethod
    def _has_citation(sentence: str) -> bool:
        return bool(_WIKILINK_RE.search(sentence))

    @classmethod
    def _is_minor_edit(cls, old: str, new: str) -> bool:
        """True iff diff size < threshold AND no new wikilinks/headings/sentences.

        "Minor" means a typo fix or small text tweak that does not introduce
        new claims. If the edit adds a brand-new sentence to the body, it is
        NOT minor — even if the character delta is below the threshold — so
        downstream checks like missing-citation still run.
        """
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

        # Count sentences instead of set-diffing so within-sentence typo fixes
        # (which change the identity of a sentence but not the count) still
        # qualify as minor. A new appended sentence increases the count.
        old_sentences = cls._extract_body_sentences(cls._strip_frontmatter(old))
        new_sentences = cls._extract_body_sentences(cls._strip_frontmatter(new))
        if len(new_sentences) > len(old_sentences):
            return False

        return True
