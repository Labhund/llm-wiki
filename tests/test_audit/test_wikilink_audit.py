from __future__ import annotations

import pytest

from llm_wiki.audit.wikilink_audit import (
    apply_wikilinks,
    build_link_pattern,
)


def test_build_link_pattern_empty_dict_returns_none():
    assert build_link_pattern({}) is None


def test_build_link_pattern_single_title():
    p = build_link_pattern({"PCA": "pca"})
    assert p is not None
    assert p.search("We use PCA for dimensionality reduction")


def test_build_link_pattern_longest_first_wins():
    """'Boltz Diffusion' must win over 'Boltz' when both are in the pattern."""
    title_to_slug = {"Boltz": "boltz", "Boltz Diffusion": "boltz-diffusion"}
    p = build_link_pattern(title_to_slug)
    m = p.search("Boltz Diffusion model")
    assert m is not None
    assert m.group(1) == "Boltz Diffusion"


def test_apply_wikilinks_basic():
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "We use PCA for reduction.", title_to_slug, "srna-embeddings", p
    )
    assert count == 1
    assert "[[pca|PCA]]" in new_text


def test_apply_wikilinks_all_occurrences():
    """All occurrences of the title are linked, not just the first."""
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "PCA is used here. PCA again here.", title_to_slug, "other", p
    )
    assert count == 2
    assert new_text.count("[[pca|PCA]]") == 2


def test_apply_wikilinks_case_insensitive_match():
    """Lower-case occurrence of a title gets linked with the canonical slug."""
    title_to_slug = {"K-Means": "k-means"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "We use k-means clustering.", title_to_slug, "other", p
    )
    assert count == 1
    assert "[[k-means" in new_text


def test_apply_wikilinks_skips_frontmatter():
    text = "---\ntitle: PCA\n---\n\nBody text."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    # PCA only appears in frontmatter → no link added
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_skips_code_fence():
    text = "Normal PCA text.\n\n```\ncode with PCA\n```"
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    # Only the first PCA (outside fence) gets linked
    assert count == 1
    assert "[[pca|PCA]]" in new_text
    assert "code with PCA" in new_text  # inside fence unchanged


def test_apply_wikilinks_skips_inline_code():
    text = "Use `PCA` in code but PCA in text."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 1
    assert "`PCA`" in new_text  # inline code unchanged


def test_apply_wikilinks_skips_existing_wikilink():
    text = "Already [[pca|PCA]] linked."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_skips_self_page():
    """A page must not link to itself."""
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(
        "PCA uses PCA decomposition.", title_to_slug, "pca", p
    )
    assert count == 0


def test_apply_wikilinks_no_change_already_all_linked():
    text = "Use [[pca|PCA]] and [[pca|PCA]] again."
    title_to_slug = {"PCA": "pca"}
    p = build_link_pattern(title_to_slug)
    new_text, count = apply_wikilinks(text, title_to_slug, "other", p)
    assert count == 0
    assert new_text == text


def test_apply_wikilinks_assertion_guards():
    """new_text always >= original length and wikilink count never shrinks."""
    title_to_slug = {"PCA": "pca", "K-Means": "k-means"}
    p = build_link_pattern(title_to_slug)
    original = "PCA and K-Means are clustering tools."
    new_text, count = apply_wikilinks(original, title_to_slug, "other", p)
    assert len(new_text) >= len(original)
    assert new_text.count("[[") >= original.count("[[")
