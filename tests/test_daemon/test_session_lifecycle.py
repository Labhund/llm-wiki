from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import SessionsConfig, WikiConfig
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


@pytest.mark.asyncio
async def test_inactivity_timer_settles_quiet_session(tmp_path):
    """A session with no writes for inactivity_timeout_seconds is settled automatically."""
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "ina.sock"
    config = WikiConfig(
        sessions=SessionsConfig(inactivity_timeout_seconds=1),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        await _request(sock_path, {
            "type": "page-create",
            "title": "Foo",
            "body": "body [[raw/x.pdf]]",
            "citations": ["raw/x.pdf"],
            "author": "alice",
        })

        # Wait long enough for the inactivity timer to fire
        await asyncio.sleep(2.5)

        # The commit should now be in git
        log = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "--format=%H"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert len(log) >= 2  # initial + the inactivity-settled commit
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_write_count_cap_force_settles(tmp_path):
    """When write_count_cap is reached, the session settles immediately."""
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "cap.sock"
    config = WikiConfig(
        sessions=SessionsConfig(write_count_cap=2, inactivity_timeout_seconds=300),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Three creates → after the 2nd one, the cap is hit and the session settles
        for i in range(3):
            await _request(sock_path, {
                "type": "page-create",
                "title": f"Page {i}",
                "body": f"body {i} [[raw/x.pdf]]",
                "citations": ["raw/x.pdf"],
                "author": "alice",
            })

        log = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "--format=%H"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        # initial + at least one cap-triggered settle commit
        assert len(log) >= 2
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_session_cap_warning_emitted(tmp_path):
    """At floor(cap * cap_warn_ratio), the response carries a warning."""
    _init_git_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    sock_path = tmp_path / "warn.sock"
    config = WikiConfig(
        sessions=SessionsConfig(
            write_count_cap=10, cap_warn_ratio=0.6, inactivity_timeout_seconds=300,
        ),
    )
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        last_resp = None
        for i in range(7):  # warning kicks in at floor(10 * 0.6) = 6
            last_resp = await _request(sock_path, {
                "type": "page-create",
                "title": f"P{i}",
                "body": f"b{i} [[raw/x.pdf]]",
                "citations": ["raw/x.pdf"],
                "author": "alice",
            })
        assert last_resp is not None
        warnings = last_resp.get("warnings", [])
        assert any(w["code"] == "session-cap-approaching" for w in warnings)
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
