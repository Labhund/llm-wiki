from pathlib import Path
from llm_wiki.manifest import ManifestEntry, ClusterSummary, build_entry
from llm_wiki.page import Page
from conftest import SAMPLE_PAGE_WITH_MARKERS


def test_build_entry_from_page(tmp_path: Path):
    p = tmp_path / "srna-embeddings.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    entry = build_entry(page, cluster="bioinformatics")
    assert entry.name == "srna-embeddings"
    assert entry.title == "sRNA Embeddings Validation"
    assert entry.cluster == "bioinformatics"
    assert entry.tokens == page.total_tokens
    assert len(entry.sections) == 4
    assert entry.sections[0].name == "overview"
    assert entry.links_to == ["clustering-metrics", "inter-rep-variant-analysis"]
    assert entry.read_count == 0
    assert entry.usefulness == 0.0
    assert entry.authority == 0.0


def test_entry_summary_tokens():
    """Manifest entry itself should serialize to predictable size."""
    entry = ManifestEntry(
        name="test",
        title="Test Page",
        summary="A short summary.",
        tags=["a", "b"],
        cluster="test-cluster",
        tokens=500,
        sections=[],
        links_to=["other"],
        links_from=[],
        read_count=0,
        usefulness=0.0,
        authority=0.0,
    )
    text = entry.to_manifest_text()
    assert "test" in text
    assert "A short summary" in text
    assert "500" in text


def test_cluster_summary():
    entries = [
        ManifestEntry(
            name=f"page-{i}", title=f"Page {i}", summary=f"Summary {i}",
            tags=[], cluster="bio", tokens=100 * (i + 1), sections=[],
            links_to=[], links_from=[], read_count=0,
            usefulness=0.0, authority=0.0,
        )
        for i in range(5)
    ]
    cluster = ClusterSummary.from_entries("bio", entries)
    assert cluster.name == "bio"
    assert cluster.page_count == 5
    assert cluster.total_tokens == 100 + 200 + 300 + 400 + 500


def test_build_entry_sets_is_synthesis_for_synthesis_page(tmp_path: Path):
    """build_entry sets is_synthesis=True when frontmatter has type: synthesis."""
    p = tmp_path / "my-synthesis.md"
    p.write_text("---\ntitle: My Synthesis\ntype: synthesis\n---\nSome content.\n")
    page = Page.parse(p)
    entry = build_entry(page, cluster="analysis")
    assert entry.is_synthesis is True


def test_build_entry_is_synthesis_false_for_extracted_page(tmp_path: Path):
    """build_entry sets is_synthesis=False when type is absent or not synthesis."""
    p = tmp_path / "ordinary.md"
    p.write_text("---\ntitle: Ordinary Page\n---\nSome content.\n")
    page = Page.parse(p)
    entry = build_entry(page, cluster="bio")
    assert entry.is_synthesis is False


def test_manifest_text_includes_synthesis_marker(tmp_path: Path):
    """to_manifest_text() includes '| synthesis' for synthesis pages and omits it otherwise."""
    synth_path = tmp_path / "synth.md"
    synth_path.write_text("---\ntitle: Synth\ntype: synthesis\n---\nContent.\n")
    synth_entry = build_entry(Page.parse(synth_path), cluster="analysis")
    assert "| synthesis" in synth_entry.to_manifest_text()

    ordinary_path = tmp_path / "ordinary.md"
    ordinary_path.write_text("---\ntitle: Ordinary\n---\nContent.\n")
    ordinary_entry = build_entry(Page.parse(ordinary_path), cluster="bio")
    assert "| synthesis" not in ordinary_entry.to_manifest_text()


def test_build_entry_marks_type_synthesis(tmp_path):
    """ManifestEntry.is_synthesis is True when frontmatter has type: synthesis."""
    p = tmp_path / "wiki" / "q-test.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ntitle: Test\ntype: synthesis\nquery: test\ncreated_by: query\n---\n\n%% section: answer %%\n\nAnswer [[foo]].\n",
        encoding="utf-8",
    )
    page = Page.parse(p)
    entry = build_entry(page, cluster="root")
    assert entry.is_synthesis is True


def test_build_entry_not_synthesis_for_concept(tmp_path):
    """build_entry sets is_synthesis=False when type is concept."""
    p = tmp_path / "wiki" / "foo.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntitle: Foo\ntype: concept\n---\n\nBody.\n", encoding="utf-8")
    page = Page.parse(p)
    entry = build_entry(page, cluster="root")
    assert entry.is_synthesis is False
