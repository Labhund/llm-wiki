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
    serve_task = loop.create_task(server.serve_forever())

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield sample_vault

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()


def _populate(vault_path: Path) -> None:
    """Run lint to seed the issue queue."""
    runner = CliRunner()
    runner.invoke(cli, ["lint", "--vault", str(vault_path)])


def test_issues_list(daemon_for_cli):
    vault_path = daemon_for_cli
    _populate(vault_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["issues", "list", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "broken-link" in result.output or "broken-wikilinks" in result.output


def test_issues_list_filter_by_type(daemon_for_cli):
    vault_path = daemon_for_cli
    _populate(vault_path)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["issues", "list", "--type", "orphan", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0, result.output
    # Should NOT contain a broken-citation issue id
    assert "broken-citation-" not in result.output


def test_issues_show_and_resolve(daemon_for_cli):
    vault_path = daemon_for_cli
    _populate(vault_path)
    runner = CliRunner()

    list_result = runner.invoke(cli, ["issues", "list", "--vault", str(vault_path)])
    # Pull the first id from the output (lines look like "  <id> — <title>")
    first_id = next(
        line.strip().split(" ")[0]
        for line in list_result.output.splitlines()
        if line.strip() and not line.strip().startswith(("Found", "id"))
    )

    show_result = runner.invoke(
        cli, ["issues", "show", first_id, "--vault", str(vault_path)]
    )
    assert show_result.exit_code == 0, show_result.output
    assert first_id in show_result.output

    resolve_result = runner.invoke(
        cli, ["issues", "resolve", first_id, "--vault", str(vault_path)]
    )
    assert resolve_result.exit_code == 0, resolve_result.output

    show_after = runner.invoke(
        cli, ["issues", "show", first_id, "--vault", str(vault_path)]
    )
    assert "resolved" in show_after.output


def test_issues_wontfix(daemon_for_cli):
    vault_path = daemon_for_cli
    _populate(vault_path)
    runner = CliRunner()

    list_result = runner.invoke(cli, ["issues", "list", "--vault", str(vault_path)])
    first_id = next(
        line.strip().split(" ")[0]
        for line in list_result.output.splitlines()
        if line.strip() and not line.strip().startswith(("Found", "id"))
    )

    wf_result = runner.invoke(
        cli, ["issues", "wontfix", first_id, "--vault", str(vault_path)]
    )
    assert wf_result.exit_code == 0, wf_result.output

    show = runner.invoke(cli, ["issues", "show", first_id, "--vault", str(vault_path)])
    assert "wontfix" in show.output
