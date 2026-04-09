from pathlib import Path

from llm_wiki.talk.page import TalkEntry, TalkPage


def test_talk_entry_default_type_is_suggestion():
    e = TalkEntry(index=0, timestamp="2026-04-10T12:00:00", author="@user", body="hello")
    assert e.type == "suggestion"


def test_resonance_type_roundtrips_through_file(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@resonance",
        body="New source corroborates this claim.",
        severity="moderate",
        type="resonance",
    )
    talk.append(entry)

    loaded = talk.load()
    assert len(loaded) == 1
    assert loaded[0].type == "resonance"
    assert loaded[0].severity == "moderate"


def test_suggestion_type_writes_no_html_comment(tmp_path: Path):
    """Default type='suggestion' with default severity must not add <!-- --> comment."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@user",
        body="A plain suggestion.",
    )
    talk.append(entry)

    raw = talk.path.read_text()
    assert "<!--" not in raw


def test_adversary_finding_type_roundtrips(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@adversary",
        body="Verdict: ambiguous.",
        severity="critical",
        type="adversary-finding",
    )
    talk.append(entry)

    loaded = talk.load()
    assert loaded[0].type == "adversary-finding"


def test_old_file_without_type_field_defaults_to_suggestion(tmp_path: Path):
    """Pre-existing talk files (no `type:` in metadata) must load with type='suggestion'."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "old-page.md"
    page_path.write_text("---\ntitle: Old\n---\nContent.\n")

    talk_path = wiki_dir / "old-page.talk.md"
    talk_path.write_text(
        "---\npage: old-page\n---\n\n"
        "**2026-01-01T00:00:00 — @user** <!-- severity:moderate -->\n"
        "Old-style entry.\n"
    )

    talk = TalkPage(talk_path)
    loaded = talk.load()
    assert loaded[0].type == "suggestion"
    assert loaded[0].severity == "moderate"
