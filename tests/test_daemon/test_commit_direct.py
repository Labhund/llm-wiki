from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.daemon.commit import CommitService


@pytest.fixture
def git_vault(tmp_path: Path):
    """Minimal git repo acting as a vault."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "wiki").mkdir()
    page = tmp_path / "wiki" / "test-page.md"
    page.write_text("# Test\n\nContent.\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


@pytest.mark.asyncio
async def test_commit_direct_commits_modified_file(git_vault: Path):
    lock = asyncio.Lock()
    svc = CommitService(vault_root=git_vault, llm=None, lock=lock)

    page = git_vault / "wiki" / "test-page.md"
    page.write_text("# Test\n\nContent with [[pca|PCA]].\n")

    sha = await svc.commit_direct(["wiki/test-page.md"], "audit: add 1 wikilink to test-page")
    assert sha is not None
    assert len(sha) == 40

    # Committed content is reflected in git
    result = subprocess.run(
        ["git", "show", "HEAD:wiki/test-page.md"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    )
    assert "[[pca|PCA]]" in result.stdout


@pytest.mark.asyncio
async def test_commit_direct_returns_none_when_nothing_staged(git_vault: Path):
    lock = asyncio.Lock()
    svc = CommitService(vault_root=git_vault, llm=None, lock=lock)

    # Nothing changed — commit_direct should return None without erroring
    sha = await svc.commit_direct(["wiki/test-page.md"], "audit: empty")
    assert sha is None
