from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.sessions import SessionRegistry
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.vault import Vault, _state_dir_for


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_service(tmp_path: Path):
    """Build a PageWriteService against a fresh empty vault under tmp_path."""
    from llm_wiki.daemon.writes import PageWriteService

    _init_git_repo(tmp_path)
    config = WikiConfig()
    vault = Vault.scan(tmp_path)
    coordinator = WriteCoordinator()
    registry = SessionRegistry(config.sessions)
    commit_service = CommitService(
        vault_root=tmp_path, llm=None, lock=asyncio.Lock(),
    )
    service = PageWriteService(
        vault=vault,
        vault_root=tmp_path,
        config=config,
        write_coordinator=coordinator,
        registry=registry,
        commit_service=commit_service,
    )
    return service, registry


@pytest.mark.asyncio
async def test_create_writes_file_with_frontmatter(tmp_path):
    service, registry = _make_service(tmp_path)
    result = await service.create(
        title="Test Page",
        body="Some body text [[raw/source.pdf]].",
        citations=["raw/source.pdf"],
        tags=["test"],
        author="alice",
        connection_id="conn-1",
        intent="create test page",
    )
    assert result.status == "ok"
    assert result.page_path == "wiki/test-page.md"

    page_file = tmp_path / "wiki" / "test-page.md"
    assert page_file.exists()
    content = page_file.read_text()
    assert "title: Test Page" in content
    assert "Some body text" in content


@pytest.mark.asyncio
async def test_create_appends_journal_entry(tmp_path):
    from llm_wiki.daemon.sessions import load_journal

    service, registry = _make_service(tmp_path)
    result = await service.create(
        title="Foo",
        body="text [[raw/x.pdf]]",
        citations=["raw/x.pdf"],
        author="alice",
        connection_id="conn-1",
        intent="i",
    )
    assert result.status == "ok"

    sess = registry.lookup_by_author("alice")
    assert sess is not None
    entries = load_journal(sess.journal_path)
    assert len(entries) == 1
    assert entries[0].tool == "wiki_create"
    assert entries[0].path == "wiki/foo.md"
    assert entries[0].intent == "i"


@pytest.mark.asyncio
async def test_create_refuses_empty_citations(tmp_path):
    service, _ = _make_service(tmp_path)
    result = await service.create(
        title="Foo",
        body="body",
        citations=[],
        author="alice",
        connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "missing-citations"


@pytest.mark.asyncio
async def test_create_rejects_name_collision(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="Foo", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    # Re-scan so the vault sees the new page
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="Foo", body="body [[raw/b.pdf]]", citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "name-collision"


@pytest.mark.asyncio
async def test_create_warns_on_near_match(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="srna-tquant", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="sRNA-tQuant-Pipeline",
        body="body [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "name-near-match"
    assert "srna-tquant" in result.details.get("similar_pages", [])


@pytest.mark.asyncio
async def test_create_force_bypasses_near_match(tmp_path):
    service, _ = _make_service(tmp_path)
    await service.create(
        title="srna-tquant", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="alice", connection_id="conn-1",
    )
    service._vault = Vault.scan(tmp_path)

    result = await service.create(
        title="sRNA-tQuant-Pipeline",
        body="body [[raw/b.pdf]]",
        citations=["raw/b.pdf"],
        author="alice", connection_id="conn-1",
        force=True,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_create_requires_author(tmp_path):
    service, _ = _make_service(tmp_path)
    result = await service.create(
        title="Foo", body="body [[raw/a.pdf]]", citations=["raw/a.pdf"],
        author="", connection_id="conn-1",
    )
    assert result.status == "error"
    assert result.code == "missing-author"
