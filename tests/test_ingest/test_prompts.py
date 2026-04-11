from __future__ import annotations

import json

from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_overview_messages,
    compose_page_content_messages,
    compose_passage_collection_messages,
    parse_concept_extraction,
    parse_content_synthesis,
    parse_overview_extraction,
    parse_page_content,
    parse_passage_collection,
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


def test_overview_messages_embed_manifest_and_clusters():
    msgs = compose_overview_messages(
        chunk_text="Boltz-2 is a new model for structure prediction.",
        manifest_lines=["boltz-1  'Boltz-1'", "protein-mpnn  'ProteinMPNN'"],
        source_ref="raw/boltz2.pdf",
        cluster_dir_names=["structural-biology", "ml-methods"],
    )
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "boltz-1" in combined
    assert "protein-mpnn" in combined
    assert "structural-biology" in combined
    assert "Boltz-2 is a new model" in combined


def test_overview_messages_no_clusters():
    msgs = compose_overview_messages(
        chunk_text="A paper.",
        manifest_lines=[],
        source_ref="raw/paper.pdf",
    )
    combined = msgs[0]["content"]
    assert "none yet" in combined.lower()


def test_parse_overview_extraction_valid():
    text = json.dumps({
        "concepts": [
            {"name": "boltz-2", "title": "Boltz-2", "action": "update",
             "cluster": "structural-biology",
             "section_names": ["binding-affinity", "ensemble-prediction"]},
        ]
    })
    result = parse_overview_extraction(text)
    assert len(result) == 1
    assert result[0].name == "boltz-2"
    assert result[0].action == "update"
    assert result[0].cluster == "structural-biology"
    assert "binding-affinity" in result[0].section_names


def test_parse_overview_extraction_defaults_action_to_create():
    text = json.dumps({"concepts": [{"name": "new-concept", "title": "New"}]})
    result = parse_overview_extraction(text)
    assert result[0].action == "create"


def test_passage_collection_messages_embed_concepts():
    concepts = [ConceptPlan(name="boltz-2", title="Boltz-2")]
    msgs = compose_passage_collection_messages(
        chunk_text="Boltz-2 achieves high accuracy.",
        concepts=concepts,
    )
    combined = msgs[0]["content"] + msgs[1]["content"]
    assert "boltz-2" in combined
    assert "Boltz-2 achieves high accuracy" in combined


def test_parse_passage_collection_valid():
    text = json.dumps({"boltz-2": ["Boltz-2 achieves SOTA.", "It uses diffusion."]})
    result = parse_passage_collection(text, concept_names=["boltz-2"])
    assert "boltz-2" in result
    assert len(result["boltz-2"]) == 2


def test_parse_passage_collection_ignores_unknown_concepts():
    text = json.dumps({"unknown": ["Some text."]})
    result = parse_passage_collection(text, concept_names=["boltz-2"])
    assert "unknown" not in result


def test_parse_content_synthesis_valid():
    text = json.dumps({"sections": [
        {"name": "overview", "heading": "Overview", "content": "[[boltz-2]] text [[raw/paper.pdf]]."}
    ]})
    result = parse_content_synthesis(text)
    assert len(result.sections) == 1
    assert result.sections[0].name == "overview"
    assert "boltz-2" in result.sections[0].content


def test_parse_content_synthesis_invalid_returns_empty():
    result = parse_content_synthesis("not json")
    assert result.sections == []


# --- Citation-format tests (inline wikilinks, NOT footnotes) ---


def test_page_content_messages_use_inline_wikilink_citations():
    """compose_page_content_messages must instruct [[raw/...]] citations, not [^N] footnotes."""
    msgs = compose_page_content_messages(
        concept_title="Boltz-2",
        passages=["Boltz-2 achieves SOTA performance."],
        source_ref="raw/boltz2.pdf",
    )
    system = msgs[0]["content"]
    # Must mention inline wikilink citation style
    assert "[[raw/boltz2.pdf]]" in system
    # Must NOT instruct footnote as the citation mechanism
    assert "[^1]" not in system
    # Must NOT have a mandatory "references" section in the structural contract
    assert '"name": "references"' not in system


def test_deep_read_synthesis_messages_use_inline_wikilink_citations():
    """compose_deep_read_synthesis_messages must instruct [[raw/...]] citations, not [^N] footnotes."""
    from llm_wiki.ingest.prompts import compose_deep_read_synthesis_messages

    concept = ConceptPlan(name="boltz-2", title="Boltz-2")
    msgs = compose_deep_read_synthesis_messages(
        concept=concept,
        paper_context="Boltz-2 is a structure prediction model.",
        source_ref="raw/boltz2.pdf",
        manifest_lines=["boltz-1  'Boltz-1'"],
        batch_concepts=[concept],
    )
    # Handle multi-block content format (cache_control=False → plain string)
    system = msgs[0]["content"]
    if isinstance(system, list):
        system = " ".join(b["text"] for b in system if b.get("type") == "text")
    assert "[[raw/boltz2.pdf]]" in system
    assert "[^1]" not in system
    assert '"name": "references"' not in system


def test_content_synthesis_messages_use_inline_wikilink_citations():
    """compose_content_synthesis_messages must instruct [[raw/...]] citations."""
    from llm_wiki.ingest.prompts import compose_content_synthesis_messages

    concept = ConceptPlan(name="boltz-2", title="Boltz-2")
    msgs = compose_content_synthesis_messages(
        concept=concept,
        passages=["Boltz-2 achieves SOTA."],
        source_ref="raw/boltz2.pdf",
        manifest_lines=[],
        batch_concepts=[concept],
    )
    system = msgs[0]["content"]
    assert "[[raw/boltz2.pdf]]" in system
    assert "[^1]" not in system
    assert '"name": "references"' not in system


def test_parse_page_content_works_without_references_section():
    """parse_page_content should accept output that has no 'references' section."""
    text = json.dumps({
        "sections": [
            {"name": "overview", "heading": "Overview",
             "content": "Boltz-2 is a model [[raw/boltz2.pdf]]."},
        ]
    })
    result = parse_page_content(text)
    assert len(result) == 1
    assert result[0].name == "overview"
    assert "[[raw/boltz2.pdf]]" in result[0].content


def test_parse_content_synthesis_works_without_references_section():
    """parse_content_synthesis should accept output that has no 'references' section."""
    text = json.dumps({
        "summary": "Boltz-2 predicts protein structure.",
        "sections": [
            {"name": "overview", "heading": "Overview",
             "content": "Boltz-2 is a model [[raw/boltz2.pdf]]."},
        ]
    })
    result = parse_content_synthesis(text)
    assert len(result.sections) == 1
    assert result.sections[0].name == "overview"
    assert "[[raw/boltz2.pdf]]" in result.sections[0].content
