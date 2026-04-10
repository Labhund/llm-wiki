import datetime
from pathlib import Path

from llm_wiki.audit.checks import find_stale_resonance, find_synthesis_without_resonance
from llm_wiki.config import WikiConfig
from llm_wiki.talk.page import TalkEntry, TalkPage


def _make_wiki(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return wiki_dir


def _write_page(wiki_dir: Path, slug: str, status: str | None = None) -> Path:
    page_path = wiki_dir / f"{slug}.md"
    fm = f"---\nstatus: {status}\n---\n" if status else "---\ntitle: Normal\n---\n"
    page_path.write_text(fm + "Content here.\n")
    return page_path


def _write_resonance_entry(page_path: Path, days_old: int) -> None:
    ts = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_old)
    ).isoformat()
    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp=ts,
        author="@resonance",
        body="New source may corroborate this claim.",
        severity="moderate",
        type="resonance",
    )
    talk.append(entry)


# --- find_stale_resonance ---

def test_stale_resonance_flags_old_open_resonance_entry(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "my-page")
    _write_resonance_entry(page_path, days_old=35)  # > 4-week default (28 days)

    config = WikiConfig()
    result = find_stale_resonance(tmp_path, config)
    assert result.check == "stale-resonance"
    assert len(result.issues) == 1
    assert result.issues[0].page == "my-page"


def test_stale_resonance_ignores_recent_entry(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "my-page")
    _write_resonance_entry(page_path, days_old=10)  # < 4 weeks

    config = WikiConfig()
    result = find_stale_resonance(tmp_path, config)
    assert len(result.issues) == 0


def test_stale_resonance_empty_wiki(tmp_path: Path):
    _make_wiki(tmp_path)
    result = find_stale_resonance(tmp_path, WikiConfig())
    assert len(result.issues) == 0


# --- find_synthesis_without_resonance ---

def test_synthesis_without_resonance_flags_old_synthesis_page(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    page_path = wiki_dir / "syn-page.md"
    page_path.write_text(
        f"---\ntype: synthesis\ningested: {old_date}\n---\nContent.\n"
    )

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = True
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 1
    assert result.issues[0].page == "syn-page"


def test_synthesis_without_resonance_skipped_when_disabled(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    page_path = wiki_dir / "syn-page.md"
    page_path.write_text(
        f"---\ntype: synthesis\ningested: {old_date}\n---\nContent.\n"
    )

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = False  # default
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 0


def test_synthesis_without_resonance_skips_if_resonance_talk_exists(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    page_path = wiki_dir / "syn-page.md"
    page_path.write_text(
        f"---\ntype: synthesis\ningested: {old_date}\n---\nContent.\n"
    )
    _write_resonance_entry(page_path, days_old=10)

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = True
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 0
