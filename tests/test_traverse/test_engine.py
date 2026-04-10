from __future__ import annotations

import json

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.traverse.engine import TraversalEngine, TraversalResult
from llm_wiki.vault import Vault

from .conftest import MockLLMClient


def _turn(salient: str, candidates: list[dict], hypothesis: str = "TBD",
          questions: list[str] | None = None, complete: bool = False) -> str:
    """Helper: build a JSON turn response."""
    return json.dumps({
        "salient_points": salient,
        "remaining_questions": questions if questions is not None else [],
        "next_candidates": candidates,
        "hypothesis": hypothesis,
        "answer_complete": complete,
    })


@pytest.mark.asyncio
async def test_no_search_results(tmp_path):
    """Empty vault → immediate candidates_exhausted."""
    (tmp_path / "wiki").mkdir()  # satisfy vault validation guard
    (tmp_path / "wiki" / "empty.md").write_text("nothing useful")
    vault = Vault.scan(tmp_path)
    config = WikiConfig()
    mock_llm = MockLLMClient([])

    engine = TraversalEngine(vault, mock_llm, config)
    result = await engine.query("nonexistent topic XYZ123")

    assert result.outcome == "candidates_exhausted"
    assert "No relevant pages" in result.answer
    assert mock_llm._call_index == 0  # No LLM calls made


@pytest.mark.asyncio
async def test_single_turn_complete(vault, config):
    """LLM says answer_complete on Turn 0 → synthesize immediately."""
    turn_0 = _turn(
        salient="Manifest mentions sRNA validation page",
        candidates=[],
        hypothesis="sRNA validation uses PCA and clustering",
        complete=True,
    )
    synthesis = (
        "sRNA embeddings are validated using PCA projection and "
        "k-means clustering [[srna-embeddings]]."
    )
    mock_llm = MockLLMClient([turn_0, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "complete"
    assert "srna-embeddings" in result.citations
    assert mock_llm._call_index == 2  # Turn 0 + synthesis
    assert len(result.log.turns) == 1
    assert result.needs_more_budget is False


@pytest.mark.asyncio
async def test_multi_turn_traversal(vault, config):
    """Two traversal turns before answer_complete → 3 LLM calls + synthesis."""
    turn_0 = _turn(
        salient="Multiple sRNA pages exist in manifest",
        candidates=[{"name": "srna-embeddings", "reason": "main page", "priority": 0.9}],
        hypothesis="sRNA validation involves clustering",
        questions=["What specific metrics?"],
    )
    turn_1 = _turn(
        salient="PCA + k-means (k=10), silhouette > 0.5 from [[srna-embeddings]]",
        candidates=[],
        hypothesis="Validated via PCA dimensionality reduction then k-means",
        complete=True,
    )
    synthesis = (
        "sRNA embeddings are validated using PCA dimensionality reduction "
        "followed by k-means clustering (k=10). Silhouette scores above 0.5 "
        "indicate well-separated clusters [[srna-embeddings]]."
    )
    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "complete"
    assert len(result.log.turns) == 2
    assert result.log.pages_visited == ["srna-embeddings"]
    # Verify rich pages_read in TurnLog has the salient_points
    turn_1_log = result.log.turns[1]
    assert len(turn_1_log.pages_read) == 1
    assert turn_1_log.pages_read[0].name == "srna-embeddings"
    assert "PCA" in turn_1_log.pages_read[0].salient_points
    assert "srna-embeddings" in result.citations
    assert mock_llm._call_index == 3


@pytest.mark.asyncio
async def test_budget_ceiling_terminates(vault):
    """Budget ceiling (80%) triggers early stop with needs_more_budget."""
    config = WikiConfig()
    config.budgets.default_query = 250  # Very tight
    config.budgets.hard_ceiling_pct = 0.8  # Ceiling at 200 tokens

    # MockLLMClient uses 100 tokens per call.
    # Turn 0: 100 used. Turn 1: 200 used. 200 >= 200 → budget_exceeded.
    turn_0 = _turn(
        salient="Found pages",
        candidates=[{"name": "srna-embeddings", "reason": "check", "priority": 0.9}],
        questions=["details?"],
    )
    turn_1 = _turn(
        salient="PCA + k-means",
        candidates=[{"name": "clustering-metrics", "reason": "check", "priority": 0.8}],
        hypothesis="Uses PCA",
        questions=["more?"],
    )
    synthesis = "Partial: sRNA uses PCA [[srna-embeddings]]."

    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "budget_exceeded"
    assert result.needs_more_budget is True


@pytest.mark.asyncio
async def test_candidates_exhausted(vault, config):
    """No more candidates → candidates_exhausted."""
    turn_0 = _turn(
        salient="Only one relevant page",
        candidates=[{"name": "srna-embeddings", "reason": "only option", "priority": 0.9}],
        questions=["details?"],
    )
    turn_1 = _turn(
        salient="Got details",
        candidates=[],  # No more
        hypothesis="sRNA uses PCA",
        questions=["cross-reference?"],
    )
    synthesis = "Based on limited info: sRNA uses PCA [[srna-embeddings]]."

    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "candidates_exhausted"
    assert result.needs_more_budget is False


@pytest.mark.asyncio
async def test_empty_salient_points_logged(vault, config):
    """Pages with empty salient_points are still recorded in the log — usage signal."""
    turn_0 = _turn(
        salient="Manifest shows multiple pages",
        candidates=[{"name": "no-structure", "reason": "might help", "priority": 0.9}],
        questions=["q?"],
    )
    # Turn 1: model reads the page but finds nothing useful
    turn_1 = _turn(
        salient="",  # empty — page wasn't useful
        candidates=[],
        hypothesis="No new info",
    )
    synthesis = "Could not find a clear answer."

    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("Some question")

    # The page is in the log even though salient_points was empty
    assert result.log.pages_visited == ["no-structure"]
    turn_1_log = result.log.turns[1]
    assert len(turn_1_log.pages_read) == 1
    assert turn_1_log.pages_read[0].name == "no-structure"
    assert turn_1_log.pages_read[0].salient_points == ""


@pytest.mark.asyncio
async def test_turn_limit(vault):
    """Reaching max turns → turn_limit outcome."""
    config = WikiConfig()
    config.budgets.max_traversal_turns = 2
    config.budgets.default_query = 100000  # Large budget so ceiling isn't hit

    turn_0 = _turn(
        salient="Found pages",
        candidates=[
            {"name": "srna-embeddings", "reason": "r", "priority": 0.9},
            {"name": "clustering-metrics", "reason": "r", "priority": 0.8},
            {"name": "inter-rep-variant-analysis", "reason": "r", "priority": 0.7},
        ],
        questions=["q1"],
    )
    turn_1 = _turn(
        salient="Info from page 1",
        candidates=[
            {"name": "clustering-metrics", "reason": "r", "priority": 0.8},
            {"name": "inter-rep-variant-analysis", "reason": "r", "priority": 0.7},
        ],
        hypothesis="Partial",
        questions=["q2"],
    )
    turn_2 = _turn(
        salient="Info from page 2",
        candidates=[{"name": "inter-rep-variant-analysis", "reason": "r", "priority": 0.7}],
        hypothesis="More complete",
        questions=["q3"],
    )
    synthesis = "Answer from limited turns [[srna-embeddings]] [[clustering-metrics]]."

    mock_llm = MockLLMClient([turn_0, turn_1, turn_2, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "turn_limit"
    assert len(result.log.turns) == 3  # Turn 0 + Turn 1 + Turn 2


@pytest.mark.asyncio
async def test_skips_missing_pages(vault, config):
    """Candidates pointing to non-existent pages are skipped."""
    turn_0 = _turn(
        salient="Found pages",
        candidates=[
            {"name": "nonexistent-page", "reason": "hallucinated", "priority": 0.9},
            {"name": "srna-embeddings", "reason": "real page", "priority": 0.8},
        ],
        questions=["details?"],
    )
    turn_1 = _turn(
        salient="Got real info from [[srna-embeddings]]",
        candidates=[],
        hypothesis="sRNA uses PCA",
        complete=True,
    )
    synthesis = "sRNA uses PCA [[srna-embeddings]]."

    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert result.outcome == "complete"
    # Only srna-embeddings was actually read (nonexistent-page skipped)
    assert result.log.pages_visited == ["srna-embeddings"]


@pytest.mark.asyncio
async def test_citation_extraction(vault, config):
    """Citations are extracted from the synthesis answer."""
    turn_0 = _turn(
        salient="Found info",
        candidates=[],
        hypothesis="Done",
        complete=True,
    )
    synthesis = (
        "According to [[srna-embeddings]], validation uses PCA. "
        "The metrics come from [[clustering-metrics#silhouette-score]]. "
        "See also [[srna-embeddings]] again."  # Duplicate should be deduped
    )

    mock_llm = MockLLMClient([turn_0, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    assert "srna-embeddings" in result.citations
    assert "clustering-metrics" in result.citations
    # Deduplicated
    assert result.citations.count("srna-embeddings") == 1


@pytest.mark.asyncio
async def test_malformed_llm_response_continues(vault, config):
    """If LLM returns garbage, engine fills defaults and continues."""
    turn_0 = "This is not JSON at all, just rambling text."
    synthesis = "Best effort answer."

    mock_llm = MockLLMClient([turn_0, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    # Should reach candidates_exhausted (defaults have empty next_candidates)
    assert result.outcome == "candidates_exhausted"
    assert result.answer == "Best effort answer."


@pytest.mark.asyncio
async def test_log_persisted_to_disk(vault, config, tmp_path):
    """When log_dir is provided, the engine writes the log to traversal_logs.jsonl."""
    log_dir = tmp_path / "traversal_logs"

    turn_0 = _turn(
        salient="Done immediately",
        candidates=[],
        hypothesis="Trivial",
        complete=True,
    )
    synthesis = "Answer [[srna-embeddings]]."

    mock_llm = MockLLMClient([turn_0, synthesis])
    engine = TraversalEngine(vault, mock_llm, config, log_dir=log_dir)

    await engine.query("trivial question")

    log_file = log_dir / "traversal_logs.jsonl"
    assert log_file.exists()
    line = log_file.read_text().strip()
    parsed = json.loads(line)
    assert parsed["query"] == "trivial question"
    assert parsed["outcome"] == "complete"


@pytest.mark.asyncio
async def test_no_log_persistence_when_log_dir_none(vault, config):
    """Without log_dir, the engine still works — log_dir is optional."""
    turn_0 = _turn(salient="x", candidates=[], complete=True)
    synthesis = "Answer."
    mock_llm = MockLLMClient([turn_0, synthesis])
    engine = TraversalEngine(vault, mock_llm, config, log_dir=None)
    result = await engine.query("q")
    assert result.outcome == "complete"


@pytest.mark.asyncio
async def test_turn_token_accounting_is_per_call_delta(vault, config):
    """Each TurnLog.tokens_used is the delta for that single LLM call.

    Sum of per-turn token deltas must match total_tokens_used minus the
    synthesis call (which is the only LLM call not represented as a TurnLog).
    """
    turn_0 = _turn(
        salient="Found pages",
        candidates=[{"name": "srna-embeddings", "reason": "main", "priority": 0.9}],
        questions=["details?"],
    )
    turn_1 = _turn(
        salient="PCA + k-means",
        candidates=[],
        hypothesis="done",
        complete=True,
    )
    synthesis = "Answer [[srna-embeddings]]."
    mock_llm = MockLLMClient([turn_0, turn_1, synthesis])
    engine = TraversalEngine(vault, mock_llm, config)

    result = await engine.query("How are sRNA embeddings validated?")

    # MockLLMClient uses 100 tokens per call. 3 calls total: turn_0, turn_1, synthesis.
    assert result.log.total_tokens_used == 300
    # Sum of per-turn deltas equals tokens used by the traversal turns only (200).
    turn_total = sum(t.tokens_used for t in result.log.turns)
    assert turn_total == 200
    # Each individual turn should be 100 (one LLM call per turn).
    for t in result.log.turns:
        assert t.tokens_used == 100


def test_traversal_result_has_synthesis_action_field():
    """TraversalResult accepts synthesis_action kwarg."""
    from llm_wiki.traverse.log import TraversalLog
    from unittest.mock import MagicMock
    log = MagicMock(spec=TraversalLog)
    log.to_dict.return_value = {}
    result = TraversalResult(
        answer="ans",
        citations=[],
        outcome="complete",
        needs_more_budget=False,
        log=log,
        synthesis_action={"action": "create", "title": "T"},
    )
    assert result.synthesis_action["action"] == "create"


def test_traversal_result_synthesis_action_defaults_none():
    """TraversalResult.synthesis_action defaults to None."""
    from llm_wiki.traverse.log import TraversalLog
    from unittest.mock import MagicMock
    log = MagicMock(spec=TraversalLog)
    result = TraversalResult(
        answer="ans",
        citations=[],
        outcome="complete",
        needs_more_budget=False,
        log=log,
    )
    assert result.synthesis_action is None
