from __future__ import annotations

import time
from pathlib import Path

import pytest

from llm_wiki.adversary.agent import AdversaryAgent, AdversaryResult
from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides
from llm_wiki.talk.page import TalkPage
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    """Async LLM stub returning a scripted verdict response."""

    def __init__(self, response_text: str) -> None:
        self.response = response_text
        self.calls: list = []

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query", **kwargs):
        from llm_wiki.traverse.llm_client import LLMResponse
        self.calls.append((messages, priority))
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=0)


def _build_vault_with_one_claim(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tiny vault with one page citing one raw markdown file.

    Returns (vault_root, page_path). Using markdown for the raw source
    avoids the liteparse dependency in tests (Phase 4 extract_text reads
    .md files directly).
    """
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "smith-2026.md").write_text(
        "# Smith 2026\n\nThe k-means algorithm uses k=10 clusters.\n"
    )

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page = wiki_dir / "k-means.md"
    page.write_text(
        "---\ntitle: K-Means\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm uses k=10 clusters [[raw/smith-2026.md]].\n"
    )
    return tmp_path, page


@pytest.fixture
def _clean_state():
    """Clean up vault state dirs created during agent tests."""
    created: list[Path] = []
    yield created
    import shutil
    for d in created:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_adversary_validated_updates_last_corroborated(tmp_path: Path, _clean_state):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=5),
    )

    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.95, "explanation": "Source matches exactly."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()

    assert isinstance(result, AdversaryResult)
    assert result.claims_checked == 1
    assert len(result.validated) == 1
    assert result.failed == []
    assert stub.calls[0][1] == "maintenance"

    overrides = ManifestOverrides.load(_state_dir_for(vault_root) / "manifest_overrides.json")
    page_override = overrides.get("k-means")
    assert page_override is not None
    assert page_override.last_corroborated is not None


@pytest.mark.asyncio
async def test_adversary_contradicted_files_issue(tmp_path: Path, _clean_state):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM(
        '{"verdict": "contradicted", "confidence": 0.9, "explanation": "Source says k=5 not k=10."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.failed) == 1
    assert len(result.issues_filed) >= 1
    assert stub.calls[0][1] == "maintenance"

    issue = queue.get(result.issues_filed[0])
    assert issue is not None
    assert issue.type == "claim-failed"
    assert issue.detected_by == "adversary"
    assert "k=5" in issue.body


@pytest.mark.asyncio
async def test_adversary_unsupported_files_issue(tmp_path: Path, _clean_state):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM(
        '{"verdict": "unsupported", "confidence": 0.8, "explanation": "Claim not in source."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.failed) == 1
    assert len(result.issues_filed) >= 1
    assert stub.calls[0][1] == "maintenance"


@pytest.mark.asyncio
async def test_adversary_ambiguous_posts_to_talk_page(tmp_path: Path, _clean_state):
    vault_root, page_path = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM(
        '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Source unclear."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.talk_posts) == 1
    assert stub.calls[0][1] == "maintenance"

    talk = TalkPage.for_page(page_path)
    assert talk.exists
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].author == "@adversary"
    assert "Source unclear" in entries[0].body

    # Parent page should have the discovery marker
    page_text = page_path.read_text(encoding="utf-8")
    assert "%% talk: [[k-means.talk]] %%" in page_text


@pytest.mark.asyncio
async def test_adversary_skips_when_raw_source_missing(tmp_path: Path, _clean_state):
    """If the cited raw file does not exist, the claim is skipped."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "p.md").write_text(
        "---\ntitle: P\n---\n\n%% section: method %%\n## Method\n\n"
        "Claim [[raw/missing.md]].\n"
    )
    _clean_state.append(_state_dir_for(tmp_path))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path / "wiki")
    agent = AdversaryAgent(vault, tmp_path, stub, queue, config)

    result = await agent.run()
    assert result.claims_checked == 0
    assert result.validated == []
    assert stub.calls == []  # never called the LLM


@pytest.mark.asyncio
async def test_adversary_empty_vault(tmp_path: Path, _clean_state):
    _clean_state.append(_state_dir_for(tmp_path))
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    config = WikiConfig()
    agent = AdversaryAgent(
        vault, tmp_path, _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}'),
        IssueQueue(tmp_path / "wiki"), config,
    )
    result = await agent.run()
    assert result.claims_checked == 0
    assert result.validated == []
    assert result.failed == []


@pytest.mark.asyncio
async def test_adversary_unparseable_response_skips_claim(tmp_path: Path, _clean_state):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM("complete garbage, not JSON")
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    # The claim was attempted but verdict could not be parsed
    assert result.claims_checked == 1
    assert result.validated == []
    assert result.failed == []


@pytest.mark.asyncio
async def test_adversary_talk_post_carries_critical_severity(tmp_path: Path, _clean_state):
    """When the adversary posts an ambiguous verdict to a talk page, the entry's
    severity is 'critical' — surfaced inline in wiki_read so the agent sees it."""
    vault_root, page_path = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM(
        '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Source unclear."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.talk_posts) == 1

    # The adversary should have posted to the talk page with severity=critical.
    talk = TalkPage.for_page(page_path)
    entries = talk.load()
    assert len(entries) >= 1
    assert any(e.severity == "critical" for e in entries)


@pytest.mark.asyncio
async def test_adversary_failed_verdict_files_critical_issue(tmp_path: Path, _clean_state):
    """A contradicted/unsupported verdict files an issue with severity='critical'."""
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))

    stub = _StubLLM(
        '{"verdict": "contradicted", "confidence": 0.9, "explanation": "wrong"}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()

    failed_issues = [i for i in queue.list(status="open") if i.type == "claim-failed"]
    assert failed_issues, "expected at least one claim-failed issue"
    for issue in failed_issues:
        assert issue.severity == "critical", \
            f"expected critical, got {issue.severity}"


@pytest.mark.asyncio
async def test_adversary_respects_configured_raw_dir(tmp_path: Path, _clean_state):
    """When vault.raw_dir is 'sources/', claims citing [[sources/...]] are found
    and the unread-source upweighting scans sources/ not raw/."""
    # Set up vault with sources/ instead of raw/
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "smith-2026.md").write_text(
        "# Smith 2026\n\nThe k-means algorithm uses k=10 clusters.\n"
    )
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "k-means.md").write_text(
        "---\ntitle: K-Means\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm uses k=10 clusters [[sources/smith-2026.md]].\n"
    )
    _clean_state.append(_state_dir_for(tmp_path))

    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=5),
        vault=VaultConfig(raw_dir="sources/"),
    )
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.9, "explanation": "Matches."}'
    )
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path / "wiki")
    agent = AdversaryAgent(vault, tmp_path, stub, queue, config)

    result = await agent.run()

    # The claim was found and verified (LLM was called)
    assert result.claims_checked == 1
    assert len(result.validated) == 1
    assert len(stub.calls) == 1


def _make_agent(tmp_path: Path, *, force_recheck_days: int = 30) -> AdversaryAgent:
    """Helper: agent on a vault with one wiki page, no raw sources."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    (wiki_dir / "page.md").write_text("---\ntitle: Page\n---\n\nContent.\n")
    config = WikiConfig(
        maintenance=MaintenanceConfig(
            adversary_claims_per_run=5,
            adversary_force_recheck_days=force_recheck_days,
        ),
    )
    vault = Vault.scan(tmp_path)
    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    return AdversaryAgent(vault, tmp_path, stub, IssueQueue(wiki_dir), config)


def test_vault_unchanged_no_ts_file(tmp_path: Path):
    """Returns False (run the adversary) when no timestamp file exists."""
    agent = _make_agent(tmp_path)
    assert agent._vault_unchanged_since_last_run() is False


def test_vault_unchanged_file_modified_after_ts(tmp_path: Path):
    """Returns False when a wiki file is newer than the stored timestamp."""
    agent = _make_agent(tmp_path)
    # Write a timestamp from 60 seconds ago
    ts = time.time() - 60
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    # Touch the wiki page to set its mtime to now
    page = tmp_path / "wiki" / "page.md"
    page.touch()
    assert agent._vault_unchanged_since_last_run() is False


def test_vault_unchanged_no_new_files(tmp_path: Path):
    """Returns True when no file is newer than the stored timestamp."""
    agent = _make_agent(tmp_path)
    # Write page first, then store a timestamp that is newer than the page
    page = tmp_path / "wiki" / "page.md"
    page.touch()
    time.sleep(0.05)  # ensure mtime < ts
    ts = time.time()
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    assert agent._vault_unchanged_since_last_run() is True


def test_vault_unchanged_force_recheck_bypasses_guard(tmp_path: Path):
    """Returns False when force_recheck_days have elapsed, even with no file changes."""
    agent = _make_agent(tmp_path, force_recheck_days=1)
    # Timestamp is 2 days ago — force-recheck window exceeded
    ts = time.time() - (2 * 86400)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    assert agent._vault_unchanged_since_last_run() is False


def test_record_last_run_ts_roundtrip(tmp_path: Path):
    """_record_last_run_ts() writes a float that _load_last_run_ts() reads back."""
    agent = _make_agent(tmp_path)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    before = time.time()
    agent._record_last_run_ts()
    after = time.time()
    ts = agent._load_last_run_ts()
    assert ts is not None
    assert before <= ts <= after


def test_load_last_run_ts_missing_file(tmp_path: Path):
    """Returns None when the timestamp file does not exist."""
    agent = _make_agent(tmp_path)
    assert agent._load_last_run_ts() is None


def test_load_last_run_ts_corrupt_file(tmp_path: Path):
    """Returns None when the timestamp file contains garbage."""
    agent = _make_agent(tmp_path)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text("not-a-float\n")
    assert agent._load_last_run_ts() is None
