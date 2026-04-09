from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_session_dataclass_round_trip(tmp_path):
    from llm_wiki.daemon.sessions import Session
    sess = Session(
        id="abc-123",
        author="claude-opus-4-6",
        connection_id="conn-1",
        opened_at="2026-04-08T10:00:00+00:00",
        last_write_at="2026-04-08T10:00:01+00:00",
        write_count=0,
        journal_path=tmp_path / "abc-123.journal",
    )
    assert sess.id == "abc-123"
    assert sess.author == "claude-opus-4-6"
    assert sess.write_count == 0


def test_journal_append_and_load(tmp_path):
    from llm_wiki.daemon.sessions import (
        JournalEntry,
        Session,
        append_journal_entry,
        load_journal,
    )
    journal_path = tmp_path / "s.journal"
    sess = Session(
        id="s", author="a", connection_id="c",
        opened_at="t", last_write_at="t", write_count=0,
        journal_path=journal_path,
    )
    entry = JournalEntry(
        ts="2026-04-08T10:00:00+00:00",
        tool="wiki_create",
        path="wiki/foo.md",
        author="a",
        intent="test",
        summary="created foo",
        content_hash_after="sha256:abc",
    )
    append_journal_entry(sess, entry)
    assert journal_path.exists()

    loaded = load_journal(journal_path)
    assert len(loaded) == 1
    assert loaded[0].tool == "wiki_create"
    assert loaded[0].path == "wiki/foo.md"
    assert loaded[0].intent == "test"


def test_journal_append_multiple_entries(tmp_path):
    from llm_wiki.daemon.sessions import (
        JournalEntry, Session, append_journal_entry, load_journal,
    )
    journal_path = tmp_path / "multi.journal"
    sess = Session(
        id="s", author="a", connection_id="c",
        opened_at="t", last_write_at="t", write_count=0,
        journal_path=journal_path,
    )
    for i in range(3):
        entry = JournalEntry(
            ts=f"t{i}",
            tool="wiki_update",
            path=f"wiki/p{i}.md",
            author="a",
            intent=f"intent {i}",
            summary=f"summary {i}",
            content_hash_after=f"sha256:{i}",
        )
        append_journal_entry(sess, entry)

    loaded = load_journal(journal_path)
    assert [e.intent for e in loaded] == ["intent 0", "intent 1", "intent 2"]


def test_journal_load_tolerates_malformed_final_line(tmp_path):
    """A truncated final line (power-failure window) is treated as the cutoff."""
    from llm_wiki.daemon.sessions import load_journal
    journal_path = tmp_path / "p.journal"
    # Two valid lines + one truncated line
    valid_line = json.dumps({
        "ts": "t1", "tool": "wiki_create", "path": "wiki/a.md",
        "author": "a", "intent": "i", "summary": "s", "content_hash_after": "h",
    })
    journal_path.write_text(
        valid_line + "\n" + valid_line + "\n" + '{"ts": "t3", "tool":',
        encoding="utf-8",
    )
    loaded = load_journal(journal_path)
    assert len(loaded) == 2  # third line ignored


def test_journal_load_missing_file_returns_empty(tmp_path):
    from llm_wiki.daemon.sessions import load_journal
    assert load_journal(tmp_path / "missing.journal") == []


def test_session_registry_get_or_open_creates_new(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    sess = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert sess.author == "alice"
    assert sess.connection_id == "conn-1"
    assert sess.write_count == 0
    assert sess.journal_path.parent == state_dir / "sessions"


def test_session_registry_returns_same_session_for_same_keys(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert s1.id == s2.id


def test_session_registry_namespace_by_connection(tmp_path):
    """Default mode: same author, different connection_id → different sessions."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert s1.id != s2.id


def test_session_registry_no_namespace_by_connection(tmp_path):
    """Advanced mode: same author across connections → one session."""
    from llm_wiki.config import SessionsConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cfg = SessionsConfig(namespace_by_connection=False)
    registry = SessionRegistry(cfg)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert s1.id == s2.id


def test_session_registry_close_removes_session(tmp_path):
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    sess = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    registry.close(sess)
    # Re-opening should produce a NEW session (different id)
    sess2 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    assert sess2.id != sess.id


def test_session_registry_get_active_returns_specific_session(tmp_path):
    """get_active scopes lookup to (author, connection_id), not just author."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    s1 = registry.get_or_open("alice", "conn-1", state_dir=state_dir)
    s2 = registry.get_or_open("alice", "conn-2", state_dir=state_dir)
    assert registry.get_active("alice", "conn-1").id == s1.id
    assert registry.get_active("alice", "conn-2").id == s2.id
    assert registry.get_active("alice", "conn-missing") is None
    assert registry.get_active("bob", "conn-1") is None


def test_session_registry_get_active_does_not_create(tmp_path):
    """get_active is read-only — never opens a new session."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.sessions import SessionRegistry
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = SessionRegistry(WikiConfig().sessions)
    assert registry.get_active("alice", "conn-1") is None
    assert registry.all_sessions() == []


def test_scan_orphaned_journals_excludes_archived(tmp_path):
    from llm_wiki.daemon.sessions import scan_orphaned_journals
    state_dir = tmp_path / "state"
    sessions_dir = state_dir / "sessions"
    archived_dir = sessions_dir / ".archived"
    sessions_dir.mkdir(parents=True)
    archived_dir.mkdir()

    (sessions_dir / "open.journal").write_text("{}\n")
    (archived_dir / "old.journal").write_text("{}\n")

    orphans = scan_orphaned_journals(state_dir)
    assert len(orphans) == 1
    assert orphans[0].name == "open.journal"
