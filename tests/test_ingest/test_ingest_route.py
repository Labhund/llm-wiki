from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


@pytest_asyncio.fixture
async def server_with_ingest(sample_vault: Path, tmp_path: Path):
    """Daemon server for testing ingest route."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    yield server, sock_path
    await server.stop()


async def _request(sock_path: Path, msg: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_ingest_route_missing_file_returns_ok(server_with_ingest):
    """Ingest with non-existent file returns ok with empty pages (extraction fails gracefully)."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {
        "type": "ingest",
        "source_path": "/nonexistent/file.md",
    })
    assert resp["status"] == "ok"
    assert "pages_created" in resp
    assert resp["pages_created"] == []


@pytest.mark.asyncio
async def test_ingest_route_missing_source_path(server_with_ingest):
    """Missing source_path field returns an error."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {"type": "ingest"})
    assert resp["status"] == "error"
    assert "source_path" in resp["message"]
