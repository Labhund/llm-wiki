import json
from pathlib import Path

from llm_wiki.ingest.proposals import (
    Proposal,
    ProposalPassage,
    cluster_dirs,
    find_wiki_page,
    list_pending_proposals,
    read_proposal_body,
    read_proposal_meta,
    update_proposal_status,
    write_proposal,
)
from llm_wiki.ingest.page_writer import PageSection


def _sample_proposal() -> Proposal:
    return Proposal(
        source="raw/boltz2.pdf",
        target_page="boltz-2",
        action="update",
        proposed_by="ingest",
        created="2026-04-10T12:00:00",
        extraction_method="pdf",
        sections=[
            PageSection(
                name="binding-affinity",
                heading="Binding Affinity Prediction",
                content="[[Boltz-2]] achieves SOTA on PDBbind [[raw/boltz2.pdf]].",
            )
        ],
        passages=[
            ProposalPassage(
                id="p1",
                text="Boltz-2 achieves state-of-the-art on PDBbind.",
                claim="Boltz-2 achieves SOTA on PDBbind",
                score=0.91,
                method="ngram",
                verifiable=True,
                ocr_sourced=False,
            )
        ],
    )


def test_write_and_read_proposal_meta(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    assert p.exists()
    meta = read_proposal_meta(p)
    assert meta["target_page"] == "boltz-2"
    assert meta["action"] == "update"
    assert meta["status"] == "pending"
    assert meta["source"] == "raw/boltz2.pdf"


def test_write_proposal_body_contains_sections(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    body = read_proposal_body(p)
    assert "%% section: binding-affinity %%" in body
    assert "## Binding Affinity Prediction" in body
    assert "[[raw/boltz2.pdf]]" in body


def test_write_proposal_evidence_block(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    raw = p.read_text()
    assert "```evidence" in raw
    import re
    m = re.search(r"```evidence\s*\n(.*?)\n```", raw, re.DOTALL)
    assert m
    evidence = json.loads(m.group(1))
    assert evidence[0]["id"] == "p1"
    assert evidence[0]["score"] == 0.91


def test_update_proposal_status(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    update_proposal_status(p, "merged")
    meta = read_proposal_meta(p)
    assert meta["status"] == "merged"


def test_list_pending_proposals(tmp_path):
    proposals_dir = tmp_path / "proposals"
    p1 = write_proposal(proposals_dir, _sample_proposal(), source_slug="boltz2")
    prop2 = _sample_proposal()
    prop2.target_page = "other-page"
    p2 = write_proposal(proposals_dir, prop2, source_slug="boltz2")
    update_proposal_status(p2, "merged")
    pending = list_pending_proposals(proposals_dir)
    assert len(pending) == 1
    assert pending[0] == p1


def test_find_wiki_page_flat(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "boltz-2.md"
    page.write_text("---\ntitle: Boltz-2\n---\n")
    assert find_wiki_page(wiki, "boltz-2") == page
    assert find_wiki_page(wiki, "does-not-exist") is None


def test_find_wiki_page_nested(tmp_path):
    wiki = tmp_path / "wiki"
    cluster = wiki / "structural-biology"
    cluster.mkdir(parents=True)
    page = cluster / "boltz-2.md"
    page.write_text("---\ntitle: Boltz-2\n---\n")
    assert find_wiki_page(wiki, "boltz-2") == page


def test_cluster_dirs_returns_subdirectories(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "structural-biology").mkdir(parents=True)
    (wiki / "ml-methods").mkdir()
    (wiki / ".hidden").mkdir()
    result = cluster_dirs(wiki)
    assert result == ["ml-methods", "structural-biology"]
    assert ".hidden" not in result


def test_proposal_includes_target_cluster(tmp_path):
    proposals_dir = tmp_path / "proposals"
    prop = _sample_proposal()
    prop.target_cluster = "structural-biology"
    p = write_proposal(proposals_dir, prop, source_slug="boltz2")
    meta = read_proposal_meta(p)
    assert meta.get("target_cluster") == "structural-biology"
