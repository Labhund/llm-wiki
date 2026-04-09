from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_raw_companion(vault_root: Path, name: str, status: str) -> Path:
    raw_dir = vault_root / "raw"
    raw_dir.mkdir(exist_ok=True)
    companion = raw_dir / name
    companion.write_text(
        f"---\nreading_status: {status}\ningested: 2026-04-10\nsource_type: paper\n---\n"
    )
    return companion


@pytest.fixture
def git_vault(tmp_path: Path):
    """Minimal git-initialized vault for commit tests."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").mkdir()
    # Initial commit so HEAD exists
    (tmp_path / "README.md").write_text("vault\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_source_mark_updates_reading_status(git_vault: Path):
    """source-mark route updates reading_status in companion frontmatter."""
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    companion = _make_raw_companion(git_vault, "paper.md", "unread")
    subprocess.run(["git", "add", "."], cwd=git_vault, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add companion"], cwd=git_vault, check=True, capture_output=True)

    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = git_vault
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(companion),
        "status": "in_progress",
        "author": "test-user",
    })

    assert response["status"] == "ok"
    assert response["new_status"] == "in_progress"
    assert response["old_status"] == "unread"

    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"


@pytest.mark.asyncio
async def test_source_mark_rejects_path_outside_raw(tmp_path: Path):
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = tmp_path
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(tmp_path / "wiki" / "page.md"),
        "status": "read",
        "author": "test",
    })
    assert response["status"] == "error"
    assert "raw/" in response["message"]


@pytest.mark.asyncio
async def test_source_mark_rejects_invalid_status(tmp_path: Path):
    from llm_wiki.daemon.server import DaemonServer
    from llm_wiki.config import WikiConfig
    (tmp_path / "raw").mkdir()
    companion = _make_raw_companion(tmp_path, "paper.md", "unread")
    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = tmp_path
    server._config = WikiConfig()
    server._commit_lock = __import__("asyncio").Lock()

    response = await server._handle_source_mark({
        "source_path": str(companion),
        "status": "maybe",
        "author": "test",
    })
    assert response["status"] == "error"
    assert "unread|in_progress|read" in response["message"]
