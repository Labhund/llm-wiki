from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.mcp.server import MCPServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


@pytest_asyncio.fixture
async def mcp_e2e(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "e2e.sock"
    # IMPORTANT: use the production-default WikiConfig() so that
    # `wiki_dir = "wiki"` and the daemon's PageWriteService writes pages
    # to `tmp_path/wiki/<slug>.md`. Phase 6a's `phase6a_daemon_server`
    # fixture uses `VaultConfig(wiki_dir="")` to align with the
    # `sample_vault` layout — but here we're creating pages from scratch
    # via `wiki_create`, and the assertions below expect them under
    # `tmp_path/wiki/...`. This is exactly the trap Plan 6b's prerequisites
    # note (top of plan 6b) explicitly warned against; do not regress it.
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)
    mcp_server = MCPServer(vault_path=tmp_path, client=client)

    yield mcp_server, tmp_path

    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


@pytest.mark.asyncio
async def test_e2e_status_through_mcp(mcp_e2e):
    mcp_server, _ = mcp_e2e
    result = await mcp_server.call_tool("wiki_status", {})
    payload = json.loads(result[0].text)
    assert payload["status"] == "ok"
    assert "page_count" in payload


@pytest.mark.asyncio
async def test_e2e_create_then_session_close(mcp_e2e):
    """A wiki_create followed by wiki_session_close produces a git commit."""
    mcp_server, vault_root = mcp_e2e

    create_result = await mcp_server.call_tool("wiki_create", {
        "title": "Test Page",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
        "intent": "smoke test",
    })
    create_payload = json.loads(create_result[0].text)
    assert create_payload["status"] == "ok"
    assert (vault_root / "wiki" / "test-page.md").exists()

    close_result = await mcp_server.call_tool("wiki_session_close", {
        "author": "alice",
    })
    close_payload = json.loads(close_result[0].text)
    assert close_payload["status"] == "ok"
    assert close_payload["settled"] is True
    assert close_payload["commit_sha"]

    # Verify the commit landed in git
    log = subprocess.run(
        ["git", "-C", str(vault_root), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "alice" in log


@pytest.mark.asyncio
async def test_e2e_create_missing_citations_raises(mcp_e2e):
    from llm_wiki.mcp.errors import McpToolError
    mcp_server, _ = mcp_e2e

    with pytest.raises(McpToolError) as exc_info:
        await mcp_server.call_tool("wiki_create", {
            "title": "Foo",
            "body": "body",
            "citations": [],
            "author": "alice",
        })
    assert exc_info.value.code == "missing-citations"


@pytest.mark.asyncio
async def test_e2e_lint_returns_attention_map(mcp_e2e):
    mcp_server, _ = mcp_e2e
    result = await mcp_server.call_tool("wiki_lint", {})
    payload = json.loads(result[0].text)
    assert payload["status"] == "ok"
    assert "attention_map" in payload
