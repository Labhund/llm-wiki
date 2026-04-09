"""Session model + journal IO + recovery scan.

A session is the unit of write grouping for commits. Its key is
`(author, connection_id)` by default; with
`config.sessions.namespace_by_connection: False`, the key is `author` alone.

The journal is one JSONL file per session at
`<state_dir>/sessions/<session-uuid>.journal`. Append is synchronous and
fsync'd — the daemon must not return `ok` for a write until its journal
entry is durably on disk. Load tolerates a truncated final line.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.config import SessionsConfig

if TYPE_CHECKING:
    from llm_wiki.daemon.commit import CommitService

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """One supervised write event in a session journal."""
    ts: str
    tool: str
    path: str
    author: str
    intent: str | None
    summary: str
    content_hash_after: str


@dataclass
class Session:
    """In-memory state for one author's open writing session."""
    id: str
    author: str
    connection_id: str
    opened_at: str
    last_write_at: str
    write_count: int
    journal_path: Path


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def append_journal_entry(session: Session, entry: JournalEntry) -> None:
    """Append one journal entry, fsync, return.

    Synchronous on purpose: callers (PageWriteService) hold the per-page
    write lock and must guarantee the journal line is durable before
    releasing the lock and returning to the agent.
    """
    session.journal_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(entry), ensure_ascii=False)
    with open(session.journal_path, "ab") as fh:
        fh.write((payload + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    session.write_count += 1
    session.last_write_at = _now_iso()


def load_journal(path: Path) -> list[JournalEntry]:
    """Load all entries from a journal file. Truncated final line is dropped."""
    if not path.exists():
        return []
    entries: list[JournalEntry] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Failed to read journal %s", path)
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Truncated/malformed final line — treat as cutoff
            logger.info("Skipping malformed journal line in %s", path)
            continue
        try:
            entries.append(JournalEntry(**data))
        except TypeError:
            logger.info("Skipping journal entry with unexpected fields in %s", path)
            continue
    return entries


def scan_orphaned_journals(state_dir: Path) -> list[Path]:
    """Return non-archived journal files under `<state_dir>/sessions/`."""
    sessions_dir = state_dir / "sessions"
    if not sessions_dir.exists():
        return []
    return [
        p for p in sorted(sessions_dir.glob("*.journal"))
        if ".archived" not in p.parts
    ]


class SessionRegistry:
    """In-memory map from (author, connection_id) → Session.

    Honors `SessionsConfig.namespace_by_connection`. The connection_id is
    supplied by the daemon's per-client handler.
    """

    def __init__(self, config: SessionsConfig) -> None:
        self._config = config
        self._sessions: dict[tuple[str, str], Session] = {}

    def _key(self, author: str, connection_id: str) -> tuple[str, str]:
        if self._config.namespace_by_connection:
            return (author, connection_id)
        return (author, "")

    def get_or_open(
        self,
        author: str,
        connection_id: str,
        state_dir: Path,
    ) -> Session:
        key = self._key(author, connection_id)
        existing = self._sessions.get(key)
        if existing is not None:
            return existing

        sess_id = uuid.uuid4().hex
        now = _now_iso()
        sess = Session(
            id=sess_id,
            author=author,
            connection_id=connection_id,
            opened_at=now,
            last_write_at=now,
            write_count=0,
            journal_path=state_dir / "sessions" / f"{sess_id}.journal",
        )
        self._sessions[key] = sess
        return sess

    def lookup_by_author(self, author: str) -> Session | None:
        """Find ANY active session for the given author.

        Convenience for tests and other call sites where exactly one
        session per author is known to exist (single-connection setups).
        Production callers that have a connection_id should use
        `get_active(author, connection_id)` instead — that's the only
        unambiguous lookup when `namespace_by_connection=true` and the
        same author has multiple concurrent connections.
        """
        for (a, _conn), sess in self._sessions.items():
            if a == author:
                return sess
        return None

    def get_active(
        self,
        author: str,
        connection_id: str,
    ) -> Session | None:
        """Find the session for `(author, connection_id)`, or None.

        Honors `namespace_by_connection` via `_key()`. Unlike
        `get_or_open`, this is read-only — it never creates a session.
        Used by `session-close` to settle exactly the session that the
        calling connection owns, never sweeping up unrelated sessions
        that happen to share the author identifier.
        """
        return self._sessions.get(self._key(author, connection_id))

    def close(self, session: Session) -> None:
        """Remove the session from the registry. Does not touch the journal."""
        key = self._key(session.author, session.connection_id)
        self._sessions.pop(key, None)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())


async def recover_sessions(
    state_dir: Path,
    commit_service: "CommitService",
) -> int:
    """Settle every orphaned journal under <state_dir>/sessions/.

    Called once at daemon startup. For each non-archived journal, builds
    a stub Session, loads its entries, and runs the commit service's
    settle pipeline. Returns the number of journals successfully recovered.
    """
    orphans = scan_orphaned_journals(state_dir)
    recovered = 0
    for journal_path in orphans:
        entries = load_journal(journal_path)
        if not entries:
            logger.info("Skipping empty journal %s", journal_path)
            continue

        # Reconstruct a stub Session from the journal's first entry
        first = entries[0]
        sess = Session(
            id=journal_path.stem,
            author=first.author,
            connection_id="recovered",
            opened_at=first.ts,
            last_write_at=entries[-1].ts,
            write_count=len(entries),
            journal_path=journal_path,
        )
        try:
            await commit_service.settle_with_fallback(sess, entries)
            recovered += 1
        except Exception:
            logger.exception("Failed to recover journal %s", journal_path)
            # Don't archive on failure — leave for the next attempt
            continue
    return recovered
