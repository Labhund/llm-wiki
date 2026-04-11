"""Tests that handle_file_changes only dispatches wiki/ files to compliance."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.daemon.server import DaemonServer


def _make_server(tmp_path: Path) -> DaemonServer:
    """Create a minimal DaemonServer with a mock dispatcher."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    (tmp_path / "raw").mkdir(exist_ok=True)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "schema").mkdir(exist_ok=True)

    config = WikiConfig(
        maintenance=MaintenanceConfig(
            compliance_debounce_secs=0.5,
            auditor_interval="1h",
        ),
        vault=VaultConfig(wiki_dir="wiki/"),
    )
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(tmp_path, sock_path, config=config)
    # Attach a mock dispatcher so we can observe submit calls
    server._dispatcher = MagicMock()
    return server


@pytest.mark.asyncio
async def test_wiki_files_dispatched(tmp_path: Path):
    """Files under wiki/ must be submitted to the dispatcher."""
    server = _make_server(tmp_path)
    wiki_page = tmp_path / "wiki" / "page.md"
    wiki_page.write_text("---\ntitle: Test\n---\nContent.\n")

    await server.handle_file_changes([wiki_page], [])
    server._dispatcher.submit.assert_called_once_with(wiki_page)


@pytest.mark.asyncio
async def test_raw_files_rejected(tmp_path: Path):
    """Files under raw/ must NOT be submitted to the dispatcher."""
    server = _make_server(tmp_path)
    raw_file = tmp_path / "raw" / "source.pdf"
    raw_file.write_bytes(b"%PDF-1.4 fake")

    await server.handle_file_changes([raw_file], [])
    server._dispatcher.submit.assert_not_called()


@pytest.mark.asyncio
async def test_inbox_files_rejected(tmp_path: Path):
    """Files under inbox/ must NOT be submitted to the dispatcher."""
    server = _make_server(tmp_path)
    inbox_file = tmp_path / "inbox" / "draft.md"
    inbox_file.write_text("draft content")

    await server.handle_file_changes([inbox_file], [])
    server._dispatcher.submit.assert_not_called()


@pytest.mark.asyncio
async def test_schema_files_rejected(tmp_path: Path):
    """Files under schema/ must NOT be submitted to the dispatcher."""
    server = _make_server(tmp_path)
    schema_file = tmp_path / "schema" / "model.json"
    schema_file.write_text("{}")

    await server.handle_file_changes([schema_file], [])
    server._dispatcher.submit.assert_not_called()


@pytest.mark.asyncio
async def test_hidden_dirs_rejected(tmp_path: Path):
    """Files under hidden dirs (e.g. .issues) must NOT be submitted."""
    server = _make_server(tmp_path)
    hidden_dir = tmp_path / ".issues"
    hidden_dir.mkdir()
    hidden_file = hidden_dir / "note.md"
    hidden_file.write_text("issue note")

    await server.handle_file_changes([hidden_file], [])
    server._dispatcher.submit.assert_not_called()


@pytest.mark.asyncio
async def test_mixed_changes_only_wiki_dispatched(tmp_path: Path):
    """When mixed files change, only wiki/ files are dispatched."""
    server = _make_server(tmp_path)

    wiki_page = tmp_path / "wiki" / "page.md"
    wiki_page.write_text("---\ntitle: Test\n---\nContent.\n")
    raw_file = tmp_path / "raw" / "source.pdf"
    raw_file.write_bytes(b"%PDF")
    inbox_file = tmp_path / "inbox" / "draft.md"
    inbox_file.write_text("draft")
    hidden_dir = tmp_path / ".issues"
    hidden_dir.mkdir()
    hidden_file = hidden_dir / "note.md"
    hidden_file.write_text("note")

    await server.handle_file_changes(
        [wiki_page, raw_file, inbox_file, hidden_file], []
    )
    assert server._dispatcher.submit.call_count == 1
    server._dispatcher.submit.assert_called_once_with(wiki_page)
