"""Commit pipeline: serial lock, summarizer, git stage/commit/archive.

The CommitService holds a single asyncio.Lock that serializes every git
operation across all sessions. It is the only entity that calls git.
The summarizer call goes through the LLMClient at priority='maintenance'
and falls back to a deterministic message if the model is unreachable.

The commit ALWAYS happens — the worst case is a less narrative subject.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from llm_wiki.daemon.sessions import JournalEntry, Session

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class SettleResult:
    """Outcome of one session settle."""
    commit_sha: str | None
    paths_committed: list[str] = field(default_factory=list)
    summary_used: Literal["llm", "fallback", "none"] = "none"


class CommitService:
    """Serializes git operations across sessions and produces commit messages.

    Construction: CommitService(vault_root, llm, lock).
    `lock` is the shared asyncio.Lock — typically owned by the DaemonServer
    so all CommitService instances (if any future code creates more than one)
    share it.
    """

    def __init__(
        self,
        vault_root: Path,
        llm: "LLMClient | None",
        lock: asyncio.Lock,
    ) -> None:
        self._vault_root = vault_root
        self._llm = llm
        self._lock = lock

    async def settle_with_fallback(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> SettleResult:
        """Try LLM-summarized settle; on failure use deterministic fallback.

        The settle is wrapped in the serial commit lock so two concurrent
        settles never race on git.
        """
        async with self._lock:
            return await self._settle_locked(session, entries)

    async def _settle_locked(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> SettleResult:
        if not entries:
            return SettleResult(commit_sha=None, summary_used="none")

        # 1. Try the LLM summarizer
        summary_subject = ""
        summary_bullets: list[str] = []
        summary_used: Literal["llm", "fallback", "none"] = "none"
        if self._llm is not None:
            try:
                from llm_wiki.librarian.prompts import (
                    compose_commit_summary_messages,
                    parse_commit_summary,
                )
                messages = compose_commit_summary_messages(session.author, entries)
                response = await self._llm.complete(
                    messages, temperature=0.0, priority="maintenance",
                )
                subject, bullets = parse_commit_summary(response.content)
                if subject:
                    summary_subject = subject
                    summary_bullets = bullets
                    summary_used = "llm"
            except Exception:
                logger.warning(
                    "Commit summarizer failed for session %s; using fallback",
                    session.id, exc_info=True,
                )

        if summary_used != "llm":
            summary_subject, summary_bullets = self._fallback_summary(session, entries)
            summary_used = "fallback"

        # 2. Stage exactly the paths from the journal
        paths = sorted({e.path for e in entries})
        for path in paths:
            self._git("add", path)

        # 3. Check if there is anything to commit
        status = self._git("status", "--porcelain", capture=True)
        staged = [
            line[3:] for line in status.splitlines()
            if line[:2] in ("A ", "M ", "D ", "AM", "MM")
        ]
        if not staged:
            logger.info(
                "Session %s: nothing to commit (paths already in tree)", session.id,
            )
            self._archive_journal(session)
            return SettleResult(commit_sha=None, summary_used="none")

        # 4. Build the message
        message = self._build_commit_message(
            session, entries, summary_subject, summary_bullets,
        )

        # 5. Commit
        self._git("commit", "-q", "-m", message)
        sha = self._git("rev-parse", "HEAD", capture=True).strip()

        # 6. Archive the journal
        self._archive_journal(session)

        return SettleResult(
            commit_sha=sha,
            paths_committed=paths,
            summary_used=summary_used,
        )

    def _git(self, *args: str, capture: bool = False) -> str:
        cmd = ["git", "-C", str(self._vault_root), *args]
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout
        subprocess.run(cmd, check=True)
        return ""

    def _build_commit_message(
        self,
        session: Session,
        entries: list[JournalEntry],
        subject: str,
        bullets: list[str],
    ) -> str:
        if not subject:
            subject = (
                f"wiki: {len(entries)} writes from {session.author} "
                f"[session {session.id[:4]}]"
            )
        else:
            subject = f"wiki: {subject}"
        if len(subject) > 72:
            subject = subject[:69] + "..."

        body_lines = [subject, ""]
        for bullet in bullets:
            body_lines.append(f"- {bullet}")
        if not bullets:
            for e in entries[:5]:
                line = f"- {e.tool} {e.path} — {e.summary}"
                if e.intent:
                    line += f" ({e.intent})"
                body_lines.append(line)
            if len(entries) > 5:
                body_lines.append(f"- ... and {len(entries) - 5} more")
        body_lines.append("")
        body_lines.append(f"Session: {session.id}")
        body_lines.append(f"Agent: {session.author}")
        body_lines.append(f"Writes: {session.write_count}")
        return "\n".join(body_lines)

    def _fallback_summary(
        self,
        session: Session,
        entries: list[JournalEntry],
    ) -> tuple[str, list[str]]:
        subject = (
            f"{len(entries)} writes from {session.author} [session {session.id[:4]}]"
        )
        bullets: list[str] = []
        for e in entries[:5]:
            bullet = f"{e.tool} {e.path}"
            if e.intent:
                bullet += f" — {e.intent}"
            bullets.append(bullet)
        if len(entries) > 5:
            bullets.append(f"... and {len(entries) - 5} more")
        return subject, bullets

    def _archive_journal(self, session: Session) -> None:
        if not session.journal_path.exists():
            return
        archived_dir = session.journal_path.parent / ".archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        target = archived_dir / session.journal_path.name
        shutil.move(str(session.journal_path), str(target))
