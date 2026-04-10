from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_wiki.cli.main import cli
from llm_wiki.daemon.lifecycle import socket_path_for
from llm_wiki.daemon.server import DaemonServer


@pytest.fixture
def daemon_for_cli(sample_vault: Path):
    """Start a daemon in a background thread so sync CLI tests can connect."""
    sock_path = socket_path_for(sample_vault)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = DaemonServer(sample_vault, sock_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.start())
    loop.create_task(server.serve_forever())

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield sample_vault

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()


def test_ps_shows_workers(daemon_for_cli):
    """`llm-wiki ps` lists background workers."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "WORKERS" in result.output
    assert "auditor" in result.output


def test_ps_shows_queue_section(daemon_for_cli):
    """`llm-wiki ps` shows LLM QUEUE section even when idle."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "LLM QUEUE" in result.output


def test_ps_shows_processes_header(daemon_for_cli):
    """`llm-wiki ps` shows PROCESSES header with token count."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "PROCESSES" in result.output
    assert "tokens used" in result.output


def test_ps_no_daemon(tmp_path):
    """`llm-wiki ps` exits non-zero when daemon is not running."""
    runner = CliRunner()
    vault_path = tmp_path / "empty_vault"
    vault_path.mkdir()
    result = runner.invoke(cli, ["ps", "--vault", str(vault_path)])
    assert result.exit_code != 0
