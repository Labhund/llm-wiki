import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


@pytest_asyncio.fixture
async def daemon_server(sample_vault: Path, tmp_path: Path):
    """Start a daemon server on a temp socket for testing."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    yield server, sock_path
    await server.stop()


async def _request(sock_path: Path, msg: dict) -> dict:
    """Send a request and return the response."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_search(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1


@pytest.mark.asyncio
async def test_read_top(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()


@pytest.mark.asyncio
async def test_read_section(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "section": "method",
    })
    assert resp["status"] == "ok"
    assert "PCA" in resp["content"]


@pytest.mark.asyncio
async def test_read_missing(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "nonexistent",
    })
    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_manifest(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "manifest", "budget": 5000})
    assert resp["status"] == "ok"
    assert len(resp["content"]) > 0


@pytest.mark.asyncio
async def test_status(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "status"})
    assert resp["status"] == "ok"
    assert resp["page_count"] == 4


@pytest.mark.asyncio
async def test_unknown_request(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "bogus"})
    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_concurrent_requests(daemon_server):
    """Multiple clients can connect simultaneously."""
    server, sock_path = daemon_server
    results = await asyncio.gather(
        _request(sock_path, {"type": "status"}),
        _request(sock_path, {"type": "search", "query": "sRNA"}),
        _request(sock_path, {"type": "manifest", "budget": 1000}),
    )
    assert all(r["status"] == "ok" for r in results)
