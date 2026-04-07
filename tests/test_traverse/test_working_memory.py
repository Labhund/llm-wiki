from __future__ import annotations

from llm_wiki.traverse.working_memory import (
    NextCandidate,
    PageRead,
    WorkingMemory,
)


def test_initial_creates_empty_memory():
    mem = WorkingMemory.initial("How does X work?", budget=16000)
    assert mem.query == "How does X work?"
    assert mem.budget_total == 16000
    assert mem.budget_used == 0
    assert mem.budget_remaining == 16000
    assert mem.turn == 0
    assert mem.pages_read == []
    assert mem.remaining_questions == ["How does X work?"]
    assert mem.next_candidates == []
    assert mem.hypothesis == ""
    assert mem.answer_complete is False


def test_budget_remaining():
    mem = WorkingMemory.initial("q", budget=10000)
    mem.budget_used = 3000
    assert mem.budget_remaining == 7000


def test_budget_remaining_floors_at_zero():
    mem = WorkingMemory.initial("q", budget=100)
    mem.budget_used = 200
    assert mem.budget_remaining == 0


def test_context_text_empty():
    mem = WorkingMemory.initial("q", budget=16000)
    text = mem.to_context_text()
    assert "Remaining Questions" in text
    assert "q" in text
    assert "Pages Read" not in text


def test_context_text_with_pages():
    mem = WorkingMemory.initial("q", budget=16000)
    mem.pages_read = [
        PageRead(
            name="srna-embeddings",
            sections_read=["overview", "method"],
            salient_points="PCA + k-means used for validation",
            relevance=0.9,
        ),
    ]
    mem.hypothesis = "sRNA validation uses clustering"
    text = mem.to_context_text()
    assert "[[srna-embeddings]]" in text
    assert "PCA + k-means" in text
    assert "sRNA validation uses clustering" in text


def test_context_text_skips_empty_salient_points():
    """Pages with empty salient_points are still listed (signal: 'looked but found nothing')."""
    mem = WorkingMemory.initial("q", budget=16000)
    mem.pages_read = [
        PageRead(
            name="not-useful",
            sections_read=["overview"],
            salient_points="",
            relevance=0.3,
        ),
    ]
    text = mem.to_context_text()
    assert "[[not-useful]]" in text
    assert "(no relevant content)" in text


def test_roundtrip_serialization():
    mem = WorkingMemory.initial("How does X?", budget=8000)
    mem.pages_read = [
        PageRead(name="page-a", sections_read=["s1"], salient_points="fact A", relevance=0.8),
    ]
    mem.remaining_questions = ["Sub-question 1"]
    mem.next_candidates = [
        NextCandidate(name="page-b", reason="might help", priority=0.7),
    ]
    mem.hypothesis = "X works via Y"
    mem.budget_used = 2000
    mem.turn = 3

    data = mem.to_dict()
    restored = WorkingMemory.from_dict(data)

    assert restored.query == "How does X?"
    assert restored.budget_total == 8000
    assert restored.budget_used == 2000
    assert restored.turn == 3
    assert len(restored.pages_read) == 1
    assert restored.pages_read[0].name == "page-a"
    assert restored.pages_read[0].salient_points == "fact A"
    assert len(restored.next_candidates) == 1
    assert restored.next_candidates[0].name == "page-b"
    assert restored.hypothesis == "X works via Y"


def test_compact_truncates_oldest_findings():
    mem = WorkingMemory.initial("q", budget=16000)
    # Add pages with long salient_points strings
    mem.pages_read = [
        PageRead(
            name="old-page",
            sections_read=["s1"],
            salient_points="A" * 500,  # Very long
            relevance=0.5,
        ),
        PageRead(
            name="recent-page",
            sections_read=["s1"],
            salient_points="B" * 500,
            relevance=0.9,
        ),
    ]
    mem.hypothesis = "test"

    # Compact to a small target — should truncate oldest first
    mem.compact(50)
    assert len(mem.pages_read[0].salient_points) <= 80


def test_compact_no_op_when_under_target():
    mem = WorkingMemory.initial("q", budget=16000)
    mem.pages_read = [
        PageRead(name="p", sections_read=["s1"], salient_points="short", relevance=0.5),
    ]
    original = mem.pages_read[0].salient_points
    mem.compact(50000)
    assert mem.pages_read[0].salient_points == original
