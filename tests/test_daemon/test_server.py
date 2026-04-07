import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

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


@pytest.mark.asyncio
async def test_query(daemon_server, sample_vault, monkeypatch):
    """Query route returns a synthesized answer and persists log to state dir."""
    from llm_wiki.vault import _state_dir_for

    server, sock_path = daemon_server

    responses = iter([
        # Turn 0: manifest analysis — model is selective, picks one page
        json.dumps({
            "salient_points": "Manifest mentions sRNA validation page",
            "remaining_questions": [],
            "next_candidates": [],
            "hypothesis": "sRNA validation uses PCA and clustering",
            "answer_complete": True,
        }),
        # Synthesis
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

    resp = await _request(sock_path, {
        "type": "query",
        "question": "How are sRNA embeddings validated?",
    })
    assert resp["status"] == "ok"
    assert "sRNA" in resp["answer"]
    assert "srna-embeddings" in resp["citations"]
    assert resp["outcome"] == "complete"
    assert "log" in resp

    # Verify the log was persisted to the state directory for the librarian
    log_file = _state_dir_for(sample_vault) / "traversal_logs" / "traversal_logs.jsonl"
    assert log_file.exists()
    line = log_file.read_text().strip()
    parsed = json.loads(line)
    assert parsed["query"] == "How are sRNA embeddings validated?"
    assert parsed["outcome"] == "complete"


@pytest.mark.asyncio
async def test_query_missing_question(daemon_server):
    """Query route returns clean error when 'question' field is missing."""
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "query"})
    assert resp["status"] == "error"
    assert "question" in resp["message"]
    assert "Missing required field" in resp["message"]
