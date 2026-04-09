from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


def test_mcp_cli_resolves_vault_from_arg(tmp_path):
    """A positional vault path is used when LLM_WIKI_VAULT is not set."""
    from llm_wiki.cli.main import cli

    # Make tmp_path look like a vault
    (tmp_path / "wiki").mkdir()

    runner = CliRunner(env={"LLM_WIKI_VAULT": ""})
    with patch("llm_wiki.cli.main._get_client") as mock_get_client, \
         patch("llm_wiki.mcp.server.MCPServer") as mock_server_cls:
        mock_get_client.return_value = MagicMock()
        mock_instance = MagicMock()

        async def fake_run():
            return None
        mock_instance.run_stdio = fake_run
        mock_server_cls.return_value = mock_instance

        result = runner.invoke(cli, ["mcp", str(tmp_path)])
        assert result.exit_code == 0, result.output
        mock_get_client.assert_called_once()
        called_with = mock_get_client.call_args[0][0]
        assert Path(called_with).resolve() == tmp_path.resolve()


def test_mcp_cli_resolves_vault_from_env(tmp_path):
    """LLM_WIKI_VAULT takes priority over the positional arg."""
    from llm_wiki.cli.main import cli

    (tmp_path / "wiki").mkdir()

    runner = CliRunner(env={"LLM_WIKI_VAULT": str(tmp_path)})
    with patch("llm_wiki.cli.main._get_client") as mock_get_client, \
         patch("llm_wiki.mcp.server.MCPServer") as mock_server_cls:
        mock_get_client.return_value = MagicMock()
        mock_instance = MagicMock()

        async def fake_run():
            return None
        mock_instance.run_stdio = fake_run
        mock_server_cls.return_value = mock_instance

        result = runner.invoke(cli, ["mcp"])
        assert result.exit_code == 0, result.output
        called_with = mock_get_client.call_args[0][0]
        assert Path(called_with).resolve() == tmp_path.resolve()


def test_mcp_cli_errors_when_no_vault():
    """Exits with a clear error if neither env var nor positional arg is set."""
    from llm_wiki.cli.main import cli

    runner = CliRunner(env={"LLM_WIKI_VAULT": ""})
    result = runner.invoke(cli, ["mcp"])
    assert result.exit_code != 0
    assert "vault" in result.output.lower()
