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
    talk.append(TalkEntry("2026-04-08T15:01:00+00:00", "@adversary", "First."))
    talk.append(TalkEntry("2026-04-08T16:22:00+00:00", "@human", "Second."))

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
        talk.append(TalkEntry(ts, f"@a{i}", f"body {i}"))

    entries = talk.load()
    assert [e.timestamp for e in entries] == timestamps
    assert [e.body for e in entries] == ["body 0", "body 1", "body 2"]


def test_append_handles_multiline_body(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "p.talk.md")
    body = "First line.\n\nSecond paragraph.\n\nThird paragraph."
    talk.append(TalkEntry("2026-04-08T10:00:00+00:00", "@adversary", body))

    entries = talk.load()
    assert len(entries) == 1
    assert "Second paragraph" in entries[0].body
    assert "Third paragraph" in entries[0].body
