from __future__ import annotations

from pathlib import Path

from llm_wiki.talk.page import TalkEntry, TalkPage


def test_for_page_derives_sidecar_path(tmp_path: Path):
    page = tmp_path / "wiki" / "srna-embeddings.md"
    page.parent.mkdir()
    page.write_text("# srna\n")

    talk = TalkPage.for_page(page)
    assert talk.path == tmp_path / "wiki" / "srna-embeddings.talk.md"
    assert talk.parent_page_slug == "srna-embeddings"


def test_exists_false_when_file_missing(tmp_path: Path):
    talk = TalkPage(tmp_path / "x.talk.md")
    assert talk.exists is False


def test_load_missing_file_returns_empty(tmp_path: Path):
    talk = TalkPage(tmp_path / "x.talk.md")
    assert talk.load() == []


def test_append_creates_file_with_frontmatter(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "srna-embeddings.talk.md")
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-08T15:01:00+00:00",
        author="@adversary",
        body="First entry body.",
    )
    talk.append(entry)

    assert talk.exists
    text = talk.path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "page: srna-embeddings" in text
    assert "@adversary" in text
    assert "First entry body." in text


def test_append_to_existing_file_preserves_prior_entries(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "srna-embeddings.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T15:01:00+00:00", "@adversary", "First."))
    talk.append(TalkEntry(0, "2026-04-08T16:22:00+00:00", "@human", "Second."))

    entries = talk.load()
    assert len(entries) == 2
    assert entries[0].body == "First."
    assert entries[1].body == "Second."
    assert entries[0].author == "@adversary"
    assert entries[1].author == "@human"


def test_load_round_trip_preserves_chronology(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "p.talk.md")
    timestamps = [
        "2026-04-01T10:00:00+00:00",
        "2026-04-02T10:00:00+00:00",
        "2026-04-03T10:00:00+00:00",
    ]
    for i, ts in enumerate(timestamps):
        talk.append(TalkEntry(0, ts, f"@a{i}", f"body {i}"))

    entries = talk.load()
    assert [e.timestamp for e in entries] == timestamps
    assert [e.body for e in entries] == ["body 0", "body 1", "body 2"]


def test_append_handles_multiline_body(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "p.talk.md")
    body = "First line.\n\nSecond paragraph.\n\nThird paragraph."
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@adversary", body))

    entries = talk.load()
    assert len(entries) == 1
    assert "Second paragraph" in entries[0].body
    assert "Third paragraph" in entries[0].body


def test_load_assigns_positional_indices(tmp_path):
    """Loaded entries have 1-based positional indices."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-01T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-02T10:00:00+00:00", "@b", "second"))
    talk.append(TalkEntry(0, "2026-04-03T10:00:00+00:00", "@c", "third"))

    entries = talk.load()
    assert [e.index for e in entries] == [1, 2, 3]


def test_load_legacy_entries_default_severity_suggestion(tmp_path):
    """Pre-Phase-6a entries (no HTML-comment) parse with severity='suggestion'."""
    path = tmp_path / "legacy.talk.md"
    path.write_text(
        "---\npage: legacy\n---\n\n"
        "**2026-04-01T10:00:00+00:00 — @adversary**\n"
        "Body of the legacy entry.\n",
        encoding="utf-8",
    )
    entries = TalkPage(path).load()
    assert len(entries) == 1
    assert entries[0].severity == "suggestion"
    assert entries[0].resolves == []
    assert entries[0].index == 1


def test_append_writes_severity_comment(tmp_path):
    """A non-default severity is emitted as <!-- severity:critical -->."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@adversary",
        body="A serious finding.",
        severity="critical",
    ))
    text = talk.path.read_text(encoding="utf-8")
    assert "<!-- severity:critical -->" in text


def test_append_writes_resolves_comment(tmp_path):
    """A `resolves` list is emitted as resolves:[1,3] in the comment."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-08T10:01:00+00:00", "@b", "second"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:02:00+00:00",
        author="@c",
        body="closes 1 and 2",
        resolves=[1, 2],
    ))
    text = talk.path.read_text(encoding="utf-8")
    assert "resolves:[1,2]" in text


def test_append_combines_severity_and_resolves_in_one_comment(tmp_path):
    """Both fields ride in a single <!-- ... --> comment, comma-separated."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "open"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:01:00+00:00",
        author="@b",
        body="closes 1",
        severity="minor",
        resolves=[1],
    ))
    text = talk.path.read_text(encoding="utf-8")
    # Look for a single comment with both fields, in either order
    assert "<!-- severity:minor, resolves:[1] -->" in text


def test_append_omits_comment_for_default_suggestion_no_resolves(tmp_path):
    """The common case (suggestion + no resolves) writes no comment — zero churn."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@a",
        body="just a thought",
    ))
    text = talk.path.read_text(encoding="utf-8")
    # No HTML comment on the header line
    assert "<!--" not in text


def test_round_trip_severity_critical(tmp_path):
    """Write a critical entry, read it back, severity is preserved."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@adversary",
        body="critical finding",
        severity="critical",
    ))
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].severity == "critical"
    assert entries[0].body == "critical finding"


def test_round_trip_resolves_list(tmp_path):
    """Write an entry with resolves=[1,3], read it back, list is preserved."""
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(0, "2026-04-08T10:00:00+00:00", "@a", "first"))
    talk.append(TalkEntry(0, "2026-04-08T10:01:00+00:00", "@b", "second"))
    talk.append(TalkEntry(0, "2026-04-08T10:02:00+00:00", "@c", "third"))
    talk.append(TalkEntry(
        index=0,
        timestamp="2026-04-08T10:03:00+00:00",
        author="@d",
        body="closer",
        resolves=[1, 3],
    ))
    entries = talk.load()
    assert entries[3].resolves == [1, 3]


def test_load_handles_gt_in_meta_value(tmp_path):
    """A `>` character in a meta field value must not silently drop the entry.

    Regression for a bug where the [^>]*? meta capture would fail to find
    its --> terminator if a field value contained `>`, dropping the entire
    header line from finditer() and silently losing the entry.
    """
    path = tmp_path / "p.talk.md"
    path.write_text(
        "---\npage: p\n---\n\n"
        "**2026-04-08T10:00:00+00:00 — @a** <!-- severity:foo>bar -->\n"
        "First entry body.\n\n"
        "**2026-04-08T11:00:00+00:00 — @b** <!-- severity:critical -->\n"
        "Second entry body.\n",
        encoding="utf-8",
    )
    entries = TalkPage(path).load()
    assert len(entries) == 2, f"expected 2 entries, got {len(entries)} — regex dropped one"
    # The first entry's severity is the literal "foo>bar" — the parser doesn't validate
    # the vocabulary, only the type (Severity Literal is for static type-check, not runtime).
    # The important assertion is that the entry exists at all.
    assert entries[0].body == "First entry body."
    assert entries[1].severity == "critical"
    assert entries[1].body == "Second entry body."


def test_append_ignores_caller_supplied_index(tmp_path):
    """A non-zero index passed by the caller is not written to disk.

    The contract is documented in TalkEntry's docstring and append()'s
    docstring: indices are positional and reassigned by load(). This test
    locks the invariant in code so a future refactor that accidentally
    serialized entry.index would be caught.
    """
    talk = TalkPage(tmp_path / "p.talk.md")
    talk.append(TalkEntry(
        index=99,
        timestamp="2026-04-08T10:00:00+00:00",
        author="@a",
        body="first",
    ))
    talk.append(TalkEntry(
        index=42,
        timestamp="2026-04-08T10:01:00+00:00",
        author="@b",
        body="second",
    ))
    text = talk.path.read_text(encoding="utf-8")
    assert "99" not in text, "writer must not serialize caller-supplied index"
    assert "42" not in text, "writer must not serialize caller-supplied index"
    # And load() reassigns indices positionally regardless of what was passed
    entries = talk.load()
    assert [e.index for e in entries] == [1, 2]


def test_compute_open_set_no_resolves_returns_all():
    """With no resolves, every entry is open."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "third"),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 2, 3]


def test_compute_open_set_single_closure():
    """An entry with resolves=[1] removes entry 1 from the open set."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 1", resolves=[1]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [2]


def test_compute_open_set_multi_closure():
    """resolves=[1,3] closes entries 1 and 3 in one shot."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "third"),
        TalkEntry(4, "t4", "@d", "closer", resolves=[1, 3]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [2, 4]


def test_compute_open_set_resolver_itself_remains_open():
    """The resolving entry is itself open until something else closes it."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 1", resolves=[1]),
    ]
    open_set = compute_open_set(entries)
    assert 2 in [e.index for e in open_set]


def test_compute_open_set_chained_closures():
    """Entry 3 closes entry 2, entry 4 closes entry 3 — only 1 and 4 are open."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "closes 2", resolves=[2]),
        TalkEntry(4, "t4", "@d", "closes 3", resolves=[3]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 4]


def test_compute_open_set_resolves_pointing_at_unknown_index_is_ignored():
    """A resolves reference to a non-existent index is harmless."""
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "closes 99", resolves=[99]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 2]


def test_compute_open_set_empty():
    from llm_wiki.talk.page import compute_open_set
    assert compute_open_set([]) == []


def test_compute_open_set_forward_pointing_resolves_does_not_close():
    """Phase 6a P6A-I5: an earlier entry pointing forward to a later index
    must NOT close that later entry. The contract is strictly-greater
    ordering — only entries appearing AFTER an entry can close it.

    This isn't reachable through `talk-append` today (indices are positional
    so the resolver always writes later than the resolved), but Phase 6b
    write routes accept caller-supplied resolves lists and the docstring
    is now load-bearing.
    """
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "tries to close 5", resolves=[5]),
        TalkEntry(3, "t3", "@c", "third"),
        TalkEntry(4, "t4", "@d", "fourth"),
        TalkEntry(5, "t5", "@e", "fifth"),
    ]
    open_set = compute_open_set(entries)
    # Entry 5 must remain open because the closer (index 2) appears earlier.
    assert 5 in [e.index for e in open_set]


def test_compute_open_set_self_closure_is_ignored():
    """Phase 6a P6A-I5: an entry that resolves itself must not close itself.

    Silently ignored — no-op gesture, doesn't error.
    """
    from llm_wiki.talk.page import compute_open_set
    entries = [
        TalkEntry(1, "t1", "@a", "first"),
        TalkEntry(2, "t2", "@b", "second"),
        TalkEntry(3, "t3", "@c", "weird self-closure", resolves=[3]),
    ]
    open_set = compute_open_set(entries)
    assert [e.index for e in open_set] == [1, 2, 3]


def test_iter_talk_pages_walks_recursive_and_skips_hidden(tmp_path):
    """P6A-M3: iter_talk_pages walks recursively, sorted, skipping hidden dirs."""
    from llm_wiki.talk.page import TalkPage, iter_talk_pages

    wiki = tmp_path / "wiki"
    wiki.mkdir()

    # Top-level talk file
    (wiki / "alpha.md").write_text("---\ntitle: A\n---\n")
    (wiki / "alpha.talk.md").write_text("---\npage: alpha\n---\n")

    # Nested talk file under a cluster directory
    (wiki / "bio").mkdir()
    (wiki / "bio" / "beta.md").write_text("---\ntitle: B\n---\n")
    (wiki / "bio" / "beta.talk.md").write_text("---\npage: beta\n---\n")

    # Hidden dir — should be skipped
    (wiki / ".issues").mkdir()
    (wiki / ".issues" / "ghost.talk.md").write_text("---\npage: ghost\n---\n")

    seen = list(iter_talk_pages(wiki))
    names = [name for name, _ in seen]
    # alpha.talk.md sorts before bio/beta.talk.md because 'a' < 'b' on the
    # full path.
    assert names == ["alpha", "beta"]
    assert "ghost" not in names

    # Each yields a TalkPage we can load
    for _, tp in seen:
        assert isinstance(tp, TalkPage)


def test_iter_talk_pages_missing_dir_yields_nothing(tmp_path):
    from llm_wiki.talk.page import iter_talk_pages
    assert list(iter_talk_pages(tmp_path / "does_not_exist")) == []


def test_iter_talk_pages_strips_talk_suffix_from_stem(tmp_path):
    """A file foo.talk.md should yield page_name='foo', not 'foo.talk'."""
    from llm_wiki.talk.page import iter_talk_pages

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "myslug.talk.md").write_text("---\npage: myslug\n---\n")

    seen = list(iter_talk_pages(wiki))
    assert [name for name, _ in seen] == ["myslug"]
