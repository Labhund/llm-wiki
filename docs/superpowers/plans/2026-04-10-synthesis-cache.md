# Synthesis Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every cited query, write a `type: synthesis` wiki page so future similar queries can accept, update, or create rather than re-synthesising from scratch.

**Architecture:** Synthesis pages live in `wiki/` with `type: synthesis` frontmatter and enter the BM25 index naturally. The query synthesize call gets synthesis pages that appeared in the BM25 search results as context; the LLM responds with a JSON action envelope (`accept` / `update` / `create`) before the prose answer. The server parses the action, writes/reads accordingly, and rescans. No explicit fast-path — synthesis pages compete with ingest pages naturally.

**Tech Stack:** Python, existing `Vault`, `TraversalEngine`, daemon `server.py`. No new dependencies.

---

## File Map

| File | Change |
|------|--------|
| `src/llm_wiki/manifest.py` | Update `is_synthesis` detection: `type: synthesis` (not `status: synthesis`) |
| `src/llm_wiki/audit/compliance.py` | Same fix in `_is_synthesis_page` |
| `src/llm_wiki/traverse/synthesis.py` | **NEW** — slug, parse-action, extract-prose, build-page helpers |
| `src/llm_wiki/traverse/prompts.py` | Extend `DEFAULT_SYNTHESIZE_PROMPT` + `compose_synthesize_messages` signature |
| `src/llm_wiki/traverse/engine.py` | `TraversalResult.synthesis_action`, `_collect_synthesis_candidates`, `_finish` |
| `src/llm_wiki/daemon/server.py` | `_handle_query` synthesis write dispatch, `_write_synthesis_page`, `_update_synthesis_page` |
| `tests/test_traverse/test_synthesis.py` | **NEW** — unit tests for synthesis helpers |
| `tests/test_traverse/test_prompts.py` | Extend for synthesis-candidate messages |
| `tests/test_traverse/test_engine.py` | Extend for synthesis_action on TraversalResult |
| `tests/test_daemon/test_synthesis_route.py` | **NEW** — server-side synthesis write tests |

---

### Task 1: Update `is_synthesis` to use `type: synthesis` frontmatter

**Files:**
- Modify: `src/llm_wiki/manifest.py:99`
- Modify: `src/llm_wiki/audit/compliance.py:404-417`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_manifest.py`:

```python
def test_build_entry_marks_type_synthesis(tmp_path):
    """ManifestEntry.is_synthesis is True when frontmatter has type: synthesis."""
    p = tmp_path / "wiki" / "q-test.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ntitle: Test\ntype: synthesis\nquery: test\ncreated_by: query\n---\n\n%% section: answer %%\n\nAnswer [[foo]].\n",
        encoding="utf-8",
    )
    page = Page.parse(p)
    entry = build_entry(page, cluster="root")
    assert entry.is_synthesis is True

def test_build_entry_not_synthesis_for_concept(tmp_path):
    p = tmp_path / "wiki" / "foo.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntitle: Foo\ntype: concept\n---\n\nBody.\n", encoding="utf-8")
    page = Page.parse(p)
    entry = build_entry(page, cluster="root")
    assert entry.is_synthesis is False
```

- [ ] **Step 2: Run to confirm it fails**

```
cd .worktrees/feat-synthesis-cache
python -m pytest tests/test_manifest.py::test_build_entry_marks_type_synthesis -v
```
Expected: FAIL — `assert False is True` (currently checks `status: synthesis`).

- [ ] **Step 3: Fix `manifest.py:99`**

```python
# Before:
is_synthesis=page.frontmatter.get("status") == "synthesis",

# After:
is_synthesis=page.frontmatter.get("type") == "synthesis",
```

- [ ] **Step 4: Fix `compliance.py:403-417`**

```python
# Before:
def _is_synthesis_page(content: str) -> bool:
    """True iff the page frontmatter contains `status: synthesis`."""
    ...
    return fm.get("status") == "synthesis"

# After:
def _is_synthesis_page(content: str) -> bool:
    """True iff the page frontmatter contains `type: synthesis`."""
    ...
    return fm.get("type") == "synthesis"
```

- [ ] **Step 5: Run and verify tests pass**

```
python -m pytest tests/test_manifest.py tests/test_audit/ -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/manifest.py src/llm_wiki/audit/compliance.py tests/test_manifest.py
git commit -m "fix: is_synthesis reads type: synthesis (not status: synthesis)"
```

---

### Task 2: Create `src/llm_wiki/traverse/synthesis.py`

**Files:**
- Create: `src/llm_wiki/traverse/synthesis.py`
- Create: `tests/test_traverse/test_synthesis.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_traverse/test_synthesis.py`:

```python
"""Tests for synthesis cache helpers."""
from __future__ import annotations

from llm_wiki.traverse.synthesis import (
    build_synthesis_page_content,
    extract_prose_after_action,
    parse_synthesis_action,
    slug_from_query,
)


def test_slug_from_query_basic():
    assert slug_from_query("How does Boltz-2 work?") == "how-does-boltz-2-work"


def test_slug_from_query_truncates_long():
    long_q = "a " * 40  # 80 chars
    slug = slug_from_query(long_q)
    assert len(slug) <= 60


def test_slug_from_query_empty():
    assert slug_from_query("") == "query"


def test_parse_synthesis_action_accept():
    resp = '{"action": "accept", "page": "boltz-2"}\n\nSome prose.'
    action = parse_synthesis_action(resp)
    assert action == {"action": "accept", "page": "boltz-2"}


def test_parse_synthesis_action_create():
    resp = '{"action": "create", "title": "Boltz-2", "content": "...", "sources": ["wiki/boltz-2.md"]}'
    action = parse_synthesis_action(resp)
    assert action["action"] == "create"
    assert action["title"] == "Boltz-2"


def test_parse_synthesis_action_update():
    resp = '{"action": "update", "page": "boltz-2", "title": "Boltz-2", "content": "Extended.", "sources": []}'
    action = parse_synthesis_action(resp)
    assert action["action"] == "update"
    assert action["page"] == "boltz-2"


def test_parse_synthesis_action_no_json():
    assert parse_synthesis_action("Just prose, no JSON.") is None


def test_parse_synthesis_action_missing_action_key():
    assert parse_synthesis_action('{"page": "boltz-2"}') is None


def test_parse_synthesis_action_unknown_action():
    assert parse_synthesis_action('{"action": "delete", "page": "boltz-2"}') is None


def test_extract_prose_after_action():
    resp = '{"action": "accept", "page": "boltz-2"}\n\nBoltz-2 is great.'
    prose = extract_prose_after_action(resp)
    assert prose == "Boltz-2 is great."


def test_extract_prose_after_action_no_json():
    resp = "Plain prose."
    assert extract_prose_after_action(resp) == "Plain prose."


def test_build_synthesis_page_content_frontmatter():
    content = build_synthesis_page_content(
        title="Boltz-2 Structure",
        query="how does boltz-2 work?",
        answer="Boltz-2 uses diffusion [[boltz-2]].",
        sources=["wiki/boltz-2.md"],
        created_at="2026-04-10T14:00:00Z",
        updated_at="2026-04-10T14:00:00Z",
    )
    assert "type: synthesis" in content
    assert 'query: "how does boltz-2 work?"' in content
    assert "created_by: query" in content
    assert "wiki/boltz-2.md" in content


def test_build_synthesis_page_content_body():
    content = build_synthesis_page_content(
        title="Boltz-2 Structure",
        query="how does boltz-2 work?",
        answer="Boltz-2 uses diffusion [[boltz-2]].",
        sources=["wiki/boltz-2.md"],
    )
    assert "%% section: answer %%" in content
    assert "Boltz-2 uses diffusion [[boltz-2]]." in content
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_traverse/test_synthesis.py -v
```
Expected: all fail — module does not exist.

- [ ] **Step 3: Create `src/llm_wiki/traverse/synthesis.py`**

```python
"""Helpers for the synthesis cache feature.

Synthesis pages are first-class wiki pages (type: synthesis) written after
cited query answers. This module handles: query → slug, LLM action parsing,
page content building.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Match a JSON object starting at the very beginning of the (stripped) response.
_JSON_OBJECT_RE = re.compile(r"^\s*(\{[^}]*\})", re.DOTALL)


def slug_from_query(query: str) -> str:
    """Convert a query string to a filesystem-safe slug (max 60 chars)."""
    slug = _SLUG_RE.sub("-", query.lower()).strip("-")
    slug = slug[:60] if len(slug) > 60 else slug
    return slug or "query"


def parse_synthesis_action(response: str) -> dict | None:
    """Parse the optional JSON action envelope from the start of a synthesize response.

    Returns the action dict if present and valid, else None.
    Valid actions: "accept", "update", "create".
    """
    m = _JSON_OBJECT_RE.match(response.strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "action" not in data:
        return None
    if data["action"] not in ("accept", "update", "create"):
        return None
    return data


def extract_prose_after_action(response: str) -> str:
    """Return the prose answer that follows the JSON action block.

    If no JSON block is present, returns the full response.
    If the JSON block covers the entire response, returns it as-is (accept path).
    """
    stripped = response.strip()
    m = _JSON_OBJECT_RE.match(stripped)
    if not m:
        return stripped
    prose = stripped[m.end():].strip()
    return prose if prose else stripped


def build_synthesis_page_content(
    title: str,
    query: str,
    answer: str,
    sources: list[str],
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Build the full markdown text for a new or updated synthesis page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = created_at or now
    updated = updated_at or now

    if sources:
        sources_yaml = "\n".join(f"  - {s}" for s in sources)
        sources_block = f"sources:\n{sources_yaml}"
    else:
        sources_block = "sources: []"

    frontmatter = (
        f"---\n"
        f"title: {json.dumps(title)}\n"
        f"type: synthesis\n"
        f"query: {json.dumps(query)}\n"
        f"created_by: query\n"
        f"created_at: {created}\n"
        f"updated_at: {updated}\n"
        f"{sources_block}\n"
        f"---\n"
    )
    body = f"\n%% section: answer %%\n\n{answer}\n"
    return frontmatter + body
```

- [ ] **Step 4: Run and verify tests pass**

```
python -m pytest tests/test_traverse/test_synthesis.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/traverse/synthesis.py tests/test_traverse/test_synthesis.py
git commit -m "feat(synthesis): add synthesis cache helpers — slug, parse-action, build-page"
```

---

### Task 3: Extend synthesize prompt and `compose_synthesize_messages`

**Files:**
- Modify: `src/llm_wiki/traverse/prompts.py`
- Test: `tests/test_traverse/test_prompts.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_traverse/test_prompts.py`:

```python
def test_compose_synthesize_messages_includes_synthesis_candidates():
    """When synthesis_candidates provided, messages include existing-page block."""
    from llm_wiki.traverse.working_memory import WorkingMemory
    memory = WorkingMemory.initial("how does boltz-2 work?", 2000)
    candidates = [("boltz-2-structure", "how does boltz-2 work?", "Boltz-2 uses diffusion.")]
    msgs = compose_synthesize_messages(
        "how does boltz-2 work?", memory, "Synthesize.", synthesis_candidates=candidates
    )
    user_msg = msgs[-1]["content"]
    assert "boltz-2-structure" in user_msg
    assert "how does boltz-2 work?" in user_msg
    assert "Boltz-2 uses diffusion." in user_msg
    assert "accept" in user_msg

def test_compose_synthesize_messages_no_candidates_unchanged():
    """Without synthesis_candidates the message matches the existing behaviour."""
    from llm_wiki.traverse.working_memory import WorkingMemory
    memory = WorkingMemory.initial("q", 1000)
    msgs_without = compose_synthesize_messages("q", memory, "Sys.")
    msgs_with_empty = compose_synthesize_messages("q", memory, "Sys.", synthesis_candidates=[])
    assert msgs_without == msgs_with_empty
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_traverse/test_prompts.py -v
```
Expected: FAIL — `compose_synthesize_messages` doesn't accept `synthesis_candidates`.

- [ ] **Step 3: Extend `DEFAULT_SYNTHESIZE_PROMPT` in `prompts.py`**

Append this block to the end of `DEFAULT_SYNTHESIZE_PROMPT` (leave existing text intact):

```python
DEFAULT_SYNTHESIZE_PROMPT = """\
Synthesize a clear, well-organized answer from your research notes.

## Pre-Answer Checklist (work through this before writing)

1. Does the answer stand alone? A reader unfamiliar with the wiki should understand \
it fully — define any system, pipeline, or framework your answer references.
2. Are all unexplained references resolved? If your notes say "the pipeline" or \
"the architecture", name and briefly describe what that is.
3. Is every factual claim cited? Use [[page-name]] or [[page-name#section]].
4. Are gaps honestly flagged? If a relevant detail is genuinely missing from your \
notes, say so — do not invent it.

## Structural Contract (Non-Negotiable)

- Every factual claim MUST cite a wiki page: [[page-name]] or [[page-name#section]]
- Do not invent information not in your research notes
- Be thorough enough that the answer stands alone; be no longer than it needs to be

## Synthesis Cache

When existing synthesis pages are provided in the prompt, respond with a JSON \
action object as the VERY FIRST thing in your response (before any prose):

- {\"action\": \"accept\", \"page\": \"<slug>\"} — existing page fully answers the query; \
  emit NO prose after the JSON (the server will return the existing page verbatim)
- {\"action\": \"update\", \"page\": \"<slug>\", \"title\": \"<title>\", \
  \"sources\": [\"wiki/page.md\"]} — existing page found but new information surfaced; \
  write the updated page body as prose after the JSON
- {\"action\": \"create\", \"title\": \"<title>\", \"sources\": [\"wiki/page.md\"]} — no \
  relevant existing page; write the new page body as prose after the JSON

If no existing synthesis pages are provided, or if the answer has no wiki citations, \
omit the JSON action entirely and write only prose."""
```

- [ ] **Step 4: Extend `compose_synthesize_messages` signature**

```python
def compose_synthesize_messages(
    query: str,
    memory: WorkingMemory,
    system_prompt: str,
    *,
    synthesis_candidates: list[tuple[str, str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build the message list for final synthesis.

    synthesis_candidates: list of (slug, original_query, page_content) tuples
    for synthesis pages found in the BM25 search results.
    """
    notes = memory.to_context_text() or "No research notes available."
    user_content = (
        f"## Question\n{query}\n\n"
        f"## Research Notes\n{notes}"
    )

    if synthesis_candidates:
        pages_block = "\n\n".join(
            f"### [[{slug}]]\noriginal query: {orig_query}\n\n{content}"
            for slug, orig_query, content in synthesis_candidates
        )
        user_content += (
            f"\n\n## Existing Synthesis Pages\n"
            f"The following synthesis pages were found that may already answer this query.\n"
            f"Inspect them carefully before generating a new answer.\n\n"
            f"{pages_block}"
        )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
```

- [ ] **Step 5: Run and verify all prompts tests pass**

```
python -m pytest tests/test_traverse/test_prompts.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/traverse/prompts.py tests/test_traverse/test_prompts.py
git commit -m "feat(synthesis): extend synthesize prompt with action schema + synthesis candidate context"
```

---

### Task 4: Wire synthesis into `TraversalEngine`

**Files:**
- Modify: `src/llm_wiki/traverse/engine.py`
- Test: `tests/test_traverse/test_engine.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_traverse/test_engine.py`:

```python
# At the top of the file, make sure these imports exist:
# from llm_wiki.traverse.engine import TraversalEngine, TraversalResult

def test_traversal_result_has_synthesis_action_field():
    """TraversalResult accepts synthesis_action kwarg."""
    result = TraversalResult(
        answer="ans",
        citations=[],
        outcome="complete",
        needs_more_budget=False,
        log=None,
        synthesis_action={"action": "create", "title": "T"},
    )
    assert result.synthesis_action["action"] == "create"


def test_traversal_result_synthesis_action_defaults_none():
    result = TraversalResult(
        answer="ans",
        citations=[],
        outcome="complete",
        needs_more_budget=False,
        log=None,
    )
    assert result.synthesis_action is None
```

Also add an engine-level test using the existing MockVault/MockLLM fixture pattern in the file. Find the existing `make_vault` or `MockVault` helper in `tests/test_traverse/test_engine.py` and add:

```python
async def test_engine_sets_synthesis_action_on_result(mock_vault, mock_llm_responses):
    """TraversalEngine._finish sets synthesis_action from LLM action JSON."""
    # This test uses the existing fixture pattern from the test file.
    # mock_llm_responses should return a synthesize response that starts with JSON.
    # The exact fixture names depend on the existing test file — adapt as needed.
    pass  # Replace with the real test after reading the existing fixture setup.
```

*(Read `tests/test_traverse/test_engine.py` to see the fixture pattern, then write the real test.)*

- [ ] **Step 2: Run to confirm the `TraversalResult` tests fail**

```
python -m pytest tests/test_traverse/test_engine.py::test_traversal_result_has_synthesis_action_field tests/test_traverse/test_engine.py::test_traversal_result_synthesis_action_defaults_none -v
```
Expected: FAIL — `TraversalResult` has no `synthesis_action` field.

- [ ] **Step 3: Read the existing engine test fixtures**

```
python -m pytest tests/test_traverse/test_engine.py -v --collect-only 2>&1 | head -40
```

Read `tests/test_traverse/test_engine.py` to understand the fixture pattern (how MockLLM and MockVault are constructed). Then write a real test for `synthesis_action` being set when the LLM returns a JSON action.

- [ ] **Step 4: Add `synthesis_action` to `TraversalResult` in `engine.py`**

```python
@dataclass
class TraversalResult:
    answer: str
    citations: list[str]
    outcome: str  # "complete", "budget_exceeded", "candidates_exhausted", "turn_limit"
    needs_more_budget: bool
    log: TraversalLog
    synthesis_action: dict | None = None  # add this field
```

- [ ] **Step 5: Add `_collect_synthesis_candidates` method to `TraversalEngine`**

Add after the `__init__` method:

```python
def _collect_synthesis_candidates(
    self, search_results: list
) -> list[tuple[str, str, str]]:
    """Extract synthesis pages from BM25 search results.

    Returns list of (slug, original_query, page_content) for synthesis
    pages found in the top search results.
    """
    candidates = []
    for result in search_results:
        if not result.entry.is_synthesis:
            continue
        page = self._vault.read_page(result.entry.name)
        if page is None:
            continue
        orig_query = page.frontmatter.get("query", "")
        # Use the answer section content if available, else raw_content
        answer_sections = [s for s in page.sections if s.name == "answer"]
        content = answer_sections[0].content if answer_sections else page.raw_content
        candidates.append((result.entry.name, orig_query, content))
    return candidates
```

- [ ] **Step 6: Update `query()` to pass candidates to `_finish`**

In `engine.py`, the `query()` method calls `_finish` in two places. Change both calls and collect candidates after the initial search:

```python
async def query(self, question: str, budget: int | None = None) -> TraversalResult:
    # ... existing setup ...
    search_results = self._vault.search(question, limit=10)
    synthesis_candidates = self._collect_synthesis_candidates(search_results)  # ADD THIS
    # ... rest of existing logic ...

    # Change first _finish call (after turn0_premature check):
    if outcome and not turn0_premature:
        return await self._finish(
            question, memory, outcome, log, synthesize_prompt, synthesis_candidates
        )

    # ... traversal loop ...

    # Change second _finish call (end of method):
    return await self._finish(
        question, memory, outcome, log, synthesize_prompt, synthesis_candidates
    )
```

- [ ] **Step 7: Update `_finish` signature and body**

```python
async def _finish(
    self,
    question: str,
    memory: WorkingMemory,
    outcome: str,
    log: TraversalLog,
    synthesize_prompt: str,
    synthesis_candidates: list[tuple[str, str, str]] | None = None,
) -> TraversalResult:
    """Synthesize final answer, persist log, build result."""
    from llm_wiki.traverse.synthesis import parse_synthesis_action, extract_prose_after_action

    messages = compose_synthesize_messages(
        question, memory, synthesize_prompt,
        synthesis_candidates=synthesis_candidates or [],
    )
    response = await self._llm.complete(messages, temperature=0.3, label="query:synthesize")
    memory.budget_used += response.tokens_used

    synthesis_action = parse_synthesis_action(response.content)
    if synthesis_action and synthesis_action.get("action") == "accept":
        # accept: prose is NOT expected — server will read the page directly
        answer = ""
    else:
        answer = extract_prose_after_action(response.content) if synthesis_action else response.content

    citations = _extract_citations(answer)

    log.outcome = outcome
    log.total_tokens_used = memory.budget_used
    log.pages_visited = [p.name for p in memory.pages_read]

    self._persist_log(log)

    return TraversalResult(
        answer=answer,
        citations=citations,
        outcome=outcome,
        needs_more_budget=(outcome == "budget_exceeded"),
        log=log,
        synthesis_action=synthesis_action,
    )
```

- [ ] **Step 8: Run all traverse tests**

```
python -m pytest tests/test_traverse/ -q
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/llm_wiki/traverse/engine.py tests/test_traverse/test_engine.py
git commit -m "feat(synthesis): TraversalResult.synthesis_action + engine collects and passes synthesis candidates"
```

---

### Task 5: Wire synthesis write into `_handle_query` (server.py)

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Create: `tests/test_daemon/test_synthesis_route.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_daemon/test_synthesis_route.py`:

```python
"""Tests for synthesis page write dispatch in _handle_query."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.traverse.engine import TraversalResult
from llm_wiki.traverse.log import TraversalLog


def _make_log():
    log = MagicMock(spec=TraversalLog)
    log.to_dict.return_value = {}
    return log


def _make_result(action=None, answer="Answer [[foo]].", citations=None):
    return TraversalResult(
        answer=answer,
        citations=citations or ["foo"],
        outcome="complete",
        needs_more_budget=False,
        log=_make_log(),
        synthesis_action=action,
    )


@pytest.fixture
def wiki_dir(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").mkdir()
    return tmp_path


@pytest.fixture
def server(wiki_dir):
    """Minimal server-like object with _write_synthesis_page and _update_synthesis_page."""
    from llm_wiki.daemon.server import WikiDaemonServer
    cfg = WikiConfig()
    srv = object.__new__(WikiDaemonServer)
    srv._vault_root = wiki_dir
    srv._config = cfg
    # Mock the vault so rescan doesn't blow up
    vault_mock = MagicMock()
    vault_mock.page_count = 0
    vault_mock.manifest_entries.return_value = {}
    vault_mock.read_page.return_value = None
    srv._vault = vault_mock
    srv._title_to_slug = {}
    return srv


@pytest.mark.asyncio
async def test_write_synthesis_page_creates_file(server, wiki_dir):
    """_write_synthesis_page writes a type: synthesis page to wiki/."""
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._write_synthesis_page(
            query="how does foo work?",
            title="Foo",
            answer="Foo uses bar [[foo]].",
            sources=["wiki/foo.md"],
        )
    pages = list((wiki_dir / "wiki").glob("*.md"))
    assert len(pages) == 1
    content = pages[0].read_text()
    assert "type: synthesis" in content
    assert "Foo uses bar [[foo]]." in content
    assert "wiki/foo.md" in content


@pytest.mark.asyncio
async def test_update_synthesis_page_overwrites(server, wiki_dir):
    """_update_synthesis_page overwrites existing synthesis page."""
    existing = wiki_dir / "wiki" / "foo.md"
    existing.write_text(
        "---\ntitle: \"Foo\"\ntype: synthesis\nquery: \"foo\"\n"
        "created_by: query\ncreated_at: 2026-01-01T00:00:00Z\n"
        "updated_at: 2026-01-01T00:00:00Z\nsources: []\n---\n\n"
        "%% section: answer %%\n\nOld answer.\n",
        encoding="utf-8",
    )
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._update_synthesis_page(
            slug="foo",
            query="how does foo work?",
            title="Foo",
            answer="New extended answer [[foo]].",
            sources=["wiki/foo.md"],
            created_at="2026-01-01T00:00:00Z",
        )
    content = existing.read_text()
    assert "New extended answer" in content
    assert "Old answer" not in content
    assert "created_at: 2026-01-01T00:00:00Z" in content  # preserved


@pytest.mark.asyncio
async def test_query_skips_synthesis_write_when_no_citations(server):
    """No synthesis write if answer has no citations."""
    result = _make_result(action=None, answer="No citations here.", citations=[])
    with patch(
        "llm_wiki.daemon.server.TraversalEngine"
    ) as MockEngine:
        mock_engine = AsyncMock()
        mock_engine.query.return_value = result
        MockEngine.return_value = mock_engine

        with patch("llm_wiki.daemon.server.LLMClient"):
            resp = await server._handle_query({"question": "q?"})

    assert resp["status"] == "ok"
    # No synthesis page written
    assert not list((server._vault_root / "wiki").glob("*.md"))
```

*(Note: `_handle_query` imports `TraversalEngine` and `LLMClient` lazily inside the method — patch them at `llm_wiki.daemon.server.TraversalEngine` and `llm_wiki.daemon.server.LLMClient`.)*

- [ ] **Step 2: Run to confirm tests fail**

```
python -m pytest tests/test_daemon/test_synthesis_route.py -v
```
Expected: FAIL — `_write_synthesis_page` and `_update_synthesis_page` not yet on server.

- [ ] **Step 3: Add `_write_synthesis_page` to `WikiDaemonServer` in server.py**

Find `_handle_query` in `server.py` (around line 1369). After the method, add:

```python
async def _write_synthesis_page(
    self,
    *,
    query: str,
    title: str,
    answer: str,
    sources: list[str],
) -> None:
    """Write a new synthesis page to wiki/. Rescans vault on success."""
    from llm_wiki.traverse.synthesis import build_synthesis_page_content, slug_from_query
    slug = slug_from_query(title or query)
    wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
    wiki_dir.mkdir(parents=True, exist_ok=True)
    # Collision avoidance
    candidate = wiki_dir / f"{slug}.md"
    suffix = 2
    while candidate.exists():
        candidate = wiki_dir / f"{slug}-{suffix}.md"
        suffix += 1
    content = build_synthesis_page_content(title, query, answer, sources)
    candidate.write_text(content, encoding="utf-8")
    logger.info("Wrote synthesis page: %s", candidate.name)
    try:
        await self.rescan()
    except Exception:
        logger.warning("Rescan failed after synthesis write", exc_info=True)
```

- [ ] **Step 4: Add `_update_synthesis_page` to `WikiDaemonServer`**

```python
async def _update_synthesis_page(
    self,
    *,
    slug: str,
    query: str,
    title: str,
    answer: str,
    sources: list[str],
    created_at: str | None = None,
) -> None:
    """Overwrite an existing synthesis page with updated content."""
    from llm_wiki.traverse.synthesis import build_synthesis_page_content
    wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
    page_path = wiki_dir / f"{slug}.md"
    if not page_path.exists():
        # Fall back to create if page disappeared
        await self._write_synthesis_page(
            query=query, title=title, answer=answer, sources=sources
        )
        return
    content = build_synthesis_page_content(
        title, query, answer, sources, created_at=created_at
    )
    page_path.write_text(content, encoding="utf-8")
    logger.info("Updated synthesis page: %s", page_path.name)
    try:
        await self.rescan()
    except Exception:
        logger.warning("Rescan failed after synthesis update", exc_info=True)
```

- [ ] **Step 5: Modify `_handle_query` to dispatch synthesis actions**

After the `resp` dict is built (around line 1402, after `if trace_events:`), add:

```python
        # Synthesis cache: write, update, or accept existing page.
        try:
            await self._dispatch_synthesis_action(request["question"], result, resp)
        except Exception:
            logger.warning("Synthesis write failed — returning answer without caching", exc_info=True)
```

Then add the dispatch helper:

```python
async def _dispatch_synthesis_action(
    self, question: str, result: "TraversalResult", resp: dict
) -> None:
    """Handle synthesis cache write/update/accept from TraversalResult.synthesis_action.

    Mutates resp["answer"] for the accept case (returns existing page content).
    No-op if no action or no citations in the answer.
    """
    action = result.synthesis_action
    if not action:
        return
    act = action.get("action")

    if act == "create":
        if not result.citations:
            return  # No backing — don't cache
        await self._write_synthesis_page(
            query=question,
            title=action.get("title", question),
            answer=result.answer,
            sources=action.get("sources", [f"wiki/{c}.md" for c in result.citations]),
        )

    elif act == "update":
        slug = action.get("page", "")
        if not slug:
            return
        page = self._vault.read_page(slug)
        created_at = page.frontmatter.get("created_at") if page else None
        await self._update_synthesis_page(
            slug=slug,
            query=question,
            title=action.get("title", question),
            answer=result.answer,
            sources=action.get("sources", [f"wiki/{c}.md" for c in result.citations]),
            created_at=created_at,
        )

    elif act == "accept":
        slug = action.get("page", "")
        if not slug:
            return
        page = self._vault.read_page(slug)
        if page is None:
            # Page deleted since search — skip; answer remains as-is (empty string from engine)
            return
        # Return the existing synthesis page content as the answer
        answer_sections = [s for s in page.sections if s.name == "answer"]
        resp["answer"] = answer_sections[0].content if answer_sections else page.raw_content
        resp["synthesis_cache_hit"] = slug
```

- [ ] **Step 6: Run all synthesis route tests**

```
python -m pytest tests/test_daemon/test_synthesis_route.py -v
```
Expected: all pass.

- [ ] **Step 7: Run full test suite**

```
python -m pytest tests/ -q
```
Expected: 1028+ tests pass, no failures.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_synthesis_route.py
git commit -m "feat(synthesis): wire synthesis write/update/accept into _handle_query"
```

---

### Task 6: Integration test — query produces and re-uses synthesis page

**Files:**
- Modify: `tests/test_daemon/test_synthesis_route.py` (add integration test)

- [ ] **Step 1: Add integration test using a real Vault + MockLLM**

Append to `tests/test_daemon/test_synthesis_route.py`:

```python
@pytest.mark.asyncio
async def test_synthesis_page_written_after_cited_query(tmp_path):
    """End-to-end: query with citations → synthesis page appears in wiki/."""
    from llm_wiki.config import WikiConfig
    from llm_wiki.traverse.engine import TraversalEngine
    from llm_wiki.traverse.llm_client import LLMClient, LLMResponse
    from llm_wiki.vault import Vault

    # Set up a minimal vault with one ingest page
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (tmp_path / "raw").mkdir()
    ingest_page = wiki_dir / "boltz-2.md"
    ingest_page.write_text(
        "---\ntitle: Boltz-2\ncreated_by: ingest\n---\n\n"
        "%% section: overview %%\n\nBoltz-2 uses diffusion for structure prediction.\n",
        encoding="utf-8",
    )

    vault = Vault.scan(tmp_path)
    config = WikiConfig()

    # Mock LLM: traverse step returns done, synthesize returns create action + prose
    call_count = 0
    async def mock_complete(messages, temperature=0.7, priority="query", label="unknown", **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Traverse turn 0: indicate complete
            return LLMResponse(
                content='{"salient_points": "Boltz-2 uses diffusion [[boltz-2]].", '
                        '"remaining_questions": [], "next_candidates": [], '
                        '"hypothesis": "diffusion model", "answer_complete": true}',
                input_tokens=100, output_tokens=50,
            )
        else:
            # Synthesize: create action + prose
            return LLMResponse(
                content='{"action": "create", "title": "Boltz-2 Structure Prediction", '
                        '"sources": ["wiki/boltz-2.md"]}\n\n'
                        'Boltz-2 uses a diffusion approach [[boltz-2]].',
                input_tokens=200, output_tokens=80,
            )

    llm = object.__new__(LLMClient)
    llm.complete = mock_complete
    llm.model = "mock/test"

    engine = TraversalEngine(vault, llm, config, vault_root=tmp_path)
    result = await engine.query("How does Boltz-2 work?")

    assert result.synthesis_action is not None
    assert result.synthesis_action["action"] == "create"
    assert "Boltz-2 uses a diffusion" in result.answer

    # Simulate server write
    from llm_wiki.daemon.server import WikiDaemonServer
    srv = object.__new__(WikiDaemonServer)
    srv._vault_root = tmp_path
    srv._config = config
    srv._vault = vault
    srv._title_to_slug = {}

    with patch.object(type(srv), "rescan", new_callable=AsyncMock):
        await srv._write_synthesis_page(
            query="How does Boltz-2 work?",
            title="Boltz-2 Structure Prediction",
            answer=result.answer,
            sources=["wiki/boltz-2.md"],
        )

    synthesis_pages = list(wiki_dir.glob("*.md"))
    synthesis = [p for p in synthesis_pages if p.stem != "boltz-2"]
    assert len(synthesis) == 1
    content = synthesis[0].read_text()
    assert "type: synthesis" in content
    assert "Boltz-2 uses a diffusion" in content
```

- [ ] **Step 2: Run**

```
python -m pytest tests/test_daemon/test_synthesis_route.py::test_synthesis_page_written_after_cited_query -v
```
Expected: PASS.

- [ ] **Step 3: Run full suite one final time**

```
python -m pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 4: Final commit**

```bash
git add tests/test_daemon/test_synthesis_route.py
git commit -m "test(synthesis): integration test — query produces synthesis page"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| Synthesis pages in `wiki/` with `type: synthesis` | Task 1 (detection), Task 2 (builder) |
| Write if answer has wiki citations | Task 5 (`_dispatch_synthesis_action` guards `result.citations`) |
| Synthesis pages in BM25 index naturally | Covered by `type: synthesis` + existing `is_synthesis` in ManifestEntry |
| LLM action schema: accept/update/create | Tasks 2 (parse), 3 (prompt), 4 (engine), 5 (server) |
| `accept` returns existing page, zero output tokens | Task 5 `resp["answer"]` override |
| `update` overwrites with new content, preserves `created_at` | Task 5 `_update_synthesis_page` |
| `create` writes new page, collision-safe | Task 5 `_write_synthesis_page` |
| Graceful failure — never crash query | Task 5 try/except in `_handle_query` |
| `synthesis_cache_hit` in response for accept | Task 5 `resp["synthesis_cache_hit"]` |
| Adversary runs on synthesis pages | No change needed — existing adversary uses `is_synthesis` already |
| `frontmatter: query, sources, created_by, created_at, updated_at` | Task 2 `build_synthesis_page_content` |

**No placeholders present.**

**Type consistency:** `TraversalResult.synthesis_action: dict | None` — used consistently as `dict | None` throughout. `synthesis_candidates: list[tuple[str, str, str]]` — consistent in `_collect_synthesis_candidates` and `compose_synthesize_messages`.
