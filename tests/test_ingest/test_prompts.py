from __future__ import annotations

from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_page_content_messages,
    parse_concept_extraction,
    parse_page_content,
)
from llm_wiki.ingest.agent import ConceptPlan
from llm_wiki.ingest.page_writer import PageSection


def test_concept_extraction_messages_contain_source_text():
    """compose_concept_extraction_messages embeds source text + ref."""
    msgs = compose_concept_extraction_messages(
        source_text="PCA reduces dimensions. k-means clusters data.",
        source_ref="raw/paper.pdf",
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "PCA reduces dimensions" in combined
    assert "raw/paper.pdf" in combined


def test_page_content_messages_contain_concept_and_passages():
    """compose_page_content_messages embeds concept title, passages, source ref."""
    msgs = compose_page_content_messages(
        concept_title="PCA",
        passages=["PCA reduces high-dimensional data."],
        source_ref="raw/paper.pdf",
    )
    assert len(msgs) == 2
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "PCA" in combined
    assert "PCA reduces high-dimensional data." in combined
    assert "raw/paper.pdf" in combined


def test_parse_concept_extraction_valid():
    """parse_concept_extraction parses well-formed JSON."""
    text = """{
        "concepts": [
            {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
            {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]}
        ]
    }"""
    result = parse_concept_extraction(text)
    assert len(result) == 2
    assert result[0].name == "pca"
    assert result[0].title == "PCA"
    assert result[0].passages == ["PCA reduces dimensions."]
    assert result[1].name == "k-means"


def test_parse_concept_extraction_fenced():
    """parse_concept_extraction handles markdown-fenced JSON."""
    text = '```json\n{"concepts": [{"name": "pca", "title": "PCA", "passages": []}]}\n```'
    result = parse_concept_extraction(text)
    assert len(result) == 1
    assert result[0].name == "pca"


def test_parse_concept_extraction_invalid_returns_empty():
    """parse_concept_extraction returns [] on bad JSON."""
    result = parse_concept_extraction("not json at all")
    assert result == []


def test_parse_page_content_valid():
    """parse_page_content parses well-formed JSON."""
    text = """{
        "sections": [
            {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.pdf]]."}
        ]
    }"""
    result = parse_page_content(text)
    assert len(result) == 1
    assert result[0].name == "overview"
    assert result[0].heading == "Overview"
    assert "PCA" in result[0].content


def test_parse_page_content_invalid_returns_empty():
    """parse_page_content returns [] on bad JSON."""
    result = parse_page_content("not json")
    assert result == []
