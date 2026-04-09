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


@pytest.mark.asyncio
async def test_daemon_registers_talk_summary_worker(daemon_server):
    """The daemon's scheduler includes a talk_summary worker after Phase 6a."""
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "scheduler-status"})
    assert resp["status"] == "ok"
    worker_names = [w["name"] for w in resp["workers"]]
    assert "talk_summary" in worker_names


@pytest_asyncio.fixture
async def phase6a_daemon_server(sample_vault: Path, tmp_path: Path):
    """Daemon server for Phase 6a tests where wiki_dir == vault_root.

    Uses wiki_dir="" so _read_talk_block can rglob cluster subdirectories.
    Clears any auditor-created issues after start so tests begin with a clean
    issue queue (the scheduler runs workers immediately on start).
    """
    import shutil
    from llm_wiki.config import VaultConfig, WikiConfig
    sock_path = tmp_path / "p6a.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir=""))
    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    # Yield control so scheduler tasks can fire, then clear auditor noise.
    await asyncio.sleep(0)
    issues_dir = sample_vault / ".issues"
    if issues_dir.exists():
        shutil.rmtree(issues_dir)
    yield server, sock_path
    await server.stop()


@pytest.mark.asyncio
async def test_read_includes_empty_issues_and_talk_blocks(phase6a_daemon_server):
    """Reading a page with no issues / no talk page returns well-shaped empty blocks."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert "issues" in resp
    assert "talk" in resp
    assert resp["issues"]["open_count"] == 0
    assert resp["issues"]["items"] == []
    assert resp["talk"]["entry_count"] == 0
    assert resp["talk"]["open_count"] == 0
    assert resp["talk"]["recent_critical"] == []
    assert resp["talk"]["recent_moderate"] == []


@pytest.mark.asyncio
async def test_read_includes_open_issues(phase6a_daemon_server, sample_vault):
    """When the page has an open issue, it shows up in the read response."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    queue = IssueQueue(sample_vault)
    queue.add(Issue(
        id=Issue.make_id("broken-link", "srna-embeddings", "fake-target"),
        type="broken-link",
        status="open",
        severity="moderate",
        title="Fake broken link",
        page="srna-embeddings",
        body="A test issue.",
        created=Issue.now_iso(),
        detected_by="auditor",
    ))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["issues"]["open_count"] == 1
    assert resp["issues"]["by_severity"]["moderate"] == 1
    assert resp["issues"]["items"][0]["title"] == "Fake broken link"


@pytest.mark.asyncio
async def test_read_inlines_critical_talk_entries(phase6a_daemon_server, sample_vault):
    """Critical and moderate talk entries appear verbatim in `recent_*`."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(
        0, "2026-04-08T10:00:00+00:00", "@adversary",
        "A critical contradiction.", severity="critical",
    ))
    talk.append(TalkEntry(
        0, "2026-04-08T10:01:00+00:00", "@compliance",
        "A moderate concern.", severity="moderate",
    ))
    talk.append(TalkEntry(
        0, "2026-04-08T10:02:00+00:00", "@user",
        "A casual suggestion.", severity="suggestion",
    ))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["talk"]["entry_count"] == 3
    assert resp["talk"]["open_count"] == 3
    assert len(resp["talk"]["recent_critical"]) == 1
    assert resp["talk"]["recent_critical"][0]["body"] == "A critical contradiction."
    assert len(resp["talk"]["recent_moderate"]) == 1
    assert resp["talk"]["recent_moderate"][0]["body"] == "A moderate concern."


@pytest.mark.asyncio
async def test_read_excludes_resolved_talk_entries_from_counts(phase6a_daemon_server, sample_vault):
    """Resolved entries don't count toward open_count or by_severity."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(0, "t1", "@adv", "first", severity="critical"))
    talk.append(TalkEntry(0, "t2", "@user", "closes 1", resolves=[1]))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["talk"]["entry_count"] == 2
    assert resp["talk"]["open_count"] == 1
    assert resp["talk"]["recent_critical"] == []


@pytest.mark.asyncio
async def test_search_route_returns_matches_array(phase6a_daemon_server):
    """The enriched search route attaches a matches array to each result."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {"type": "search", "query": "k-means", "limit": 5})
    assert resp["status"] == "ok"
    assert resp["results"]
    for r in resp["results"]:
        assert "matches" in r
        assert isinstance(r["matches"], list)
        for m in r["matches"]:
            assert "line" in m
            assert "before" in m
            assert "match" in m
            assert "after" in m


@pytest.mark.asyncio
async def test_lint_response_includes_attention_map(phase6a_daemon_server):
    """The lint route response carries an attention_map block."""
    server, sock_path = phase6a_daemon_server
    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    assert "attention_map" in resp
    am = resp["attention_map"]
    assert "pages_needing_attention" in am
    assert "totals" in am
    assert "by_page" in am
    assert "issues" in am["totals"]
    assert "talk" in am["totals"]
    for severity in ("critical", "moderate", "minor"):
        assert severity in am["totals"]["issues"]
    for severity in ("critical", "moderate", "minor", "suggestion", "new_connection"):
        assert severity in am["totals"]["talk"]


@pytest.mark.asyncio
async def test_lint_attention_map_aggregates_issue_severities(phase6a_daemon_server, sample_vault):
    """An open critical issue raises the totals.issues.critical count."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    queue = IssueQueue(sample_vault)
    queue.add(Issue(
        id=Issue.make_id("broken-citation", "srna-embeddings", "raw/missing.pdf"),
        type="broken-citation",
        status="open",
        severity="critical",
        title="Missing source",
        page="srna-embeddings",
        body="A test critical issue.",
        created=Issue.now_iso(),
        detected_by="auditor",
        metadata={"target": "raw/missing.pdf"},
    ))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    assert am["totals"]["issues"]["critical"] >= 1
    assert "srna-embeddings" in am["pages_needing_attention"]
    assert am["by_page"]["srna-embeddings"]["issues"]["critical"] >= 1


@pytest.mark.asyncio
async def test_lint_attention_map_aggregates_talk_severities(phase6a_daemon_server, sample_vault):
    """A critical talk entry raises the totals.talk.critical count."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(
        0, "2026-04-08T10:00:00+00:00", "@adv",
        "Critical talk entry", severity="critical",
    ))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    assert am["totals"]["talk"]["critical"] >= 1
    assert "srna-embeddings" in am["pages_needing_attention"]


@pytest.mark.asyncio
async def test_lint_attention_map_excludes_resolved_talk_entries(phase6a_daemon_server, sample_vault):
    """Resolved talk entries don't show up in the attention map counts."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(0, "t1", "@adv", "first", severity="critical"))
    talk.append(TalkEntry(0, "t2", "@user", "closes 1", resolves=[1]))

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    # The critical entry is resolved → must not be counted
    by_page = am["by_page"].get("srna-embeddings", {})
    talk_counts = by_page.get("talk", {})
    assert talk_counts.get("critical", 0) == 0


@pytest.mark.asyncio
async def test_issues_routes_include_severity(phase6a_daemon_server, sample_vault):
    """Both issues-list and issues-get carry the severity field."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    queue = IssueQueue(sample_vault)
    queue.add(Issue(
        id=Issue.make_id("broken-citation", "srna-embeddings", "raw/missing.pdf"),
        type="broken-citation",
        status="open",
        severity="critical",
        title="Missing source",
        page="srna-embeddings",
        body="A test issue.",
        created=Issue.now_iso(),
        detected_by="auditor",
    ))

    list_resp = await _request(sock_path, {"type": "issues-list"})
    assert list_resp["status"] == "ok"
    assert list_resp["issues"]
    for item in list_resp["issues"]:
        assert "severity" in item, f"issues-list dropped severity: {item}"

    get_resp = await _request(sock_path, {
        "type": "issues-get",
        "id": Issue.make_id("broken-citation", "srna-embeddings", "raw/missing.pdf"),
    })
    assert get_resp["status"] == "ok"
    assert "severity" in get_resp["issue"]
    assert get_resp["issue"]["severity"] == "critical"
