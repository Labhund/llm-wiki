from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import Issue, IssueQueue

# Threshold for the minor-edit shortcut. Spec section 5 Compliance Review uses 50 chars.
_MINOR_EDIT_CHARS = 50
_NEW_IDEA_PARAGRAPH_CHARS = 200

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(?:##|###)\s+(\S.*)$", re.MULTILINE)

# Sentence splitter — naive but adequate for v1. Splits on sentence-final
# punctuation followed by whitespace or end-of-string, or on blank lines.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# A line that opens or closes a fenced code block (``` or ~~~).
_CODE_FENCE_RE = re.compile(r"^(?:```|~~~)")
# A line that is a %% marker (not body content).
_MARKER_LINE_RE = re.compile(r"^%%\s*section:")

_HEADING_LINE_RE = re.compile(r"^(?P<level>##|###)\s+(?P<text>.+?)\s*$")
_MARKER_LINE_WITH_CAPTURE_RE = re.compile(r"^%%\s*section:\s*[^%]*?%%\s*$")


def _slugify(text: str) -> str:
    """Heading text -> slug. 'Sub Heading' -> 'sub-heading'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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

        # Auto-fix structural drift first; downstream checks see the fixed content.
        new_content = self._check_structural_drift(result, page_path, new_content)
        self._check_missing_citation(result, old_content, new_content)
        self._check_new_idea(result, old_content, new_content)

        # Patch token counts into section markers as a bookkeeping step.
        # No-op on pages without %% markers; safe to call unconditionally.
        from llm_wiki.ingest.page_writer import patch_token_estimates
        patch_token_estimates(page_path)

        return result

    def _check_new_idea(
        self,
        result: ComplianceResult,
        old_content: str | None,
        new_content: str,
    ) -> None:
        """A paragraph >= 200 chars added by the edit is flagged as new-idea.

        Skipped for first-time-seen pages (old_content is None) — those are
        creations, not edits, and the entire file is "new" trivially.
        """
        if old_content is None:
            return

        old_paragraphs = self._extract_paragraphs(self._strip_frontmatter(old_content))
        new_paragraphs = self._extract_paragraphs(self._strip_frontmatter(new_content))
        added = [p for p in new_paragraphs if p not in old_paragraphs]
        large_new = [p for p in added if len(p) >= _NEW_IDEA_PARAGRAPH_CHARS]
        if not large_new:
            return

        result.reasons.append("new-idea")
        for paragraph in large_new:
            preview = paragraph.strip()[:80]
            issue = Issue(
                id=Issue.make_id("new-idea", result.page, preview),
                type="new-idea",
                status="open",
                severity="moderate",
                title=f"New paragraph added to '{result.page}'",
                page=result.page,
                body=(
                    f"A substantive new paragraph was added to [[{result.page}]]:\n\n"
                    f"> {preview}{'...' if len(paragraph) > 80 else ''}\n\n"
                    f"Librarian: review whether this should be integrated, sourced, "
                    f"or moved to the talk page."
                ),
                created=Issue.now_iso(),
                detected_by="compliance",
                metadata={"preview": preview, "length": len(paragraph)},
            )
            _, was_new = self._queue.add(issue)
            if was_new:
                result.issues_filed.append(issue.id)

    @staticmethod
    def _extract_paragraphs(body: str) -> list[str]:
        """Split body into paragraphs (separated by blank lines).

        Skips lines that are headings, %% markers, or fenced code blocks.
        """
        paragraphs: list[str] = []
        current: list[str] = []
        in_code = False
        for line in body.splitlines():
            if _CODE_FENCE_RE.match(line.strip()):
                in_code = not in_code
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            if in_code:
                continue
            stripped = line.strip()
            if not stripped:
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            if stripped.startswith("#") or _MARKER_LINE_RE.match(stripped):
                if current:
                    paragraphs.append(" ".join(current).strip())
                    current = []
                continue
            current.append(stripped)
        if current:
            paragraphs.append(" ".join(current).strip())
        return [p for p in paragraphs if p]

    def _check_structural_drift(
        self,
        result: ComplianceResult,
        page_path: Path,
        new_content: str,
    ) -> str:
        """Insert %% section markers above any heading that lacks one.

        Returns the (possibly mutated) page content. Updates result.reasons
        and result.auto_fixed in place. Writes the updated content back to
        the file if any markers were inserted.

        Headings inside fenced code blocks (``` or ~~~) are skipped entirely:
        such lines are literal code content, not document structure, and
        mutating them would violate "human prose is sacred."
        """
        # Walk line-by-line tracking fence state so code-block contents are
        # never mistaken for real headings. Record (line_start_offset, heading_text)
        # pairs for headings outside fences, and also record which headings are
        # already preceded (on the immediately prior non-blank line) by a marker.
        orphan_headings: list[tuple[int, str]] = []  # (line_start, heading_text)
        offset = 0
        in_code = False
        prev_nonblank_was_marker = False
        prev_nonblank_marker_slug: str | None = None
        headings_with_markers: set[str] = set()

        for line in new_content.splitlines(keepends=True):
            stripped = line.strip()
            line_start = offset
            offset += len(line)

            if _CODE_FENCE_RE.match(stripped):
                in_code = not in_code
                prev_nonblank_was_marker = False
                prev_nonblank_marker_slug = None
                continue

            if in_code:
                # Inside a fenced code block: never treat as heading or marker.
                # Blank lines inside a fence don't reset marker state because
                # we're not tracking structure here anyway.
                continue

            if not stripped:
                # Blank lines do NOT reset marker adjacency: a marker followed
                # by a blank line followed by a heading still counts.
                continue

            if _MARKER_LINE_WITH_CAPTURE_RE.match(stripped):
                prev_nonblank_was_marker = True
                # Extract the slug text between "section:" and "%%"
                inner = stripped[len("%%"):].rstrip("%").strip()
                if inner.startswith("section:"):
                    prev_nonblank_marker_slug = inner[len("section:"):].strip().lower()
                else:
                    prev_nonblank_marker_slug = None
                continue

            heading_match = _HEADING_LINE_RE.match(stripped)
            if heading_match is not None:
                heading_text = heading_match.group("text").strip()
                slug = _slugify(heading_text)
                if prev_nonblank_was_marker:
                    # This heading already has a preceding marker.
                    headings_with_markers.add(heading_text.lower())
                elif slug:
                    orphan_headings.append((line_start, heading_text))
                prev_nonblank_was_marker = False
                prev_nonblank_marker_slug = None
                continue

            # Any other content line breaks marker adjacency.
            prev_nonblank_was_marker = False
            prev_nonblank_marker_slug = None

        # Filter orphans whose text already has a marker elsewhere (rare but
        # matches the old lower-cased set semantics) and compute slugs.
        orphans: list[tuple[int, str]] = []  # (line_start, slug)
        for line_start, heading_text in orphan_headings:
            if heading_text.lower() in headings_with_markers:
                continue
            slug = _slugify(heading_text)
            if not slug:
                continue
            orphans.append((line_start, slug))

        if not orphans:
            return new_content

        # Insert markers in reverse order so earlier offsets remain valid.
        updated = new_content
        inserted_slugs: list[str] = []
        for line_start, slug in reversed(orphans):
            marker_line = f"%% section: {slug} %%\n"
            updated = updated[:line_start] + marker_line + updated[line_start:]
            inserted_slugs.append(slug)

        # Write back to disk
        page_path.write_text(updated, encoding="utf-8")

        result.reasons.append("structural-drift")
        for slug in reversed(inserted_slugs):  # restore original order for stability
            result.auto_fixed.append(f"inserted-marker:{slug}")

        return updated

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
        # Synthesis pages have no external citation requirement — the analysis
        # session itself is the source. Skip the citation check entirely.
        if self._is_synthesis_page(new_content):
            return
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
                severity="moderate",
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
        # Edge case: a paragraph reformat that splits one sentence into two via \n\n
        # will fail this check and exit the minor-edit shortcut, which can produce a
        # false-positive missing-citation issue. The user can resolve such issues
        # manually. We accept this trade-off to ensure that genuinely new sentences
        # — which need citations — always get checked.
        if len(new_sentences) > len(old_sentences):
            return False

        return True

    @staticmethod
    def _is_synthesis_page(content: str) -> bool:
        """True iff the page frontmatter contains `type: synthesis`."""
        if not content.startswith("---\n"):
            return False
        try:
            end = content.index("\n---", 4)
        except ValueError:
            return False
        fm_text = content[3:end].strip()
        import yaml
        try:
            fm = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            return False
        return fm.get("type") == "synthesis"
