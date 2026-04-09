from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


@pytest_asyncio.fixture
async def write_daemon(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "write.sock"
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    yield server, sock_path
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


async def _request(
    sock_path: Path,
    msg: dict,
    *,
    connection_id: str | None = "test-conn",
) -> dict:
    """Send a request, auto-injecting connection_id for session continuity.

    Tests within the same test file share `connection_id="test-conn"` by
    default, so multiple writes from one test land in one daemon session.
    Pass `connection_id=None` to omit the field entirely (e.g. to test the
    `missing connection_id` error path) or `connection_id="other"` to
    simulate a separate MCP client.
    """
    if connection_id is not None and "connection_id" not in msg:
        msg = {**msg, "connection_id": connection_id}
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_page_create_route_writes_file(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Test Page",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
        "intent": "test",
    })
    assert resp["status"] == "ok"
    assert resp["page_path"] == "wiki/test-page.md"
    assert "session_id" in resp
    assert (server._vault_root / "wiki" / "test-page.md").exists()


@pytest.mark.asyncio
async def test_page_create_missing_citations_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body",
        "citations": [],
        "author": "alice",
    })
    assert resp["status"] == "error"
    assert resp["code"] == "missing-citations"


@pytest.mark.asyncio
async def test_page_create_missing_author_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
    })
    assert resp["status"] == "error"
    # Handler's required-field loop returns `message`, not `code`
    assert "author" in resp["message"]


@pytest.mark.asyncio
async def test_page_create_missing_connection_id_returns_error(write_daemon):
    """connection_id is required in the request payload — the daemon won't guess."""
    server, sock_path = write_daemon
    resp = await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id=None,  # opt out of auto-injection
    )
    assert resp["status"] == "error"
    assert "connection_id" in resp["message"]


@pytest.mark.asyncio
async def test_page_update_route_applies_patch(write_daemon):
    server, sock_path = write_daemon
    # Create the page first
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "alpha\nbeta\ngamma\n",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    patch = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
        " gamma\n"
        "*** End Patch\n"
    )
    resp = await _request(sock_path, {
        "type": "page-update",
        "page": "foo",
        "patch": patch,
        "author": "alice",
        "intent": "uppercase beta",
    })
    assert resp["status"] == "ok"
    assert "BETA" in (server._vault_root / "wiki" / "foo.md").read_text()


@pytest.mark.asyncio
async def test_page_update_patch_conflict_carries_excerpt(write_daemon):
    server, sock_path = write_daemon
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo", "body": "a\nb\n", "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    bad_patch = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " nonexistent\n"
        "+new\n"
        "*** End Patch\n"
    )
    resp = await _request(sock_path, {
        "type": "page-update",
        "page": "foo",
        "patch": bad_patch,
        "author": "alice",
    })
    assert resp["status"] == "error"
    assert resp["code"] == "patch-conflict"
    assert "current_excerpt" in resp


@pytest.mark.asyncio
async def test_page_append_route_inserts_section(write_daemon):
    server, sock_path = write_daemon
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "## Existing\n\nbody.\n",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    resp = await _request(sock_path, {
        "type": "page-append",
        "page": "foo",
        "section_heading": "New",
        "body": "new content [[raw/y.pdf]]",
        "citations": ["raw/y.pdf"],
        "author": "alice",
    })
    assert resp["status"] == "ok"
    content = (server._vault_root / "wiki" / "foo.md").read_text()
    assert "## New" in content
