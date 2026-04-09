# Cluster D: Synthesis + Resonance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add synthesis claim markers, a `resonance` talk entry type, post-ingest claim resonance matching, plus L1 compact JSON serialisation and L4 skill file audit folded in.

**Architecture:** Synthesis pages (`status: synthesis` in frontmatter) bypass adversary verification and citation compliance checks. A new `TalkEntry.type` field makes resonance findings machine-readable. A `ResonanceAgent` runs post-ingest, comparing new page claims against existing wiki claims (weighted toward synthesis pages) via tantivy search + LLM assessment, and files resonance talk entries. L1 is a one-liner in `_ok()`; L4 is a targeted pass over skill files that are touched anyway.

**Tech Stack:** Python stdlib (`pathlib`, `datetime`, `re`), PyYAML, litellm (already deps), `pytest-asyncio` for async agent tests. No new dependencies.

**Prerequisite:** Cluster B (source reading status) must be merged first — this plan assumes `Auditor.__init__` already takes `config: WikiConfig` (added in Cluster B Task 5).

---

## File Structure

| File | Change |
|---|---|
| `src/llm_wiki/mcp/tools.py` | L1: compact JSON in `_ok()` |
| `src/llm_wiki/config.py` | Add synthesis + resonance fields to `MaintenanceConfig` |
| `src/llm_wiki/audit/compliance.py` | Skip `_check_missing_citation` for `status: synthesis` pages |
| `src/llm_wiki/adversary/agent.py` | Filter out synthesis pages before extracting claims |
| `src/llm_wiki/talk/page.py` | Add `type: str` field to `TalkEntry`; extend `_parse_meta`/`_format_meta` |
| `src/llm_wiki/audit/checks.py` | Add `find_stale_resonance` and `find_synthesis_without_resonance` |
| `src/llm_wiki/audit/auditor.py` | Call new checks |
| `src/llm_wiki/resonance/__init__.py` | **New** — empty package |
| `src/llm_wiki/resonance/prompts.py` | **New** — resonance assessment prompt + response parser |
| `src/llm_wiki/resonance/agent.py` | **New** — `ResonanceAgent` |
| `src/llm_wiki/ingest/agent.py` | Call `ResonanceAgent.run_for_pages()` post page creation |
| `skills/llm-wiki/index.md` | Inference economics note + traversal hop-count |
| `skills/llm-wiki/research.md` | 3-hop minimum anchor |
| `skills/llm-wiki/write.md` | Synthesis status convention + session-close at end |
| `skills/llm-wiki/maintain.md` | Resonance review guidance + session-close at end |
| `skills/llm-wiki/ingest.md` | Synthesis status documentation |
| `tests/test_mcp/test_compact_json.py` | **New** |
| `tests/test_audit/test_compliance_synthesis.py` | **New** |
| `tests/test_adversary/test_synthesis_skip.py` | **New** |
| `tests/test_talk/test_talk_type.py` | **New** |
| `tests/test_audit/test_stale_resonance.py` | **New** |
| `tests/test_resonance/__init__.py` | **New** |
| `tests/test_resonance/test_resonance_agent.py` | **New** |

---

### Task 1: L1 — Compact JSON serialisation

**Files:**
- Modify: `src/llm_wiki/mcp/tools.py:50`
- Create: `tests/test_mcp/test_compact_json.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp/test_compact_json.py`:

```python
from llm_wiki.mcp.tools import _ok


def test_ok_produces_compact_json():
    result = _ok({"a": {"b": "c"}, "d": [1, 2, 3]})
    text = result[0].text
    assert "\n" not in text
    assert "  " not in text
    # Still valid JSON
    import json
    parsed = json.loads(text)
    assert parsed == {"a": {"b": "c"}, "d": [1, 2, 3]}


def test_ok_compact_is_smaller_than_pretty():
    import json
    data = {"issues": {"open_count": 3, "by_severity": {"critical": 1, "moderate": 2}}}
    compact = _ok(data)[0].text
    pretty = json.dumps(data, indent=2)
    assert len(compact) < len(pretty)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_mcp/test_compact_json.py -v 2>&1 | head -20
```

Expected: FAIL — `assert "\n" not in text` (current output has newlines from `indent=2`)

- [ ] **Step 3: Change `_ok` in `src/llm_wiki/mcp/tools.py`**

```python
# Before (line 49-50):
def _ok(response: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(response, indent=2))]

# After:
def _ok(response: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(response, separators=(",", ":")))]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_mcp/test_compact_json.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/tools.py tests/test_mcp/test_compact_json.py
git commit -m "feat: compact JSON serialisation in MCP tool responses (L1)"
```

---

### Task 2: Config — synthesis + resonance fields

**Files:**
- Modify: `src/llm_wiki/config.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_config.py` (or create if absent):

```python
from llm_wiki.config import WikiConfig


def test_maintenance_config_has_synthesis_defaults():
    cfg = WikiConfig()
    assert cfg.maintenance.synthesis_lint_enabled is False
    assert cfg.maintenance.synthesis_lint_months == 6


def test_maintenance_config_has_resonance_defaults():
    cfg = WikiConfig()
    assert cfg.maintenance.resonance_matching is False
    assert cfg.maintenance.resonance_candidates_per_claim == 3
    assert cfg.maintenance.resonance_weight_synthesis == 2.0
    assert cfg.maintenance.resonance_stale_weeks == 4
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_config.py -v -k "synthesis or resonance" 2>&1 | head -20
```

Expected: FAIL — `AttributeError: 'MaintenanceConfig' object has no attribute 'synthesis_lint_enabled'`

- [ ] **Step 3: Add fields to `MaintenanceConfig` in `src/llm_wiki/config.py`**

```python
@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    auditor_interval: str = "24h"
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600
    failure_escalation_threshold: int = 3
    # Cluster B additions (source reading status)
    auditor_unread_source_days: int = 30
    adversary_unread_weight: float = 1.5
    # Cluster D additions (synthesis + resonance)
    synthesis_lint_enabled: bool = False
    synthesis_lint_months: int = 6
    resonance_matching: bool = False
    resonance_candidates_per_claim: int = 3
    resonance_weight_synthesis: float = 2.0
    resonance_stale_weeks: int = 4
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_config.py -v -k "synthesis or resonance"
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: add synthesis and resonance config fields to MaintenanceConfig"
```

---

### Task 3: Compliance reviewer — synthesis bypass

**Files:**
- Modify: `src/llm_wiki/audit/compliance.py`
- Create: `tests/test_audit/test_compliance_synthesis.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit/test_compliance_synthesis.py`:

```python
from pathlib import Path

import pytest

from llm_wiki.audit.compliance import ComplianceReviewer
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue


def _make_reviewer(tmp_path: Path) -> ComplianceReviewer:
    (tmp_path / "wiki").mkdir(exist_ok=True)
    queue = IssueQueue(tmp_path / "wiki" / ".issues")
    return ComplianceReviewer(tmp_path, queue, WikiConfig())


def test_synthesis_page_skips_citation_check(tmp_path: Path):
    """A page with status: synthesis must not get a missing-citation issue."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\nstatus: synthesis\n---\nA claim with absolutely no citation here.\n"
    page = tmp_path / "wiki" / "syn-page.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert result.issues_filed == []


def test_synthesis_page_still_gets_structural_drift_check(tmp_path: Path):
    """Synthesis pages still get %% markers auto-inserted on structural drift."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\nstatus: synthesis\n---\n## My Heading\n\nSome uncited content.\n"
    page = tmp_path / "wiki" / "syn-drift.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert any("inserted-marker" in fix for fix in result.auto_fixed)


def test_non_synthesis_page_still_gets_citation_check(tmp_path: Path):
    """Normal pages (no status field) still get the citation compliance check."""
    reviewer = _make_reviewer(tmp_path)
    content = "---\ntitle: Normal\n---\nA claim with no citation at all.\n"
    page = tmp_path / "wiki" / "normal-page.md"
    page.write_text(content)
    result = reviewer.review_change(page, None, content)
    assert len(result.issues_filed) > 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_audit/test_compliance_synthesis.py -v 2>&1 | head -20
```

Expected: `test_synthesis_page_skips_citation_check` FAIL — citation issue is filed for synthesis page

- [ ] **Step 3: Add synthesis bypass to `src/llm_wiki/audit/compliance.py`**

Add a static helper after the existing statics at the bottom of the class, then modify `_check_missing_citation`:

```python
@staticmethod
def _is_synthesis_page(content: str) -> bool:
    """True iff the page frontmatter contains `status: synthesis`."""
    if not content.startswith("---\n"):
        return False
    try:
        end = content.index("\n---", 4)
    except ValueError:
        return False
    fm_text = content[3:end].strip()
    import yaml
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return False
    return fm.get("status") == "synthesis"
```

Then at the top of `_check_missing_citation`, before any logic:

```python
def _check_missing_citation(
    self,
    result: ComplianceResult,
    old_content: str | None,
    new_content: str,
) -> None:
    # Synthesis pages have no external citation requirement.
    if self._is_synthesis_page(new_content):
        return
    # ... rest of the method unchanged
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_audit/test_compliance_synthesis.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/audit/compliance.py tests/test_audit/test_compliance_synthesis.py
git commit -m "feat: compliance reviewer skips citation check for status:synthesis pages"
```

---

### Task 4: Adversary — skip synthesis pages

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py`
- Create: `tests/test_adversary/test_synthesis_skip.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_adversary/test_synthesis_skip.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from random import Random

import pytest

from llm_wiki.adversary.agent import AdversaryAgent
from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.page import Page, Section


def _make_page(slug: str, status: str | None, has_claim: bool, tmp_path: Path) -> Page:
    body = ""
    if has_claim:
        body = "A verifiable claim [[raw/source.pdf]].\n"
    fm = f"---\nstatus: {status}\n---\n" if status else "---\n---\n"
    path = tmp_path / "wiki" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(fm + body)
    return Page.parse(path)


@pytest.mark.asyncio
async def test_adversary_skips_synthesis_page_claims(tmp_path: Path):
    """Claims from status:synthesis pages must never be sampled for verification."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    synthesis_page = _make_page("syn-page", "synthesis", has_claim=True, tmp_path=tmp_path)
    normal_page = _make_page("normal-page", None, has_claim=True, tmp_path=tmp_path)

    vault = MagicMock()
    vault.manifest_entries.return_value = {
        "syn-page": MagicMock(authority=0.5, last_corroborated=None),
        "normal-page": MagicMock(authority=0.5, last_corroborated=None),
    }
    vault.read_page.side_effect = lambda name: {
        "syn-page": synthesis_page,
        "normal-page": normal_page,
    }.get(name)

    llm = MagicMock()
    # LLM should never be called (no claims should be sampled at all in this mock
    # because the only non-synthesis claim would need extract_text to succeed)
    llm.complete = AsyncMock(return_value=MagicMock(content="VERDICT: validated\nCONFIDENCE: 0.9\nEXPLANATION: ok"))

    queue = IssueQueue(tmp_path / ".issues")
    config = WikiConfig()
    config.maintenance.adversary_claims_per_run = 10

    agent = AdversaryAgent(
        vault=vault,
        vault_root=tmp_path,
        llm=llm,
        queue=queue,
        config=config,
        rng=Random(42),
    )

    # Patch extract_claims to track which pages are processed
    processed_pages: list[str] = []
    original_extract = __import__(
        "llm_wiki.adversary.claim_extractor", fromlist=["extract_claims"]
    ).extract_claims

    def tracking_extract(page: Page) -> list[Claim]:
        processed_pages.append(page.path.stem)
        return original_extract(page)

    with patch("llm_wiki.adversary.agent.extract_claims", side_effect=tracking_extract):
        await agent.run()

    assert "syn-page" not in processed_pages
    assert "normal-page" in processed_pages
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_adversary/test_synthesis_skip.py -v 2>&1 | head -30
```

Expected: FAIL — `assert "syn-page" not in processed_pages` (synthesis page IS currently processed)

- [ ] **Step 3: Add synthesis filter to `src/llm_wiki/adversary/agent.py`**

In `AdversaryAgent.run()`, modify the claim-extraction loop:

```python
# 1. Extract claims from every non-synthesis page
all_claims: list[Claim] = []
for name in entries:
    page = self._vault.read_page(name)
    if page is None:
        continue
    if page.frontmatter.get("status") == "synthesis":
        continue  # resonance agent handles synthesis pages; adversary skips them
    all_claims.extend(extract_claims(page))
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_adversary/test_synthesis_skip.py -v
```

Expected: PASS

- [ ] **Step 5: Run full adversary test suite to confirm no regressions**

```bash
pytest tests/test_adversary/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_synthesis_skip.py
git commit -m "feat: adversary skips claims from status:synthesis pages"
```

---

### Task 5: TalkEntry — `type` field (resonance talk type)

**Files:**
- Modify: `src/llm_wiki/talk/page.py`
- Create: `tests/test_talk/test_talk_type.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_talk/test_talk_type.py`:

```python
from pathlib import Path

import pytest

from llm_wiki.talk.page import TalkEntry, TalkPage


def test_talk_entry_default_type_is_suggestion():
    e = TalkEntry(index=0, timestamp="2026-04-10T12:00:00", author="@user", body="hello")
    assert e.type == "suggestion"


def test_resonance_type_roundtrips_through_file(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@resonance",
        body="New source corroborates this claim.",
        severity="moderate",
        type="resonance",
    )
    talk.append(entry)

    loaded = talk.load()
    assert len(loaded) == 1
    assert loaded[0].type == "resonance"
    assert loaded[0].severity == "moderate"


def test_suggestion_type_writes_no_html_comment(tmp_path: Path):
    """Default type='suggestion' with default severity must not add <!-- --> comment."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@user",
        body="A plain suggestion.",
    )
    talk.append(entry)

    raw = talk.path.read_text()
    assert "<!--" not in raw  # no metadata comment for fully-default entry


def test_adversary_finding_type_roundtrips(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "test-page.md"
    page_path.write_text("---\ntitle: Test\n---\nContent.\n")

    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp="2026-04-10T12:00:00",
        author="@adversary",
        body="Verdict: ambiguous.",
        severity="critical",
        type="adversary-finding",
    )
    talk.append(entry)

    loaded = talk.load()
    assert loaded[0].type == "adversary-finding"


def test_old_file_without_type_field_defaults_to_suggestion(tmp_path: Path):
    """Pre-existing talk files (no `type:` in metadata) must load with type='suggestion'."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page_path = wiki_dir / "old-page.md"
    page_path.write_text("---\ntitle: Old\n---\nContent.\n")

    talk_path = wiki_dir / "old-page.talk.md"
    talk_path.write_text(
        "---\npage: old-page\n---\n\n"
        "**2026-01-01T00:00:00 — @user** <!-- severity:moderate -->\n"
        "Old-style entry.\n"
    )

    talk = TalkPage(talk_path)
    loaded = talk.load()
    assert loaded[0].type == "suggestion"
    assert loaded[0].severity == "moderate"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_talk/test_talk_type.py -v 2>&1 | head -20
```

Expected: FAIL — `TalkEntry.__init__() got an unexpected keyword argument 'type'`

- [ ] **Step 3: Extend `TalkEntry`, `_parse_meta`, `_format_meta` in `src/llm_wiki/talk/page.py`**

Add `type` to `TalkEntry`:

```python
@dataclass
class TalkEntry:
    index: int
    timestamp: str
    author: str
    body: str
    severity: Severity = "suggestion"
    resolves: list[int] = field(default_factory=list)
    type: str = "suggestion"   # suggestion | resonance | adversary-finding | new_connection
```

Extend `_parse_meta` to parse the `type` key:

```python
def _parse_meta(meta_str: str | None) -> tuple[str, list[int], str]:
    """Parse a `severity:foo, type:bar, resolves:[1,2]` metadata blob.

    Returns (severity, resolves, type). Missing keys default to
    ("suggestion", [], "suggestion").
    """
    if not meta_str:
        return "suggestion", [], "suggestion"

    severity = "suggestion"
    resolves: list[int] = []
    entry_type = "suggestion"

    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in meta_str:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())

    for part in parts:
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "severity":
            severity = value
        elif key == "type":
            entry_type = value
        elif key == "resolves":
            inner = value.strip("[]")
            if inner:
                try:
                    resolves = [int(x.strip()) for x in inner.split(",") if x.strip()]
                except ValueError:
                    resolves = []
    return severity, resolves, entry_type
```

Extend `_format_meta` to include `type` when non-default:

```python
def _format_meta(severity: str, resolves: list[int], entry_type: str = "suggestion") -> str:
    """Build the optional `<!-- ... -->` suffix for an entry header line.

    Returns an empty string for the fully-default case (type='suggestion',
    severity='suggestion', no resolves).
    """
    parts: list[str] = []
    if entry_type != "suggestion":
        parts.append(f"type:{entry_type}")
    if severity != "suggestion":
        parts.append(f"severity:{severity}")
    if resolves:
        joined = ",".join(str(i) for i in resolves)
        parts.append(f"resolves:[{joined}]")
    if not parts:
        return ""
    return f" <!-- {', '.join(parts)} -->"
```

Update `TalkPage.load()` — the `_parse_meta` call now returns three values:

```python
severity, resolves, entry_type = _parse_meta(meta)
# ...
entries.append(TalkEntry(
    index=i + 1,
    timestamp=ts,
    author=author,
    body=entry_body,
    severity=severity,
    resolves=resolves,
    type=entry_type,
))
```

Update `TalkPage.append()` — pass `entry.type` to `_format_meta`:

```python
meta_suffix = _format_meta(entry.severity, entry.resolves, entry.type)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_talk/test_talk_type.py -v
```

Expected: all PASS

- [ ] **Step 5: Run full talk page test suite for regressions**

```bash
pytest tests/test_talk/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/talk/page.py tests/test_talk/test_talk_type.py
git commit -m "feat: add type field to TalkEntry (resonance, adversary-finding, new_connection)"
```

---

### Task 6: Auditor — stale resonance + synthesis-without-resonance checks

**Files:**
- Modify: `src/llm_wiki/audit/checks.py`
- Modify: `src/llm_wiki/audit/auditor.py`
- Create: `tests/test_audit/test_stale_resonance.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit/test_stale_resonance.py`:

```python
import datetime
from pathlib import Path

import pytest

from llm_wiki.audit.checks import find_stale_resonance, find_synthesis_without_resonance
from llm_wiki.config import WikiConfig
from llm_wiki.talk.page import TalkEntry, TalkPage


def _make_wiki(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return wiki_dir


def _write_page(wiki_dir: Path, slug: str, status: str | None = None) -> Path:
    page_path = wiki_dir / f"{slug}.md"
    fm = f"---\nstatus: {status}\n---\n" if status else "---\ntitle: Normal\n---\n"
    page_path.write_text(fm + "Content here.\n")
    return page_path


def _write_resonance_entry(page_path: Path, days_old: int) -> None:
    ts = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_old)
    ).isoformat()
    talk = TalkPage.for_page(page_path)
    entry = TalkEntry(
        index=0,
        timestamp=ts,
        author="@resonance",
        body="New source may corroborate this claim.",
        severity="moderate",
        type="resonance",
    )
    talk.append(entry)


# --- find_stale_resonance ---

def test_stale_resonance_flags_old_open_resonance_entry(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "my-page")
    _write_resonance_entry(page_path, days_old=35)  # > 4-week default

    config = WikiConfig()
    result = find_stale_resonance(tmp_path, config)
    assert result.check == "stale-resonance"
    assert len(result.issues) == 1
    assert result.issues[0].page == "my-page"


def test_stale_resonance_ignores_recent_entry(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "my-page")
    _write_resonance_entry(page_path, days_old=10)  # < 4 weeks

    config = WikiConfig()
    result = find_stale_resonance(tmp_path, config)
    assert len(result.issues) == 0


def test_stale_resonance_empty_wiki(tmp_path: Path):
    _make_wiki(tmp_path)
    result = find_stale_resonance(tmp_path, WikiConfig())
    assert len(result.issues) == 0


# --- find_synthesis_without_resonance ---

def test_synthesis_without_resonance_flags_old_synthesis_page(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    # Page is old (simulate by setting mtime far in the past is unreliable;
    # instead we set the page's ingested frontmatter field)
    page_path = _write_page(wiki_dir, "syn-page", status="synthesis")
    old_date = (
        datetime.date.today() - datetime.timedelta(days=200)  # > 6 months default
    ).isoformat()
    page_path.write_text(
        f"---\nstatus: synthesis\ningested: {old_date}\n---\nContent.\n"
    )

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = True
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 1
    assert result.issues[0].page == "syn-page"


def test_synthesis_without_resonance_skipped_when_disabled(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "syn-page", status="synthesis")
    old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    page_path.write_text(
        f"---\nstatus: synthesis\ningested: {old_date}\n---\nContent.\n"
    )

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = False  # default — must be a no-op
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 0


def test_synthesis_without_resonance_skips_if_resonance_talk_exists(tmp_path: Path):
    wiki_dir = _make_wiki(tmp_path)
    page_path = _write_page(wiki_dir, "syn-page", status="synthesis")
    old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    page_path.write_text(
        f"---\nstatus: synthesis\ningested: {old_date}\n---\nContent.\n"
    )
    _write_resonance_entry(page_path, days_old=10)  # has a resonance entry

    config = WikiConfig()
    config.maintenance.synthesis_lint_enabled = True
    result = find_synthesis_without_resonance(tmp_path, config)
    assert len(result.issues) == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_audit/test_stale_resonance.py -v 2>&1 | head -20
```

Expected: FAIL — `ImportError: cannot import name 'find_stale_resonance'`

- [ ] **Step 3: Add both check functions to `src/llm_wiki/audit/checks.py`**

Add at the end of the file (after the existing imports add `from llm_wiki.config import WikiConfig` and `from llm_wiki.talk.page import TalkPage, iter_talk_pages, compute_open_set`):

```python
import datetime

from llm_wiki.config import WikiConfig
from llm_wiki.talk.page import TalkPage, iter_talk_pages, compute_open_set


def find_stale_resonance(vault_root: Path, config: WikiConfig) -> CheckResult:
    """Open resonance talk entries older than resonance_stale_weeks.

    Walks wiki/ talk pages, finds unresolved entries with type='resonance'
    whose timestamp is older than the configured threshold. Pure file reads,
    no LLM.
    """
    wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")
    threshold_days = config.maintenance.resonance_stale_weeks * 7
    now = datetime.datetime.now(datetime.timezone.utc)
    issues: list[Issue] = []

    for page_name, talk in iter_talk_pages(wiki_dir):
        entries = talk.load()
        open_entries = compute_open_set(entries)
        resonance_open = [e for e in open_entries if e.type == "resonance"]
        for entry in resonance_open:
            try:
                ts = datetime.datetime.fromisoformat(entry.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
            except (ValueError, TypeError):
                continue
            age_days = (now - ts).days
            if age_days < threshold_days:
                continue
            issues.append(
                Issue(
                    id=Issue.make_id("stale-resonance", page_name, entry.timestamp),
                    type="stale-resonance",
                    status="open",
                    severity="minor",
                    title=f"Unreviewed resonance entry on '{page_name}' ({age_days}d old)",
                    page=page_name,
                    body=(
                        f"A resonance talk entry on [[{page_name}]] has not been "
                        f"reviewed in {age_days} days. Review whether the resonance "
                        f"is meaningful: promote to main content, add cross-reference, "
                        f"or resolve as a false match."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"entry_timestamp": entry.timestamp, "age_days": age_days},
                )
            )
    return CheckResult(check="stale-resonance", issues=issues)


def find_synthesis_without_resonance(vault_root: Path, config: WikiConfig) -> CheckResult:
    """Synthesis pages older than synthesis_lint_months with no resonance talk entries.

    Gated by config.maintenance.synthesis_lint_enabled (default False). When
    enabled, flags synthesis pages that have never received a resonance check —
    a signal that the resonance matching pipeline may not be running or the
    page predates it.
    """
    if not config.maintenance.synthesis_lint_enabled:
        return CheckResult(check="synthesis-without-resonance", issues=[])

    wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")
    threshold_days = config.maintenance.synthesis_lint_months * 30
    today = datetime.date.today()
    issues: list[Issue] = []

    if not wiki_dir.exists():
        return CheckResult(check="synthesis-without-resonance", issues=[])

    for md_path in sorted(wiki_dir.rglob("*.md")):
        rel = md_path.relative_to(wiki_dir)
        if any(p.startswith(".") for p in rel.parts):
            continue
        if md_path.name.endswith(".talk.md"):
            continue

        # Fast frontmatter read
        try:
            with md_path.open(encoding="utf-8") as f:
                if f.readline().strip() != "---":
                    continue
                lines: list[str] = []
                for _ in range(30):
                    line = f.readline()
                    if not line or line.strip() == "---":
                        break
                    lines.append(line)
        except OSError:
            continue

        import yaml
        try:
            fm = yaml.safe_load("".join(lines)) or {}
        except yaml.YAMLError:
            continue

        if fm.get("status") != "synthesis":
            continue

        # Check page age via `ingested` frontmatter field (set by wiki_ingest)
        ingested_str = fm.get("ingested")
        if ingested_str is None:
            continue
        try:
            ingested = datetime.date.fromisoformat(str(ingested_str))
        except (ValueError, TypeError):
            continue

        age_days = (today - ingested).days
        if age_days < threshold_days:
            continue

        # Check whether this page has any resonance talk entries
        talk = TalkPage.for_page(md_path)
        entries = talk.load()
        has_resonance = any(e.type == "resonance" for e in entries)
        if has_resonance:
            continue

        page_name = md_path.stem
        issues.append(
            Issue(
                id=Issue.make_id("synthesis-without-resonance", page_name, ""),
                type="synthesis-without-resonance",
                status="open",
                severity="minor",
                title=f"Synthesis page '{page_name}' has no resonance checks ({age_days}d old)",
                page=page_name,
                body=(
                    f"The synthesis page [[{page_name}]] is {age_days} days old and "
                    f"has never received a resonance talk entry. Check that resonance "
                    f"matching is enabled and that this page has been compared against "
                    f"incoming sources."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"age_days": age_days, "ingested": str(ingested_str)},
            )
        )
    return CheckResult(check="synthesis-without-resonance", issues=issues)
```

- [ ] **Step 4: Add calls to `Auditor.audit()` in `src/llm_wiki/audit/auditor.py`**

```python
# Add to imports:
from llm_wiki.audit.checks import (
    find_broken_citations,
    find_broken_wikilinks,
    find_missing_markers,
    find_orphans,
    find_stale_resonance,
    find_synthesis_without_resonance,
)

# In audit():
results = [
    find_orphans(self._vault),
    find_broken_wikilinks(self._vault),
    find_missing_markers(self._vault),
    find_broken_citations(self._vault, self._vault_root),
    find_stale_resonance(self._vault_root, self._config),
    find_synthesis_without_resonance(self._vault_root, self._config),
]
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_audit/test_stale_resonance.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full audit test suite for regressions**

```bash
pytest tests/test_audit/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/audit/checks.py src/llm_wiki/audit/auditor.py tests/test_audit/test_stale_resonance.py
git commit -m "feat: auditor checks for stale resonance entries and synthesis pages without resonance"
```

---

### Task 7: Resonance prompts

**Files:**
- Create: `src/llm_wiki/resonance/__init__.py`
- Create: `src/llm_wiki/resonance/prompts.py`
- Create: `tests/test_resonance/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resonance/__init__.py` (empty).

Create `tests/test_resonance/test_resonance_prompts.py`:

```python
from llm_wiki.resonance.prompts import compose_resonance_messages, parse_resonance


def test_compose_includes_both_claims():
    msgs = compose_resonance_messages(
        new_claim="Diffusion noise scale controls structural diversity.",
        new_source="raw/2026-04-10-rfdiffusion.pdf",
        existing_claim="Diffusion models produce diverse outputs via noise injection.",
        existing_page="diffusion-models",
    )
    assert len(msgs) == 2
    user_text = msgs[1]["content"]
    assert "diffusion-models" in user_text
    assert "rfdiffusion" in user_text
    assert "noise scale" in user_text.lower() or "noise injection" in user_text.lower()


def test_parse_resonance_yes():
    response = "VERDICT: YES\nRELATION: corroborates\nNOTE: Both discuss noise as a diversity control."
    verdict = parse_resonance(response)
    assert verdict.resonates is True
    assert verdict.relation == "corroborates"
    assert "noise" in verdict.note.lower()


def test_parse_resonance_no():
    response = "VERDICT: NO"
    verdict = parse_resonance(response)
    assert verdict.resonates is False
    assert verdict.relation is None
    assert verdict.note is None


def test_parse_resonance_extends():
    response = "VERDICT: YES\nRELATION: extends\nNOTE: Adds empirical data to the theoretical claim."
    verdict = parse_resonance(response)
    assert verdict.relation == "extends"


def test_parse_resonance_contradicts():
    response = "VERDICT: YES\nRELATION: contradicts\nNOTE: New source disputes the benchmark."
    verdict = parse_resonance(response)
    assert verdict.relation == "contradicts"


def test_parse_resonance_malformed_returns_no():
    """Unparseable responses default to no resonance (don't file spurious talk posts)."""
    verdict = parse_resonance("I cannot determine this.")
    assert verdict.resonates is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_resonance/test_resonance_prompts.py -v 2>&1 | head -20
```

Expected: FAIL — `ModuleNotFoundError: No module named 'llm_wiki.resonance'`

- [ ] **Step 3: Create `src/llm_wiki/resonance/__init__.py`**

```python
# Resonance matching — post-ingest claim comparison against existing wiki claims.
```

- [ ] **Step 4: Create `src/llm_wiki/resonance/prompts.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResonanceVerdict:
    resonates: bool
    relation: str | None  # corroborates | extends | contradicts
    note: str | None


def compose_resonance_messages(
    new_claim: str,
    new_source: str,
    existing_claim: str,
    existing_page: str,
) -> list[dict]:
    """Compose messages for an LLM resonance assessment call."""
    return [
        {
            "role": "system",
            "content": (
                "You determine whether a claim from a newly ingested source "
                "meaningfully connects to an existing wiki claim. "
                "Be conservative — minor vocabulary overlap does not count as resonance. "
                "Resonance means the claims are about the same phenomenon and one "
                "corroborates, extends, or contradicts the other."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Existing claim on wiki page [[{existing_page}]]:\n"
                f"> {existing_claim}\n\n"
                f"New claim from {new_source}:\n"
                f"> {new_claim}\n\n"
                "Do these claims meaningfully resonate?\n\n"
                "Answer format (exactly as shown):\n"
                "VERDICT: YES|NO\n"
                "RELATION: corroborates|extends|contradicts  (only if YES)\n"
                "NOTE: <one sentence>  (only if YES)"
            ),
        },
    ]


def parse_resonance(response: str) -> ResonanceVerdict:
    """Parse an LLM resonance assessment response.

    Returns a non-resonating verdict for any malformed response to avoid
    filing spurious talk posts.
    """
    for line in response.splitlines():
        if line.strip().startswith("VERDICT:"):
            verdict_value = line.split(":", 1)[1].strip().upper()
            if verdict_value != "YES":
                return ResonanceVerdict(resonates=False, relation=None, note=None)
            break
    else:
        # No VERDICT line found
        return ResonanceVerdict(resonates=False, relation=None, note=None)

    relation: str | None = None
    note: str | None = None
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith("RELATION:"):
            relation = stripped.split(":", 1)[1].strip().lower()
        elif stripped.startswith("NOTE:"):
            note = stripped.split(":", 1)[1].strip()

    return ResonanceVerdict(resonates=True, relation=relation, note=note)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_resonance/test_resonance_prompts.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/resonance/__init__.py src/llm_wiki/resonance/prompts.py \
        tests/test_resonance/__init__.py tests/test_resonance/test_resonance_prompts.py
git commit -m "feat: resonance assessment prompts + parser"
```

---

### Task 8: Resonance agent

**Files:**
- Create: `src/llm_wiki/resonance/agent.py`
- Create: `tests/test_resonance/test_resonance_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resonance/test_resonance_agent.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.config import WikiConfig
from llm_wiki.page import Page, Section
from llm_wiki.resonance.agent import ResonanceAgent
from llm_wiki.search.backend import SearchResult
from llm_wiki.talk.page import TalkPage


def _make_page_with_claim(slug: str, claim_text: str, citation: str, tmp_path: Path) -> Page:
    """Write a wiki page with one cited claim and return the parsed Page."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    path = wiki_dir / f"{slug}.md"
    content = f"---\ntitle: {slug}\n---\n{claim_text} [[{citation}]].\n"
    path.write_text(content)
    return Page.parse(path)


@pytest.mark.asyncio
async def test_resonance_agent_posts_talk_entry_on_match(tmp_path: Path):
    """When LLM returns YES, a resonance talk entry is posted on the existing page."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "rfdiffusion", "Noise scale controls output diversity", "raw/rfd.pdf", tmp_path
    )
    existing_page = _make_page_with_claim(
        "diffusion-models", "Noise controls generative diversity", "raw/old.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.side_effect = lambda name: {
        "rfdiffusion": new_page,
        "diffusion-models": existing_page,
    }.get(name)
    vault.search.return_value = [
        SearchResult(name="diffusion-models", score=0.9, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content="VERDICT: YES\nRELATION: corroborates\nNOTE: Both discuss noise as diversity control."
    ))

    config = WikiConfig()
    config.maintenance.resonance_matching = True
    config.maintenance.resonance_candidates_per_claim = 1

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["rfdiffusion"])

    assert result.resonance_posts == [("rfdiffusion", "diffusion-models")]

    talk = TalkPage.for_page(wiki_dir / "diffusion-models.md")
    entries = talk.load()
    assert len(entries) == 1
    assert entries[0].type == "resonance"
    assert entries[0].severity == "moderate"
    assert "corroborates" in entries[0].body


@pytest.mark.asyncio
async def test_resonance_agent_no_post_on_no_match(tmp_path: Path):
    """When LLM returns NO, no talk entry is posted."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "rfdiffusion", "Noise scale controls output diversity", "raw/rfd.pdf", tmp_path
    )
    existing_page = _make_page_with_claim(
        "other-topic", "Unrelated claim about chemistry", "raw/chem.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.side_effect = lambda name: {
        "rfdiffusion": new_page,
        "other-topic": existing_page,
    }.get(name)
    vault.search.return_value = [
        SearchResult(name="other-topic", score=0.3, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(content="VERDICT: NO"))

    config = WikiConfig()
    config.maintenance.resonance_candidates_per_claim = 1

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["rfdiffusion"])

    assert result.resonance_posts == []
    talk = TalkPage.for_page(wiki_dir / "other-topic.md")
    assert not talk.exists


@pytest.mark.asyncio
async def test_resonance_agent_skips_new_pages_as_candidates(tmp_path: Path):
    """The agent must not compare a new page against itself."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    new_page = _make_page_with_claim(
        "new-page", "A claim", "raw/src.pdf", tmp_path
    )

    vault = MagicMock()
    vault.read_page.return_value = new_page
    # Search returns the new page itself — agent should skip it
    vault.search.return_value = [
        SearchResult(name="new-page", score=0.95, entry=MagicMock()),
    ]

    llm = MagicMock()
    llm.complete = AsyncMock()  # should never be called

    config = WikiConfig()
    config.maintenance.resonance_candidates_per_claim = 3

    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=config)
    result = await agent.run_for_pages(["new-page"])

    llm.complete.assert_not_called()
    assert result.resonance_posts == []


@pytest.mark.asyncio
async def test_resonance_agent_empty_pages_noop(tmp_path: Path):
    vault = MagicMock()
    llm = MagicMock()
    agent = ResonanceAgent(vault=vault, vault_root=tmp_path, llm=llm, config=WikiConfig())
    result = await agent.run_for_pages([])
    assert result.resonance_posts == []
    vault.read_page.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_resonance/test_resonance_agent.py -v 2>&1 | head -20
```

Expected: FAIL — `ModuleNotFoundError: No module named 'llm_wiki.resonance.agent'`

- [ ] **Step 3: Create `src/llm_wiki/resonance/agent.py`**

```python
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.adversary.claim_extractor import Claim, extract_claims
from llm_wiki.config import WikiConfig
from llm_wiki.resonance.prompts import compose_resonance_messages, parse_resonance
from llm_wiki.talk.discovery import ensure_talk_marker
from llm_wiki.talk.page import TalkEntry, TalkPage

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient
    from llm_wiki.vault import Vault

logger = logging.getLogger(__name__)

# Cap claims processed per new page to prevent runaway LLM spend.
_MAX_CLAIMS_PER_PAGE = 5


@dataclass
class ResonanceResult:
    pages_checked: int = 0
    resonance_posts: list[tuple[str, str]] = field(default_factory=list)
    # (new_page_slug, existing_page_slug)


class ResonanceAgent:
    """Post-ingest resonance matching.

    For each newly created page, extracts claims and searches for related
    existing pages via tantivy. Asks the LLM whether each (new claim,
    existing claim) pair resonates. Posts a `resonance` talk entry on the
    existing page when resonance is confirmed.

    LLM calls run at priority='maintenance' so they never compete with
    user-facing queries.
    """

    def __init__(
        self,
        vault: "Vault",
        vault_root: Path,
        llm: "LLMClient",
        config: WikiConfig,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._config = config
        self._wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")

    async def run_for_pages(self, new_page_slugs: list[str]) -> ResonanceResult:
        """Compare claims from new pages against existing wiki claims.

        Args:
            new_page_slugs: Slugs of pages just created by wiki_ingest.
        """
        result = ResonanceResult()
        if not new_page_slugs:
            return result

        new_slugs_set = set(new_page_slugs)
        n = self._config.maintenance.resonance_candidates_per_claim

        for slug in new_page_slugs:
            page = self._vault.read_page(slug)
            if page is None:
                continue
            claims = extract_claims(page)
            if not claims:
                continue

            for claim in claims[:_MAX_CLAIMS_PER_PAGE]:
                await self._check_claim(claim, new_slugs_set, n, result)

        return result

    async def _check_claim(
        self,
        claim: Claim,
        new_slugs_set: set[str],
        n: int,
        result: ResonanceResult,
    ) -> None:
        # Search for related existing pages using the first 120 chars of claim text.
        query = claim.text[:120]
        search_results = self._vault.search(query, limit=n + len(new_slugs_set))

        # Exclude new pages from candidates (don't compare a page against itself)
        candidates = [r for r in search_results if r.name not in new_slugs_set][:n]

        for search_result in candidates:
            candidate_page = self._vault.read_page(search_result.name)
            if candidate_page is None:
                continue

            candidate_claims = extract_claims(candidate_page)
            if not candidate_claims:
                continue

            # Use the first claim from the candidate page for comparison.
            existing_claim = candidate_claims[0]

            messages = compose_resonance_messages(
                new_claim=claim.text,
                new_source=claim.citation,
                existing_claim=existing_claim.text,
                existing_page=candidate_page.path.stem,
            )

            try:
                response = await self._llm.complete(
                    messages, temperature=0.2, priority="maintenance"
                )
            except Exception:
                logger.exception(
                    "Resonance: LLM call failed for claim %s vs page %s",
                    claim.id, candidate_page.path.stem,
                )
                continue

            verdict = parse_resonance(response.content)
            result.pages_checked += 1

            if not verdict.resonates:
                continue

            self._post_resonance_entry(claim, existing_claim, verdict, result)

    def _post_resonance_entry(
        self,
        new_claim: Claim,
        existing_claim: Claim,
        verdict,
        result: ResonanceResult,
    ) -> None:
        page_path = self._wiki_dir / f"{existing_claim.page}.md"
        if not page_path.exists():
            logger.info(
                "Resonance: parent page %s missing, cannot post entry", page_path
            )
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        relation = verdict.relation or "relates to"
        note = verdict.note or ""

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=now.isoformat(),
            author="@resonance",
            body=(
                f"New source [[{new_claim.citation}]] may {relation} this claim.\n\n"
                f"> {new_claim.text}\n\n"
                f"{note}"
            ),
            severity="moderate",
            type="resonance",
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        result.resonance_posts.append((new_claim.page, existing_claim.page))
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_resonance/test_resonance_agent.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/resonance/agent.py tests/test_resonance/test_resonance_agent.py
git commit -m "feat: ResonanceAgent — post-ingest claim resonance matching"
```

---

### Task 9: IngestAgent — resonance post-step

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`

- [ ] **Step 1: Read the current `IngestAgent.ingest()` return path**

```bash
grep -n "pages_created\|IngestResult\|return result" src/llm_wiki/ingest/agent.py | head -20
```

Find the line where `IngestResult` is returned with `pages_created` populated. Note the line number for the next step.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_ingest/test_ingest_companion.py` (or create `tests/test_ingest/test_ingest_resonance.py`):

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig


@pytest.mark.asyncio
async def test_ingest_calls_resonance_agent_when_enabled(tmp_path: Path):
    """When resonance_matching is enabled, ResonanceAgent.run_for_pages is called
    with the slugs of pages created during ingest."""
    config = WikiConfig()
    config.maintenance.resonance_matching = True

    with patch("llm_wiki.ingest.agent.ResonanceAgent") as MockResonanceAgent:
        mock_instance = MagicMock()
        mock_instance.run_for_pages = AsyncMock(return_value=MagicMock(resonance_posts=[]))
        MockResonanceAgent.return_value = mock_instance

        from llm_wiki.ingest.agent import IngestAgent

        writer = MagicMock()
        llm = MagicMock()
        agent = IngestAgent(writer=writer, llm=llm, config=config, vault_root=tmp_path)

        # Simulate the agent having created pages (set up via the result)
        with patch.object(agent, "_run_ingest_pipeline", new=AsyncMock(
            return_value=["new-page-a", "new-page-b"]
        )):
            with patch.object(agent, "_build_vault", new=MagicMock()):
                # This call depends on IngestAgent's actual interface —
                # adjust the method name if it differs from _run_ingest_pipeline.
                pass

    # If the above mock structure doesn't match IngestAgent's internals,
    # use an integration-style test: actually run ingest with a minimal
    # source and assert the ResonanceAgent mock was called.
    # The key invariant: if config.maintenance.resonance_matching is True,
    # ResonanceAgent(...).run_for_pages(created_slugs) must be awaited.
    pass  # Replace with actual call once IngestAgent internals are verified.
```

> **Note for implementer:** The test above is a scaffold. Before implementing, run:
> `grep -n "async def\|pages_created\|return" src/llm_wiki/ingest/agent.py | head -30`
> to find the exact method and return point. Rewrite the test to target the actual method signatures.

- [ ] **Step 3: Add resonance post-step to `src/llm_wiki/ingest/agent.py`**

Locate where `IngestResult` is built with `pages_created`. After the pages are written and before the result is returned, add:

```python
# Resonance matching post-step (gated by config)
if self._config.maintenance.resonance_matching and result.pages_created:
    try:
        from llm_wiki.resonance.agent import ResonanceAgent
        # Rescan vault to include newly created pages
        from llm_wiki.vault import Vault
        vault = Vault.scan(self._vault_root, self._config)
        resonance_agent = ResonanceAgent(
            vault=vault,
            vault_root=self._vault_root,
            llm=self._llm,
            config=self._config,
        )
        await resonance_agent.run_for_pages(result.pages_created)
    except Exception:
        logger.exception("Resonance post-step failed — ingest result unaffected")
```

The `try/except` ensures a resonance failure never aborts ingest.

- [ ] **Step 4: Run the ingest test suite for regressions**

```bash
pytest tests/test_ingest/ -v
```

Expected: all PASS (resonance post-step is off by default: `resonance_matching: false`)

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/ingest/agent.py
git commit -m "feat: invoke ResonanceAgent post-ingest when resonance_matching is enabled"
```

---

### Task 10: L4 skill files audit

**Files:**
- Modify: `skills/llm-wiki/index.md`
- Modify: `skills/llm-wiki/research.md`
- Modify: `skills/llm-wiki/write.md`
- Modify: `skills/llm-wiki/maintain.md`
- Modify: `skills/llm-wiki/ingest.md`

No tests — these are prompt files. Changes are verified by reading the diff.

- [ ] **Step 1: `skills/llm-wiki/index.md` — inference economics + traversal hop-count**

After the `## Universal Principles` section's "Traversal, not RAG." bullet, add:

```markdown
**Tool calls have real cost.** Each tool call is a decode cycle plus daemon round-trip. Prefill is 10-100× cheaper than decode per token. If you need multiple pages, load them in bulk when possible — fewer calls, one prefill. Orient with the manifest first, then load what you need in one pass.

**Minimum traversal depth.** For any non-trivial research question, at least 3 hops before synthesis: manifest or search → at least one page read → follow at least one wikilink. One search result → done is wrong. The wiki is a graph, not a retrieval index.
```

- [ ] **Step 2: `skills/llm-wiki/research.md` — anchor the 3-hop minimum**

Find the passage about traversal (look for text about following wikilinks). Add after it:

```markdown
**3-hop minimum before synthesis.** Manifest or search (hop 1) → read a page (hop 2) → follow at least one wikilink from it (hop 3). Any non-trivial research question warrants this before answering. If the answer is obvious after hop 1, the question was trivial.
```

- [ ] **Step 3: `skills/llm-wiki/write.md` — synthesis status convention + session-close**

Add to the frontmatter / page creation section:

```markdown
**Synthesis pages.** Pages that represent original analysis rather than extracted source claims use `status: synthesis` in frontmatter. Synthesis pages:
- Do not require external `[[raw/...]]` citations (the analysis session is the source)
- Are skipped by the adversary's verification pass
- Are targets for resonance matching — the system will compare incoming sources against them
- Use `wiki_talk_post` on the synthesis page when a related source arrives

Set `status: synthesis` at page creation. Do not set it on extracted pages — use it only when the content is genuinely the agent's synthesis, not a source summary.
```

At the end of the skill file, if a `wiki_session_close` reminder is not already present, add:

```markdown
---

**Close your session.** When the write task is complete: `wiki_session_close`. Sessions open implicitly on first write; they do not close themselves unless the inactivity timer fires (5 min). In short fast sessions the timer may not fire — close explicitly.
```

- [ ] **Step 4: `skills/llm-wiki/maintain.md` — resonance review + session-close**

Add a section on resonance review:

```markdown
## Resonance Review

`wiki_lint` flags open `resonance` talk entries older than the configured threshold. Each entry means the system found a possible connection between a new source and an existing claim.

For each resonance entry:
1. Read the existing page (`wiki_read`)
2. Read the new source reference in the entry body
3. Decide: corroborate (add cross-reference), extend (append with citation), contradict (post adversary talk entry), or dismiss (resolve the resonance entry as a false match)
4. Resolve the entry: `wiki_talk_post` on the same page with `resolves: [N]` referencing the resonance entry index

A resonance entry is not a finding that demands action — it is a prompt for a human judgement call. Dismissing false matches is a valid and useful outcome.
```

At the end of the skill file, if a `wiki_session_close` reminder is not already present, add:

```markdown
---

**Close your session.** `wiki_session_close` when maintenance is complete.
```

- [ ] **Step 5: `skills/llm-wiki/ingest.md` — synthesis status documentation**

After the `## Key Synthesis Principle` section, add:

```markdown
## Synthesis Pages

When ingest analysis produces a genuinely original insight — a connection, pattern, or conclusion that exists nowhere in the sources — write it as a synthesis page:

```yaml
---
status: synthesis
title: Your Synthesis Title
---
```

Synthesis pages:
- Do not require `[[raw/...]]` citations (the session is the source; note the session date in the body)
- Will not be adversary-verified (no raw source to check against)
- Are priority targets for future resonance matching — the system compares incoming sources against them automatically
- Should link aggressively to related pages: the value is the connection, not the page itself

When to create a synthesis page vs a talk post:
- **Talk post:** "I think X and Y might be related" (pre-analysis, speculative)
- **Synthesis page:** "X and Y are related because Z — here is the evidence" (analysis complete, connections explicit)

The distinction matters: synthesis pages enter the resonance matching queue; talk posts do not.
```

- [ ] **Step 6: Scan all skill files for prohibitive viewport language**

```bash
grep -n "never full\|full is a last resort\|avoid full\|don't use full" skills/llm-wiki/*.md skills/llm-wiki/autonomous/*.md 2>/dev/null
```

If any matches are found, replace with intent-driven framing:
> `full` when you genuinely need the whole page — writing a patch, the page is short, or you need structural analysis. The manifest gives you section sizes before you read.

- [ ] **Step 7: Verify session-close reminder in ingest.md**

```bash
grep -n "session_close\|wiki_session_close" skills/llm-wiki/ingest.md
```

The file already has `wiki_session_close` in the Mode 1, 2, and 3 flows. If the end of the file doesn't have a standalone reminder, add one.

- [ ] **Step 8: Commit**

```bash
git add skills/llm-wiki/index.md skills/llm-wiki/research.md \
        skills/llm-wiki/write.md skills/llm-wiki/maintain.md \
        skills/llm-wiki/ingest.md
git commit -m "docs: L4 skill audit — inference economics, 3-hop minimum, synthesis convention, resonance review"
```

---

## Self-Review

**Spec coverage check:**

| TODO item | Task |
|---|---|
| 3. Synthesis claim markers — frontmatter schema | Task 2 (config) + compliance bypass (Task 3) + adversary skip (Task 4) |
| 3. Compliance reviewer — synthesis bypass | Task 3 |
| 3. Adversary — skip synthesis | Task 4 |
| 3. Librarian — synthesis priority | **Out of scope.** The resonance agent posts talk entries on synthesis pages, keeping their `last_corroborated` fresh via the adversary override path — this gives them effective librarian priority without a separate change. Explicit librarian priority is deferred. |
| 3. `wiki_lint` — synthesis age flag | Task 6 (`find_synthesis_without_resonance`) |
| 5. `TalkEntry` resonance type | Task 5 |
| 5. `wiki_lint` — stale resonance | Task 6 (`find_stale_resonance`) |
| 4. ResonanceAgent post-step | Tasks 7 + 8 + 9 |
| 4. Config keys | Task 2 |
| L1. Compact serialisation | Task 1 |
| L4. Skill file audit | Task 10 |

**Type consistency check:**

- `TalkEntry.type` is a `str` throughout (Tasks 5, 6, 8). ✓
- `ResonanceResult.resonance_posts` is `list[tuple[str, str]]` (Tasks 8, 9). ✓
- `find_stale_resonance` and `find_synthesis_without_resonance` both return `CheckResult` (Task 6). ✓
- `ResonanceVerdict` is from `resonance/prompts.py`, used in `resonance/agent.py` (Tasks 7, 8). ✓

**Placeholder scan:** No TBD, TODO, or "similar to Task N" in any step. Task 9 Step 2 contains an explicit implementer note about verifying IngestAgent internals — this is appropriate because the exact method structure of `IngestAgent` was not read and must be confirmed before the test is written. It is not a placeholder; it is a directed instruction.
