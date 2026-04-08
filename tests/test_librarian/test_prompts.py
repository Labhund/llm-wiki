from __future__ import annotations

from llm_wiki.librarian.log_reader import PageUsage
from llm_wiki.librarian.prompts import (
    compose_refinement_messages,
    parse_refinement,
)


def test_compose_refinement_messages_includes_required_sections():
    usage = PageUsage(
        name="srna-embeddings",
        read_count=12,
        turn_appearances=14,
        total_relevance=11.2,
        salient_samples=["uses k=10", "validated via PCA"],
        queries=["how do we validate sRNA embeddings?", "what k for k-means?"],
    )
    messages = compose_refinement_messages(
        page_name="srna-embeddings",
        page_title="sRNA Embeddings",
        page_content="## Overview\n\nValidation pipeline for sRNA embeddings...",
        usage=usage,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user = messages[1]["content"]
    assert "srna-embeddings" in user
    assert "sRNA Embeddings" in user
    assert "uses k=10" in user
    assert "how do we validate sRNA embeddings?" in user


def test_parse_refinement_valid_json():
    text = '{"tags": ["bioinformatics", "validation"], "summary": "Validates sRNA embeddings."}'
    tags, summary = parse_refinement(text)
    assert tags == ["bioinformatics", "validation"]
    assert summary == "Validates sRNA embeddings."


def test_parse_refinement_fenced_json():
    text = """```json
{"tags": ["a", "b"], "summary": "S."}
```"""
    tags, summary = parse_refinement(text)
    assert tags == ["a", "b"]
    assert summary == "S."


def test_parse_refinement_missing_summary():
    text = '{"tags": ["a"]}'
    tags, summary = parse_refinement(text)
    assert tags == ["a"]
    assert summary is None


def test_parse_refinement_missing_tags():
    text = '{"summary": "S."}'
    tags, summary = parse_refinement(text)
    assert tags == []
    assert summary == "S."


def test_parse_refinement_invalid_types_yields_safe_defaults():
    text = '{"tags": "not-a-list", "summary": 42}'
    tags, summary = parse_refinement(text)
    assert tags == []
    assert summary is None


def test_parse_refinement_garbage_returns_empty():
    tags, summary = parse_refinement("not JSON at all")
    assert tags == []
    assert summary is None


def test_parse_refinement_extra_text_around_json():
    text = "Sure, here's the response:\n\n{\"tags\": [\"x\"], \"summary\": \"y\"}\n\nLet me know."
    tags, summary = parse_refinement(text)
    assert tags == ["x"]
    assert summary == "y"


def test_parse_refinement_filters_non_string_tags():
    text = '{"tags": ["valid", 42, null, "also-valid"], "summary": "ok"}'
    tags, summary = parse_refinement(text)
    assert tags == ["valid", "also-valid"]
