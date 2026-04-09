"""PageWriteService — the entity that actually performs supervised writes.

Both the daemon route handlers and the session-aware ingest agent use
this service. Background workers MUST NOT instantiate or call it — that
contract is enforced mechanically by tests/test_daemon/test_ast_hard_rule.py.

Each write:
  1. Validates inputs (citations required, author required, etc.)
  2. Acquires the per-page write lock
  3. Performs the file operation
  4. Computes the post-write content hash
  5. Builds a JournalEntry and appends it (synchronous, fsync'd)
  6. Returns a WriteResult that the route handler turns into a response dict
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.name_similarity import find_near_matches
from llm_wiki.daemon.sessions import (
    JournalEntry,
    Session,
    SessionRegistry,
    _now_iso,
    append_journal_entry,
)
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.vault import Vault, _state_dir_for

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    status: Literal["ok", "error"]
    page_path: str = ""
    journal_id: str = ""
    session_id: str = ""
    content_hash: str = ""
    warnings: list[dict] = field(default_factory=list)
    code: str | None = None
    details: dict = field(default_factory=dict)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug or "untitled"


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class PageWriteService:
    """Performs all supervised page writes. Used by route handlers and ingest."""

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        config: WikiConfig,
        write_coordinator: WriteCoordinator,
        registry: SessionRegistry,
        commit_service: CommitService,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._config = config
        self._coordinator = write_coordinator
        self._registry = registry
        self._commit_service = commit_service
        self._state_dir = _state_dir_for(vault_root)

    @property
    def _wiki_dir(self) -> Path:
        return self._vault_root / self._config.vault.wiki_dir.rstrip("/")

    def _maybe_warn_cap(self, session: Session, warnings: list[dict]) -> None:
        cap = self._config.sessions.write_count_cap
        ratio = self._config.sessions.cap_warn_ratio
        threshold = int(cap * ratio)
        if session.write_count >= threshold:
            warnings.append({
                "code": "session-cap-approaching",
                "writes_used": session.write_count,
                "writes_remaining": max(0, cap - session.write_count),
                "message": (
                    "Session is approaching the write count cap. Call "
                    "wiki_session_close at a clean breakpoint before the "
                    "daemon force-settles."
                ),
            })

    async def _maybe_force_settle(self, session: Session) -> None:
        cap = self._config.sessions.write_count_cap
        if session.write_count >= cap:
            from llm_wiki.daemon.sessions import load_journal
            entries = load_journal(session.journal_path)
            await self._commit_service.settle_with_fallback(session, entries)
            self._registry.close(session)

    async def create(
        self,
        *,
        title: str,
        body: str,
        citations: list[str],
        author: str,
        connection_id: str,
        tags: list[str] | None = None,
        intent: str | None = None,
        force: bool = False,
    ) -> WriteResult:
        """Create a new page with frontmatter, body, and citations."""
        if not author:
            return WriteResult(status="error", code="missing-author")
        if (
            self._config.write.require_citations_on_create
            and not citations
        ):
            return WriteResult(
                status="error",
                code="missing-citations",
                details={
                    "message": (
                        "wiki_create requires at least one citation. If you cannot "
                        "cite a source, post your idea to the talk page instead via "
                        "wiki_talk_post."
                    ),
                },
            )

        slug = _slugify(title)
        page_path = self._wiki_dir / f"{slug}.md"
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        # Hard collision check (case-insensitive exact match)
        existing_names = list(self._vault.manifest_entries().keys())
        existing_lower = {n.lower() for n in existing_names}
        if slug.lower() in existing_lower:
            return WriteResult(
                status="error",
                code="name-collision",
                details={"page_path": journal_path_rel},
            )

        # Soft near-match check (Jaccard + Levenshtein)
        if not force:
            near = find_near_matches(slug, existing_names, self._config.write)
            if near:
                return WriteResult(
                    status="error",
                    code="name-near-match",
                    details={
                        "similar_pages": near,
                        "force": (
                            "Pass force=true to wiki_create to override "
                            "this check."
                        ),
                    },
                )

        warnings: list[dict] = []
        async with self._coordinator.lock_for(slug):
            page_path.parent.mkdir(parents=True, exist_ok=True)
            content = self._build_page_content(title, body, citations, tags or [])
            page_path.write_text(content, encoding="utf-8")
            content_hash = _content_hash(content)

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_create",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=f"created {slug}",
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)
            self._maybe_warn_cap(session, warnings)

        await self._maybe_force_settle(session)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            warnings=warnings,
        )

    async def update(
        self,
        *,
        page: str,
        patch: str,
        author: str,
        connection_id: str,
        intent: str | None = None,
    ) -> WriteResult:
        """Apply a V4A patch to an existing page."""
        if not author:
            return WriteResult(status="error", code="missing-author")

        page_path = self._wiki_dir / f"{page}.md"
        if not page_path.exists():
            return WriteResult(
                status="error",
                code="page-not-found",
                details={"page": page},
            )
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        from llm_wiki.daemon.v4a_patch import (
            PatchConflict,
            PatchParseError,
            apply_patch,
            parse_patch,
        )

        try:
            parsed = parse_patch(patch)
        except PatchParseError as exc:
            return WriteResult(
                status="error",
                code="patch-parse-error",
                details={"message": str(exc)},
            )

        warnings: list[dict] = []
        async with self._coordinator.lock_for(page):
            current = page_path.read_text(encoding="utf-8")
            try:
                new_content, apply_result = apply_patch(
                    parsed,
                    current,
                    fuzzy_threshold=self._config.write.patch_fuzzy_match_threshold,
                )
            except PatchConflict as exc:
                return WriteResult(
                    status="error",
                    code="patch-conflict",
                    details={
                        "message": str(exc),
                        "current_excerpt": exc.current_excerpt,
                    },
                )

            page_path.write_text(new_content, encoding="utf-8")
            content_hash = _content_hash(new_content)
            diff_summary = f"+{apply_result.additions} -{apply_result.removals}"

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_update",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=diff_summary,
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)
            self._maybe_warn_cap(session, warnings)

        await self._maybe_force_settle(session)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            warnings=warnings,
            details={"diff_summary": diff_summary},
        )

    async def append(
        self,
        *,
        page: str,
        section_heading: str,
        body: str,
        citations: list[str],
        author: str,
        connection_id: str,
        after_heading: str | None = None,
        intent: str | None = None,
    ) -> WriteResult:
        """Append a new section to an existing page."""
        if not author:
            return WriteResult(status="error", code="missing-author")
        if (
            self._config.write.require_citations_on_append
            and not citations
        ):
            return WriteResult(
                status="error",
                code="missing-citations",
                details={
                    "message": (
                        "wiki_append requires at least one citation. Post to "
                        "the talk page instead if you cannot cite a source."
                    ),
                },
            )

        page_path = self._wiki_dir / f"{page}.md"
        if not page_path.exists():
            return WriteResult(
                status="error",
                code="page-not-found",
                details={"page": page},
            )
        journal_path_rel = str(page_path.relative_to(self._vault_root))

        async with self._coordinator.lock_for(page):
            current = page_path.read_text(encoding="utf-8")
            lines = current.splitlines(keepends=True)

            section_slug = _slugify(section_heading)
            new_block = (
                f"\n%% section: {section_slug} %%\n"
                f"## {section_heading}\n\n"
                f"{body.strip()}\n"
            )
            warnings: list[dict] = []

            if after_heading is None:
                # Append at end of file
                new_lines = lines + [new_block]
            else:
                # Find heading line(s) — exact match only
                heading_indices = self._find_heading_lines(lines, after_heading)
                if not heading_indices:
                    available = self._list_headings(lines)
                    return WriteResult(
                        status="error",
                        code="heading-not-found",
                        details={
                            "after_heading": after_heading,
                            "available_headings": available,
                        },
                    )
                if len(heading_indices) > 1:
                    warnings.append({
                        "code": "heading-multiple-matches",
                        "count": len(heading_indices),
                        "used_line": heading_indices[0] + 1,
                        "message": (
                            f"after_heading={after_heading!r} matched "
                            f"{len(heading_indices)} headings; using the first."
                        ),
                    })
                # Insert immediately after the matched section closes
                insert_at = self._end_of_section(lines, heading_indices[0])
                new_lines = lines[:insert_at] + [new_block] + lines[insert_at:]

            new_content = "".join(new_lines)
            page_path.write_text(new_content, encoding="utf-8")
            content_hash = _content_hash(new_content)

            session = self._registry.get_or_open(
                author, connection_id, state_dir=self._state_dir,
            )
            entry = JournalEntry(
                ts=_now_iso(),
                tool="wiki_append",
                path=journal_path_rel,
                author=author,
                intent=intent,
                summary=f"+section {section_slug}",
                content_hash_after=content_hash,
            )
            append_journal_entry(session, entry)
            self._maybe_warn_cap(session, warnings)

        await self._maybe_force_settle(session)

        return WriteResult(
            status="ok",
            page_path=journal_path_rel,
            journal_id=str(session.write_count),
            session_id=session.id,
            content_hash=content_hash,
            warnings=warnings,
        )

    @staticmethod
    def _find_heading_lines(lines: list[str], heading_text: str) -> list[int]:
        """Return line indices where `## <heading_text>` appears (exact match)."""
        target = f"## {heading_text}"
        return [
            i for i, line in enumerate(lines)
            if line.rstrip("\n").rstrip("\r").strip() == target
        ]

    @staticmethod
    def _list_headings(lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            stripped = line.rstrip("\n").rstrip("\r").strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                out.append(stripped[3:])
        return out

    @staticmethod
    def _end_of_section(lines: list[str], heading_idx: int) -> int:
        """Return the line index where the section starting at `heading_idx` ends.

        The section ends at the next `##` or `#` heading at the same or shallower
        level, or at end of file.
        """
        for i in range(heading_idx + 1, len(lines)):
            stripped = lines[i].lstrip()
            if stripped.startswith("## ") or stripped.startswith("# "):
                return i
        return len(lines)

    def _build_page_content(
        self,
        title: str,
        body: str,
        citations: list[str],
        tags: list[str],
    ) -> str:
        fm = {"title": title}
        if len(citations) == 1:
            fm["source"] = f"[[{citations[0]}]]"
        else:
            fm["sources"] = [f"[[{c}]]" for c in citations]
        if tags:
            fm["tags"] = tags
        frontmatter = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"
