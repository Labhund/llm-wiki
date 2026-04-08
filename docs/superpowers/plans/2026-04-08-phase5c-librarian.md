# Phase 5c: Librarian — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap reference:** See `docs/superpowers/plans/2026-04-08-phase5-maintenance-agents-roadmap.md` for cross-cutting design decisions and the relationship to sub-phases 5a/5b/5d. **Read the roadmap's "Cross-cutting design decisions" and "What's already in place" sections before starting Task 1.**
>
> **Prerequisites:** Sub-phases 5a (issue queue) and 5b (scheduler) must be merged. This plan registers a new worker via `_register_maintenance_workers` from 5b and reuses `IssueQueue` from 5a.

**Goal:** A scheduled librarian agent that consumes traversal logs to refine `ManifestEntry` tags/summary via LLM and recomputes authority scores from the link graph plus usage. Librarian state survives `Vault.scan()` via a sidecar JSON override file — pages keep their refined tags and authority across daemon restarts and rescans.

**Architecture:** Three new pure modules plus an LLM-using agent.

1. `librarian/log_reader.py` — `aggregate_logs(log_path) → dict[str, PageUsage]` walks `traversal_logs.jsonl` and produces per-page usage signals (read count, average relevance, recent salient points, recent queries).
2. `librarian/authority.py` — `compute_authority(entries, usage)` implements the spec formula: `0.3 * inlink + 0.4 * usefulness + 0.2 * freshness + 0.1 * outlink_quality`. Each component is normalized to `[0, 1]`. None `last_corroborated` → 0.5 neutral freshness per spec.
3. `librarian/overrides.py` — `ManifestOverrides` is a JSON sidecar at `<state_dir>/manifest_overrides.json` keyed by page name. Stores `{tags, summary_override, authority, last_corroborated, read_count, usefulness, last_refreshed_read_count}` per page. Atomic write via temp file + rename.
4. `librarian/agent.py` — `LibrarianAgent` does two things on each `run()`: (a) for each page whose accumulated reads since last refresh ≥ `manifest_refresh_after_traversals`, call the LLM to propose new tags + summary; (b) recompute authority for every entry from the latest usage. Both write through `ManifestOverrides`. `recalc_authority()` is also exposed as a standalone method so the `authority_recalc` worker can run it on a different cadence.

`Vault.scan()` is modified to load overrides and apply them on top of programmatically-built entries — this is the only invasive change to the existing core. Tests that don't write an overrides file are unaffected because the loader returns an empty store when the file is missing.

**Tech Stack:** Python 3.11+, existing `LLMClient`/`LLMQueue` infrastructure (Phase 3), pytest-asyncio. **All LLM calls use `priority="maintenance"`.** No new third-party dependencies.

---

## File Structure

```
src/llm_wiki/
  librarian/
    __init__.py             # package marker
    log_reader.py           # PageUsage, aggregate_logs
    authority.py            # compute_authority, normalization helpers
    overrides.py            # PageOverride, ManifestOverrides
    prompts.py              # compose_refinement_messages, parse_refinement
    agent.py                # LibrarianAgent, LibrarianResult
  vault.py                  # MODIFIED: load + apply overrides during scan
  daemon/
    server.py               # MODIFIED: register librarian + authority_recalc workers

tests/
  test_librarian/
    __init__.py
    test_log_reader.py
    test_authority.py
    test_overrides.py
    test_prompts.py
    test_agent.py
  test_vault.py             # MODIFIED: add override-application test
  test_librarian/test_integration.py
```

**Type flow across tasks:**

- `log_reader.py` defines `PageUsage(name, read_count, turn_appearances, total_relevance, salient_samples, queries)` with property `avg_relevance`. `aggregate_logs(log_path)` returns `dict[str, PageUsage]`.
- `authority.py` defines `compute_authority(entries: dict[str, ManifestEntry], usage: dict[str, PageUsage]) → dict[str, float]`. Pure function — no side effects.
- `overrides.py` defines `PageOverride(tags, summary_override, authority, last_corroborated, read_count, usefulness, last_refreshed_read_count)` and `ManifestOverrides(path)` with `get/set/delete/prune/save/load`.
- `prompts.py` defines `compose_refinement_messages(page_name, page_title, page_content, usage)` and `parse_refinement(text) → tuple[list[str], str | None]` returning `(tags, summary)`. Mirrors Phase 4's prompt parsing patterns (handles fenced JSON, missing fields, invalid types).
- `agent.py` defines `LibrarianResult(pages_refined, authorities_updated, issues_filed)` and `LibrarianAgent(vault, vault_root, llm, queue, config)` with `run()` and `recalc_authority()`.
- `vault.py` adds an internal helper `_apply_overrides(entries, overrides)` called inside `Vault.scan()` after entries are built but BEFORE constructing the `ManifestStore` (so `links_from` is computed against the override-aware authority order).
- `daemon/server.py` extends `_register_maintenance_workers()` to add two more workers: `librarian` (full run) and `authority_recalc` (just `recalc_authority()`). Both use the existing `LLMClient` construction pattern from `_handle_query`/`_handle_ingest`.

**Cross-cutting reminders from the roadmap:**
- Manifest persistence is a sidecar JSON, NEVER frontmatter mutation. Page files are not touched by the librarian.
- All LLM calls go through `LLMClient.complete(..., priority="maintenance")`.
- "Human prose is sacred": the librarian MAY update sidecar `tags`, `summary_override`, `authority`, and `last_corroborated`. It MUST NOT modify any markdown body content.
- Empty vault is valid: every check, function, and worker must handle a zero-page vault and a missing `traversal_logs.jsonl` without raising.

**Concurrency note:** `librarian` and `authority_recalc` are registered as separate workers and may execute concurrently. `ManifestOverrides.save()` uses an atomic temp-file-and-rename so concurrent writes never corrupt the file — the latest writer wins. Both operations are idempotent in steady state, so a lost write is recovered on the next run. This is acceptable for v1.

---

### Task 1: Package Skeleton

**Files:**
- Create: `src/llm_wiki/librarian/__init__.py`
- Create: `tests/test_librarian/__init__.py`

- [ ] **Step 1: Create empty package markers**

```python
# src/llm_wiki/librarian/__init__.py
```

```python
# tests/test_librarian/__init__.py
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /home/labhund/repos/llm-wiki && pytest -q`
Expected: All Phase 1-5b tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/llm_wiki/librarian/__init__.py tests/test_librarian/__init__.py
git commit -m "feat: phase 5c skeleton — librarian package"
```

---

### Task 2: `PageUsage` + `aggregate_logs`

**Files:**
- Create: `src/llm_wiki/librarian/log_reader.py`
- Create: `tests/test_librarian/test_log_reader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_librarian/test_log_reader.py
from __future__ import annotations

import json
from pathlib import Path

from llm_wiki.librarian.log_reader import PageUsage, aggregate_logs


def _write_log(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_aggregate_logs_missing_file_returns_empty(tmp_path: Path):
    """A missing log file produces an empty result without raising."""
    result = aggregate_logs(tmp_path / "nope.jsonl")
    assert result == {}


def test_aggregate_logs_empty_file_returns_empty(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    log_file.write_text("")
    assert aggregate_logs(log_file) == {}


def test_aggregate_logs_single_query_single_page(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "How does k-means work?",
            "budget": 16000,
            "timestamp": "2026-04-01T12:00:00+00:00",
            "turns": [
                {
                    "turn": 0,
                    "pages_read": [
                        {
                            "name": "k-means",
                            "sections_read": ["overview"],
                            "salient_points": "k=10 chosen via elbow method",
                            "relevance": 0.9,
                        }
                    ],
                    "tokens_used": 1000,
                    "hypothesis": "k-means clusters with k=10",
                    "remaining_questions": [],
                    "next_candidates": [],
                }
            ],
            "outcome": "complete",
            "total_tokens_used": 1000,
            "pages_visited": ["k-means"],
        }
    ])

    result = aggregate_logs(log_file)
    assert "k-means" in result
    usage = result["k-means"]
    assert isinstance(usage, PageUsage)
    assert usage.read_count == 1
    assert usage.turn_appearances == 1
    assert usage.avg_relevance == 0.9
    assert usage.salient_samples == ["k=10 chosen via elbow method"]
    assert usage.queries == ["How does k-means work?"]


def test_aggregate_logs_multiple_queries_distinct_pages(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q1",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
        {
            "query": "q2",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "useful", "relevance": 0.8},
                {"name": "b", "sections_read": [], "salient_points": "also useful", "relevance": 0.7},
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    result = aggregate_logs(log_file)
    assert set(result) == {"a", "b"}
    assert result["a"].read_count == 2  # appeared in two distinct queries
    assert result["a"].turn_appearances == 2
    assert abs(result["a"].avg_relevance - 0.65) < 1e-6
    assert result["b"].read_count == 1
    assert "useful" in result["a"].salient_samples
    assert "also useful" in result["a"].salient_samples


def test_aggregate_logs_distinct_query_count_not_double_counted(tmp_path: Path):
    """If a page appears in multiple turns of the same query, read_count = 1."""
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q1",
            "turns": [
                {"turn": 0, "pages_read": [{"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}],
                 "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []},
                {"turn": 1, "pages_read": [{"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.7}],
                 "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []},
            ],
        },
    ])

    result = aggregate_logs(log_file)
    assert result["a"].read_count == 1               # one query
    assert result["a"].turn_appearances == 2         # but two turn appearances
    assert abs(result["a"].avg_relevance - 0.6) < 1e-6


def test_aggregate_logs_caps_samples(tmp_path: Path):
    """salient_samples and queries are capped to the last 5."""
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": f"point {i}", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(10)
    ])

    result = aggregate_logs(log_file)
    assert len(result["a"].salient_samples) == 5
    assert len(result["a"].queries) == 5
    # Most recent ones are kept
    assert "point 9" in result["a"].salient_samples
    assert "q9" in result["a"].queries


def test_aggregate_logs_skips_empty_salient_points(tmp_path: Path):
    log_file = tmp_path / "logs.jsonl"
    _write_log(log_file, [
        {
            "query": "q",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "a", "sections_read": [], "salient_points": "", "relevance": 0.5}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])
    result = aggregate_logs(log_file)
    assert result["a"].salient_samples == []
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_log_reader.py -v`
Expected: ImportError — `llm_wiki.librarian.log_reader` does not exist.

- [ ] **Step 3: Implement `PageUsage` + `aggregate_logs`**

```python
# src/llm_wiki/librarian/log_reader.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_SAMPLE_CAP = 5


@dataclass
class PageUsage:
    """Per-page usage signals aggregated from traversal_logs.jsonl."""
    name: str
    read_count: int = 0              # distinct queries that read this page
    turn_appearances: int = 0        # total turn-level appearances
    total_relevance: float = 0.0
    salient_samples: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    @property
    def avg_relevance(self) -> float:
        if self.turn_appearances == 0:
            return 0.0
        return self.total_relevance / self.turn_appearances


def aggregate_logs(log_path: Path) -> dict[str, PageUsage]:
    """Walk a traversal_logs.jsonl file and produce per-page usage signals.

    Returns an empty dict if the file does not exist or is empty. The
    most recent SAMPLE_CAP salient_points and queries per page are kept.

    A page that appears in multiple turns of the same query is counted
    once toward read_count but its turn_appearances and total_relevance
    accumulate normally.
    """
    usage: dict[str, PageUsage] = {}
    if not log_path.exists():
        return usage

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            query = entry.get("query") or ""
            seen_in_query: set[str] = set()
            for turn in entry.get("turns") or []:
                for page in turn.get("pages_read") or []:
                    name = page.get("name")
                    if not name:
                        continue
                    pu = usage.setdefault(name, PageUsage(name=name))
                    if name not in seen_in_query:
                        pu.read_count += 1
                        seen_in_query.add(name)
                    pu.turn_appearances += 1
                    relevance = page.get("relevance")
                    if isinstance(relevance, (int, float)):
                        pu.total_relevance += float(relevance)
                    salient = page.get("salient_points")
                    if isinstance(salient, str) and salient.strip():
                        pu.salient_samples.append(salient)
            # Track which queries each page appeared in (for prompt context)
            for name in seen_in_query:
                if query:
                    usage[name].queries.append(query)

    for pu in usage.values():
        pu.salient_samples = pu.salient_samples[-_SAMPLE_CAP:]
        pu.queries = pu.queries[-_SAMPLE_CAP:]

    return usage
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_log_reader.py -v`
Expected: All seven tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/log_reader.py tests/test_librarian/test_log_reader.py
git commit -m "feat: aggregate_logs — per-page usage from traversal logs"
```

---

### Task 3: `compute_authority`

**Files:**
- Create: `src/llm_wiki/librarian/authority.py`
- Create: `tests/test_librarian/test_authority.py`

Spec formula: `authority = 0.3*inlink + 0.4*usefulness + 0.2*freshness + 0.1*outlink_quality`. Each component is normalized to `[0, 1]`. `freshness = 0.5` neutral when `last_corroborated` is None (per spec — "haven't failed, just haven't been verified yet").

- [ ] **Step 1: Write failing tests**

```python
# tests/test_librarian/test_authority.py
from __future__ import annotations

import datetime

from llm_wiki.librarian.authority import compute_authority, freshness_score
from llm_wiki.librarian.log_reader import PageUsage
from llm_wiki.manifest import ManifestEntry, SectionInfo


def _entry(name: str, links_to: list[str] | None = None, links_from: list[str] | None = None,
           last_corroborated: str | None = None) -> ManifestEntry:
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
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_authority.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compute_authority` + `freshness_score`**

```python
# src/llm_wiki/librarian/authority.py
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
_FRESHNESS_FLOOR = 0.5
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
    if delta_days >= _FRESHNESS_DECAY_DAYS:
        return _FRESHNESS_FLOOR
    # Linear decay from 1.0 at 0 days to 0.5 at 90 days
    return 1.0 - (1.0 - _FRESHNESS_FLOOR) * (delta_days / _FRESHNESS_DECAY_DAYS)


def compute_authority(
    entries: dict[str, "ManifestEntry"],
    usage: dict[str, PageUsage],
) -> dict[str, float]:
    """Compute authority scores for every entry.

    authority = 0.3*inlink_norm + 0.4*usefulness + 0.2*freshness + 0.1*outlink_quality

    - inlink_norm: links_from count / max links_from in vault (0 if vault max is 0)
    - usefulness: avg_relevance from usage, capped at 1.0
    - freshness: per freshness_score()
    - outlink_quality: fraction of links_to that resolve to known pages
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
        result[name] = score

    return result
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_authority.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/authority.py tests/test_librarian/test_authority.py
git commit -m "feat: compute_authority — spec formula with freshness decay"
```

---

### Task 4: `PageOverride` + `ManifestOverrides`

**Files:**
- Create: `src/llm_wiki/librarian/overrides.py`
- Create: `tests/test_librarian/test_overrides.py`

A JSON sidecar at `<state_dir>/manifest_overrides.json` that survives `Vault.scan()`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_librarian/test_overrides.py
from __future__ import annotations

import json
from pathlib import Path

from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride


def test_load_missing_file_returns_empty(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "nope.json")
    assert store.get("any") is None


def test_set_and_get_round_trip(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    override = PageOverride(
        tags=["bioinformatics", "validation"],
        summary_override="Validates sRNA embeddings via PCA + k-means",
        authority=0.74,
        last_corroborated="2026-04-01T12:00:00+00:00",
        read_count=12,
        usefulness=0.82,
        last_refreshed_read_count=10,
    )
    store.set("srna-embeddings", override)
    store.save()

    reloaded = ManifestOverrides.load(path)
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["bioinformatics", "validation"]
    assert got.summary_override == "Validates sRNA embeddings via PCA + k-means"
    assert abs(got.authority - 0.74) < 1e-6
    assert got.last_corroborated == "2026-04-01T12:00:00+00:00"
    assert got.read_count == 12
    assert abs(got.usefulness - 0.82) < 1e-6
    assert got.last_refreshed_read_count == 10


def test_get_missing_returns_none(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "x.json")
    assert store.get("nope") is None


def test_save_creates_atomic_file(tmp_path: Path):
    """save() writes the file (no temp leftovers in steady state)."""
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(authority=0.5))
    store.save()

    assert path.exists()
    siblings = list(path.parent.iterdir())
    # No leftover .tmp files after a successful save
    assert all(not p.name.endswith(".tmp") for p in siblings)


def test_save_writes_valid_json(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(tags=["x"], authority=0.5))
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "a" in data
    assert data["a"]["tags"] == ["x"]
    assert data["a"]["authority"] == 0.5


def test_prune_removes_unknown_pages(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("alive", PageOverride(authority=0.5))
    store.set("deleted", PageOverride(authority=0.3))
    store.prune({"alive"})
    store.save()

    reloaded = ManifestOverrides.load(path)
    assert reloaded.get("alive") is not None
    assert reloaded.get("deleted") is None


def test_delete_removes_one_entry(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "x.json")
    store.set("a", PageOverride(authority=0.5))
    store.set("b", PageOverride(authority=0.3))
    store.delete("a")
    assert store.get("a") is None
    assert store.get("b") is not None


def test_creates_parent_dir_on_save(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(authority=0.5))
    store.save()
    assert path.exists()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_overrides.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `PageOverride` + `ManifestOverrides`**

```python
# src/llm_wiki/librarian/overrides.py
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class PageOverride:
    """Librarian-managed metadata that survives Vault.scan()."""
    tags: list[str] = field(default_factory=list)
    summary_override: str | None = None
    authority: float = 0.0
    last_corroborated: str | None = None
    read_count: int = 0
    usefulness: float = 0.0
    last_refreshed_read_count: int = 0


class ManifestOverrides:
    """JSON-backed sidecar of librarian-managed page metadata.

    Atomic writes via temp-file-and-rename so concurrent workers
    (librarian + authority_recalc) cannot corrupt the file. Last
    writer wins; both operations are idempotent in steady state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, PageOverride] = {}

    @classmethod
    def load(cls, path: Path) -> "ManifestOverrides":
        store = cls(path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return store
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            store._entries[name] = PageOverride(
                tags=list(raw.get("tags") or []),
                summary_override=raw.get("summary_override"),
                authority=float(raw.get("authority", 0.0) or 0.0),
                last_corroborated=raw.get("last_corroborated"),
                read_count=int(raw.get("read_count", 0) or 0),
                usefulness=float(raw.get("usefulness", 0.0) or 0.0),
                last_refreshed_read_count=int(raw.get("last_refreshed_read_count", 0) or 0),
            )
        return store

    def get(self, page_name: str) -> PageOverride | None:
        return self._entries.get(page_name)

    def set(self, page_name: str, override: PageOverride) -> None:
        self._entries[page_name] = override

    def delete(self, page_name: str) -> None:
        self._entries.pop(page_name, None)

    def prune(self, valid_names: set[str]) -> None:
        for name in list(self._entries):
            if name not in valid_names:
                del self._entries[name]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(override) for name, override in self._entries.items()}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def __len__(self) -> int:
        return len(self._entries)

    def names(self) -> list[str]:
        return list(self._entries)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_overrides.py -v`
Expected: All overrides tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/overrides.py tests/test_librarian/test_overrides.py
git commit -m "feat: ManifestOverrides — atomic JSON sidecar for librarian state"
```

---

### Task 5: `Vault.scan()` applies overrides

**Files:**
- Modify: `src/llm_wiki/vault.py`
- Modify: `tests/test_vault.py`

`Vault.scan()` loads overrides from `<state_dir>/manifest_overrides.json` and applies them on top of programmatically-built entries before constructing the `ManifestStore`. This is the only invasive change to the existing core.

- [ ] **Step 1: Add failing test**

Append to `tests/test_vault.py`:

```python
def test_vault_scan_applies_manifest_overrides(sample_vault, tmp_path):
    """Tags, authority, and other librarian-managed fields survive Vault.scan()."""
    from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
    from llm_wiki.vault import Vault, _state_dir_for

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides_path = state_dir / "manifest_overrides.json"
    store = ManifestOverrides.load(overrides_path)
    store.set("srna-embeddings", PageOverride(
        tags=["bioinformatics", "embeddings", "validation"],
        summary_override="Validates sRNA embeddings via PCA and k-means",
        authority=0.74,
        last_corroborated="2026-04-01T12:00:00+00:00",
        read_count=12,
        usefulness=0.82,
        last_refreshed_read_count=10,
    ))
    store.save()

    vault = Vault.scan(sample_vault)
    entry = vault.manifest_entries()["srna-embeddings"]

    assert entry.tags == ["bioinformatics", "embeddings", "validation"]
    assert entry.summary == "Validates sRNA embeddings via PCA and k-means"
    assert abs(entry.authority - 0.74) < 1e-6
    assert entry.last_corroborated == "2026-04-01T12:00:00+00:00"
    assert entry.read_count == 12
    assert abs(entry.usefulness - 0.82) < 1e-6


def test_vault_scan_prunes_overrides_for_deleted_pages(sample_vault, tmp_path):
    """An override for a page that no longer exists in the vault is removed on scan."""
    from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
    from llm_wiki.vault import Vault, _state_dir_for

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = state_dir / "manifest_overrides.json"

    store = ManifestOverrides.load(overrides_path)
    store.set("srna-embeddings", PageOverride(authority=0.5))
    store.set("deleted-page", PageOverride(authority=0.9))
    store.save()

    Vault.scan(sample_vault)

    reloaded = ManifestOverrides.load(overrides_path)
    assert reloaded.get("srna-embeddings") is not None
    assert reloaded.get("deleted-page") is None
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_vault.py -v -k overrides or prunes`
Expected: Failures — overrides are not applied.

- [ ] **Step 3: Modify `Vault.scan()`**

In `src/llm_wiki/vault.py`, update `Vault.scan()` after entries are built and BEFORE constructing `ManifestStore`:

```python
        # Build search index
        index_path = state_dir / "index"
        backend = TantivyBackend(index_path)
        backend.index_entries(entries)

        # Phase 5c: apply librarian-managed overrides on top of built entries
        from llm_wiki.librarian.overrides import ManifestOverrides
        overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
        _apply_overrides(entries, overrides)
        overrides.prune({e.name for e in entries})
        overrides.save()

        # Build manifest store
        store = ManifestStore(entries)
```

Add the helper function at the bottom of `src/llm_wiki/vault.py` (outside the class):

```python
def _apply_overrides(entries: list[ManifestEntry], overrides: "ManifestOverrides") -> None:
    """Apply librarian-managed metadata to programmatically-built entries.

    Tags, authority, last_corroborated, read_count, and usefulness come
    straight from the override. summary_override (if present) replaces the
    auto-generated summary; otherwise the auto-generated summary stands.
    """
    for entry in entries:
        override = overrides.get(entry.name)
        if override is None:
            continue
        if override.tags:
            entry.tags = list(override.tags)
        if override.summary_override:
            entry.summary = override.summary_override
        entry.authority = override.authority
        entry.last_corroborated = override.last_corroborated
        entry.read_count = override.read_count
        entry.usefulness = override.usefulness
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_vault.py -v`
Expected: All vault tests pass — including new override tests AND all existing scan tests (which use a vault with no override file, so the loader returns an empty store and the entries are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py
git commit -m "feat: Vault.scan applies + prunes manifest overrides"
```

---

### Task 6: Librarian prompts

**Files:**
- Create: `src/llm_wiki/librarian/prompts.py`
- Create: `tests/test_librarian/test_prompts.py`

JSON-based prompt with structural contract, mirroring Phase 4's prompt parser patterns.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_librarian/test_prompts.py
from __future__ import annotations

from llm_wiki.librarian.log_reader import PageUsage
from llm_wiki.librarian.prompts import (
    compose_refinement_messages,
    parse_refinement,
)


def test_compose_refinement_messages_includes_required_sections():
    usage = PageUsage(
        name="srna-embeddings",
        read_count=12,
        turn_appearances=14,
        total_relevance=11.2,
        salient_samples=["uses k=10", "validated via PCA"],
        queries=["how do we validate sRNA embeddings?", "what k for k-means?"],
    )
    messages = compose_refinement_messages(
        page_name="srna-embeddings",
        page_title="sRNA Embeddings",
        page_content="## Overview\n\nValidation pipeline for sRNA embeddings...",
        usage=usage,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user = messages[1]["content"]
    assert "srna-embeddings" in user
    assert "sRNA Embeddings" in user
    assert "uses k=10" in user
    assert "how do we validate sRNA embeddings?" in user


def test_parse_refinement_valid_json():
    text = '{"tags": ["bioinformatics", "validation"], "summary": "Validates sRNA embeddings."}'
    tags, summary = parse_refinement(text)
    assert tags == ["bioinformatics", "validation"]
    assert summary == "Validates sRNA embeddings."


def test_parse_refinement_fenced_json():
    text = """```json
{"tags": ["a", "b"], "summary": "S."}
```"""
    tags, summary = parse_refinement(text)
    assert tags == ["a", "b"]
    assert summary == "S."


def test_parse_refinement_missing_summary():
    text = '{"tags": ["a"]}'
    tags, summary = parse_refinement(text)
    assert tags == ["a"]
    assert summary is None


def test_parse_refinement_missing_tags():
    text = '{"summary": "S."}'
    tags, summary = parse_refinement(text)
    assert tags == []
    assert summary == "S."


def test_parse_refinement_invalid_types_yields_safe_defaults():
    text = '{"tags": "not-a-list", "summary": 42}'
    tags, summary = parse_refinement(text)
    assert tags == []
    assert summary is None


def test_parse_refinement_garbage_returns_empty():
    tags, summary = parse_refinement("not JSON at all")
    assert tags == []
    assert summary is None


def test_parse_refinement_extra_text_around_json():
    text = "Sure, here's the response:\n\n{\"tags\": [\"x\"], \"summary\": \"y\"}\n\nLet me know."
    tags, summary = parse_refinement(text)
    assert tags == ["x"]
    assert summary == "y"


def test_parse_refinement_filters_non_string_tags():
    text = '{"tags": ["valid", 42, null, "also-valid"], "summary": "ok"}'
    tags, summary = parse_refinement(text)
    assert tags == ["valid", "also-valid"]
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_prompts.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement librarian prompts**

```python
# src/llm_wiki/librarian/prompts.py
from __future__ import annotations

import json
import re

from llm_wiki.librarian.log_reader import PageUsage


_LIBRARIAN_SYSTEM = """\
You are the librarian for a wiki, refining a page's manifest entry based on \
how the page is actually being used.

## Task

Given the page content and recent traversal usage signals, propose:
1. Updated tags — 3 to 7 lowercase hyphenated tags that reflect what queries \
this page actually answers
2. A one-sentence summary that describes what the page covers, prioritizing \
how it has been used over the page's stated topic

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "tags": ["tag-a", "tag-b", "tag-c"],
  "summary": "One sentence describing the page."
}"""


def compose_refinement_messages(
    page_name: str,
    page_title: str,
    page_content: str,
    usage: PageUsage,
    page_content_chars: int = 4000,
) -> list[dict[str, str]]:
    """Build the message list for tag/summary refinement."""
    truncated = page_content[:page_content_chars]

    usage_lines: list[str] = []
    if usage.queries:
        usage_lines.append("## Recent Queries")
        for q in usage.queries:
            usage_lines.append(f"- {q}")
    if usage.salient_samples:
        usage_lines.append("\n## Recent Salient Points")
        for s in usage.salient_samples:
            usage_lines.append(f"- {s}")
    if not usage_lines:
        usage_lines.append("## Usage")
        usage_lines.append("(no recent traversal data)")

    usage_section = "\n".join(usage_lines)

    user = (
        f"## Page\n{page_name}\n\n"
        f"## Title\n{page_title}\n\n"
        f"## Page Content\n{truncated}\n\n"
        f"{usage_section}"
    )
    return [
        {"role": "system", "content": _LIBRARIAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from an LLM response (handles fenced blocks)."""
    candidates = []
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


def parse_refinement(text: str) -> tuple[list[str], str | None]:
    """Parse a librarian LLM response into (tags, summary)."""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return [], None

    raw_tags = data.get("tags")
    if isinstance(raw_tags, list):
        tags = [t for t in raw_tags if isinstance(t, str) and t]
    else:
        tags = []

    raw_summary = data.get("summary")
    summary = raw_summary if isinstance(raw_summary, str) and raw_summary.strip() else None

    return tags, summary
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_prompts.py -v`
Expected: All prompt tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/prompts.py tests/test_librarian/test_prompts.py
git commit -m "feat: librarian prompts — refinement compose + parse"
```

---

### Task 7: `LibrarianAgent.recalc_authority()` (programmatic, no LLM)

**Files:**
- Create: `src/llm_wiki/librarian/agent.py` (partial — `recalc_authority` only)
- Create: `tests/test_librarian/test_agent.py` (partial — recalc tests only)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_librarian/test_agent.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent, LibrarianResult
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    """Async LLM stub matching LLMClient.complete shape."""

    def __init__(self, response_text: str = '{"tags": [], "summary": null}') -> None:
        self.response = response_text
        self.calls: list[list[dict]] = []

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        self.calls.append(messages)
        return LLMResponse(content=self.response, tokens_used=100)


def _seed_log(state_dir: Path, entries: list[dict]) -> None:
    log_dir = state_dir / "traversal_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "traversal_logs.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.mark.asyncio
async def test_recalc_authority_writes_overrides_for_every_page(sample_vault: Path):
    """recalc_authority computes scores for every entry and persists them."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How does k-means work?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "uses k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        },
    ])

    vault = Vault.scan(sample_vault)
    queue = IssueQueue(sample_vault / "wiki")  # may not exist; OK for this test
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), queue, WikiConfig())

    count = await agent.recalc_authority()

    assert count == vault.page_count

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    for name in vault.manifest_entries():
        override = overrides.get(name)
        assert override is not None, f"missing override for {name}"
        assert 0.0 <= override.authority <= 1.0


@pytest.mark.asyncio
async def test_recalc_authority_does_not_call_llm(sample_vault: Path):
    """recalc_authority is purely programmatic."""
    vault = Vault.scan(sample_vault)
    stub = _StubLLM()
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    await agent.recalc_authority()

    assert stub.calls == []


@pytest.mark.asyncio
async def test_recalc_authority_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    count = await agent.recalc_authority()
    assert count == 0


@pytest.mark.asyncio
async def test_recalc_authority_preserves_existing_tags_and_summary(sample_vault: Path):
    """recalc_authority must not clobber tags/summary set by prior refinement."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["preserved-tag"],
        summary_override="preserved summary",
        authority=0.0,
        read_count=12,
        last_refreshed_read_count=12,
    ))
    overrides.save()

    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    await agent.recalc_authority()

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["preserved-tag"]
    assert got.summary_override == "preserved summary"
    assert got.read_count == 12
    assert got.last_refreshed_read_count == 12
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_agent.py -v`
Expected: ImportError — `llm_wiki.librarian.agent` does not exist.

- [ ] **Step 3: Implement `LibrarianAgent.recalc_authority`**

```python
# src/llm_wiki/librarian/agent.py
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.authority import compute_authority
from llm_wiki.librarian.log_reader import PageUsage, aggregate_logs
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.vault import Vault, _state_dir_for

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class LibrarianResult:
    """Outcome of one LibrarianAgent.run() invocation."""
    pages_refined: list[str] = field(default_factory=list)
    authorities_updated: int = 0
    issues_filed: list[str] = field(default_factory=list)


class LibrarianAgent:
    """Refines manifest entries from usage signals.

    Two operations:
      - run() — full refresh: re-aggregate logs, refine tags/summary for
        pages above threshold, then recompute authority.
      - recalc_authority() — programmatic, no LLM. Recompute authority for
        every page from current usage + link graph.

    Both write through ManifestOverrides. The librarian and authority_recalc
    workers may run on different cadences (config.maintenance.librarian_interval
    vs authority_recalc).
    """

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: "LLMClient",
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._queue = queue
        self._config = config
        self._state_dir = _state_dir_for(vault_root)
        self._overrides_path = self._state_dir / "manifest_overrides.json"
        self._log_path = self._state_dir / "traversal_logs" / "traversal_logs.jsonl"

    async def recalc_authority(self) -> int:
        """Recompute authority for every entry and persist via overrides.

        Returns:
            The number of authority values written.
        """
        entries = self._vault.manifest_entries()
        if not entries:
            return 0

        usage = aggregate_logs(self._log_path)
        scores = compute_authority(entries, usage)

        overrides = ManifestOverrides.load(self._overrides_path)
        for name, score in scores.items():
            existing = overrides.get(name) or PageOverride()
            existing.authority = score
            # Persist read_count + usefulness alongside authority for the next refresh
            pu = usage.get(name)
            if pu is not None:
                existing.read_count = pu.read_count
                existing.usefulness = min(1.0, pu.avg_relevance)
            overrides.set(name, existing)

        overrides.prune(set(entries))
        overrides.save()

        return len(scores)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_agent.py -v`
Expected: All four recalc tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/agent.py tests/test_librarian/test_agent.py
git commit -m "feat: LibrarianAgent.recalc_authority — programmatic scoring"
```

---

### Task 8: `LibrarianAgent.refresh_page()` (single-page LLM refresh)

**Files:**
- Modify: `src/llm_wiki/librarian/agent.py`
- Modify: `tests/test_librarian/test_agent.py`

`refresh_page(page_name)` reads the page content + recent usage, calls the LLM, parses the response, and writes the new tags/summary to overrides.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_librarian/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_refresh_page_updates_overrides_with_llm_output(sample_vault: Path):
    """refresh_page calls the LLM and writes the parsed tags/summary."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    _seed_log(state_dir, [
        {
            "query": "How are sRNA embeddings validated?",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": ["overview"], "salient_points": "PCA + k=10", "relevance": 0.9}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM(
        '{"tags": ["embeddings", "validation", "k-means"], "summary": "Validates sRNA embeddings via PCA + k-means."}'
    )
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")

    assert refreshed is True
    assert len(stub.calls) == 1

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = overrides.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["embeddings", "validation", "k-means"]
    assert got.summary_override == "Validates sRNA embeddings via PCA + k-means."
    assert got.last_refreshed_read_count == 1   # one query in the seeded log


@pytest.mark.asyncio
async def test_refresh_page_unknown_page_returns_false(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, _StubLLM(), IssueQueue(sample_vault / "wiki"), WikiConfig())
    assert await agent.refresh_page("nope") is False


@pytest.mark.asyncio
async def test_refresh_page_invalid_llm_response_does_not_corrupt_overrides(sample_vault: Path):
    """If the LLM returns junk, the override is left unchanged."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["original"],
        summary_override="original summary",
        authority=0.5,
    ))
    overrides.save()

    stub = _StubLLM("complete garbage, not JSON")
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), WikiConfig())

    refreshed = await agent.refresh_page("srna-embeddings")
    assert refreshed is False

    reloaded = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["original"]
    assert got.summary_override == "original summary"
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_agent.py -v -k refresh_page`
Expected: AttributeError — no `refresh_page` method.

- [ ] **Step 3: Implement `refresh_page`**

Append to `LibrarianAgent` in `src/llm_wiki/librarian/agent.py`:

```python
    async def refresh_page(self, page_name: str) -> bool:
        """Refine tags + summary for a single page via LLM.

        Returns True if the override was updated, False if the page is
        unknown or the LLM response could not be parsed.
        """
        from llm_wiki.librarian.prompts import (
            compose_refinement_messages,
            parse_refinement,
        )

        page = self._vault.read_page(page_name)
        if page is None:
            return False

        usage = aggregate_logs(self._log_path).get(page_name) or PageUsage(name=page_name)

        messages = compose_refinement_messages(
            page_name=page_name,
            page_title=page.title,
            page_content=page.raw_content,
            usage=usage,
        )

        response = await self._llm.complete(
            messages, temperature=0.4, priority="maintenance"
        )
        tags, summary = parse_refinement(response.content)

        if not tags and summary is None:
            logger.info("Librarian: empty refinement for %s, skipping write", page_name)
            return False

        overrides = ManifestOverrides.load(self._overrides_path)
        existing = overrides.get(page_name) or PageOverride()
        if tags:
            existing.tags = tags
        if summary is not None:
            existing.summary_override = summary
        existing.read_count = usage.read_count
        existing.usefulness = min(1.0, usage.avg_relevance)
        existing.last_refreshed_read_count = usage.read_count
        overrides.set(page_name, existing)
        overrides.save()
        return True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_agent.py -v`
Expected: All agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/agent.py tests/test_librarian/test_agent.py
git commit -m "feat: LibrarianAgent.refresh_page — single-page LLM refinement"
```

---

### Task 9: `LibrarianAgent.run()` orchestration

**Files:**
- Modify: `src/llm_wiki/librarian/agent.py`
- Modify: `tests/test_librarian/test_agent.py`

`run()` orchestrates the full librarian pass: aggregate logs, find refresh candidates (pages where `read_count - last_refreshed_read_count >= manifest_refresh_after_traversals`), call `refresh_page()` for each, then call `recalc_authority()`.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_librarian/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_run_refreshes_pages_above_threshold(sample_vault: Path):
    """A page with accumulated reads ≥ threshold gets refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Threshold is 3 in our test config
    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 3

    # 4 distinct queries reading srna-embeddings
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"point {i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(4)
    ])

    stub = _StubLLM('{"tags": ["validation"], "summary": "Refined."}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()

    assert isinstance(result, LibrarianResult)
    assert "srna-embeddings" in result.pages_refined
    assert result.authorities_updated == vault.page_count
    # The other fixture pages have zero reads, so they should NOT be refreshed
    assert "clustering-metrics" not in result.pages_refined


@pytest.mark.asyncio
async def test_run_skips_pages_below_threshold(sample_vault: Path):
    """A page with reads < threshold is not refreshed."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 10

    _seed_log(state_dir, [
        {
            "query": "q",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": "x", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
    ])

    stub = _StubLLM('{"tags": ["x"], "summary": "y"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert result.pages_refined == []
    assert stub.calls == []  # no LLM calls
    assert result.authorities_updated == vault.page_count   # authority still recalculated


@pytest.mark.asyncio
async def test_run_uses_delta_since_last_refresh(sample_vault: Path):
    """A page already refreshed at read_count=10 is not re-refreshed at read_count=12 with threshold=5."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
    overrides.set("srna-embeddings", PageOverride(
        tags=["existing"],
        last_refreshed_read_count=10,
    ))
    overrides.save()

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 5

    # Seed 12 distinct queries reading srna-embeddings (delta since last refresh = 2)
    _seed_log(state_dir, [
        {
            "query": f"q{i}",
            "turns": [{"turn": 0, "pages_read": [
                {"name": "srna-embeddings", "sections_read": [], "salient_points": f"p{i}", "relevance": 0.8}
            ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
        }
        for i in range(12)
    ])

    stub = _StubLLM('{"tags": ["new"], "summary": "new summary"}')
    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()
    assert "srna-embeddings" not in result.pages_refined
    assert stub.calls == []


@pytest.mark.asyncio
async def test_run_empty_vault(tmp_path: Path):
    vault = Vault.scan(tmp_path)
    agent = LibrarianAgent(vault, tmp_path, _StubLLM(), IssueQueue(tmp_path / "wiki"), WikiConfig())
    result = await agent.run()
    assert result.pages_refined == []
    assert result.authorities_updated == 0
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `pytest tests/test_librarian/test_agent.py -v -k test_run`
Expected: AttributeError — no `run` method.

- [ ] **Step 3: Implement `run()`**

Append to `LibrarianAgent` in `src/llm_wiki/librarian/agent.py`:

```python
    async def run(self) -> LibrarianResult:
        """Full librarian pass: refresh candidates above threshold, then recalc authority."""
        result = LibrarianResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        threshold = self._config.budgets.manifest_refresh_after_traversals
        usage = aggregate_logs(self._log_path)
        overrides = ManifestOverrides.load(self._overrides_path)

        # Identify refresh candidates: read_count - last_refreshed_read_count >= threshold
        candidates: list[str] = []
        for name, pu in usage.items():
            if name not in entries:
                continue
            existing = overrides.get(name)
            last_refreshed = existing.last_refreshed_read_count if existing else 0
            if pu.read_count - last_refreshed >= threshold:
                candidates.append(name)

        # Refresh each candidate via LLM
        for name in candidates:
            try:
                refreshed = await self.refresh_page(name)
            except Exception:
                logger.exception("Librarian: refresh_page failed for %s", name)
                continue
            if refreshed:
                result.pages_refined.append(name)

        # Recalculate authority for everything afterwards (uses the latest overrides)
        result.authorities_updated = await self.recalc_authority()
        return result
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_librarian/test_agent.py -v`
Expected: All agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/librarian/agent.py tests/test_librarian/test_agent.py
git commit -m "feat: LibrarianAgent.run — orchestrate refresh + authority recalc"
```

---

### Task 10: Wire librarian + `authority_recalc` workers into `DaemonServer`

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_daemon/test_scheduler.py`

The 5b extension point `_register_maintenance_workers()` adds two more `ScheduledWorker`s: `librarian` (full run on `librarian_interval`) and `authority_recalc` (just `recalc_authority()` on `authority_recalc`).

- [ ] **Step 1: Write failing test**

Append to `tests/test_daemon/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_daemon_server_registers_librarian_workers(sample_vault: Path, tmp_path: Path):
    """Starting DaemonServer registers librarian + authority_recalc workers."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "librarian.sock"
    config = WikiConfig(
        maintenance=MaintenanceConfig(
            auditor_interval="1h",
            librarian_interval="1h",
            authority_recalc="1h",
        ),
    )
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        names = set(server._scheduler.worker_names)
        assert "auditor" in names
        assert "librarian" in names
        assert "authority_recalc" in names
    finally:
        await server.stop()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_daemon/test_scheduler.py -v -k librarian_workers`
Expected: AssertionError — librarian / authority_recalc workers not registered.

- [ ] **Step 3: Extend `_register_maintenance_workers`**

In `src/llm_wiki/daemon/server.py`, extend `_register_maintenance_workers()` (added in 5b Task 10):

```python
    def _register_maintenance_workers(self) -> None:
        """Register all maintenance workers with the scheduler.

        Sub-phase 5b registers the auditor.
        Sub-phase 5c adds librarian + authority_recalc.
        Sub-phase 5d will add the adversary.
        """
        assert self._scheduler is not None

        async def run_auditor() -> None:
            from llm_wiki.audit.auditor import Auditor
            from llm_wiki.issues.queue import IssueQueue
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            auditor = Auditor(self._vault, queue, self._vault_root)
            report = auditor.audit()
            logger.info(
                "Auditor: %d new issues, %d existing",
                len(report.new_issue_ids), len(report.existing_issue_ids),
            )

        async def run_librarian() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
            logger.info(
                "Librarian: refined=%d authorities=%d issues=%d",
                len(result.pages_refined), result.authorities_updated, len(result.issues_filed),
            )

        async def run_authority_recalc() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            count = await agent.recalc_authority()
            logger.info("Authority recalc: %d entries updated", count)

        self._scheduler.register(
            ScheduledWorker(
                name="auditor",
                interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
                coro_factory=run_auditor,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="librarian",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_librarian,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="authority_recalc",
                interval_seconds=parse_interval(self._config.maintenance.authority_recalc),
                coro_factory=run_authority_recalc,
            )
        )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `pytest tests/test_daemon/test_scheduler.py -v`
Expected: All scheduler tests pass — including the new librarian-registration test AND the existing 5b auditor-registration test.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_scheduler.py
git commit -m "feat: register librarian + authority_recalc workers in daemon"
```

---

### Task 11: End-to-end integration test

**Files:**
- Create: `tests/test_librarian/test_integration.py`

End-to-end: synthesize a traversal log, scan a vault, run `LibrarianAgent.run()` with a stub LLM, then re-scan and assert the entries carry the refined tags + summary.

- [ ] **Step 1: Write failing test**

```python
# tests/test_librarian/test_integration.py
"""End-to-end: traversal log → librarian.run() → vault rescan → entries reflect refinement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.agent import LibrarianAgent
from llm_wiki.vault import Vault, _state_dir_for


class _StubLLM:
    def __init__(self, response_text: str) -> None:
        self.response = response_text

    async def complete(self, messages, temperature: float = 0.7, priority: str = "query"):
        from llm_wiki.traverse.llm_client import LLMResponse
        return LLMResponse(content=self.response, tokens_used=100)


@pytest.mark.asyncio
async def test_librarian_full_lifecycle(sample_vault: Path):
    """Run librarian → rescan vault → assert entry has the refined fields."""
    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Seed 5 distinct queries that read srna-embeddings (clears default threshold of 10? Use override below)
    log_dir = state_dir / "traversal_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "traversal_logs.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for i in range(5):
            f.write(json.dumps({
                "query": f"How are sRNA embeddings validated? variant {i}",
                "turns": [{"turn": 0, "pages_read": [
                    {"name": "srna-embeddings", "sections_read": ["overview"],
                     "salient_points": f"PCA k=10 (sample {i})", "relevance": 0.85}
                ], "tokens_used": 0, "hypothesis": "", "remaining_questions": [], "next_candidates": []}],
            }) + "\n")

    config = WikiConfig()
    config.budgets.manifest_refresh_after_traversals = 3

    stub = _StubLLM(
        '{"tags": ["validation", "embeddings", "k-means"], '
        '"summary": "Validates sRNA embeddings via PCA + k-means."}'
    )

    vault = Vault.scan(sample_vault)
    agent = LibrarianAgent(vault, sample_vault, stub, IssueQueue(sample_vault / "wiki"), config)

    result = await agent.run()

    assert "srna-embeddings" in result.pages_refined
    assert result.authorities_updated >= 1

    # Rescan to verify the override survives + is applied to the manifest entry
    rescanned = Vault.scan(sample_vault)
    entry = rescanned.manifest_entries()["srna-embeddings"]

    assert entry.tags == ["validation", "embeddings", "k-means"]
    assert "PCA + k-means" in entry.summary
    assert entry.authority > 0.0
    assert entry.read_count == 5
```

- [ ] **Step 2: Run test, expect PASS**

Run: `pytest tests/test_librarian/test_integration.py -v`
Expected: PASS — all the underlying machinery is in place from earlier tasks.

If the test fails because `entry.summary` is the auto-generated default (rather than the override), check that Task 5's `_apply_overrides` reads `summary_override` and writes it to `entry.summary` only when present.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: All tests pass — Phase 1-4, Phase 5a, Phase 5b, and all new Phase 5c tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_librarian/test_integration.py
git commit -m "test: phase 5c librarian end-to-end integration"
```

---

### Task 12: README + roadmap update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Project Structure**

Add to the package layout in `README.md` under `src/llm_wiki/`:

```
  librarian/
    log_reader.py       # PageUsage, aggregate_logs (reads traversal_logs.jsonl)
    authority.py        # PageRank-style scoring formula
    overrides.py        # ManifestOverrides JSON sidecar
    prompts.py          # Tag/summary refinement prompt
    agent.py            # LibrarianAgent (refresh + recalc_authority)
```

- [ ] **Step 2: Update Roadmap**

Mark 5c as complete:

```markdown
- [x] **Phase 5a: Issue Queue + Auditor + Lint** — Structural integrity checks, persistent issue queue, `llm-wiki lint`
- [x] **Phase 5b: Background Workers + Compliance Review** — Async scheduler, debounced compliance pipeline
- [x] **Phase 5c: Librarian** — Usage-driven manifest refinement, authority scoring
- [ ] **Phase 5d: Adversary + Talk Pages** — Claim verification, async discussion sidecars
- [ ] **Phase 6: MCP Server** — High-level + low-level tools for agent integration
```

- [ ] **Step 3: Update Documentation references**

Add to the Documentation list:

```markdown
- **[Phase 5c Plan](docs/superpowers/plans/2026-04-08-phase5c-librarian.md)** — Implementation plan for librarian agent + authority scoring
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README updates for phase 5c — librarian, authority scoring"
```

---

## Self-review checklist

Before declaring this plan complete, verify:

- [ ] `aggregate_logs` handles missing file, empty file, malformed JSON lines, and pages appearing in multiple turns of the same query
- [ ] `compute_authority` matches the spec formula exactly with weights (0.3, 0.4, 0.2, 0.1)
- [ ] `freshness_score` returns 0.5 for None `last_corroborated` AND clamps at 0.5 floor for very old timestamps
- [ ] `ManifestOverrides.save()` uses temp-file-and-rename (verify no leftover `.tmp` files in tests)
- [ ] `Vault.scan()` modification is non-breaking when no override file exists (existing scan tests still pass)
- [ ] `_apply_overrides` only sets `entry.summary` when `summary_override` is truthy (preserves auto-generated summary otherwise)
- [ ] `_apply_overrides` only sets `entry.tags` when `override.tags` is non-empty (so an empty override doesn't blank tags)
- [ ] `LibrarianAgent.refresh_page` uses `priority="maintenance"` on the LLM call
- [ ] `LibrarianAgent.run` uses `read_count - last_refreshed_read_count` as the trigger, NOT raw `read_count`
- [ ] `LibrarianAgent.recalc_authority` is purely programmatic — `_StubLLM().calls == []` after a recalc
- [ ] All three workers (`auditor`, `librarian`, `authority_recalc`) are registered and visible in `scheduler-status`
- [ ] No LLM call uses `priority="query"` or default `priority` from a maintenance worker
- [ ] Empty vault is exercised by every check, agent method, and integration test

## Spec sections satisfied by 5c

- §5 Librarian row — usage-driven refinement, authority recalculation
- §4 Hierarchical Manifest paragraph — librarian-managed `tags`, `summary`, `authority`
- §4 Manifest lifecycle paragraph — refresh trigger via `manifest_refresh_after_traversals`, override storage in state dir
- §5 Authority Scoring section (full) — PageRank-inspired formula with freshness decay

## What's deferred from this sub-phase

Explicitly out of scope (handled by later sub-phases or future work):

- Cross-reference suggestions (defer to future enhancement)
- Cluster summary refinement (defer)
- Talk-page reading by the librarian (deferred to 5d, since 5d ships talk pages)
- Honcho integration (out of phase 5 entirely)
- Stale-page issue filing (deferred to a future enhancement; the heuristic is too noisy for v1)
- LLM-driven librarian decision-making about when to file issues vs. update silently

## Dependencies

- **Requires 5a** for `IssueQueue` (passed to `LibrarianAgent` even though 5c doesn't currently file issues — it's plumbed for forward compatibility with stale-page detection)
- **Requires 5b** for the scheduler and the `_register_maintenance_workers` extension point
- Sub-phase 5d (adversary) will:
  - Read `ManifestOverrides` to update `last_corroborated` after validating claims
  - Read `entry.authority` for weighted claim sampling
  - Add an `adversary` worker via the same extension point in Task 10

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-phase5c-librarian.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Either option uses this plan as the input. The most fragile tasks are 5 (Vault.scan modification — affects every existing test) and 9 (run orchestration — depends on the trigger semantics being correct). Review those carefully.
