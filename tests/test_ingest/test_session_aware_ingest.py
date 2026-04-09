from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.commit import CommitService
from llm_wiki.daemon.sessions import SessionRegistry, load_journal
from llm_wiki.daemon.writer import WriteCoordinator
from llm_wiki.daemon.writes import PageWriteService
from llm_wiki.vault import Vault


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _make_write_service(tmp_path: Path):
    config = WikiConfig()
    (tmp_path / "wiki").mkdir(exist_ok=True)
    vault = Vault.scan(tmp_path)
    return PageWriteService(
        vault=vault,
        vault_root=tmp_path,
        config=config,
        write_coordinator=WriteCoordinator(),
        registry=SessionRegistry(config.sessions),
        commit_service=CommitService(
            vault_root=tmp_path, llm=None, lock=asyncio.Lock(),
        ),
    )


@pytest.mark.asyncio
async def test_ingest_creates_pages_via_write_service(tmp_path):
    """An ingest run produces journal entries under the calling agent's session."""
    from llm_wiki.ingest.agent import IngestAgent

    _init_git_repo(tmp_path)
    service = _make_write_service(tmp_path)
    config = WikiConfig()

    # Mock LLM that returns one concept and a page
    from llm_wiki.traverse.llm_client import LLMResponse

    responses = iter([
        # Concept extraction response
        '{"concepts": [{"name": "test-concept", "title": "Test Concept", '
        '"passages": ["This is test content."]}]}',
        # Page content response
        '{"sections": [{"name": "overview", "heading": "Overview", '
        '"content": "Test page content [[raw/source.md]]."}]}',
    ])

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="ingest"):
            return LLMResponse(content=next(responses), tokens_used=10)

    # Create the source file
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source = raw_dir / "source.md"
    source.write_text("# Test source\n\nTest source content for ingestion.\n")

    agent = IngestAgent(MockLLM(), config)
    result = await agent.ingest(
        source, tmp_path,
        author="alice",
        connection_id="conn-1",
        write_service=service,
    )
    assert result.pages_created or result.pages_updated

    # The journal should carry an entry for the new page
    sess = service._registry.lookup_by_author("alice")
    assert sess is not None
    entries = load_journal(sess.journal_path)
    assert len(entries) >= 1
    assert any(e.tool == "wiki_create" for e in entries)


@pytest.mark.asyncio
async def test_ingest_route_requires_author_or_defaults_to_cli(tmp_path):
    """The daemon's ingest route requires an author or defaults to 'cli'."""
    # This is tested via the daemon route in test_write_routes.py / test_ingest_route.py;
    # here we just exercise the agent with author='cli' to confirm it works.
    from llm_wiki.ingest.agent import IngestAgent

    _init_git_repo(tmp_path)
    service = _make_write_service(tmp_path)
    config = WikiConfig()

    from llm_wiki.traverse.llm_client import LLMResponse
    responses = iter([
        '{"concepts": [{"name": "x", "title": "X", "passages": ["x."]}]}',
        '{"sections": [{"name": "o", "heading": "O", "content": "x [[raw/s.md]]."}]}',
    ])

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="ingest"):
            return LLMResponse(content=next(responses), tokens_used=10)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source = raw_dir / "s.md"
    source.write_text("# x\n\nx body\n")

    agent = IngestAgent(MockLLM(), config)
    result = await agent.ingest(
        source, tmp_path,
        author="cli",
        connection_id="cli-conn",
        write_service=service,
    )
    assert result.pages_created or result.pages_updated
