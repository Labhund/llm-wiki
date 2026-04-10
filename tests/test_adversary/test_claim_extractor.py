from __future__ import annotations

from pathlib import Path

from llm_wiki.adversary.claim_extractor import Claim, extract_claims
from llm_wiki.page import Page


def _make_page(tmp_path: Path, content: str) -> Page:
    page_file = tmp_path / "test.md"
    page_file.write_text(content, encoding="utf-8")
    return Page.parse(page_file)


def test_extract_claims_simple_citation(tmp_path: Path):
    """A sentence ending in [[raw/...]] is extracted as a claim."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "The k-means algorithm uses k=10 clusters [[raw/smith-2026.pdf]].\n"
    ))

    claims = extract_claims(page)
    assert len(claims) == 1
    claim = claims[0]
    assert isinstance(claim, Claim)
    assert claim.page == "test"
    assert claim.section == "overview"
    assert "k=10 clusters" in claim.text
    assert claim.citation == "raw/smith-2026.pdf"


def test_extract_claims_id_is_deterministic(tmp_path: Path):
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "Same sentence [[raw/a.pdf]].\n"
    ))
    claims1 = extract_claims(page)
    claims2 = extract_claims(page)
    assert claims1[0].id == claims2[0].id
    assert len(claims1[0].id) == 12


def test_extract_claims_skips_non_raw_citations(tmp_path: Path):
    """Wikilinks pointing at other pages (not raw/) are NOT claims."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: related %%\n## Related\n\n"
        "See [[other-page]] for details.\n"
    ))
    claims = extract_claims(page)
    assert claims == []


def test_extract_claims_skips_code_blocks(tmp_path: Path):
    """Citations inside fenced code blocks are not claims."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "```python\n# Citation [[raw/should-not-extract.pdf]] in a comment\n```\n"
    ))
    claims = extract_claims(page)
    assert claims == []


def test_extract_claims_skips_tilde_code_blocks(tmp_path: Path):
    """Tilde-fenced (~~~) code blocks are also skipped."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "~~~python\n# Citation [[raw/should-not-extract.pdf]] in a comment\n~~~\n"
    ))
    claims = extract_claims(page)
    assert claims == []


def test_extract_claims_skips_marker_lines(tmp_path: Path):
    """%% marker lines are not body content."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "Real claim [[raw/a.pdf]].\n"
    ))
    claims = extract_claims(page)
    # Only the real claim should be extracted, not anything from the marker
    assert len(claims) == 1
    assert "Real claim" in claims[0].text


def test_extract_claims_skips_frontmatter_source(tmp_path: Path):
    """frontmatter source field is not a body claim."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\nsource: \"[[raw/source.pdf]]\"\n---\n\n"
        "%% section: overview %%\n## Overview\n\nNo claims here.\n"
    ))
    claims = extract_claims(page)
    assert claims == []


def test_extract_claims_multiple_sentences_per_section(tmp_path: Path):
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "First claim [[raw/a.pdf]]. Second claim [[raw/b.pdf]]. "
        "Sentence without citation. Third claim [[raw/c.pdf]].\n"
    ))
    claims = extract_claims(page)
    citations = [c.citation for c in claims]
    assert "raw/a.pdf" in citations
    assert "raw/b.pdf" in citations
    assert "raw/c.pdf" in citations
    assert len(citations) == 3


def test_extract_claims_multiple_sections(tmp_path: Path):
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nClaim A [[raw/a.pdf]].\n"
        "%% section: method %%\n## Method\n\nClaim B [[raw/b.pdf]].\n"
    ))
    claims = extract_claims(page)
    sections = {c.section for c in claims}
    assert sections == {"overview", "method"}


def test_extract_claims_handles_trailing_punctuation_after_link(tmp_path: Path):
    """`text [[raw/x.pdf]].` should still be recognized as a claim."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\n"
        "Claim with period [[raw/a.pdf]].\n"
    ))
    claims = extract_claims(page)
    assert len(claims) == 1
    assert claims[0].citation == "raw/a.pdf"


def test_extract_claims_empty_page(tmp_path: Path):
    page = _make_page(tmp_path, "---\ntitle: Empty\n---\n\n")
    assert extract_claims(page) == []


def test_extract_claims_custom_raw_dir(tmp_path: Path):
    """extract_claims with a custom raw_dir matches that prefix, not 'raw/'."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The result is positive [[sources/smith-2026.pdf]].\n"
    ))
    # With default raw_dir="raw", should find no claims (prefix mismatch)
    assert extract_claims(page) == []
    # With raw_dir="sources", should find the claim
    claims = extract_claims(page, raw_dir="sources")
    assert len(claims) == 1
    assert claims[0].citation == "sources/smith-2026.pdf"


def test_extract_claims_custom_raw_dir_ignores_default_prefix(tmp_path: Path):
    """When raw_dir is customised, the default 'raw/' prefix is NOT matched."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The result is positive [[raw/smith-2026.pdf]].\n"
    ))
    # Explicitly passing raw_dir="sources" — should NOT match [[raw/...]]
    claims = extract_claims(page, raw_dir="sources")
    assert claims == []


def test_extract_claims_raw_dir_strips_trailing_slash(tmp_path: Path):
    """raw_dir="raw/" (with trailing slash) works identically to "raw"."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm converges [[raw/jones.md]].\n"
    ))
    claims = extract_claims(page, raw_dir="raw/")
    assert len(claims) == 1
    assert claims[0].citation == "raw/jones.md"
