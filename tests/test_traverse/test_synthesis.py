"""Tests for synthesis cache helpers."""
from __future__ import annotations

from llm_wiki.traverse.synthesis import (
    build_synthesis_page_content,
    extract_prose_after_action,
    parse_synthesis_action,
    slug_from_query,
)


def test_slug_from_query_basic():
    assert slug_from_query("How does Boltz-2 work?") == "how-does-boltz-2-work"


def test_slug_from_query_truncates_long():
    long_q = "a " * 40  # 80 chars
    slug = slug_from_query(long_q)
    assert len(slug) <= 60


def test_slug_from_query_empty():
    assert slug_from_query("") == "query"


def test_parse_synthesis_action_accept():
    resp = '{"action": "accept", "page": "boltz-2"}\n\nSome prose.'
    action = parse_synthesis_action(resp)
    assert action == {"action": "accept", "page": "boltz-2"}


def test_parse_synthesis_action_create():
    resp = '{"action": "create", "title": "Boltz-2", "content": "...", "sources": ["wiki/boltz-2.md"]}'
    action = parse_synthesis_action(resp)
    assert action["action"] == "create"
    assert action["title"] == "Boltz-2"


def test_parse_synthesis_action_update():
    resp = '{"action": "update", "page": "boltz-2", "title": "Boltz-2", "content": "Extended.", "sources": []}'
    action = parse_synthesis_action(resp)
    assert action["action"] == "update"
    assert action["page"] == "boltz-2"


def test_parse_synthesis_action_no_json():
    assert parse_synthesis_action("Just prose, no JSON.") is None


def test_parse_synthesis_action_missing_action_key():
    assert parse_synthesis_action('{"page": "boltz-2"}') is None


def test_parse_synthesis_action_unknown_action():
    assert parse_synthesis_action('{"action": "delete", "page": "boltz-2"}') is None


def test_extract_prose_after_action():
    resp = '{"action": "accept", "page": "boltz-2"}\n\nBoltz-2 is great.'
    prose = extract_prose_after_action(resp)
    assert prose == "Boltz-2 is great."


def test_extract_prose_after_action_no_json():
    resp = "Plain prose."
    assert extract_prose_after_action(resp) == "Plain prose."


def test_build_synthesis_page_content_frontmatter():
    content = build_synthesis_page_content(
        title="Boltz-2 Structure",
        query="how does boltz-2 work?",
        answer="Boltz-2 uses diffusion [[boltz-2]].",
        sources=["wiki/boltz-2.md"],
        created_at="2026-04-10T14:00:00Z",
        updated_at="2026-04-10T14:00:00Z",
    )
    assert "type: synthesis" in content
    assert 'query: "how does boltz-2 work?"' in content
    assert "created_by: query" in content
    assert "wiki/boltz-2.md" in content


def test_build_synthesis_page_content_body():
    content = build_synthesis_page_content(
        title="Boltz-2 Structure",
        query="how does boltz-2 work?",
        answer="Boltz-2 uses diffusion [[boltz-2]].",
        sources=["wiki/boltz-2.md"],
    )
    assert "%% section: answer %%" in content
    assert "Boltz-2 uses diffusion [[boltz-2]]." in content
