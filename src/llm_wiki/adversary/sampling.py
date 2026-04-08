from __future__ import annotations

import datetime
import math
from random import Random
from typing import TYPE_CHECKING

from llm_wiki.adversary.claim_extractor import Claim

if TYPE_CHECKING:
    from llm_wiki.manifest import ManifestEntry


# Age factor decay window. Just-checked pages get min weight 0.2;
# never-checked pages get max weight 1.0; linear interpolation between.
_AGE_MIN = 0.2
_AGE_MAX = 1.0
_AGE_DECAY_DAYS = 90.0


def age_factor(last_corroborated_iso: str | None, now: datetime.datetime) -> float:
    """Sampling weight component based on time since last adversary check.

    Pages never checked get the maximum weight (1.0). Recently checked pages
    get the minimum (0.2), increasing linearly back to 1.0 at the decay
    window. This is the OPPOSITE of the librarian's freshness score —
    here we want to revisit STALE claims, not reward fresh ones.
    """
    if last_corroborated_iso is None:
        return _AGE_MAX
    try:
        last = datetime.datetime.fromisoformat(last_corroborated_iso)
    except (ValueError, TypeError):
        return _AGE_MAX
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    delta_days = max(0.0, (now - last).total_seconds() / 86400.0)
    if delta_days >= _AGE_DECAY_DAYS:
        return _AGE_MAX
    return _AGE_MIN + (_AGE_MAX - _AGE_MIN) * (delta_days / _AGE_DECAY_DAYS)


def sample_claims(
    claims: list[Claim],
    entries: dict[str, "ManifestEntry"],
    n: int,
    rng: Random,
    now: datetime.datetime,
) -> list[Claim]:
    """Weighted sample without replacement using the Efraimidis-Spirakis trick.

    weight(claim) = age_factor(claim_page) * (1.5 - authority(claim_page))

    Each claim is assigned key = -ln(rng.random()) / weight; the smallest
    n keys win. Deterministic for a seeded rng.
    """
    if not claims or n <= 0:
        return []

    keyed: list[tuple[float, Claim]] = []
    for claim in claims:
        entry = entries.get(claim.page)
        if entry is not None:
            authority = entry.authority
            last_corr = entry.last_corroborated
        else:
            authority = 0.0
            last_corr = None
        weight = age_factor(last_corr, now) * (1.5 - authority)
        if weight <= 0:
            weight = 1e-9
        u = rng.random()
        if u <= 0:
            u = 1e-9
        key = -math.log(u) / weight
        keyed.append((key, claim))

    keyed.sort(key=lambda kv: kv[0])
    return [c for _, c in keyed[:n]]
