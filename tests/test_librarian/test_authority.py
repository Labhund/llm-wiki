from __future__ import annotations

import datetime

from llm_wiki.librarian.authority import compute_authority, freshness_score
from llm_wiki.librarian.log_reader import PageUsage
from llm_wiki.manifest import ManifestEntry, SectionInfo


def _entry(name: str, links_to: list[str] | None = None, links_from: list[str] | None = None,
           last_corroborated: str | None = None, is_synthesis: bool = False) -> ManifestEntry:
    return ManifestEntry(
        name=name,
        title=name.title(),
        summary="",
        tags=[],
        cluster="default",
        tokens=100,
        sections=[SectionInfo(name="content", tokens=100)],
        links_to=links_to or [],
        links_from=links_from or [],
        last_corroborated=last_corroborated,
        is_synthesis=is_synthesis,
    )


# --- freshness_score helper ---


def test_freshness_none_is_neutral():
    """Pages never adversary-checked get neutral 0.5 (per spec)."""
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    assert freshness_score(None, now) == 0.5


def test_freshness_recent_is_max():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    yesterday = (now - datetime.timedelta(days=1)).isoformat()
    assert freshness_score(yesterday, now) == 1.0


def test_freshness_decays_to_neutral_at_90_days():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    old = (now - datetime.timedelta(days=90)).isoformat()
    score = freshness_score(old, now)
    assert abs(score - 0.5) < 0.01


def test_freshness_old_clamps_at_neutral():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    very_old = (now - datetime.timedelta(days=365)).isoformat()
    score = freshness_score(very_old, now)
    assert score == 0.5  # clamped, never below neutral for unverified pages


def test_freshness_invalid_iso_returns_neutral():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    assert freshness_score("not-a-date", now) == 0.5


# --- compute_authority ---


def test_compute_authority_empty_vault():
    assert compute_authority({}, {}) == {}


def test_compute_authority_no_usage_no_inlinks():
    """A page with no inlinks, no usage, no corroboration → just freshness * 0.2."""
    entries = {"a": _entry("a")}
    result = compute_authority(entries, {})
    # 0.3*0 + 0.4*0 + 0.2*0.5 + 0.1*0 = 0.10
    assert abs(result["a"] - 0.10) < 1e-6


def test_compute_authority_max_inlinks_normalizes_to_one():
    """The page with the most inlinks gets the full inlink contribution."""
    entries = {
        "popular": _entry("popular", links_from=["x", "y", "z"]),
        "lonely": _entry("lonely"),
    }
    result = compute_authority(entries, {})
    # popular: 0.3*1.0 + 0.4*0 + 0.2*0.5 + 0.1*0 = 0.40
    # lonely: 0.3*0 + 0.4*0 + 0.2*0.5 + 0.1*0 = 0.10
    assert abs(result["popular"] - 0.40) < 1e-6
    assert abs(result["lonely"] - 0.10) < 1e-6


def test_compute_authority_usage_contribution():
    """A page with high usage relevance gets the full usefulness contribution."""
    entries = {"a": _entry("a")}
    usage = {"a": PageUsage(name="a", read_count=5, turn_appearances=5, total_relevance=5.0)}
    result = compute_authority(entries, usage)
    # 0.3*0 + 0.4*1.0 + 0.2*0.5 + 0.1*0 = 0.50
    assert abs(result["a"] - 0.50) < 1e-6


def test_compute_authority_outlink_quality():
    """outlink_quality = fraction of links_to that resolve to pages in the vault."""
    entries = {
        "src": _entry("src", links_to=["dst", "missing"]),
        "dst": _entry("dst", links_from=["src"]),
    }
    result = compute_authority(entries, {})
    # src outlink_quality = 1/2 = 0.5
    # src: 0.3*0 + 0.4*0 + 0.2*0.5 + 0.1*0.5 = 0.15
    assert abs(result["src"] - 0.15) < 1e-6


def test_compute_authority_full_formula():
    """A page maxing out every component scores 1.0."""
    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = (now - datetime.timedelta(days=1)).isoformat()
    entries = {
        "star": _entry(
            "star",
            links_to=["target"],
            links_from=["a", "b", "c"],
            last_corroborated=yesterday,
        ),
        "target": _entry("target", links_from=["star"]),
    }
    usage = {"star": PageUsage(name="star", read_count=5, turn_appearances=5, total_relevance=5.0)}
    result = compute_authority(entries, usage)
    # star: 0.3*1.0 + 0.4*1.0 + 0.2*1.0 + 0.1*1.0 = 1.00
    assert abs(result["star"] - 1.00) < 1e-6


# --- synthesis_boost parameter ---


def test_synthesis_boost_raises_score():
    """A synthesis page with synthesis_boost=2.0 gets a higher score than without."""
    entries = {"synth": _entry("synth", is_synthesis=True)}
    baseline = compute_authority(entries, {}, synthesis_boost=1.0)
    boosted = compute_authority(entries, {}, synthesis_boost=2.0)
    assert boosted["synth"] > baseline["synth"]


def test_synthesis_boost_caps_at_one():
    """A synthesis page score never exceeds 1.0 regardless of boost magnitude."""
    entries = {
        "synth": _entry("synth", links_from=["a", "b", "c"], is_synthesis=True),
        "a": _entry("a"), "b": _entry("b"), "c": _entry("c"),
    }
    usage = {"synth": PageUsage(name="synth", read_count=5, turn_appearances=5, total_relevance=5.0)}
    result = compute_authority(entries, usage, synthesis_boost=10.0)
    assert result["synth"] <= 1.0


def test_synthesis_boost_below_one_penalises():
    """synthesis_boost < 1.0 reduces the synthesis page score (penalty case)."""
    entries = {
        "synth": _entry("synth", is_synthesis=True),
    }
    baseline = compute_authority(entries, {})
    penalised = compute_authority(entries, {}, synthesis_boost=0.5)
    assert penalised["synth"] < baseline["synth"]


def test_synthesis_boost_does_not_affect_non_synthesis():
    """Non-synthesis pages are unaffected by synthesis_boost."""
    entries = {"regular": _entry("regular")}
    baseline = compute_authority(entries, {}, synthesis_boost=1.0)
    boosted = compute_authority(entries, {}, synthesis_boost=3.0)
    assert boosted["regular"] == baseline["regular"]
