import asyncio
import os
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


def test_query_via_daemon(daemon_for_cli, monkeypatch):
    """Query command sends a query request and prints the synthesized answer."""
    import json
    from unittest.mock import MagicMock

    responses = iter([
        json.dumps({
            "salient_points": "Manifest mentions sRNA validation page",
            "remaining_questions": [],
            "next_candidates": [],
            "hypothesis": "sRNA validation uses PCA and clustering",
            "answer_complete": True,
        }),
        "sRNA embeddings are validated using PCA and k-means [[srna-embeddings]].",
    ])

    async def mock_acompletion(**kwargs):
        content = next(responses)
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = content
        mock_resp.usage = MagicMock()
        mock_resp.usage.total_tokens = 100
        return mock_resp

    monkeypatch.setattr("litellm.acompletion", mock_acompletion)

    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "How are sRNA embeddings validated?",
        "--vault", str(vault_path),
    ])
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "sRNA" in result.output
    assert "Citations:" in result.output
    assert "srna-embeddings" in result.output


def test_default_vault_uses_env_var(tmp_path, monkeypatch):
    """--vault defaults to LLM_WIKI_VAULT env var when set."""
    (tmp_path / "wiki").mkdir()
    monkeypatch.setenv("LLM_WIKI_VAULT", str(tmp_path))

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == str(tmp_path)


def test_default_vault_falls_back_to_home_wiki(tmp_path, monkeypatch):
    """--vault defaults to ~/wiki when LLM_WIKI_VAULT is unset and ~/wiki exists."""
    monkeypatch.delenv("LLM_WIKI_VAULT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "wiki").mkdir()

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == str(tmp_path / "wiki")


def test_default_vault_falls_back_to_dot(tmp_path, monkeypatch):
    """--vault defaults to '.' when neither LLM_WIKI_VAULT nor ~/wiki is set."""
    monkeypatch.delenv("LLM_WIKI_VAULT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Do NOT create tmp_path/wiki

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == "."
