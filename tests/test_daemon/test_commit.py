from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.sessions import JournalEntry, Session


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with a noop initial commit."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_session(tmp_path: Path) -> Session:
    return Session(
        id="abc123",
        author="researcher-3",
        connection_id="conn-1",
        opened_at="2026-04-08T10:00:00+00:00",
        last_write_at="2026-04-08T10:00:01+00:00",
        write_count=2,
        journal_path=tmp_path / "state" / "sessions" / "abc123.journal",
    )


def _make_entry(path: str, intent: str = "test edit") -> JournalEntry:
    return JournalEntry(
        ts="2026-04-08T10:00:00+00:00",
        tool="wiki_update",
        path=path,
        author="researcher-3",
        intent=intent,
        summary="+1 -1 @ ## Methods",
        content_hash_after="sha256:abc",
    )


@pytest.mark.asyncio
async def test_commit_service_settle_with_fallback_writes_commit(tmp_path):
    """A settle with no LLM produces a deterministic-message commit."""
    from llm_wiki.daemon.commit import CommitService
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("---\ntitle: Foo\n---\n\nbody.\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    entries = [_make_entry("wiki/foo.md")]

    result = await service.settle_with_fallback(sess, entries)
    assert result.commit_sha is not None
    assert "wiki/foo.md" in result.paths_committed
    assert result.summary_used == "fallback"

    # Verify the commit landed in git
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "researcher-3" in log
    assert "Session: abc123" in log
    assert "Agent: researcher-3" in log
    assert "Writes: 2" in log


@pytest.mark.asyncio
async def test_commit_service_settle_with_llm_uses_summary(tmp_path):
    """When the LLM returns a summary, it shows up in the commit message."""
    from llm_wiki.daemon.commit import CommitService
    from llm_wiki.traverse.llm_client import LLMResponse

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("body.\n")

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance", **kwargs):
            return LLMResponse(
                content=(
                    "fix learning rate per source table 3\n\n"
                    "- updated Methods section\n"
                    "- corrected the cited number"
                ),
                input_tokens=20,
                output_tokens=0,
            )

    service = CommitService(vault_root=tmp_path, llm=MockLLM(), lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(
        sess, [_make_entry("wiki/foo.md", intent="fix learning rate")],
    )
    assert result.summary_used == "llm"
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "fix learning rate per source table 3" in log
    assert "updated Methods section" in log


@pytest.mark.asyncio
async def test_commit_service_falls_back_when_llm_raises(tmp_path):
    """LLM exception → deterministic fallback message; commit still lands."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    class FailingLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("model unreachable")

    service = CommitService(vault_root=tmp_path, llm=FailingLLM(), lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(sess, [_make_entry("wiki/foo.md")])
    assert result.commit_sha is not None
    assert result.summary_used == "fallback"


@pytest.mark.asyncio
async def test_commit_service_serial_lock_serializes(tmp_path):
    """Two concurrent settle calls do not race on git."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("a\n")
    (tmp_path / "wiki" / "b.md").write_text("b\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess_a = Session("ida", "alice", "c1", "t", "t", 1, tmp_path / "state" / "sessions" / "ida.journal")
    sess_b = Session("idb", "bob", "c2", "t", "t", 1, tmp_path / "state" / "sessions" / "idb.journal")

    results = await asyncio.gather(
        service.settle_with_fallback(sess_a, [_make_entry("wiki/a.md")]),
        service.settle_with_fallback(sess_b, [_make_entry("wiki/b.md")]),
    )
    # Both should have committed
    assert all(r.commit_sha is not None for r in results)
    # Two distinct commits in history
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 3  # initial + two settles


@pytest.mark.asyncio
async def test_commit_service_archives_journal_after_commit(tmp_path):
    """After settle, the journal is moved to .archived/."""
    from llm_wiki.daemon.commit import CommitService
    from llm_wiki.daemon.sessions import append_journal_entry

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("foo\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    # Make the journal real on disk by appending
    entry = _make_entry("wiki/foo.md")
    append_journal_entry(sess, entry)
    assert sess.journal_path.exists()

    await service.settle_with_fallback(sess, [entry])
    assert not sess.journal_path.exists()
    archived = tmp_path / "state" / "sessions" / ".archived" / "abc123.journal"
    assert archived.exists()


@pytest.mark.asyncio
async def test_commit_service_handles_nothing_to_commit(tmp_path):
    """If the user already committed the journaled paths, settle skips cleanly."""
    from llm_wiki.daemon.commit import CommitService

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "foo.md"
    page.write_text("body.\n")
    # User commits manually
    subprocess.run(["git", "-C", str(tmp_path), "add", "wiki/foo.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "manual"], check=True)

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    sess = _make_session(tmp_path)
    result = await service.settle_with_fallback(sess, [_make_entry("wiki/foo.md")])
    assert result.commit_sha is None  # nothing to commit
    assert result.summary_used == "none"
