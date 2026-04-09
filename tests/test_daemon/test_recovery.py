from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.commit import CommitService


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _write_journal(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


@pytest.mark.asyncio
async def test_recover_sessions_processes_orphaned_journal(tmp_path):
    """An orphaned journal on startup is settled into a commit."""
    from llm_wiki.daemon.sessions import recover_sessions

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    state_dir = tmp_path / "state"
    journal_path = state_dir / "sessions" / "abc123.journal"
    _write_journal(journal_path, [{
        "ts": "2026-04-08T10:00:00+00:00",
        "tool": "wiki_create",
        "path": "wiki/foo.md",
        "author": "researcher-3",
        "intent": "create test",
        "summary": "created foo",
        "content_hash_after": "sha256:abc",
    }])

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 1

    # Journal should be archived
    assert not journal_path.exists()
    archived = state_dir / "sessions" / ".archived" / "abc123.journal"
    assert archived.exists()

    # The commit should be in git history
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "researcher-3" in log


@pytest.mark.asyncio
async def test_recover_sessions_handles_truncated_journal(tmp_path):
    """A journal with a truncated final line still recovers earlier entries."""
    from llm_wiki.daemon.sessions import recover_sessions

    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "foo.md").write_text("body.\n")

    state_dir = tmp_path / "state"
    journal_path = state_dir / "sessions" / "trunc.journal"
    journal_path.parent.mkdir(parents=True)
    valid = json.dumps({
        "ts": "t", "tool": "wiki_create", "path": "wiki/foo.md",
        "author": "a", "intent": "i", "summary": "s", "content_hash_after": "h",
    })
    journal_path.write_text(valid + "\n" + '{"ts": "t2", "tool":', encoding="utf-8")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 1


@pytest.mark.asyncio
async def test_recover_sessions_no_orphans(tmp_path):
    from llm_wiki.daemon.sessions import recover_sessions
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_sessions_skips_archived(tmp_path):
    from llm_wiki.daemon.sessions import recover_sessions

    state_dir = tmp_path / "state"
    archived_dir = state_dir / "sessions" / ".archived"
    archived_dir.mkdir(parents=True)
    (archived_dir / "old.journal").write_text("{}\n")

    service = CommitService(vault_root=tmp_path, llm=None, lock=asyncio.Lock())
    recovered = await recover_sessions(state_dir=state_dir, commit_service=service)
    assert recovered == 0
