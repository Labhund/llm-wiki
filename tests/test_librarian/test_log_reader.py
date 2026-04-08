from __future__ import annotations

import json
from pathlib import Path

from llm_wiki.librarian.log_reader import PageUsage, aggregate_logs


def _write_log(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_aggregate_logs_missing_file_returns_empty(tmp_path: Path):
    """A missing log file produces an empty result without raising."""
    result = aggregate_logs(tmp_path / "nope.jsonl")
    assert result == {}


def test_aggregate_logs_empty_file_returns_empty(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    log_file.write_text("")
    assert aggregate_logs(log_file) == {}


def test_aggregate_logs_single_query_single_page(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "How does k-means work?",
            "budget": 16000,
            "timestamp": "2026-04-01T12:00:00+00:00",
            "turns": [
                {
                    "turn": 0,
                    "pages_read": [
                        {
                            "name": "k-means",
                            "sections_read": ["overview"],
                            "salient_points": "k=10 chosen via elbow method",
                            "relevance": 0.9,
                        }
                    ],
                    "tokens_used": 1000,
                    "hypothesis": "k-means clusters with k=10",
                    "remaining_questions": [],
                    "next_candidates": [],
                }
            ],
            "outcome": "complete",
            "total_tokens_used": 1000,
            "pages_visited": ["k-means"],
        }
    ])

    result = aggregate_logs(log_file)
    assert "k-means" in result
    usage = result["k-means"]
    assert isinstance(usage, PageUsage)
    assert usage.read_count == 1
    assert usage.turn_appearances == 1
    assert usage.avg_relevance == 0.9
    assert usage.salient_samples == ["k=10 chosen via elbow method"]
    assert usage.queries == ["How does k-means work?"]


def test_aggregate_logs_multiple_queries_distinct_pages(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q1",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
        {
            "query": "q2",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "useful", "relevance": 0.8},
                {"name": "b", "sections_read": [], "salient_points": "also useful", "relevance": 0.7},
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    result = aggregate_logs(log_file)
    assert set(result) == {"a", "b"}
    assert result["a"].read_count == 2  # appeared in two distinct queries
    assert result["a"].turn_appearances == 2
    assert abs(result["a"].avg_relevance - 0.65) < 1e-6
    assert result["b"].read_count == 1
    assert "useful" in result["a"].salient_samples
    assert "also useful" in result["b"].salient_samples


def test_aggregate_logs_distinct_query_count_not_double_counted(tmp_path: Path):
    """If a page appears in multiple turns of the same query, read_count = 1."""
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q1",
            "turns": [
                {"turn": 0, "pages_read": [{"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}],
                 "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []},
                {"turn": 1, "pages_read": [{"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.7}],
                 "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []},
            ],
        },
    ])

    result = aggregate_logs(log_file)
    assert result["a"].read_count == 1               # one query
    assert result["a"].turn_appearances == 2         # but two turn appearances
    assert abs(result["a"].avg_relevance - 0.6) < 1e-6


def test_aggregate_logs_caps_samples(tmp_path: Path):
    """salient_samples and queries are capped to the last 5."""
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": f"point {i}", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(10)
    ])

    result = aggregate_logs(log_file)
    assert len(result["a"].salient_samples) == 5
    assert len(result["a"].queries) == 5
    # Most recent ones are kept
    assert "point 9" in result["a"].salient_samples
    assert "q9" in result["a"].queries


def test_aggregate_logs_skips_empty_salient_points(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])
    result = aggregate_logs(log_file)
    assert result["a"].salient_samples == []
