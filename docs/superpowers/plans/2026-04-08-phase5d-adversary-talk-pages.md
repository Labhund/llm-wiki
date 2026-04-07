# Phase 5d: Adversary + Talk Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap reference:** See `docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md` for cross-cutting design decisions and the relationship to sub-phases 5a/5b/5c. **Read the roadmap's "Cross-cutting design decisions" and "What's already in place" sections before starting Task 1.**
>
> **Prerequisites:** Sub-phases 5a (issue queue), 5b (scheduler), and 5c (manifest overrides) must be merged. This plan registers a new worker via `_register_maintenance_workers` from 5b, files issues via `IssueQueue` from 5a, and updates `last_corroborated` via `ManifestOverrides` from 5c.

**Goal:** A scheduled adversary agent that samples wiki claims, fetches the cited raw source, and verifies the wiki claim against the raw text via LLM. Findings are dispatched by verdict: `validated` updates `last_corroborated` (so the freshness component of authority improves); `contradicted` and `unsupported` file `claim-failed` issues; `ambiguous` posts to the page's talk page asking for human review. Talk pages provide async human-agent discussion as flat chronological logs.

**Architecture:** Two new packages plus minimal wiring.

1. `adversary/` — claim extraction (sentence-level, with `[[raw/...]]` suffix), weighted sampling (favors stale and low-authority claims), verification prompt (returns `{verdict, confidence, explanation}` JSON), and `AdversaryAgent.run()` orchestration that dispatches by verdict.
2. `talk/` — `TalkPage` parser/writer for `<page>.talk.md` sidecar files, plus `ensure_talk_marker(page_path)` that injects an invisible `%% talk: [[<slug>.talk]] %%` marker on the parent page. Talk pages are excluded from `Vault.scan()`'s page indexing.
3. Minimal daemon/CLI surface: `talk` route family + `llm-wiki talk` command group for read/post/list.

The adversary reuses Phase 4's `extract_text()` to load raw source content. Claims with missing or unparseable raw sources are skipped (Phase 5a's auditor already flags broken citations — the adversary doesn't double-file).

**Tech Stack:** Python 3.11+, pytest-asyncio, existing `LLMClient`/`LLMQueue` (Phase 3), `extract_text` (Phase 4), `IssueQueue` (5a), `ManifestOverrides` (5c). **All LLM calls use `priority="maintenance"`.** No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  adversary/
    __init__.py             # package marker
    claim_extractor.py      # Claim, extract_claims
    sampling.py             # sample_claims (weighted by age + inverse authority)
    prompts.py              # compose_verification_messages, parse_verification, Verdict
    agent.py                # AdversaryAgent, AdversaryResult
  talk/
    __init__.py             # package marker
    page.py                 # TalkEntry, TalkPage
    discovery.py            # ensure_talk_marker
  vault.py                  # MODIFIED: exclude *.talk.md from page indexing
  daemon/
    server.py               # MODIFIED: register adversary worker, add talk-* routes
  cli/
    main.py                 # MODIFIED: add `talk` command group

tests/
  test_adversary/
    __init__.py
    test_claim_extractor.py
    test_sampling.py
    test_prompts.py
    test_agent.py
    test_integration.py
  test_talk/
    __init__.py
    test_page.py
    test_discovery.py
  test_daemon/
    test_talk_route.py
  test_cli/
    test_talk_cmd.py
  test_vault.py             # MODIFIED: assert *.talk.md excluded from pages
```

**Type flow across tasks:**

- `claim_extractor.py` defines `Claim(page, section, text, citation)` with property `id` (deterministic 12-char hex hash). `extract_claims(page) → list[Claim]` walks `page.sections`, splits into sentences, finds sentences ending in `[[raw/...]]`, skips code blocks and `%%` markers.
- `sampling.py` defines `sample_claims(claims, entries, n, rng, now) → list[Claim]`. Weight per claim = `age_factor(last_corroborated, now) * (1.5 - authority)`. Deterministic when `rng` is seeded.
- `prompts.py` defines `Verdict = Literal["validated", "contradicted", "unsupported", "ambiguous"]` and `compose_verification_messages(claim, raw_text)` + `parse_verification(text) → tuple[Verdict | None, float, str]`. Mirrors Phase 4's prompt parsing patterns.
- `agent.py` defines `AdversaryResult(claims_checked, validated, failed, issues_filed, talk_posts)` and `AdversaryAgent(vault, vault_root, llm, queue, config)` with `run()` and an internal `_verify_claim(claim) → Verdict`.
- `talk/page.py` defines `TalkEntry(timestamp, author, body)` and `TalkPage(path)` with `load/append/exists/for_page/parent_page_slug`.
- `talk/discovery.py` defines `ensure_talk_marker(page_path) → bool` (returns True if a marker was inserted; idempotent).
- `daemon/server.py` adds `talk-read`, `talk-append`, `talk-list` routes and extends `_register_maintenance_workers()` to register an `adversary` worker via the same extension point 5c uses.

**Cross-cutting reminders from the roadmap:**
- The adversary updates `ManifestOverrides.last_corroborated` for validated claims. It MUST NOT modify any markdown body content.
- "Human prose is sacred": the adversary's only write paths are (a) the issue queue, (b) talk pages, and (c) the override sidecar. Talk pages may be appended to but never rewritten — the format is append-only.
- All LLM calls use `priority="maintenance"`.
- Empty vault is valid: every function and worker must handle a vault with zero pages, zero claims, or a missing raw file without raising.
- Talk pages are excluded from `Vault.scan()`'s page indexing — the daemon should not treat them as wiki pages.

---

### Task 1: Package Skeletons

**Files:**
- Create: `src/llm_wiki/adversary/__init__.py`
- Create: `src/llm_wiki/talk/__init__.py`
- Create: `tests/test_adversary/__init__.py`
- Create: `tests/test_talk/__init__.py`

- [ ] **Step 1: Create empty package markers**

```python
# src/llm_wiki/adversary/__init__.py
```

```python
# src/llm_wiki/talk/__init__.py
```

```python
# tests/test_adversary/__init__.py
```

```python
# tests/test_talk/__init__.py
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `pytest -q`
Expected: All Phase 1-5c tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/llm_wiki/adversary/__init__.py src/llm_wiki/talk/__init__.py \
        tests/test_adversary/__init__.py tests/test_talk/__init__.py
git commit -m "feat: phase 5d skeleton — adversary + talk packages"
```

---

### Task 2: `Claim` dataclass + `extract_claims`

**Files:**
- Create: `src/llm_wiki/adversary/claim_extractor.py`
- Create: `tests/test_adversary/test_claim_extractor.py`

A `Claim` is a sentence in a page section that ends with a `[[raw/...]]` citation. The extractor walks `page.sections`, splits each section's content into sentences, and yields one `Claim` per sentence that ends in a verifiable citation.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_adversary/test_claim_extractor.py
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
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_adversary/test_claim_extractor.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `Claim` + `extract_claims`**

```python
# src/llm_wiki/adversary/claim_extractor.py
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_wiki.page import Page


# A sentence is "completed" by sentence-final punctuation OR end of section.
# We split on sentence-final punctuation followed by whitespace/end.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CODE_FENCE_RE = re.compile(r"^```")
_MARKER_LINE_RE = re.compile(r"^%%\s*section:")
# Find a [[raw/...]] citation that is the LAST wikilink in the sentence
# (allowing optional trailing punctuation/whitespace after the link).
_TRAILING_RAW_CITATION_RE = re.compile(
    r"\[\[(raw/[^\]|]+)(?:\|[^\]]+)?\]\]\s*[.!?]?\s*$"
)


@dataclass
class Claim:
    """One verifiable assertion: a sentence with a [[raw/...]] suffix citation."""
    page: str
    section: str
    text: str
    citation: str

    @property
    def id(self) -> str:
        """12-char hex hash deterministic in (page, section, text)."""
        digest = hashlib.sha256(
            f"{self.page}|{self.section}|{self.text}".encode("utf-8")
        ).hexdigest()
        return digest[:12]


def extract_claims(page: "Page") -> list[Claim]:
    """Extract all verifiable claims from a parsed page.

    A claim is a sentence inside a section body that ends with a
    [[raw/...]] citation. Code blocks, %% marker lines, and frontmatter
    fields are excluded. The page's frontmatter `source` field is NOT
    counted as a claim — only body content is.
    """
    claims: list[Claim] = []
    page_slug = page.path.stem

    for section in page.sections:
        sentences = _extract_body_sentences(section.content)
        for sentence in sentences:
            match = _TRAILING_RAW_CITATION_RE.search(sentence)
            if match is None:
                continue
            citation = match.group(1)
            claims.append(Claim(
                page=page_slug,
                section=section.name,
                text=sentence.strip(),
                citation=citation,
            ))
    return claims


def _extract_body_sentences(content: str) -> list[str]:
    """Sentences from non-code, non-marker lines of a section body."""
    keep_lines: list[str] = []
    in_code = False
    for line in content.splitlines():
        stripped = line.strip()
        if _CODE_FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code:
            continue
        if _MARKER_LINE_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        keep_lines.append(line)
    joined = " ".join(l.strip() for l in keep_lines if l.strip())
    if not joined:
        return []
    sentences = _SENTENCE_SPLIT_RE.split(joined)
    return [s.strip() for s in sentences if s.strip()]
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_adversary/test_claim_extractor.py -v`
Expected: All ten claim extractor tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/claim_extractor.py tests/test_adversary/test_claim_extractor.py
git commit -m "feat: extract_claims — sentence-level claim extraction with raw/ filter"
```

---

### Task 3: `sample_claims` weighted sampling

**Files:**
- Create: `src/llm_wiki/adversary/sampling.py`
- Create: `tests/test_adversary/test_sampling.py`

Spec sampling weights:
- **Age factor:** `1.0` if never adversary-checked, decreasing toward `0.2` for recently-checked claims
- **Inverse authority:** `(1.5 - authority)` so authority `0.0` → weight `1.5`, authority `1.0` → weight `0.5`
- **Random jitter:** seeded RNG produces deterministic results in tests

Final weight: `age_factor * inverse_authority`. Sample without replacement using the standard "weighted top-k" trick: `key = -ln(rng.random()) / weight`, sort by key, take first n.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_adversary/test_sampling.py
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
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_adversary/test_sampling.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `sample_claims` + `age_factor`**

```python
# src/llm_wiki/adversary/sampling.py
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
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_adversary/test_sampling.py -v`
Expected: All sampling tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/sampling.py tests/test_adversary/test_sampling.py
git commit -m "feat: sample_claims — weighted by age + inverse authority"
```

---

### Task 4: Adversary verification prompt + parser

**Files:**
- Create: `src/llm_wiki/adversary/prompts.py`
- Create: `tests/test_adversary/test_prompts.py`

LLM is given the wiki claim text and the raw source text. Returns a JSON verdict with one of: `validated`, `contradicted`, `unsupported`, `ambiguous`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_adversary/test_prompts.py
from __future__ import annotations

from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.adversary.prompts import (
    compose_verification_messages,
    parse_verification,
)


def _claim() -> Claim:
    return Claim(
        page="srna-embeddings",
        section="method",
        text="The k-means algorithm uses k=10 clusters [[raw/smith-2026.pdf]].",
        citation="raw/smith-2026.pdf",
    )


def test_compose_verification_messages_includes_claim_and_source():
    messages = compose_verification_messages(_claim(), raw_text="Source text body here.")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "k=10 clusters" in user
    assert "Source text body here." in user
    assert "srna-embeddings" in user
    assert "raw/smith-2026.pdf" in user


def test_compose_verification_messages_truncates_long_source():
    """Very long raw text is truncated to fit within the prompt budget."""
    long_text = "x" * 100_000
    messages = compose_verification_messages(_claim(), raw_text=long_text, max_chars=4000)
    user = messages[1]["content"]
    # Should contain a truncated version, not the full text
    assert len(user) < 100_000
    assert "x" in user


def test_parse_verification_validated():
    text = '{"verdict": "validated", "confidence": 0.9, "explanation": "Source matches."}'
    verdict, confidence, explanation = parse_verification(text)
    assert verdict == "validated"
    assert confidence == 0.9
    assert explanation == "Source matches."


def test_parse_verification_contradicted():
    text = '{"verdict": "contradicted", "confidence": 0.85, "explanation": "Source says k=5, not k=10."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "contradicted"


def test_parse_verification_unsupported():
    text = '{"verdict": "unsupported", "confidence": 0.7, "explanation": "Claim not in source."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "unsupported"


def test_parse_verification_ambiguous():
    text = '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Source unclear."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "ambiguous"


def test_parse_verification_invalid_verdict_returns_none():
    text = '{"verdict": "maybe", "confidence": 0.5, "explanation": "x"}'
    verdict, _, _ = parse_verification(text)
    assert verdict is None


def test_parse_verification_garbage_returns_none():
    verdict, confidence, explanation = parse_verification("not JSON")
    assert verdict is None
    assert confidence == 0.0
    assert explanation == ""


def test_parse_verification_missing_fields_safe_defaults():
    text = '{"verdict": "validated"}'
    verdict, confidence, explanation = parse_verification(text)
    assert verdict == "validated"
    assert confidence == 0.0
    assert explanation == ""


def test_parse_verification_fenced_json():
    text = """```json
{"verdict": "validated", "confidence": 0.9, "explanation": "ok"}
```"""
    verdict, _, _ = parse_verification(text)
    assert verdict == "validated"
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_adversary/test_prompts.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement prompts**

```python
# src/llm_wiki/adversary/prompts.py
from __future__ import annotations

import json
import re
from typing import Literal

from llm_wiki.adversary.claim_extractor import Claim

Verdict = Literal["validated", "contradicted", "unsupported", "ambiguous"]
_VALID_VERDICTS = {"validated", "contradicted", "unsupported", "ambiguous"}


_ADVERSARY_SYSTEM = """\
You are the adversary for a wiki. Your job is to verify whether a wiki claim \
is actually supported by the raw source it cites.

## Task

You will see ONE wiki claim and the text of the raw source it cites. Decide \
which of these verdicts applies:

- "validated"     — The source clearly supports the claim as written.
- "contradicted"  — The source clearly says something different, in a way that \
                    makes the claim wrong.
- "unsupported"   — The claim is not actually present in the source, even \
                    though the source is on-topic.
- "ambiguous"     — The source could be read either way, or you cannot tell.

Be strict. If the source says "X correlates with Y" but the claim says "X causes \
Y", that is "contradicted" — you must NOT extend the source's interpretation.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "verdict": "validated|contradicted|unsupported|ambiguous",
  "confidence": 0.85,
  "explanation": "One or two sentences explaining your verdict."
}"""


def compose_verification_messages(
    claim: Claim,
    raw_text: str,
    max_chars: int = 8000,
) -> list[dict[str, str]]:
    """Build the verification prompt for one claim against its raw source."""
    truncated = raw_text[:max_chars]
    user = (
        f"## Wiki Page\n{claim.page}\n\n"
        f"## Section\n{claim.section}\n\n"
        f"## Wiki Claim\n{claim.text}\n\n"
        f"## Cited Source\n{claim.citation}\n\n"
        f"## Source Text\n{truncated}"
    )
    return [
        {"role": "system", "content": _ADVERSARY_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    bare = re.search(r"\{.*\}", text, re.DOTALL)
    if bare:
        try:
            return json.loads(bare.group(0))
        except json.JSONDecodeError:
            pass
    return None


def parse_verification(text: str) -> tuple[Verdict | None, float, str]:
    """Parse an adversary LLM response into (verdict, confidence, explanation).

    Invalid verdicts return None. Missing confidence/explanation default to
    0.0 / empty string.
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None, 0.0, ""

    raw_verdict = data.get("verdict")
    if not isinstance(raw_verdict, str) or raw_verdict not in _VALID_VERDICTS:
        return None, 0.0, ""

    raw_confidence = data.get("confidence", 0.0)
    confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else 0.0

    raw_explanation = data.get("explanation", "")
    explanation = raw_explanation if isinstance(raw_explanation, str) else ""

    return raw_verdict, confidence, explanation  # type: ignore[return-value]
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_adversary/test_prompts.py -v`
Expected: All prompt tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/prompts.py tests/test_adversary/test_prompts.py
git commit -m "feat: adversary verification prompt + parser"
```

---

### Task 5: `TalkEntry` + `TalkPage` parser

**Files:**
- Create: `src/llm_wiki/talk/page.py`
- Create: `tests/test_talk/test_page.py`

Talk pages are sidecar `.talk.md` files with frontmatter `page: <slug>` and chronological entries. Format per spec:

```markdown
---
page: srna-embeddings
---

**2026-04-08T15:01:00+00:00 — @adversary**
First entry body.

**2026-04-08T16:22:00+00:00 — @human**
Second entry body.
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_talk/test_page.py
from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.talk.page import TalkEntry, TalkPage


def test_for_page_derives_sidecar_path(tmp_path: Path):
    page = tmp_path / "wiki" / "srna-embeddings.md"
    page.parent.mkdir()
    page.write_text("# srna\n")

    talk = TalkPage.for_page(page)
    assert talk.path == tmp_path / "wiki" / "srna-embeddings.talk.md"
    assert talk.parent_page_slug == "srna-embeddings"


def test_exists_false_when_file_missing(tmp_path: Path):
    talk = TalkPage(tmp_path / "x.talk.md")
    assert talk.exists is False


def test_load_missing_file_returns_empty(tmp_path: Path):
    talk = TalkPage(tmp_path / "x.talk.md")
    assert talk.load() == []


def test_append_creates_file_with_frontmatter(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "srna-embeddings.talk.md")
    entry = TalkEntry(
        timestamp="2026-04-08T15:01:00+00:00",
        author="@adversary",
        body="First entry body.",
    )
    talk.append(entry)

    assert talk.exists
    text = talk.path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "page: srna-embeddings" in text
    assert "@adversary" in text
    assert "First entry body." in text


def test_append_to_existing_file_preserves_prior_entries(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "srna-embeddings.talk.md")
    talk.append(TalkEntry("2026-04-08T15:01:00+00:00", "@adversary", "First."))
    talk.append(TalkEntry("2026-04-08T16:22:00+00:00", "@human", "Second."))

    entries = talk.load()
    assert len(entries) == 2
    assert entries[0].body == "First."
    assert entries[1].body == "Second."
    assert entries[0].author == "@adversary"
    assert entries[1].author == "@human"


def test_load_round_trip_preserves_chronology(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "p.talk.md")
    timestamps = [
        "2026-04-01T10:00:00+00:00",
        "2026-04-02T10:00:00+00:00",
        "2026-04-03T10:00:00+00:00",
    ]
    for i, ts in enumerate(timestamps):
        talk.append(TalkEntry(ts, f"@a{i}", f"body {i}"))

    entries = talk.load()
    assert [e.timestamp for e in entries] == timestamps
    assert [e.body for e in entries] == ["body 0", "body 1", "body 2"]


def test_append_handles_multiline_body(tmp_path: Path):
    talk = TalkPage(tmp_path / "wiki" / "p.talk.md")
    body = "First line.\n\nSecond paragraph.\n\nThird paragraph."
    talk.append(TalkEntry("2026-04-08T10:00:00+00:00", "@adversary", body))

    entries = talk.load()
    assert len(entries) == 1
    assert "Second paragraph" in entries[0].body
    assert "Third paragraph" in entries[0].body
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_talk/test_page.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `TalkEntry` + `TalkPage`**

```python
# src/llm_wiki/talk/page.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


# Matches an entry header line: **<iso-timestamp> — @<author>**
_ENTRY_HEADER_RE = re.compile(
    r"^\*\*(?P<ts>\S+)\s*[—-]\s*(?P<author>@\S+)\*\*\s*$",
    re.MULTILINE,
)


@dataclass
class TalkEntry:
    """One chronological entry in a talk page."""
    timestamp: str
    author: str
    body: str


class TalkPage:
    """Append-only sidecar discussion file at <wiki_dir>/<page>.talk.md.

    Format:
        ---
        page: <slug>
        ---

        **<timestamp> — @<author>**
        body...

        **<timestamp> — @<author>**
        body...

    Talk pages are excluded from Vault.scan() page indexing — see Task 8.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def for_page(cls, page_path: Path) -> "TalkPage":
        """Derive the sidecar talk path for a wiki page path."""
        return cls(page_path.parent / f"{page_path.stem}.talk.md")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return self._path.exists()

    @property
    def parent_page_slug(self) -> str:
        """Strip the .talk suffix from the file stem to get the parent slug."""
        stem = self._path.stem  # foo.talk
        if stem.endswith(".talk"):
            return stem[: -len(".talk")]
        return stem

    def load(self) -> list[TalkEntry]:
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        body = self._strip_frontmatter(text)

        headers = list(_ENTRY_HEADER_RE.finditer(body))
        entries: list[TalkEntry] = []
        for i, match in enumerate(headers):
            ts = match.group("ts")
            author = match.group("author")
            content_start = match.end()
            content_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
            entry_body = body[content_start:content_end].strip()
            entries.append(TalkEntry(timestamp=ts, author=author, body=entry_body))
        return entries

    def append(self, entry: TalkEntry) -> None:
        """Append a new entry, creating the file with frontmatter if missing."""
        block = (
            f"\n**{entry.timestamp} — {entry.author}**\n"
            f"{entry.body.strip()}\n"
        )
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            frontmatter = yaml.dump(
                {"page": self.parent_page_slug},
                default_flow_style=False,
            ).strip()
            self._path.write_text(
                f"---\n{frontmatter}\n---\n{block}", encoding="utf-8"
            )
        else:
            existing = self._path.read_text(encoding="utf-8").rstrip()
            self._path.write_text(existing + "\n" + block, encoding="utf-8")

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return text
        return text[end + 4:].lstrip()
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_talk/test_page.py -v`
Expected: All TalkPage tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/talk/page.py tests/test_talk/test_page.py
git commit -m "feat: TalkPage — append-only sidecar discussion files"
```

---

### Task 6: `ensure_talk_marker` discovery

**Files:**
- Create: `src/llm_wiki/talk/discovery.py`
- Create: `tests/test_talk/test_discovery.py`

When a talk page exists, the parent wiki page should contain a `%% talk: [[<slug>.talk]] %%` marker so agents reading the page can discover the discussion. The marker is invisible in Obsidian preview mode.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_talk/test_discovery.py
from __future__ import annotations

from pathlib import Path

from llm_wiki.talk.discovery import ensure_talk_marker


def test_ensure_talk_marker_inserts_when_missing(tmp_path: Path):
    page = tmp_path / "srna-embeddings.md"
    page.write_text("---\ntitle: sRNA\n---\n\nContent.\n")

    inserted = ensure_talk_marker(page)
    assert inserted is True

    text = page.read_text(encoding="utf-8")
    assert "%% talk: [[srna-embeddings.talk]] %%" in text
    # Marker is at the end
    assert text.rstrip().endswith("%% talk: [[srna-embeddings.talk]] %%")


def test_ensure_talk_marker_idempotent(tmp_path: Path):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: P\n---\n\nContent.\n")

    assert ensure_talk_marker(page) is True
    assert ensure_talk_marker(page) is False  # already present
    text = page.read_text(encoding="utf-8")
    # Marker only appears once
    assert text.count("%% talk: [[p.talk]] %%") == 1


def test_ensure_talk_marker_preserves_existing_content(tmp_path: Path):
    page = tmp_path / "p.md"
    original = "---\ntitle: P\n---\n\n## Overview\n\nImportant content [[raw/x.pdf]].\n"
    page.write_text(original)

    ensure_talk_marker(page)
    text = page.read_text(encoding="utf-8")
    assert "## Overview" in text
    assert "Important content [[raw/x.pdf]]" in text
    assert "title: P" in text
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_talk/test_discovery.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `ensure_talk_marker`**

```python
# src/llm_wiki/talk/discovery.py
from __future__ import annotations

from pathlib import Path


def ensure_talk_marker(page_path: Path) -> bool:
    """Append a %% talk: [[<slug>.talk]] %% marker to a wiki page if missing.

    The marker is invisible in Obsidian's preview mode but visible in source
    mode and parseable by the daemon. Idempotent: returns False if the
    marker is already present.
    """
    slug = page_path.stem
    marker = f"%% talk: [[{slug}.talk]] %%"
    text = page_path.read_text(encoding="utf-8")
    if marker in text:
        return False
    page_path.write_text(text.rstrip() + f"\n\n{marker}\n", encoding="utf-8")
    return True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_talk/test_discovery.py -v`
Expected: All discovery tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/talk/discovery.py tests/test_talk/test_discovery.py
git commit -m "feat: ensure_talk_marker — invisible discovery marker for talk pages"
```

---

### Task 7: Exclude `*.talk.md` from `Vault.scan()`

**Files:**
- Modify: `src/llm_wiki/vault.py`
- Modify: `tests/test_vault.py`

Talk pages are sidecars, not wiki pages. They must not appear in `vault.manifest_entries()` or be searchable.

- [ ] **Step 1: Add failing test**

Append to `tests/test_vault.py`:

```python
def test_vault_scan_excludes_talk_pages(sample_vault):
    """*.talk.md files are not indexed as wiki pages."""
    from llm_wiki.vault import Vault

    # Create a talk page sidecar in the fixture vault
    talk = sample_vault / "bioinformatics" / "srna-embeddings.talk.md"
    talk.write_text(
        "---\npage: srna-embeddings\n---\n\n"
        "**2026-04-08T10:00:00+00:00 — @adversary**\nVerified.\n"
    )

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()

    # The wiki page is still indexed
    assert "srna-embeddings" in entries
    # The talk page is NOT
    assert "srna-embeddings.talk" not in entries
    assert not any(name.endswith(".talk") for name in entries)
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_vault.py -v -k excludes_talk`
Expected: Failure — `srna-embeddings.talk` appears as a page entry.

- [ ] **Step 3: Modify `Vault.scan()`**

In `src/llm_wiki/vault.py`, update the file filter inside `Vault.scan()`:

```python
        md_files = sorted(root.rglob("*.md"))
        md_files = [
            f for f in md_files
            if not any(p.startswith(".") for p in f.relative_to(root).parts)
            and not f.name.endswith(".talk.md")
        ]
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_vault.py -v`
Expected: All vault tests pass — including the new exclusion test AND the existing scan tests (which never used .talk.md fixtures, so they're unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py
git commit -m "feat: Vault.scan excludes *.talk.md sidecar files"
```

---

### Task 8: `AdversaryAgent.run()`

**Files:**
- Create: `src/llm_wiki/adversary/agent.py`
- Create: `tests/test_adversary/test_agent.py`

The orchestrator: extract claims from every page, sample N, fetch raw text via `extract_text`, verify each claim via LLM, dispatch by verdict.

Verdict pathways:
- `validated` → update `ManifestOverrides.last_corroborated[page]` to `now` + ensure_talk_marker on the parent page (no, only update overrides; talk marker is for ambiguous)
- `contradicted` / `unsupported` → file `claim-failed` issue with the explanation
- `ambiguous` → append a `@adversary` entry to the page's talk page + `ensure_talk_marker(page_path)` so humans can discover it
- raw extraction failure → log + skip (5a's auditor already files broken-citation)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_adversary/test_agent.py
from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.adversary.agent import AdversaryAgent, AdversaryResult
from llm_wiki.config import MaintenanceConfig, WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides
from llm_wiki.talk.page import TalkPage
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    """Async LLM stub returning a scripted verdict response."""

    def __init__(self, response_text: str) -> None:
        self.response = response_text
        self.calls: list = []

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        self.calls.append((messages, priority))
        return LLMResponse(content=self.response, tokens_used=100)


def _build_vault_with_one_claim(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tiny vault with one page citing one raw markdown file.

    Returns (vault_root, page_path). Using markdown for the raw source
    avoids the liteparse dependency in tests (Phase 4 extract_text reads
    .md files directly).
    """
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "smith-2026.md").write_text(
        "# Smith 2026\n\nThe k-means algorithm uses k=10 clusters.\n"
    )

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page = wiki_dir / "k-means.md"
    page.write_text(
        "---\ntitle: K-Means\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm uses k=10 clusters [[raw/smith-2026.md]].\n"
    )
    return tmp_path, page


@pytest.mark.asyncio
async def test_adversary_validated_updates_last_corroborated(tmp_path: Path):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=5),
    )
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.95, "explanation": "Source matches exactly."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()

    assert isinstance(result, AdversaryResult)
    assert result.claims_checked == 1
    assert len(result.validated) == 1
    assert result.failed == []
    assert stub.calls[0][1] == "maintenance"

    overrides = ManifestOverrides.load(_state_dir_for(vault_root) / "manifest_overrides.json")
    page_override = overrides.get("k-means")
    assert page_override is not None
    assert page_override.last_corroborated is not None


@pytest.mark.asyncio
async def test_adversary_contradicted_files_issue(tmp_path: Path):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM(
        '{"verdict": "contradicted", "confidence": 0.9, "explanation": "Source says k=5 not k=10."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.failed) == 1
    assert len(result.issues_filed) >= 1

    issue = queue.get(result.issues_filed[0])
    assert issue is not None
    assert issue.type == "claim-failed"
    assert issue.detected_by == "adversary"
    assert "k=5" in issue.body


@pytest.mark.asyncio
async def test_adversary_unsupported_files_issue(tmp_path: Path):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM(
        '{"verdict": "unsupported", "confidence": 0.8, "explanation": "Claim not in source."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.failed) == 1
    assert len(result.issues_filed) >= 1


@pytest.mark.asyncio
async def test_adversary_ambiguous_posts_to_talk_page(tmp_path: Path):
    vault_root, page_path = _build_vault_with_one_claim(tmp_path)
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM(
        '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Source unclear."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    assert len(result.talk_posts) == 1

    talk = TalkPage.for_page(page_path)
    assert talk.exists
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].author == "@adversary"
    assert "Source unclear" in entries[0].body

    # Parent page should have the discovery marker
    page_text = page_path.read_text(encoding="utf-8")
    assert "%% talk: [[k-means.talk]] %%" in page_text


@pytest.mark.asyncio
async def test_adversary_skips_when_raw_source_missing(tmp_path: Path):
    """If the cited raw file does not exist, the claim is skipped."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "p.md").write_text(
        "---\ntitle: P\n---\n\n%% section: method %%\n## Method\n\n"
        "Claim [[raw/missing.md]].\n"
    )
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path / "wiki")
    agent = AdversaryAgent(vault, tmp_path, stub, queue, config)

    result = await agent.run()
    assert result.claims_checked == 0
    assert result.validated == []
    assert stub.calls == []  # never called the LLM


@pytest.mark.asyncio
async def test_adversary_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    config = WikiConfig()
    agent = AdversaryAgent(
        vault, tmp_path, _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}'),
        IssueQueue(tmp_path / "wiki"), config,
    )
    result = await agent.run()
    assert result.claims_checked == 0
    assert result.validated == []
    assert result.failed == []


@pytest.mark.asyncio
async def test_adversary_unparseable_response_skips_claim(tmp_path: Path):
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    config.vault.wiki_dir = "wiki/"

    stub = _StubLLM("complete garbage, not JSON")
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    result = await agent.run()
    # The claim was attempted but verdict could not be parsed
    assert result.claims_checked == 1
    assert result.validated == []
    assert result.failed == []
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_adversary/test_agent.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `AdversaryAgent`**

```python
# src/llm_wiki/adversary/agent.py
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

from llm_wiki.adversary.claim_extractor import Claim, extract_claims
from llm_wiki.adversary.prompts import (
    Verdict,
    compose_verification_messages,
    parse_verification,
)
from llm_wiki.adversary.sampling import sample_claims
from llm_wiki.config import WikiConfig
from llm_wiki.ingest.extractor import extract_text
from llm_wiki.issues.queue import Issue, IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.talk.discovery import ensure_talk_marker
from llm_wiki.talk.page import TalkEntry, TalkPage
from llm_wiki.vault import Vault, _state_dir_for

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class AdversaryResult:
    claims_checked: int = 0
    validated: list[str] = field(default_factory=list)         # claim ids
    failed: list[str] = field(default_factory=list)            # claim ids
    issues_filed: list[str] = field(default_factory=list)      # issue ids
    talk_posts: list[str] = field(default_factory=list)        # page slugs


class AdversaryAgent:
    """Verifies sampled wiki claims against their cited raw sources.

    Verdict pathways:
      - validated   → update ManifestOverrides.last_corroborated for the page
      - contradicted/unsupported → file a 'claim-failed' issue
      - ambiguous   → append an @adversary entry to the page's talk page,
                      ensure the parent has a discovery marker
      - raw extract fails → log + skip (auditor handles broken-citation)
    """

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: "LLMClient",
        queue: IssueQueue,
        config: WikiConfig,
        rng: Random | None = None,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._queue = queue
        self._config = config
        self._rng = rng or Random()
        self._state_dir = _state_dir_for(vault_root)
        self._overrides_path = self._state_dir / "manifest_overrides.json"
        self._wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")

    async def run(self) -> AdversaryResult:
        result = AdversaryResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        # 1. Extract claims from every page
        all_claims: list[Claim] = []
        for name in entries:
            page = self._vault.read_page(name)
            if page is None:
                continue
            all_claims.extend(extract_claims(page))

        if not all_claims:
            return result

        # 2. Sample
        n = self._config.maintenance.adversary_claims_per_run
        now = datetime.datetime.now(datetime.timezone.utc)
        sampled = sample_claims(all_claims, entries, n=n, rng=self._rng, now=now)

        # 3. Verify each
        for claim in sampled:
            await self._process_claim(claim, result, now)

        return result

    async def _process_claim(
        self,
        claim: Claim,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        # Resolve raw source
        raw_path = self._vault_root / claim.citation
        if not raw_path.exists():
            logger.info("Adversary: raw source missing for %s, skipping", claim.id)
            return

        extraction = await extract_text(raw_path)
        if not extraction.success:
            logger.info(
                "Adversary: extraction failed for %s (%s), skipping",
                raw_path, extraction.error,
            )
            return

        result.claims_checked += 1

        messages = compose_verification_messages(claim, raw_text=extraction.content)
        try:
            response = await self._llm.complete(
                messages, temperature=0.2, priority="maintenance"
            )
        except Exception:
            logger.exception("Adversary: LLM call failed for claim %s", claim.id)
            return

        verdict, confidence, explanation = parse_verification(response.content)
        if verdict is None:
            logger.info("Adversary: unparseable verdict for claim %s", claim.id)
            return

        if verdict == "validated":
            self._handle_validated(claim, result, now)
        elif verdict in ("contradicted", "unsupported"):
            self._handle_failed(claim, verdict, confidence, explanation, result)
        else:  # ambiguous
            self._handle_ambiguous(claim, explanation, result, now)

    def _handle_validated(
        self,
        claim: Claim,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        overrides = ManifestOverrides.load(self._overrides_path)
        existing = overrides.get(claim.page) or PageOverride()
        existing.last_corroborated = now.isoformat()
        overrides.set(claim.page, existing)
        overrides.save()
        result.validated.append(claim.id)

    def _handle_failed(
        self,
        claim: Claim,
        verdict: Verdict,
        confidence: float,
        explanation: str,
        result: AdversaryResult,
    ) -> None:
        issue = Issue(
            id=Issue.make_id("claim-failed", claim.page, claim.id),
            type="claim-failed",
            status="open",
            title=f"Claim on '{claim.page}' is {verdict}",
            page=claim.page,
            body=(
                f"The adversary checked the claim:\n\n> {claim.text}\n\n"
                f"against [[{claim.citation}]] and ruled it **{verdict}** "
                f"(confidence {confidence:.2f}).\n\n"
                f"Explanation: {explanation}"
            ),
            created=Issue.now_iso(),
            detected_by="adversary",
            metadata={
                "claim_id": claim.id,
                "section": claim.section,
                "citation": claim.citation,
                "verdict": verdict,
                "confidence": confidence,
            },
        )
        _, was_new = self._queue.add(issue)
        result.failed.append(claim.id)
        if was_new:
            result.issues_filed.append(issue.id)

    def _handle_ambiguous(
        self,
        claim: Claim,
        explanation: str,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        page_path = self._wiki_dir / f"{claim.page}.md"
        if not page_path.exists():
            logger.info("Adversary: parent page %s missing, cannot post talk entry", page_path)
            return

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            timestamp=now.isoformat(),
            author="@adversary",
            body=(
                f"Checked claim against [[{claim.citation}]] — verdict is ambiguous.\n\n"
                f"> {claim.text}\n\n"
                f"{explanation}"
            ),
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        result.talk_posts.append(claim.page)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_adversary/test_agent.py -v`
Expected: All adversary agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_agent.py
git commit -m "feat: AdversaryAgent.run — verdict-driven dispatch (validated/failed/ambiguous)"
```

---

### Task 9: Daemon `talk-*` routes

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Create: `tests/test_daemon/test_talk_route.py`

Three routes: `talk-read` (return entries for a page), `talk-append` (append a new entry as a specified author — defaults to `@human` from CLI), `talk-list` (list pages that have talk pages).

- [ ] **Step 1: Write failing test**

```python
# tests/test_daemon/test_talk_route.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.config import VaultConfig, WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


def _vault_with_page_and_talk(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text("---\ntitle: T\n---\n\nBody.\n")
    (wiki / "test-page.talk.md").write_text(
        "---\npage: test-page\n---\n\n"
        "**2026-04-08T10:00:00+00:00 — @adversary**\nVerified the k=10 claim.\n"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_talk_read_returns_entries(tmp_path: Path):
    vault_root = _vault_with_page_and_talk(tmp_path)
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(vault_root, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-read", "page": "test-page"})
        assert resp["status"] == "ok"
        assert len(resp["entries"]) == 1
        assert resp["entries"][0]["author"] == "@adversary"
        assert "k=10" in resp["entries"][0]["body"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_read_missing_page_returns_empty(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text("---\ntitle: T\n---\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-read", "page": "test-page"})
        assert resp["status"] == "ok"
        assert resp["entries"] == []
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_creates_entry(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@human",
            "body": "Looks good to me.",
        })
        assert resp["status"] == "ok"

        read_resp = client.request({"type": "talk-read", "page": "p"})
        assert len(read_resp["entries"]) == 1
        assert read_resp["entries"][0]["author"] == "@human"
        assert "Looks good" in read_resp["entries"][0]["body"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_list_returns_pages_with_talk_files(tmp_path: Path):
    vault_root = _vault_with_page_and_talk(tmp_path)
    (vault_root / "wiki" / "without-talk.md").write_text("---\ntitle: W\n---\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(vault_root, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-list"})
        assert resp["status"] == "ok"
        assert "test-page" in resp["pages"]
        assert "without-talk" not in resp["pages"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_daemon/test_talk_route.py -v`
Expected: Failures with `Unknown request type: talk-*`.

- [ ] **Step 3: Implement the routes**

In `src/llm_wiki/daemon/server.py`, add to `_route()`:

```python
            case "talk-read":
                return self._handle_talk_read(request)
            case "talk-append":
                return self._handle_talk_append(request)
            case "talk-list":
                return self._handle_talk_list()
```

Add the handler methods (next to other talk-related handlers):

```python
    def _handle_talk_read(self, request: dict) -> dict:
        from llm_wiki.talk.page import TalkPage
        if "page" not in request:
            return {"status": "error", "message": "Missing required field: page"}
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{request['page']}.md"
        talk = TalkPage.for_page(page_path)
        entries = [
            {"timestamp": e.timestamp, "author": e.author, "body": e.body}
            for e in talk.load()
        ]
        return {"status": "ok", "entries": entries}

    def _handle_talk_append(self, request: dict) -> dict:
        import datetime
        from llm_wiki.talk.discovery import ensure_talk_marker
        from llm_wiki.talk.page import TalkEntry, TalkPage

        for field in ("page", "author", "body"):
            if field not in request:
                return {"status": "error", "message": f"Missing required field: {field}"}

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{request['page']}.md"
        if not page_path.exists():
            return {"status": "error", "message": f"Page not found: {request['page']}"}

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            author=request["author"],
            body=request["body"],
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        return {"status": "ok"}

    def _handle_talk_list(self) -> dict:
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        pages: list[str] = []
        if wiki_dir.exists():
            for talk_file in sorted(wiki_dir.rglob("*.talk.md")):
                # Skip files inside hidden directories
                rel = talk_file.relative_to(wiki_dir)
                if any(p.startswith(".") for p in rel.parts):
                    continue
                stem = talk_file.stem  # foo.talk
                if stem.endswith(".talk"):
                    pages.append(stem[: -len(".talk")])
        return {"status": "ok", "pages": pages}
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_talk_route.py -v`
Expected: All four talk-route tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_talk_route.py
git commit -m "feat: daemon talk-read/talk-append/talk-list routes"
```

---

### Task 10: CLI `llm-wiki talk` command group

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Create: `tests/test_cli/test_talk_cmd.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli/test_talk_cmd.py
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from llm_wiki.cli.main import cli


def _seed_vault(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")
    return tmp_path


def test_talk_post_then_read(tmp_path: Path):
    """Post a talk entry, then read it back."""
    vault_root = _seed_vault(tmp_path)
    runner = CliRunner()

    post = runner.invoke(cli, [
        "talk", "post", "p", "--message", "test message",
        "--vault", str(vault_root),
    ])
    assert post.exit_code == 0, post.output

    read = runner.invoke(cli, ["talk", "read", "p", "--vault", str(vault_root)])
    assert read.exit_code == 0, read.output
    assert "test message" in read.output
    assert "@human" in read.output


def test_talk_list(tmp_path: Path):
    vault_root = _seed_vault(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["talk", "post", "p", "--message", "x", "--vault", str(vault_root)])

    result = runner.invoke(cli, ["talk", "list", "--vault", str(vault_root)])
    assert result.exit_code == 0, result.output
    assert "p" in result.output


def test_talk_read_empty_page(tmp_path: Path):
    vault_root = _seed_vault(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["talk", "read", "p", "--vault", str(vault_root)])
    assert result.exit_code == 0
    assert "no entries" in result.output.lower() or "0 entries" in result.output.lower()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_cli/test_talk_cmd.py -v`
Expected: `Error: No such command 'talk'`.

- [ ] **Step 3: Implement the `talk` command group**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.group()
def talk() -> None:
    """Read, post, and list talk-page entries."""
    pass


@talk.command("read")
@click.argument("page")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def talk_read(page: str, vault_path: Path) -> None:
    """Show all talk entries for a page."""
    client = _get_client(vault_path)
    resp = client.request({"type": "talk-read", "page": page})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk read failed"))

    entries = resp["entries"]
    if not entries:
        click.echo(f"No entries on {page}.talk.")
        return

    click.echo(f"{len(entries)} entries on {page}.talk:\n")
    for entry in entries:
        click.echo(f"**{entry['timestamp']} — {entry['author']}**")
        click.echo(entry["body"])
        click.echo()


@talk.command("post")
@click.argument("page")
@click.option("--message", "-m", required=True, help="Message body")
@click.option("--author", default="@human", help="Author tag (defaults to @human)")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def talk_post(page: str, message: str, author: str, vault_path: Path) -> None:
    """Append a talk-page entry."""
    client = _get_client(vault_path)
    resp = client.request({
        "type": "talk-append",
        "page": page,
        "author": author,
        "body": message,
    })
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk post failed"))
    click.echo(f"Posted to {page}.talk as {author}.")


@talk.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def talk_list(vault_path: Path) -> None:
    """List all pages that have a talk sidecar."""
    client = _get_client(vault_path)
    resp = client.request({"type": "talk-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk list failed"))

    pages = resp["pages"]
    if not pages:
        click.echo("No talk pages.")
        return

    click.echo(f"{len(pages)} talk page(s):")
    for page in pages:
        click.echo(f"  {page}")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_cli/test_talk_cmd.py -v`
Expected: All three talk CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_talk_cmd.py
git commit -m "feat: llm-wiki talk command group (read/post/list)"
```

---

### Task 11: Wire adversary worker into `DaemonServer`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_daemon/test_scheduler.py`

Extend `_register_maintenance_workers()` (added in 5b, extended in 5c) with a fourth worker: `adversary`.

- [ ] **Step 1: Add failing test**

Append to `tests/test_daemon/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_daemon_server_registers_adversary_worker(sample_vault: Path, tmp_path: Path):
    """Starting DaemonServer registers the adversary worker."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "adversary.sock"
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_interval="1h"))
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        names = set(server._scheduler.worker_names)
        assert "adversary" in names
        # All four workers from 5b + 5c + 5d should be registered
        assert {"auditor", "librarian", "authority_recalc", "adversary"} <= names
    finally:
        await server.stop()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v -k adversary_worker`
Expected: AssertionError — `adversary` not in worker_names.

- [ ] **Step 3: Extend `_register_maintenance_workers`**

In `src/llm_wiki/daemon/server.py`, add the adversary worker inside `_register_maintenance_workers()`:

```python
        async def run_adversary() -> None:
            from llm_wiki.adversary.agent import AdversaryAgent
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = AdversaryAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
            logger.info(
                "Adversary: checked=%d validated=%d failed=%d talk=%d issues=%d",
                result.claims_checked, len(result.validated), len(result.failed),
                len(result.talk_posts), len(result.issues_filed),
            )

        self._scheduler.register(
            ScheduledWorker(
                name="adversary",
                interval_seconds=parse_interval(self._config.maintenance.adversary_interval),
                coro_factory=run_adversary,
            )
        )
```

Place this `register` call after the existing `librarian` and `authority_recalc` registrations.

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All scheduler tests pass — including the new adversary registration test AND all earlier worker registration tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_scheduler.py
git commit -m "feat: register adversary worker in daemon scheduler"
```

---

### Task 12: End-to-end integration test

**Files:**
- Create: `tests/test_adversary/test_integration.py`

Full pathway: build a vault with a page citing a real raw markdown file, run the adversary with a stub LLM verdict, then verify the dispatch:
- validated → overrides updated
- contradicted → issue filed
- ambiguous → talk page created + parent has discovery marker

- [ ] **Step 1: Write failing test**

```python
# tests/test_adversary/test_integration.py
"""End-to-end: vault → adversary.run() → verdict dispatch → state assertions."""
from __future__ import annotations

from pathlib import Path
from random import Random

import pytest

from llm_wiki.adversary.agent import AdversaryAgent
from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides
from llm_wiki.talk.page import TalkPage
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    def __init__(self, response_text: str) -> None:
        self.response = response_text

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        return LLMResponse(content=self.response, tokens_used=100)


def _build_vault_with_three_claims(tmp_path: Path) -> tuple[Path, list[Path]]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for slug in ("a", "b", "c"):
        (raw_dir / f"src-{slug}.md").write_text(f"# Source {slug}\n\nClaim {slug} is true.\n")

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    pages: list[Path] = []
    for slug in ("a", "b", "c"):
        page = wiki_dir / f"page-{slug}.md"
        page.write_text(
            f"---\ntitle: Page {slug}\n---\n\n"
            f"%% section: claim %%\n## Claim\n\n"
            f"Claim {slug} is true [[raw/src-{slug}.md]].\n"
        )
        pages.append(page)
    return tmp_path, pages


@pytest.mark.asyncio
async def test_adversary_full_lifecycle_validated(tmp_path: Path):
    vault_root, _ = _build_vault_with_three_claims(tmp_path)
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=10),
        vault=VaultConfig(wiki_dir="wiki/"),
    )
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.95, "explanation": "Source matches."}'
    )

    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config, rng=Random(42))

    result = await agent.run()
    assert result.claims_checked == 3
    assert len(result.validated) == 3
    assert result.failed == []
    assert result.talk_posts == []

    overrides = ManifestOverrides.load(_state_dir_for(vault_root) / "manifest_overrides.json")
    for slug in ("a", "b", "c"):
        po = overrides.get(f"page-{slug}")
        assert po is not None
        assert po.last_corroborated is not None


@pytest.mark.asyncio
async def test_adversary_full_lifecycle_mixed_verdicts(tmp_path: Path):
    """Different verdicts dispatched correctly across multiple claims."""
    vault_root, pages = _build_vault_with_three_claims(tmp_path)
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=10),
        vault=VaultConfig(wiki_dir="wiki/"),
    )

    # Cycle through verdicts using a counter on the stub
    class _CyclingLLM:
        verdicts = [
            '{"verdict": "validated", "confidence": 0.95, "explanation": "ok"}',
            '{"verdict": "contradicted", "confidence": 0.85, "explanation": "bad"}',
            '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "unclear"}',
        ]

        def __init__(self) -> None:
            self.i = 0

        async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
            from llm_wiki.traverse.llm_client import LLMResponse
            response = self.verdicts[self.i % 3]
            self.i += 1
            return LLMResponse(content=response, tokens_used=100)

    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, _CyclingLLM(), queue, config, rng=Random(0))

    result = await agent.run()
    assert result.claims_checked == 3
    assert len(result.validated) == 1
    assert len(result.failed) == 1
    assert len(result.talk_posts) == 1

    # The talk post page should have a real talk file with one entry
    talk_pages = result.talk_posts
    talk_page_slug = talk_pages[0]
    talk_path = vault_root / "wiki" / f"{talk_page_slug}.talk.md"
    assert talk_path.exists()
    talk = TalkPage(talk_path)
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].author == "@adversary"

    # The parent page should have the talk discovery marker
    parent_path = vault_root / "wiki" / f"{talk_page_slug}.md"
    parent_text = parent_path.read_text(encoding="utf-8")
    assert f"%% talk: [[{talk_page_slug}.talk]] %%" in parent_text

    # The contradicted verdict should have filed an issue
    issues = queue.list(type="claim-failed")
    assert len(issues) == 1
```

- [ ] **Step 2: Run test, expect PASS**

Run: `pytest tests/test_adversary/test_integration.py -v`
Expected: PASS — all underlying machinery is in place.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: All tests pass — Phase 1-4, Phase 5a, Phase 5b, Phase 5c, and all new Phase 5d tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_adversary/test_integration.py
git commit -m "test: phase 5d adversary end-to-end integration with mixed verdicts"
```

---

### Task 13: README + roadmap update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Quick Start**

Add to the daemon management section in `README.md`:

```markdown
# Talk pages — async discussion sidecars
llm-wiki talk read <page-name> --vault /path/to/your/vault
llm-wiki talk post <page-name> --message "..." --vault /path/to/your/vault
llm-wiki talk list --vault /path/to/your/vault
```

- [ ] **Step 2: Update Project Structure**

Add to the package layout in `README.md` under `src/llm_wiki/`:

```
  adversary/
    claim_extractor.py    # Sentence-level claim extraction
    sampling.py           # Weighted sampling (age + inverse authority)
    prompts.py            # Verification prompt + parser
    agent.py              # AdversaryAgent (verdict dispatch)
  talk/
    page.py               # TalkEntry, TalkPage (append-only sidecars)
    discovery.py          # ensure_talk_marker (invisible discovery marker)
```

- [ ] **Step 3: Update Roadmap**

Mark 5d as complete (and Phase 5 as a whole):

```markdown
- [x] **Phase 5a: Issue Queue + Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [x] **Phase 5b: Background Workers + Compliance Review** — Async scheduler, debounced compliance pipeline
- [x] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [x] **Phase 5d: Adversary + Talk Pages** — Claim verification, async discussion sidecars
- [ ] **Phase 6: MCP Server** — High-level + low-level tools for agent integration
```

- [ ] **Step 4: Update Documentation references**

Add to the Documentation list:

```markdown
- **[Phase 5d Plan](docs/superpowers/plans/2026-04-08-phase5d-adversary-talk-pages.md)** — Implementation plan for adversary agent + talk pages
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README updates for phase 5d — adversary, talk pages"
```

---

## Self-review checklist

Before declaring this plan complete, verify:

- [ ] `extract_claims` skips code blocks, marker lines, and frontmatter source fields
- [ ] `Claim.id` is deterministic in `(page, section, text)`
- [ ] `sample_claims` is deterministic for a seeded RNG (test asserts identical sequences)
- [ ] `sample_claims` favors low authority AND stale `last_corroborated` (statistical tests with 50+ seeds)
- [ ] `parse_verification` rejects invalid verdicts (returns `None`) and handles fenced JSON
- [ ] `TalkPage.append` creates frontmatter on first call, preserves prior entries on subsequent calls
- [ ] `TalkPage.load` round-trips multiple entries in chronological order
- [ ] `ensure_talk_marker` is idempotent (verify count stays at 1)
- [ ] `Vault.scan` excludes `*.talk.md` from page indexing AND doesn't break existing scan tests
- [ ] `AdversaryAgent.run` handles every verdict pathway (validated/contradicted/unsupported/ambiguous) AND missing raw source AND unparseable LLM response
- [ ] `AdversaryAgent` updates `ManifestOverrides.last_corroborated` only on `validated` verdict
- [ ] `AdversaryAgent` files issues only on `contradicted`/`unsupported`, never on `validated`/`ambiguous`
- [ ] `AdversaryAgent` posts to talk + inserts marker only on `ambiguous`
- [ ] All four scheduled workers (`auditor`, `librarian`, `authority_recalc`, `adversary`) are registered
- [ ] All adversary LLM calls use `priority="maintenance"` (test asserts this on the stub)
- [ ] Empty vault is exercised by every check, agent method, and integration test
- [ ] Tests use markdown files for raw sources (so Phase 4's extract_text doesn't need liteparse)

## Spec sections satisfied by 5d

- §5 Adversary row — full, including spec's claim selection weighting (age × inverse authority × random)
- §5 Talk Pages section — v1 flat chronological log, talk-page discovery marker, daemon append path
- §4 Manifest entry `last_corroborated` field — populated by adversary on validated verdicts (closes the freshness loop the librarian computes)

## What's deferred from this sub-phase

Explicitly out of scope (handled by future work):

- Threading on talk pages (v1 is flat chronological per spec)
- Auto-archive of old talk entries (`.archive/` rotation, deferred per spec)
- LLM-driven librarian reading of talk pages (out of phase 5)
- Multi-source claim verification (one source per claim is fine)
- Cross-reference suggestions from adversary findings (defer to future enhancement)
- Adversary writing back its findings to the wiki page itself (it never modifies body content — only files issues, posts to talk, updates overrides)

## Dependencies

- **Requires 5a** for `IssueQueue` and `Issue` (claim-failed issues)
- **Requires 5b** for the scheduler and `_register_maintenance_workers` extension point
- **Requires 5c** for `ManifestOverrides` (to update `last_corroborated`)
- Reuses Phase 4's `extract_text()` (markdown raw sources work without liteparse — see test fixture pattern)

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-phase5d-adversary-talk-pages.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Either option uses this plan as the input. The most fragile tasks are 8 (AdversaryAgent — many code paths to get right) and 7 (Vault.scan modification — affects every existing test, but the change is one line and additive). Review those carefully.

After all four sub-phases (5a + 5b + 5c + 5d) land, **Phase 5 is complete** and the roadmap moves on to Phase 6 (MCP Server).
