from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


async def _request(
    sock_path: Path,
    msg: dict,
    *,
    connection_id: str | None = "test-conn",
) -> dict:
    if connection_id is not None and "connection_id" not in msg:
        msg = {**msg, "connection_id": connection_id}
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest_asyncio.fixture
async def write_daemon(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "sc.sock"
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


@pytest.mark.asyncio
async def test_session_close_settles_active_session(write_daemon):
    server, sock_path = write_daemon
    # Create a page so there's a session with one journal entry
    await _request(sock_path, {
        "type": "page-create",
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })

    resp = await _request(sock_path, {
        "type": "session-close",
        "author": "alice",
    })
    assert resp["status"] == "ok"
    assert resp["settled"] is True
    assert resp["commit_sha"]

    # The commit should be in git
    log = subprocess.run(
        ["git", "-C", str(server._vault_root), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "alice" in log


@pytest.mark.asyncio
async def test_session_close_idempotent(write_daemon):
    server, sock_path = write_daemon
    # Close without ever opening — must not error
    resp = await _request(sock_path, {
        "type": "session-close",
        "author": "noone",
    })
    assert resp["status"] == "ok"
    assert resp["settled"] is False


@pytest.mark.asyncio
async def test_session_close_missing_author_returns_error(write_daemon):
    server, sock_path = write_daemon
    resp = await _request(sock_path, {"type": "session-close"})
    assert resp["status"] == "error"
    assert "author" in resp["message"]


@pytest.mark.asyncio
async def test_session_close_missing_connection_id_returns_error(write_daemon):
    """connection_id is required: the daemon won't guess which session to settle."""
    server, sock_path = write_daemon
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id=None,  # opt out of auto-injection
    )
    assert resp["status"] == "error"
    assert "connection_id" in resp["message"]


@pytest.mark.asyncio
async def test_session_close_only_settles_matching_connection(write_daemon):
    """Two sessions with same author, different connection_id → close affects only one."""
    server, sock_path = write_daemon
    # Open two sessions for alice on different connection_ids
    await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Page A",
            "body": "body A [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id="conn-A",
    )
    await _request(
        sock_path,
        {
            "type": "page-create",
            "title": "Page B",
            "body": "body B [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        },
        connection_id="conn-B",
    )

    # Close only conn-A
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id="conn-A",
    )
    assert resp["settled"] is True

    # conn-B's session is still open — closing it produces another commit
    resp = await _request(
        sock_path,
        {"type": "session-close", "author": "alice"},
        connection_id="conn-B",
    )
    assert resp["settled"] is True

    # Two commits total (in addition to the initial)
    log = subprocess.run(
        ["git", "-C", str(server._vault_root), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 3  # initial + conn-A settle + conn-B settle


@pytest.mark.asyncio
async def test_daemon_shutdown_settles_open_sessions(tmp_path):
    """When DaemonServer.stop() is called, every open session is settled into git."""
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "shutdown.sock"
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Create a page (opens a session, journals one entry)
        await _request(sock_path, {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        })
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()

    # The commit should be in git after stop()
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(log) >= 2  # initial + alice's commit
