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
        mock_resp.usage.prompt_tokens = 80
        mock_resp.usage.completion_tokens = 20
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


def test_get_client_reports_daemon_exit_immediately(tmp_path, monkeypatch):
    """_get_client() reports the daemon's stderr immediately when it exits, not after 30s."""
    import sys
    from subprocess import Popen as RealPopen, DEVNULL
    import pytest

    # Create a minimal vault
    (tmp_path / "wiki").mkdir()
    (tmp_path / "schema").mkdir()

    # Script that exits immediately with an error message on stderr
    exit_script = tmp_path / "bad_daemon.py"
    exit_script.write_text('import sys; print("vault config missing", file=sys.stderr); sys.exit(1)')

    from llm_wiki.daemon.client import DaemonClient

    class FakePopenFast:
        """Subprocess that exits immediately, writing error to the provided stderr."""
        def __init__(self, cmd, *, start_new_session, stdout, stderr, **kwargs):
            # Run the bad_daemon script, writing to the provided stderr fd
            self._proc = RealPopen(
                [sys.executable, str(exit_script)],
                stderr=stderr,
                stdout=DEVNULL,
                start_new_session=False,
            )

        def poll(self):
            return self._proc.poll()

    monkeypatch.setattr("llm_wiki.cli.main.subprocess.Popen", FakePopenFast)
    monkeypatch.setattr(DaemonClient, "is_running", lambda self: False)

    from llm_wiki.cli.main import _get_client
    import click

    with pytest.raises(click.ClickException) as exc_info:
        _get_client(tmp_path)

    error_msg = exc_info.value.format_message()
    assert "vault config missing" in error_msg


def test_ingest_dry_run_output(daemon_for_cli, monkeypatch, tmp_path):
    """Dry-run output shows concept list without section details."""
    from llm_wiki.daemon.client import DaemonClient

    def fake_request(self, msg, timeout=30.0):
        if msg.get("type") != "ingest":
            return {"status": "ok"}
        return {
            "status": "ok",
            "dry_run": True,
            "source_path": msg["source_path"],
            "source_chars": 1000,
            "extraction_warning": None,
            "concepts_found": 2,
            "concepts": [
                {"name": "pca", "title": "PCA", "action": "create", "passage_count": 3},
                {"name": "k-means", "title": "K-Means", "action": "update", "passage_count": 2},
            ],
        }

    monkeypatch.setattr(DaemonClient, "request", fake_request)

    vault_path = daemon_for_cli
    source = vault_path / "test.md"
    source.write_text("# Test")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--dry-run", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "DRY RUN — test.md (1,000 chars)" in result.output
    assert "[NEW]" in result.output
    assert "[UPD]" in result.output
    assert "  2 concepts total" in result.output
    assert "section" not in result.output
    assert "content_chars" not in result.output


def test_ingest_streaming_output(daemon_for_cli, monkeypatch, tmp_path):
    """CLI ingest prints [PROGRESS], [DONE], [SUMMARY] lines (non-TTY mode)."""
    from llm_wiki.daemon.client import DaemonClient

    def fake_stream(self, msg, on_frame):
        on_frame({"type": "progress", "stage": "extracting"})
        on_frame({"type": "progress", "stage": "concepts_found", "count": 2})
        on_frame({
            "type": "progress", "stage": "concept_done",
            "name": "boltz-diffusion", "title": "Boltz Diffusion", "action": "created",
            "num": 1, "total": 2,
        })
        on_frame({
            "type": "progress", "stage": "concept_done",
            "name": "structure-prediction", "title": "Structure Prediction", "action": "updated",
            "num": 2, "total": 2,
        })
        on_frame({
            "type": "done", "status": "ok",
            "pages_created": 1, "pages_updated": 1,
            "created": ["boltz-diffusion"], "updated": ["structure-prediction"],
            "concepts_found": 2,
        })

    monkeypatch.setattr(DaemonClient, "stream_ingest_sync", fake_stream)

    vault_path = daemon_for_cli
    source = vault_path / "paper.md"
    source.write_text("# Test paper")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault_path)])

    assert result.exit_code == 0, result.output
    assert "[PROGRESS] concepts_found: 2" in result.output
    assert "[DONE] boltz-diffusion (created)" in result.output
    assert "[DONE] structure-prediction (updated)" in result.output
    assert "[SUMMARY] 1 created, 1 updated" in result.output


def test_ingest_streaming_error_frame_exits_nonzero(daemon_for_cli, monkeypatch, tmp_path):
    """Error frame mid-stream causes non-zero exit with concepts_written in message."""
    from llm_wiki.daemon.client import DaemonClient

    def fake_stream(self, msg, on_frame):
        on_frame({"type": "progress", "stage": "extracting"})
        on_frame({"type": "progress", "stage": "concepts_found", "count": 3})
        on_frame({
            "type": "progress", "stage": "concept_done",
            "name": "topic-a", "title": "Topic A", "action": "created",
            "num": 1, "total": 3,
        })
        on_frame({
            "type": "error", "status": "error",
            "message": "LLM timeout",
            "concepts_written": 1,
        })

    monkeypatch.setattr(DaemonClient, "stream_ingest_sync", fake_stream)

    vault_path = daemon_for_cli
    source = vault_path / "paper.md"
    source.write_text("# Test paper")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault_path)])

    assert result.exit_code != 0
    assert "LLM timeout" in result.output
    assert "1 concept(s) written before error" in result.output


def test_ingest_auto_copies_source_outside_vault(daemon_for_cli, monkeypatch, tmp_path):
    """Source file outside vault is auto-copied to raw/ before sending to daemon."""
    from llm_wiki.daemon.client import DaemonClient
    captured_paths: list[str] = []

    def fake_stream(self, msg, on_frame):
        captured_paths.append(msg.get("source_path", ""))
        on_frame({"type": "done", "pages_created": 0, "pages_updated": 0, "warnings": []})

    monkeypatch.setattr(DaemonClient, "stream_ingest_sync", fake_stream)

    vault_path = daemon_for_cli
    # Source is OUTSIDE the vault — place it in the parent of tmp_path
    # (daemon_for_cli uses the same tmp_path, so vault_path IS tmp_path)
    source = tmp_path.parent / "outside-paper.pdf"
    source.write_bytes(b"%PDF")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output

    # The path sent to daemon should point into raw/
    assert captured_paths, "stream_ingest_sync was not called"
    sent_path = Path(captured_paths[0])
    assert sent_path.is_relative_to(vault_path / "raw"), (
        f"Expected path inside vault/raw/, got {sent_path}"
    )
    # And the file should actually be there
    assert (vault_path / "raw" / "outside-paper.pdf").exists()


def test_cli_ingest_sends_proposal_mode(daemon_for_cli, monkeypatch):
    """The ingest command sends proposal_mode=True in its request."""
    from llm_wiki.daemon.client import DaemonClient

    sent_msgs: list[dict] = []

    def fake_stream(self, msg, on_frame):
        sent_msgs.append(msg)
        on_frame({"type": "done", "pages_created": 0, "pages_updated": 0, "warnings": []})

    monkeypatch.setattr(DaemonClient, "stream_ingest_sync", fake_stream)

    vault_path = daemon_for_cli
    source = vault_path / "raw" / "paper.pdf"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"%PDF")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert sent_msgs, "stream_ingest_sync was not called"
    assert sent_msgs[0].get("proposal_mode") is True


def test_proposals_list_shows_pending(daemon_for_cli, monkeypatch):
    """proposals list outputs pending proposal info."""
    from llm_wiki.daemon.client import DaemonClient

    monkeypatch.setattr(DaemonClient, "request", lambda self, msg: {
        "status": "ok",
        "proposals": [{"path": "inbox/proposals/2026-04-10-paper-boltz-2.md",
                       "target_page": "boltz-2", "action": "update", "status": "pending",
                       "source": "raw/paper.pdf"}],
    })

    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["proposals", "list", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "boltz-2" in result.output
