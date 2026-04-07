from llm_wiki.manifest import ManifestEntry, ManifestStore, SectionInfo


def _make_entries(cluster: str, count: int) -> list[ManifestEntry]:
    return [
        ManifestEntry(
            name=f"{cluster}-page-{i}",
            title=f"Page {i} in {cluster}",
            summary=f"Summary for page {i}.",
            tags=["tag-a"],
            cluster=cluster,
            tokens=200,
            sections=[SectionInfo(name="content", tokens=200)],
            links_to=[],
            links_from=[],
        )
        for i in range(count)
    ]


def test_store_level0():
    entries = _make_entries("bio", 5) + _make_entries("ml", 3)
    store = ManifestStore(entries)
    level0 = store.level0()
    assert len(level0) == 2
    names = [c.name for c in level0]
    assert "bio" in names
    assert "ml" in names


def test_store_level1():
    entries = _make_entries("bio", 5)
    store = ManifestStore(entries)
    page = store.level1("bio", page_size=3, cursor=0)
    assert len(page.entries) == 3
    assert page.has_more is True
    assert page.next_cursor == 3

    page2 = store.level1("bio", page_size=3, cursor=3)
    assert len(page2.entries) == 2
    assert page2.has_more is False


def test_store_level2():
    entries = _make_entries("bio", 3)
    store = ManifestStore(entries)
    entry = store.level2("bio-page-1")
    assert entry is not None
    assert entry.name == "bio-page-1"


def test_store_level2_missing():
    store = ManifestStore([])
    assert store.level2("nonexistent") is None


def test_store_budget_aware_manifest():
    entries = _make_entries("bio", 10)
    store = ManifestStore(entries)
    # Small budget: should return fewer entries
    text = store.manifest_text(budget=200)
    # Large budget: should return more
    text_large = store.manifest_text(budget=5000)
    assert len(text_large) >= len(text)


def test_links_from_computed():
    entries = [
        ManifestEntry(
            name="a", title="A", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=["b", "c"], links_from=[],
        ),
        ManifestEntry(
            name="b", title="B", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=["a"], links_from=[],
        ),
        ManifestEntry(
            name="c", title="C", summary="", tags=[], cluster="c",
            tokens=100, sections=[], links_to=[], links_from=[],
        ),
    ]
    store = ManifestStore(entries)
    b_entry = store.level2("b")
    assert "a" in b_entry.links_from
    c_entry = store.level2("c")
    assert "a" in c_entry.links_from
