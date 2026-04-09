from __future__ import annotations

import pytest

from llm_wiki.config import WriteConfig


def test_is_near_match_proper_subset_via_jaccard():
    from llm_wiki.daemon.name_similarity import is_near_match
    # 'sRNA-tQuant-Pipeline' is a token superset of 'sRNA-tQuant'
    assert is_near_match(
        "sRNA-tQuant-Pipeline", "sRNA-tQuant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_typo_via_levenshtein():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "attentnion-mechanism", "attention-mechanism",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_high_token_overlap():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "the-attention-mechanism", "attention-mechanism",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_completely_different_returns_false():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert not is_near_match(
        "transformer-architecture", "k-means-clustering",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_case_insensitive():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "SRNA-TQUANT", "srna-tquant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_is_near_match_underscore_normalized_to_hyphen():
    from llm_wiki.daemon.name_similarity import is_near_match
    assert is_near_match(
        "srna_tquant", "srna-tquant",
        jaccard_threshold=0.5, levenshtein_threshold=0.85,
    )


def test_find_near_matches_returns_subset():
    from llm_wiki.daemon.name_similarity import find_near_matches
    cfg = WriteConfig()
    existing = ["transformer-architecture", "srna-tquant", "k-means-clustering"]
    matches = find_near_matches("sRNA-tQuant-Pipeline", existing, cfg)
    assert matches == ["srna-tquant"]


def test_find_near_matches_empty_when_nothing_close():
    from llm_wiki.daemon.name_similarity import find_near_matches
    cfg = WriteConfig()
    existing = ["transformer-architecture", "k-means-clustering"]
    matches = find_near_matches("brand-new-topic", existing, cfg)
    assert matches == []
