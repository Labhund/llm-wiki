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
    """Start a daemon in a background thread so sync CLI tests can connect.

    Mirrors the fixture in tests/test_cli/test_commands.py — the auto-start
    subprocess path is unsuitable for unit tests because it spawns external
    processes that race with the sample_vault cleanup hook.
    """
    sock_path = socket_path_for(sample_vault)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = DaemonServer(sample_vault, sock_path)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.start())
    serve_task = loop.create_task(server.serve_forever())

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield sample_vault

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()


def test_lint_cmd_prints_grouped_report(daemon_for_cli):
    """`llm-wiki lint` runs the daemon's lint route and prints a grouped report."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["lint", "--vault", str(vault_path)])

    assert result.exit_code == 0, result.output
    # Output should mention each check name and a count
    assert "orphans" in result.output
    assert "broken-wikilinks" in result.output
    assert "missing-markers" in result.output
    assert "broken-citations" in result.output


def test_lint_cmd_idempotent_quiet_on_rerun(daemon_for_cli):
    """Second invocation reports zero new issues."""
    vault_path = daemon_for_cli
    runner = CliRunner()
    runner.invoke(cli, ["lint", "--vault", str(vault_path)])
    result = runner.invoke(cli, ["lint", "--vault", str(vault_path)])

    assert result.exit_code == 0, result.output
    assert "0 new" in result.output or "no new" in result.output.lower()
