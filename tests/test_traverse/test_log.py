from __future__ import annotations

import json
from pathlib import Path

from llm_wiki.traverse.log import TraversalLog, TurnLog
from llm_wiki.traverse.working_memory import PageRead


def test_turn_log_to_dict_with_rich_pages():
    """TurnLog stores PageRead objects with full salient_points."""
    turn = TurnLog(
        turn=1,
        pages_read=[
            PageRead(
                name="srna-embeddings",
                sections_read=["overview"],
                salient_points="PCA + k-means with silhouette > 0.5",
                relevance=0.9,
            ),
        ],
        tokens_used=150,
        hypothesis="sRNA uses PCA",
        remaining_questions=["What about k-means?"],
        next_candidates=["clustering-metrics"],
    )
    d = turn.to_dict()
    assert d["turn"] == 1
    assert d["tokens_used"] == 150
    assert len(d["pages_read"]) == 1
    page = d["pages_read"][0]
    assert page["name"] == "srna-embeddings"
    assert page["salient_points"] == "PCA + k-means with silhouette > 0.5"
    assert page["sections_read"] == ["overview"]
    assert page["relevance"] == 0.9


def test_turn_log_with_unhelpful_page():
    """A page with empty salient_points is logged — meaningful signal."""
    turn = TurnLog(
        turn=2,
        pages_read=[
            PageRead(name="off-topic", sections_read=["top"], salient_points="", relevance=0.2),
        ],
        tokens_used=80,
        hypothesis="unchanged",
        remaining_questions=["original"],
        next_candidates=[],
    )
    d = turn.to_dict()
    assert d["pages_read"][0]["salient_points"] == ""


def test_traversal_log_add_turn():
    log = TraversalLog(query="How does X?", budget=16000)
    turn = TurnLog(
        turn=0, pages_read=[], tokens_used=100,
        hypothesis="", remaining_questions=["How does X?"],
        next_candidates=["page-a"],
    )
    log.add_turn(turn)
    assert len(log.turns) == 1
    assert log.turns[0].turn == 0


def test_traversal_log_to_dict():
    log = TraversalLog(query="q", budget=8000)
    log.add_turn(TurnLog(
        turn=0, pages_read=[], tokens_used=50,
        hypothesis="h", remaining_questions=[], next_candidates=[],
    ))
    log.outcome = "complete"
    log.total_tokens_used = 250
    log.pages_visited = ["page-a"]

    d = log.to_dict()
    assert d["query"] == "q"
    assert d["budget"] == 8000
    assert d["outcome"] == "complete"
    assert d["total_tokens_used"] == 250
    assert d["pages_visited"] == ["page-a"]
    assert len(d["turns"]) == 1


def test_traversal_log_save_appends_jsonl(tmp_path: Path):
    """save() writes one line per log to traversal_logs.jsonl."""
    log_dir = tmp_path / "traversal_logs"

    log1 = TraversalLog(query="first question", budget=16000)
    log1.outcome = "complete"
    log1.total_tokens_used = 1500
    log1.pages_visited = ["page-a"]
    log1.save(log_dir)

    log2 = TraversalLog(query="second question", budget=8000)
    log2.outcome = "candidates_exhausted"
    log2.total_tokens_used = 600
    log2.save(log_dir)

    log_file = log_dir / "traversal_logs.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2

    parsed1 = json.loads(lines[0])
    parsed2 = json.loads(lines[1])
    assert parsed1["query"] == "first question"
    assert parsed1["outcome"] == "complete"
    assert parsed2["query"] == "second question"
    assert parsed2["outcome"] == "candidates_exhausted"


def test_traversal_log_save_creates_directory(tmp_path: Path):
    log_dir = tmp_path / "deeply" / "nested" / "logs"
    log = TraversalLog(query="q", budget=1000)
    log.save(log_dir)
    assert (log_dir / "traversal_logs.jsonl").exists()


def test_traversal_log_has_timestamp():
    """TraversalLog auto-populates an ISO 8601 UTC timestamp on creation."""
    import datetime as dt
    before = dt.datetime.now(dt.timezone.utc)
    log = TraversalLog(query="q", budget=1000)
    after = dt.datetime.now(dt.timezone.utc)

    parsed = dt.datetime.fromisoformat(log.timestamp)
    # Stored timestamp must be tz-aware UTC and within the call window
    assert parsed.tzinfo is not None
    assert before <= parsed <= after


def test_traversal_log_to_dict_includes_timestamp():
    log = TraversalLog(query="q", budget=1000)
    d = log.to_dict()
    assert "timestamp" in d
    assert d["timestamp"] == log.timestamp
