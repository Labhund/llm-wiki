from __future__ import annotations

import datetime
from random import Random

from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.adversary.sampling import age_factor, sample_claims
from llm_wiki.manifest import ManifestEntry, SectionInfo


def _claim(page: str, idx: int = 0) -> Claim:
    return Claim(page=page, section="s", text=f"sentence {idx}", citation=f"raw/{page}.pdf")


def _entry(name: str, authority: float = 0.5, last_corroborated: str | None = None) -> ManifestEntry:
    return ManifestEntry(
        name=name, title=name.title(), summary="", tags=[], cluster="default",
        tokens=100, sections=[SectionInfo("c", 100)],
        links_to=[], links_from=[],
        authority=authority,
        last_corroborated=last_corroborated,
    )


# --- age_factor ---


def test_age_factor_none_is_max():
    """Pages never checked → highest priority for adversary review."""
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    assert age_factor(None, now) == 1.0


def test_age_factor_recent_is_low():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    yesterday = (now - datetime.timedelta(days=1)).isoformat()
    score = age_factor(yesterday, now)
    assert 0.0 <= score < 0.5


def test_age_factor_old_approaches_one():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    very_old = (now - datetime.timedelta(days=365)).isoformat()
    score = age_factor(very_old, now)
    assert 0.8 <= score <= 1.0


def test_age_factor_invalid_iso_returns_max():
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    assert age_factor("garbage", now) == 1.0


# --- sample_claims ---


def test_sample_claims_empty():
    result = sample_claims([], {}, n=5, rng=Random(0), now=datetime.datetime.now(datetime.timezone.utc))
    assert result == []


def test_sample_claims_n_cap():
    """Sample size never exceeds the claims list length."""
    claims = [_claim(f"p{i}", i) for i in range(3)]
    entries = {f"p{i}": _entry(f"p{i}") for i in range(3)}
    result = sample_claims(claims, entries, n=10, rng=Random(0), now=datetime.datetime.now(datetime.timezone.utc))
    assert len(result) == 3


def test_sample_claims_seeded_rng_is_deterministic():
    claims = [_claim(f"p{i}", i) for i in range(20)]
    entries = {f"p{i}": _entry(f"p{i}") for i in range(20)}
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    a = sample_claims(claims, entries, n=5, rng=Random(42), now=now)
    b = sample_claims(claims, entries, n=5, rng=Random(42), now=now)
    assert [c.id for c in a] == [c.id for c in b]


def test_sample_claims_favors_low_authority():
    """With many runs, low-authority pages are picked more often."""
    high_auth = [_claim(f"high{i}", i) for i in range(10)]
    low_auth = [_claim(f"low{i}", i) for i in range(10)]
    entries = {
        **{f"high{i}": _entry(f"high{i}", authority=0.95) for i in range(10)},
        **{f"low{i}": _entry(f"low{i}", authority=0.05) for i in range(10)},
    }
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)

    low_picked = 0
    for seed in range(50):
        sample = sample_claims(high_auth + low_auth, entries, n=2, rng=Random(seed), now=now)
        low_picked += sum(1 for c in sample if c.page.startswith("low"))

    # Low-authority claims should make up clearly more than half of picks
    assert low_picked > 60, f"low-authority claims should be favored, got {low_picked}/100"


def test_sample_claims_favors_stale_pages():
    """Pages never checked beat pages just checked."""
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    yesterday = (now - datetime.timedelta(days=1)).isoformat()

    stale = [_claim(f"stale{i}", i) for i in range(10)]
    fresh = [_claim(f"fresh{i}", i) for i in range(10)]
    entries = {
        **{f"stale{i}": _entry(f"stale{i}", last_corroborated=None) for i in range(10)},
        **{f"fresh{i}": _entry(f"fresh{i}", last_corroborated=yesterday) for i in range(10)},
    }

    stale_picked = 0
    for seed in range(50):
        sample = sample_claims(stale + fresh, entries, n=2, rng=Random(seed), now=now)
        stale_picked += sum(1 for c in sample if c.page.startswith("stale"))

    assert stale_picked > 60, f"stale claims should be favored, got {stale_picked}/100"


def test_sample_claims_handles_unknown_page_in_entries():
    """A claim whose page is missing from entries falls back to defaults."""
    claims = [_claim("orphan", 0)]
    now = datetime.datetime(2026, 4, 8, tzinfo=datetime.timezone.utc)
    result = sample_claims(claims, {}, n=1, rng=Random(0), now=now)
    assert len(result) == 1


def test_sample_claims_unread_source_weight():
    """Claims from unread sources are picked more often when unread_weight > 1."""
    now = datetime.datetime(2026, 4, 10, tzinfo=datetime.timezone.utc)
    unread = [_claim(f"u{i}", i) for i in range(10)]
    read_claims = [_claim(f"r{i}", i) for i in range(10)]

    # Give all entries equal authority and freshness so only unread_weight differs
    entries = {
        **{f"u{i}": _entry(f"u{i}", authority=0.5) for i in range(10)},
        **{f"r{i}": _entry(f"r{i}", authority=0.5) for i in range(10)},
    }
    # Unread source paths match claim citations: "raw/u0.pdf" etc.
    unread_sources = {f"raw/u{i}.pdf" for i in range(10)}

    unread_picked = 0
    for seed in range(50):
        sample = sample_claims(
            unread + read_claims, entries, n=2,
            rng=Random(seed), now=now,
            unread_sources=unread_sources,
            unread_weight=3.0,
        )
        unread_picked += sum(1 for c in sample if c.page.startswith("u"))

    assert unread_picked > 60, f"unread claims should be favored, got {unread_picked}/100"


def test_sample_claims_unread_weight_none_has_no_effect():
    """Passing unread_sources=None must not change sampling behavior."""
    now = datetime.datetime(2026, 4, 10, tzinfo=datetime.timezone.utc)
    claims = [_claim(f"p{i}", i) for i in range(10)]
    entries = {f"p{i}": _entry(f"p{i}") for i in range(10)}
    a = sample_claims(claims, entries, n=5, rng=Random(7), now=now, unread_sources=None)
    b = sample_claims(claims, entries, n=5, rng=Random(7), now=now)
    assert [c.id for c in a] == [c.id for c in b]
