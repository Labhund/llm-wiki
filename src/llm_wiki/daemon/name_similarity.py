"""Name similarity for wiki_create near-match detection.

Two-stage hybrid:
  1. Jaccard token overlap (handles supersets like 'sRNA-tQuant-Pipeline'
     vs 'sRNA-tQuant' that Levenshtein misses).
  2. Normalized Levenshtein (handles typos like 'attentnion' vs 'attention'
     that token overlap misses).

Either stage flagging is enough to return True. The exact case-insensitive
collision check is the caller's responsibility (it runs first as a hard
'name-collision' error before this hybrid runs).
"""

from __future__ import annotations

from typing import Iterable

from llm_wiki.config import WriteConfig
from llm_wiki.daemon.v4a_patch import levenshtein


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _tokens(name: str) -> set[str]:
    return set(_normalize(name).split("-")) - {""}


def is_near_match(
    name: str,
    existing: str,
    jaccard_threshold: float,
    levenshtein_threshold: float,
) -> bool:
    """Return True if `name` and `existing` are likely the same concept."""
    a_tokens = _tokens(name)
    b_tokens = _tokens(existing)

    if a_tokens and b_tokens:
        union = a_tokens | b_tokens
        if union:
            jaccard = len(a_tokens & b_tokens) / len(union)
            if jaccard > jaccard_threshold:
                return True
        if a_tokens < b_tokens or b_tokens < a_tokens:
            return True

    a_str = _normalize(name)
    b_str = _normalize(existing)
    if a_str and b_str:
        longest = max(len(a_str), len(b_str))
        if longest > 0:
            sim = 1.0 - (levenshtein(a_str, b_str) / longest)
            if sim > levenshtein_threshold:
                return True

    return False


def find_near_matches(
    name: str,
    existing_names: Iterable[str],
    cfg: WriteConfig,
) -> list[str]:
    """Return existing names that are near-matches of `name`."""
    return [
        existing for existing in existing_names
        if is_near_match(
            name, existing,
            jaccard_threshold=cfg.name_jaccard_threshold,
            levenshtein_threshold=cfg.name_levenshtein_threshold,
        )
    ]
