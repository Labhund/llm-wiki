from __future__ import annotations

import json

import pytest

from llm_wiki.traverse.parsing import (
    parse_traverse_response,
    validate_traverse_response,
)


def test_parse_raw_json():
    data = {
        "salient_points": "Found info",
        "remaining_questions": [],
        "next_candidates": [],
        "hypothesis": "X is Y",
        "answer_complete": True,
    }
    result = parse_traverse_response(json.dumps(data))
    assert result == data


def test_parse_json_in_markdown_code_block():
    data = {"salient_points": "info", "hypothesis": "theory", "answer_complete": False,
            "remaining_questions": ["q"], "next_candidates": []}
    text = f"Here's my analysis:\n```json\n{json.dumps(data)}\n```"
    result = parse_traverse_response(text)
    assert result == data


def test_parse_json_in_plain_code_block():
    data = {"salient_points": "info", "hypothesis": "h", "answer_complete": True,
            "remaining_questions": [], "next_candidates": []}
    text = f"Result:\n```\n{json.dumps(data)}\n```\nDone."
    result = parse_traverse_response(text)
    assert result == data


def test_parse_json_embedded_in_text():
    data = {"salient_points": "stuff", "hypothesis": "h", "answer_complete": False,
            "remaining_questions": [], "next_candidates": []}
    text = f"I think the answer is: {json.dumps(data)} That's my response."
    result = parse_traverse_response(text)
    assert result == data


def test_parse_raises_on_no_json():
    with pytest.raises(ValueError, match="Could not parse JSON"):
        parse_traverse_response("This is just plain text with no JSON at all.")


def test_parse_handles_whitespace():
    data = {"salient_points": "x", "hypothesis": "y", "answer_complete": True,
            "remaining_questions": [], "next_candidates": []}
    text = f"\n\n  {json.dumps(data)}  \n\n"
    result = parse_traverse_response(text)
    assert result == data


def test_validate_valid_response():
    data = {
        "salient_points": "Found info about sRNA",
        "remaining_questions": ["What about PCA?"],
        "next_candidates": [{"name": "pca-page", "reason": "need PCA details", "priority": 0.8}],
        "hypothesis": "sRNA uses PCA",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert errors == []


def test_validate_empty_salient_points_is_valid():
    """Empty salient_points is a meaningful, valid signal: 'I looked, found nothing useful.'"""
    data = {
        "salient_points": "",
        "remaining_questions": ["still searching"],
        "next_candidates": [{"name": "other-page", "reason": "try this", "priority": 0.6}],
        "hypothesis": "still forming",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert errors == []


def test_validate_missing_fields():
    errors = validate_traverse_response({"salient_points": "x"})
    assert len(errors) >= 1
    assert "Missing" in errors[0]


def test_validate_bad_candidate_type():
    data = {
        "salient_points": "x",
        "remaining_questions": [],
        "next_candidates": ["not-an-object"],
        "hypothesis": "h",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert any("must be an object" in e for e in errors)


def test_validate_candidate_missing_name():
    data = {
        "salient_points": "x",
        "remaining_questions": [],
        "next_candidates": [{"reason": "no name field"}],
        "hypothesis": "h",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert any("missing 'name'" in e for e in errors)


def test_validate_answer_complete_not_bool():
    data = {
        "salient_points": "x",
        "remaining_questions": [],
        "next_candidates": [],
        "hypothesis": "h",
        "answer_complete": "yes",
    }
    errors = validate_traverse_response(data)
    assert any("boolean" in e for e in errors)


def test_validate_next_candidates_is_none():
    """next_candidates=None must return an error, not raise."""
    data = {
        "salient_points": "x",
        "remaining_questions": [],
        "next_candidates": None,
        "hypothesis": "h",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert any("must be a list" in e for e in errors)


def test_validate_next_candidates_is_dict():
    """next_candidates as a dict must return a clear error, not crash or mislead."""
    data = {
        "salient_points": "x",
        "remaining_questions": [],
        "next_candidates": {"name": "page-a", "priority": 0.9},  # Wrong shape
        "hypothesis": "h",
        "answer_complete": False,
    }
    errors = validate_traverse_response(data)
    assert any("must be a list" in e for e in errors)
