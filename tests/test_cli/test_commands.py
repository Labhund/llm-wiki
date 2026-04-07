import asyncio
import threading
from pathlib import Path

import pytest
import pytest_asyncio
from click.testing import CliRunner

from llm_wiki.cli.main import cli
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.lifecycle import socket_path_for


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
    # Clean up server and socket
    loop.run_until_complete(server.stop())
    loop.close()


def test_init_command(sample_vault: Path):
    """Init still works without daemon (direct scan)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output


def test_init_nonexistent():
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "/nonexistent/path"])
    assert result.exit_code != 0


def test_status_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--vault", str(vault_path)])
    assert result.exit_code == 0
    assert "page" in result.output.lower()


def test_search_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["search", "sRNA", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()


def test_search_no_results_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["search", "quantum physics", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "no results" in result.output.lower()


def test_read_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()


def test_read_section_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output


def test_read_grep_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output


def test_read_missing_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "nonexistent", "--vault", str(vault_path)]
    )
    assert result.exit_code != 0 or "not found" in result.output.lower()


def test_manifest_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["manifest", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert len(result.output) > 0
