from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_wiki.cli.main import cli
from llm_wiki.daemon.lifecycle import socket_path_for
from llm_wiki.daemon.server import DaemonServer


def _seed_vault(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")
    return tmp_path


@pytest.fixture
def talk_daemon(tmp_path: Path):
    """Start a daemon in a background thread so sync CLI tests can connect."""
    vault_path = _seed_vault(tmp_path)
    sock_path = socket_path_for(vault_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = DaemonServer(vault_path, sock_path)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.start())
    loop.create_task(server.serve_forever())

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield vault_path

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()

    # Clean up state dir under ~/.llm-wiki/vaults/
    import shutil
    from llm_wiki.vault import _state_dir_for
    state_dir = _state_dir_for(vault_path)
    if state_dir.exists():
        shutil.rmtree(state_dir, ignore_errors=True)


def test_talk_post_then_read(talk_daemon: Path):
    """Post a talk entry, then read it back."""
    vault_root = talk_daemon
    runner = CliRunner()

    post = runner.invoke(cli, [
        "talk", "post", "p", "--message", "test message",
        "--vault", str(vault_root),
    ])
    assert post.exit_code == 0, post.output

    read = runner.invoke(cli, ["talk", "read", "p", "--vault", str(vault_root)])
    assert read.exit_code == 0, read.output
    assert "test message" in read.output
    assert "@human" in read.output


def test_talk_list(talk_daemon: Path):
    vault_root = talk_daemon
    runner = CliRunner()
    runner.invoke(cli, ["talk", "post", "p", "--message", "x", "--vault", str(vault_root)])

    result = runner.invoke(cli, ["talk", "list", "--vault", str(vault_root)])
    assert result.exit_code == 0, result.output
    assert "p" in result.output


def test_talk_read_empty_page(talk_daemon: Path):
    vault_root = talk_daemon
    runner = CliRunner()
    result = runner.invoke(cli, ["talk", "read", "p", "--vault", str(vault_root)])
    assert result.exit_code == 0
    assert "no entries" in result.output.lower() or "0 entries" in result.output.lower()
