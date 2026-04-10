from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent, LibrarianResult
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    """Async LLM stub matching LLMClient.complete shape."""

    def __init__(self, response_text: str = '{"tags": [], "summary": null}') -> None:
        self.response = response_text
        # Each entry is {"messages": [...], "priority": str | None, "temperature": float}
        self.calls: list[dict] = []

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        self.calls.append({
            "messages": messages,
            "priority": priority,
            "temperature": temperature,
        })
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=0)


def _seed_log(state_dir: Path, entries: list[dict]) -> None:
    log_dir = state_dir / "traversal_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "traversal_logs.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.mark.asyncio
async def test_recalc_authority_writes_overrides_for_every_page(sample_vault: Path):
    """recalc_authority computes scores for every entry and persists them."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How does k-means work?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "uses k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    vault = Vault.scan(sample_vault)
    queue = IssueQueue(sample_vault / "wiki")  # may not exist; OK for this test
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), queue, WikiConfig())

    count = await agent.recalc_authority()

    assert count == vault.page_count

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    for name in vault.manifest_entries():
        override = overrides.get(name)
        assert override is not None, f"missing override for {name}"
        assert 0.0 <= override.authority <= 1.0


@pytest.mark.asyncio
async def test_recalc_authority_does_not_call_llm(sample_vault: Path):
    """recalc_authority is purely programmatic."""
    vault = Vault.scan(sample_vault)
    stub = _StubLLM()
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    await agent.recalc_authority()

    assert stub.calls == []


@pytest.mark.asyncio
async def test_recalc_authority_empty_vault(tmp_path: Path):
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    count = await agent.recalc_authority()
    assert count == 0


@pytest.mark.asyncio
async def test_recalc_authority_with_passed_usage_matches_self_loaded(sample_vault: Path):
    """Passing a pre-aggregated usage dict yields the same overrides as loading logs internally."""
    from llm_wiki.librarian.log_reader import aggregate_logs

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How does k-means work?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
        {
            "query": "What is clustering?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "clustering-metrics", "sections_read": [], "salient_points": "silhouette", "relevance": 0.7}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    overrides_path = state_dir / "manifest_overrides.json"

    # Run 1: recalc_authority() loads logs internally (default path)
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    await agent.recalc_authority()
    baseline = ManifestOverrides.load(overrides_path)
    baseline_scores = {name: baseline.get(name).authority for name in vault.manifest_entries()}

    # Wipe the overrides file so the second run writes from scratch
    overrides_path.unlink()

    # Run 2: recalc_authority(usage=...) with the same logs, loaded once externally
    log_path = state_dir / "traversal_logs" / "traversal_logs.jsonl"
    usage = aggregate_logs(log_path)
    await agent.recalc_authority(usage=usage)
    passed = ManifestOverrides.load(overrides_path)
    passed_scores = {name: passed.get(name).authority for name in vault.manifest_entries()}

    assert passed_scores == baseline_scores


@pytest.mark.asyncio
async def test_refresh_page_updates_overrides_with_llm_output(sample_vault: Path):
    """refresh_page calls the LLM and writes the parsed tags/summary."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How are sRNA embeddings validated?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": ["overview"], "salient_points": "PCA + k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM(
        '{"tags": ["embeddings", "validation", "k-means"], "summary": "Validates sRNA embeddings via PCA + k-means."}'
    )
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")

    assert refreshed is True
    assert len(stub.calls) == 1
    # Librarian LLM calls MUST be tagged as maintenance priority so they
    # can be throttled/backgrounded separately from user-facing queries.
    assert stub.calls[0]["priority"] == "maintenance"

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = overrides.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["embeddings", "validation", "k-means"]
    assert got.summary_override == "Validates sRNA embeddings via PCA + k-means."
    assert got.last_refreshed_read_count == 1   # one query in the seeded log


@pytest.mark.asyncio
async def test_refresh_page_unknown_page_returns_false(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    assert await agent.refresh_page("nope") is False


@pytest.mark.asyncio
async def test_refresh_page_invalid_llm_response_does_not_corrupt_overrides(sample_vault: Path):
    """If the LLM returns junk, the override is left unchanged."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["original"],
        summary_override="original summary",
        authority=0.5,
    ))
    overrides.save()

    stub = _StubLLM("complete garbage, not JSON")
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")
    assert refreshed is False

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["original"]
    assert got.summary_override == "original summary"


@pytest.mark.asyncio
async def test_recalc_authority_preserves_existing_tags_and_summary(sample_vault: Path):
    """recalc_authority must not clobber tags/summary set by prior refinement."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["preserved-tag"],
        summary_override="preserved summary",
        authority=0.0,
        read_count=12,
        last_refreshed_read_count=12,
    ))
    overrides.save()

    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    await agent.recalc_authority()

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["preserved-tag"]
    assert got.summary_override == "preserved summary"
    assert got.read_count == 12
    assert got.last_refreshed_read_count == 12


@pytest.mark.asyncio
async def test_run_refreshes_pages_above_threshold(sample_vault: Path):
    """A page with accumulated reads ≥ threshold gets refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Threshold is 3 in our test config
    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 3

    # 4 distinct queries reading srna-embeddings
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"point {i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(4)
    ])

    stub = _StubLLM('{"tags": ["validation"], "summary": "Refined."}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()

    assert isinstance(result, LibrarianResult)
    assert "srna-embeddings" in result.pages_refined
    assert result.authorities_updated == vault.page_count
    # The other fixture pages have zero reads, so they should NOT be refreshed
    assert "clustering-metrics" not in result.pages_refined


@pytest.mark.asyncio
async def test_run_skips_pages_below_threshold(sample_vault: Path):
    """A page with reads < threshold is not refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 10

    _seed_log(state_dir, [
        {
            "query": "q",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "x", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM('{"tags": ["x"], "summary": "y"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert result.pages_refined == []
    assert stub.calls == []  # no LLM calls
    assert result.authorities_updated == vault.page_count   # authority still recalculated


@pytest.mark.asyncio
async def test_run_uses_delta_since_last_refresh(sample_vault: Path):
    """A page already refreshed at read_count=10 is not re-refreshed at read_count=12 with threshold=5."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["existing"],
        last_refreshed_read_count=10,
    ))
    overrides.save()

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 5

    # Seed 12 distinct queries reading srna-embeddings (delta since last refresh = 2)
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"p{i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(12)
    ])

    stub = _StubLLM('{"tags": ["new"], "summary": "new summary"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert "srna-embeddings" not in result.pages_refined
    assert stub.calls == []


@pytest.mark.asyncio
async def test_run_empty_vault(tmp_path: Path):
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    result = await agent.run()
    assert result.pages_refined == []
    assert result.authorities_updated == 0


@pytest.mark.asyncio
async def test_refresh_talk_summaries_below_threshold_does_nothing(tmp_path):
    """A talk page with fewer than min_new_entries open entries is skipped."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault, _state_dir_for

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(wiki / "p.talk.md")
    # Two entries — below the default threshold of 5
    talk.append(TalkEntry(0, "t1", "@a", "first"))
    talk.append(TalkEntry(0, "t2", "@b", "second"))

    cfg = WikiConfig()
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("LLM should not be called below threshold")

    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0

    store = TalkSummaryStore.load(_state_dir_for(tmp_path) / "talk_summaries.json")
    assert store.get("p") is None


@pytest.mark.asyncio
async def test_refresh_talk_summaries_above_threshold_summarizes(tmp_path):
    """When open entries cross the threshold, the LLM is called and the
    summary is persisted to the store. The high-water mark is set to the
    max entry index in the file."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.traverse.llm_client import LLMResponse
    from llm_wiki.vault import Vault, _state_dir_for

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(wiki / "p.talk.md")
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    cfg = WikiConfig()  # threshold default = 5

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            return LLMResponse(content="Five open entries about validation.", input_tokens=10, output_tokens=0)

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, MockLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 1

    store = TalkSummaryStore.load(_state_dir_for(tmp_path) / "talk_summaries.json")
    record = store.get("p")
    assert record is not None
    assert "validation" in record.summary
    # high-water mark is the max entry index = 5
    assert record.last_max_index == 5


@pytest.mark.asyncio
async def test_refresh_talk_summaries_robust_to_intervening_closures(tmp_path):
    """Closures between runs lower the open count but should not mask new
    arrivals. The threshold counts entries with index > last_max_index that
    are still open — measuring arrivals, not net open state."""
    import datetime as _dt
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.traverse.llm_client import LLMResponse
    from llm_wiki.vault import Vault, _state_dir_for

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(wiki / "p.talk.md")

    # First run: 5 entries, all open → summarize, high-water = 5
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    cfg = WikiConfig()
    call_count = {"n": 0}

    class CountingLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            call_count["n"] += 1
            return LLMResponse(content="Summary text.", input_tokens=10, output_tokens=0)

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, CountingLLM(), queue, cfg)
    assert await agent.refresh_talk_summaries() == 1
    assert call_count["n"] == 1

    # Backdate the rate-limit timestamp so the next run isn't blocked by it
    state_dir = _state_dir_for(tmp_path)
    store = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    rec = store.get("p")
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    store.set("p", summary=rec.summary, last_max_index=rec.last_max_index, last_summary_ts=old_ts)
    store.save()

    # Second run: append 5 NEW entries (indices 6-10), then a closer that resolves 1-4
    for i in range(5, 10):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))
    talk.append(TalkEntry(
        0, "t-closer", "@closer", "closes 1-4", resolves=[1, 2, 3, 4],
    ))
    # Open count is now: entry 5 + entries 6-10 + closer = 7
    # Last summary high-water = 5
    # New entries with index > 5 that are open = entries 6-10 + closer = 6 → above threshold

    assert await agent.refresh_talk_summaries() == 1
    assert call_count["n"] == 2  # LLM called again because new arrivals exceeded threshold

    rec2 = store.load(state_dir / "talk_summaries.json").get("p")
    assert rec2 is not None
    assert rec2.last_max_index == 11  # max index in the file is now 11


@pytest.mark.asyncio
async def test_refresh_talk_summaries_excludes_resolved_entries(tmp_path):
    """Resolved entries are not counted toward the threshold."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(wiki / "p.talk.md")
    # Five entries, but four of them get resolved → only one open + the resolver
    for i in range(5):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))
    talk.append(TalkEntry(0, "t-close", "@closer", "closes 1-4", resolves=[1, 2, 3, 4]))

    # Threshold is 5; open entries = 2 (entry 5 + the closer) → below threshold
    cfg = WikiConfig()

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("LLM should not be called — open count is 2, below 5")

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0


@pytest.mark.asyncio
async def test_refresh_talk_summaries_rate_limit_blocks_resummary(tmp_path):
    """A page summarized within `talk_summary_min_interval_seconds` is skipped."""
    import datetime as _dt
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.vault import Vault, _state_dir_for

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\n## Body\n\ncontent\n")
    talk = TalkPage(wiki / "p.talk.md")
    for i in range(6):
        talk.append(TalkEntry(0, f"t{i}", f"@a{i}", f"entry {i}"))

    # Pre-populate the store with a recent summary covering only entry 1.
    # This leaves entries 2-6 (= 5 new arrivals) above the threshold, so the
    # threshold check would pass on its own. The recent timestamp must be
    # what blocks the resummary — that's the contract under test.
    state_dir = _state_dir_for(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    store = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    store.set("p", summary="recent", last_max_index=1, last_summary_ts=now_iso)
    store.save()

    cfg = WikiConfig()  # min_interval default = 3600s

    class UnusedLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("rate limit should block this call")

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, UnusedLLM(), queue, cfg)
    summarized = await agent.refresh_talk_summaries()
    assert summarized == 0


@pytest.mark.asyncio
async def test_refresh_talk_summaries_prunes_deleted_pages(tmp_path):
    """When a talk file is deleted, the next refresh prunes its store entry.

    Phase 6a P6A-I3 carryover: TalkSummaryStore was missing the prune step
    that ManifestOverrides has, so deleted-page records grew unbounded in
    the JSON sidecar.
    """
    from llm_wiki.config import WikiConfig
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.librarian.agent import LibrarianAgent
    from llm_wiki.librarian.talk_summary import TalkSummaryStore
    from llm_wiki.talk.page import TalkEntry, TalkPage
    from llm_wiki.traverse.llm_client import LLMResponse
    from llm_wiki.vault import Vault, _state_dir_for

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "alpha.md").write_text("---\ntitle: A\n---\n\n## Body\n\nx\n")
    (wiki / "beta.md").write_text("---\ntitle: B\n---\n\n## Body\n\ny\n")

    # Both pages get 5 open entries → both summarized on the first run.
    for slug in ("alpha", "beta"):
        talk = TalkPage(wiki / f"{slug}.talk.md")
        for i in range(5):
            talk.append(TalkEntry(0, f"t{i}", f"@u{i}", f"entry {i}"))

    cfg = WikiConfig()

    class StubLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance"):
            return LLMResponse(content="Stub summary.", input_tokens=10, output_tokens=0)

    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, StubLLM(), queue, cfg)

    assert await agent.refresh_talk_summaries() == 2

    state_dir = _state_dir_for(tmp_path)
    store = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    assert store.get("alpha") is not None
    assert store.get("beta") is not None

    # Delete one talk file (and the page itself, to cover the realistic
    # case of removing a page from the wiki).
    (wiki / "alpha.talk.md").unlink()
    (wiki / "alpha.md").unlink()

    # Even though `beta` is rate-limited and won't be re-summarized, the
    # refresh pass should still prune the deleted page from the store.
    refreshed = await agent.refresh_talk_summaries()
    assert refreshed == 0  # beta blocked by rate limit; alpha deleted

    store2 = TalkSummaryStore.load(state_dir / "talk_summaries.json")
    assert store2.get("alpha") is None, "deleted page should be pruned"
    assert store2.get("beta") is not None, "live page should remain"
