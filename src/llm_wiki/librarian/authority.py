from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from llm_wiki.librarian.log_reader import PageUsage

if TYPE_CHECKING:
    from llm_wiki.manifest import ManifestEntry

# Spec formula weights
_W_INLINK = 0.3
_W_USEFULNESS = 0.4
_W_FRESHNESS = 0.2
_W_OUTLINK = 0.1

# Freshness decay window: linear from 1.0 (just checked) to 0.5 (neutral) at 90 days.
# Pages never checked also get 0.5 (neutral). Per spec, never below neutral.
# A short grace window treats "recently verified" as maximally fresh — the formula
# kicks in only once the verification is older than the grace period.
_FRESHNESS_FLOOR = 0.5
_FRESHNESS_GRACE_DAYS = 7.0
_FRESHNESS_DECAY_DAYS = 90.0


def freshness_score(
    last_corroborated_iso: str | None,
    now: datetime.datetime,
) -> float:
    """Compute the freshness component of the authority score.

    Per spec: pages never adversary-checked get a neutral 0.5, not zero.
    Recently checked pages decay linearly toward 0.5 over the decay window.
    """
    if last_corroborated_iso is None:
        return _FRESHNESS_FLOOR
    try:
        last = datetime.datetime.fromisoformat(last_corroborated_iso)
    except (ValueError, TypeError):
        return _FRESHNESS_FLOOR
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    delta_days = max(0.0, (now - last).total_seconds() / 86400.0)
    if delta_days <= _FRESHNESS_GRACE_DAYS:
        return 1.0
    if delta_days >= _FRESHNESS_DECAY_DAYS:
        return _FRESHNESS_FLOOR
    # Linear decay from 1.0 after the grace period to 0.5 at the decay horizon
    span = _FRESHNESS_DECAY_DAYS - _FRESHNESS_GRACE_DAYS
    return 1.0 - (1.0 - _FRESHNESS_FLOOR) * ((delta_days - _FRESHNESS_GRACE_DAYS) / span)


def compute_authority(
    entries: dict[str, "ManifestEntry"],
    usage: dict[str, PageUsage],
    *,
    synthesis_boost: float = 1.0,
) -> dict[str, float]:
    """Compute authority scores for every entry.

    authority = 0.3*inlink_norm + 0.4*usefulness + 0.2*freshness + 0.1*outlink_quality

    - inlink_norm: links_from count / max links_from in vault (0 if vault max is 0)
    - usefulness: avg_relevance from usage, capped at 1.0
    - freshness: per freshness_score()
    - outlink_quality: fraction of links_to that resolve to known pages
    - synthesis_boost: multiplier applied to synthesis pages (capped at 1.0)
    """
    if not entries:
        return {}

    now = datetime.datetime.now(datetime.timezone.utc)
    max_inlinks = max((len(e.links_from) for e in entries.values()), default=0)
    known_names = set(entries)

    result: dict[str, float] = {}
    for name, entry in entries.items():
        inlink = (len(entry.links_from) / max_inlinks) if max_inlinks > 0 else 0.0

        pu = usage.get(name)
        usefulness = min(1.0, pu.avg_relevance) if pu else 0.0

        fresh = freshness_score(entry.last_corroborated, now)

        if entry.links_to:
            valid = sum(1 for t in entry.links_to if t in known_names)
            outlink = valid / len(entry.links_to)
        else:
            outlink = 0.0

        score = (
            _W_INLINK * inlink
            + _W_USEFULNESS * usefulness
            + _W_FRESHNESS * fresh
            + _W_OUTLINK * outlink
        )

        if getattr(entry, "is_synthesis", False) and synthesis_boost != 1.0:
            score = min(1.0, score * synthesis_boost)

        result[name] = score

    return result
