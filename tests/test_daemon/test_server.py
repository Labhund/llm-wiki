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
    Registers no maintenance workers (enabled_workers=set()) so the scheduler
    doesn't race with the test body — the test fixture's job is to set up a
    quiescent daemon, and waiting for "the auditor finished writing then
    rmtree'd its output" is fragile the moment any worker body grows an
    `await`. P6A-I6 carryover.
    """
    from llm_wiki.config import VaultConfig, WikiConfig
    sock_path = tmp_path / "p6a.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir=""))
    server = DaemonServer(
        sample_vault, sock_path, config=config, enabled_workers=set(),
    )
    await server.start()
    yield server, sock_path
    await server.stop()


@pytest.mark.asyncio
async def test_enabled_workers_filters_registration(sample_vault: Path, tmp_path: Path):
    """P6A-I6: enabled_workers narrows scheduler registration.

    None (default) registers all workers. An explicit set registers only
    those workers. An empty set registers nothing — useful for tests that
    want a quiescent daemon with no background side effects.
    """
    from llm_wiki.config import VaultConfig, WikiConfig

    config = WikiConfig(vault=VaultConfig(wiki_dir=""))

    # Default: all workers.
    sock1 = tmp_path / "all.sock"
    server_all = DaemonServer(sample_vault, sock1, config=config)
    await server_all.start()
    try:
        all_names = set(server_all._scheduler.worker_names)
        assert all_names == {
            "auditor", "librarian", "authority_recalc",
            "adversary", "talk_summary",
        }
    finally:
        await server_all.stop()

    # Explicit subset.
    sock2 = tmp_path / "subset.sock"
    server_sub = DaemonServer(
        sample_vault, sock2, config=config,
        enabled_workers={"talk_summary", "auditor"},
    )
    await server_sub.start()
    try:
        assert set(server_sub._scheduler.worker_names) == {"talk_summary", "auditor"}
    finally:
        await server_sub.stop()

    # Empty set: nothing registered. Note: we can't check the filesystem
    # for "no issues created" because sample_vault is session-scoped and
    # an earlier sub-test in this same function already ran the auditor.
    # The worker_names assertion is sufficient — no registered worker
    # means no execution path can fire.
    sock3 = tmp_path / "none.sock"
    server_none = DaemonServer(
        sample_vault, sock3, config=config, enabled_workers=set(),
    )
    await server_none.start()
    try:
        assert server_none._scheduler.worker_names == []
        assert server_none._scheduler.last_run_iso("auditor") is None
    finally:
        await server_none.stop()


@pytest.mark.asyncio
async def test_enabled_workers_unknown_name_raises(sample_vault: Path, tmp_path: Path):
    """An unknown worker name in enabled_workers should fail loudly at start.

    Silently ignoring would let typos hide for months. Better to surface
    the bad name when the daemon starts than to wonder why a worker never ran.
    """
    from llm_wiki.config import VaultConfig, WikiConfig

    config = WikiConfig(vault=VaultConfig(wiki_dir=""))
    sock_path = tmp_path / "bad.sock"
    server = DaemonServer(
        sample_vault, sock_path, config=config,
        enabled_workers={"talk_summary", "not_a_real_worker"},
    )
    with pytest.raises(ValueError, match="not_a_real_worker"):
        await server.start()
    # Cleanup in case start() partially succeeded.
    try:
        await server.stop()
    except Exception:
        pass


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
    assert resp["issues"]["n"] == 0
    assert resp["issues"]["items"] == []
    assert resp["talk"]["cnt"] == 0
    assert resp["talk"]["open"] == 0
    assert resp["talk"]["crit"] == []
    assert resp["talk"]["mod"] == []


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
    assert resp["issues"]["n"] == 1
    assert resp["issues"]["sev"]["moderate"] == 1
    assert resp["issues"]["items"][0]["title"] == "Fake broken link"


@pytest.mark.asyncio
async def test_read_inlines_critical_talk_entries(phase6a_daemon_server, sample_vault):
    """Critical and moderate talk entries appear verbatim in `crit`/`mod`."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "wiki" / "bioinformatics" / "srna-embeddings.md"
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
    assert resp["talk"]["cnt"] == 3
    assert resp["talk"]["open"] == 3
    assert len(resp["talk"]["crit"]) == 1
    assert resp["talk"]["crit"][0]["body"] == "A critical contradiction."
    assert len(resp["talk"]["mod"]) == 1
    assert resp["talk"]["mod"][0]["body"] == "A moderate concern."


@pytest.mark.asyncio
async def test_read_excludes_resolved_talk_entries_from_counts(phase6a_daemon_server, sample_vault):
    """Resolved entries don't count toward open or sev."""
    from llm_wiki.talk.page import TalkEntry, TalkPage

    server, sock_path = phase6a_daemon_server
    page_path = sample_vault / "wiki" / "bioinformatics" / "srna-embeddings.md"
    talk = TalkPage.for_page(page_path)
    talk.append(TalkEntry(0, "t1", "@adv", "first", severity="critical"))
    talk.append(TalkEntry(0, "t2", "@user", "closes 1", resolves=[1]))

    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert resp["talk"]["cnt"] == 2
    assert resp["talk"]["open"] == 1
    assert resp["talk"]["crit"] == []


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
    page_path = sample_vault / "wiki" / "bioinformatics" / "srna-embeddings.md"
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
    page_path = sample_vault / "wiki" / "bioinformatics" / "srna-embeddings.md"
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


@pytest.mark.asyncio
async def test_attention_map_clamps_unknown_severity_to_minor(phase6a_daemon_server, sample_vault):
    """An issue with a non-vocabulary severity is clamped to 'minor' in the totals."""
    from llm_wiki.issues.queue import Issue, IssueQueue

    server, sock_path = phase6a_daemon_server
    queue = IssueQueue(sample_vault)
    # Hand-craft an issue file bypassing the dataclass type-check, by writing
    # the YAML directly with an out-of-vocabulary severity.
    queue.add(Issue(
        id=Issue.make_id("broken-citation", "srna-embeddings", "raw/x.pdf"),
        type="broken-citation",
        status="open",
        severity="critical",  # legitimate value to satisfy the dataclass
        title="Test",
        page="srna-embeddings",
        body="b",
        created=Issue.now_iso(),
        detected_by="auditor",
    ))
    # Then patch the on-disk file to have a typo, which is the realistic
    # "human edited the YAML and made a typo" scenario.
    issue_id = Issue.make_id("broken-citation", "srna-embeddings", "raw/x.pdf")
    issue_file = sample_vault / ".issues" / f"{issue_id}.md"
    text = issue_file.read_text(encoding="utf-8")
    text = text.replace("severity: critical", "severity: bogus")
    issue_file.write_text(text, encoding="utf-8")

    resp = await _request(sock_path, {"type": "lint"})
    assert resp["status"] == "ok"
    am = resp["attention_map"]
    # The bogus severity should NOT have created a new key in totals
    assert set(am["totals"]["issues"].keys()) == {"critical", "moderate", "minor"}
    # And the count should land in "minor" (the clamp default for issues)
    assert am["totals"]["issues"]["minor"] >= 1
