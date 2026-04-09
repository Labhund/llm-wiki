# Interface Optimisation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce LLM token overhead in MCP tool responses (L2 field renames) and improve cold-agent tool selection via better tool descriptions (L3).

**Context:** L1 (compact JSON serialisation) is already done — `_ok()` uses `separators=(",",":")`. L4 (skill file audit) is largely done — no prohibited viewport language, session-close covered, 3-hop guidance in place. This plan covers only what remains: L2 field renames and L3 tool description updates.

**Architecture:** L2 renames 7 verbose keys in `server.py` response dicts; all test and gallery references update in lockstep. L3 rewrites 6 tool descriptions in `tools.py` — no logic changes, no tests needed.

**Tech Stack:** Python, pytest, no new dependencies.

**Worktree:** `.worktrees/interface-optimisation` on branch `feat/interface-optimisation`

---

## Field rename map (L2)

| Location | Old key | New key |
|---|---|---|
| `issues` block | `open_count` | `n` |
| `issues` block | `by_severity` | `sev` |
| `talk` block | `entry_count` | `cnt` |
| `talk` block | `open_count` | `open` |
| `talk` block | `by_severity` | `sev` |
| `talk` block | `recent_critical` | `crit` |
| `talk` block | `recent_moderate` | `mod` |

These are all programmatically-read fields — the agent extracts the value and acts on it; the key name never surfaces in agent prose or output.

---

## File structure

| File | Change |
|---|---|
| `src/llm_wiki/daemon/server.py` | Rename 7 keys in `_read_issues_block` and `_read_talk_block` |
| `tests/test_daemon/test_server.py` | Update ~12 assertions to new key names |
| `tests/test_mcp/test_tools.py` | Update fixture dict |
| `tests/test_mcp/test_compact_json.py` | Update test dict |
| `docs/gallery.md` | Update all response examples |
| `src/llm_wiki/mcp/tools.py` | Rewrite 6 tool descriptions |

---

## Task 1: L2 — Response field renames

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_daemon/test_server.py`
- Modify: `tests/test_mcp/test_tools.py`
- Modify: `tests/test_mcp/test_compact_json.py`
- Modify: `docs/gallery.md`

- [ ] **Step 1: Write failing tests**

In `tests/test_daemon/test_server.py`, the assertions currently use old field names. Run a targeted check to see the current state:

```bash
grep -n "open_count\|by_severity\|entry_count\|recent_critical\|recent_moderate" tests/test_daemon/test_server.py
```

Before touching `server.py`, update every assertion to the new names so the tests fail immediately:

Old → New in `tests/test_daemon/test_server.py`:

```python
# OLD                                      # NEW
resp["issues"]["open_count"]           →   resp["issues"]["n"]
resp["issues"]["by_severity"]          →   resp["issues"]["sev"]
resp["talk"]["entry_count"]            →   resp["talk"]["cnt"]
resp["talk"]["open_count"]             →   resp["talk"]["open"]
resp["talk"]["recent_critical"]        →   resp["talk"]["crit"]
resp["talk"]["recent_moderate"]        →   resp["talk"]["mod"]
```

Also update `tests/test_mcp/test_tools.py` fixture (line ~79):
```python
# OLD
"issues": {"open_count": 0, "by_severity": {}, "items": []},
...
"talk": {"entry_count": 0, "open_count": 0, "by_severity": {}, ...}

# NEW
"issues": {"n": 0, "sev": {}, "items": []},
...
"talk": {"cnt": 0, "open": 0, "sev": {}, ...}
```

Also update `tests/test_mcp/test_compact_json.py` (line ~17):
```python
# OLD
data = {"issues": {"open_count": 3, "by_severity": {"critical": 1, "moderate": 2}}}

# NEW
data = {"issues": {"n": 3, "sev": {"critical": 1, "moderate": 2}}}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd .worktrees/interface-optimisation && pytest tests/test_daemon/test_server.py tests/test_mcp/test_tools.py tests/test_mcp/test_compact_json.py -q 2>&1 | tail -10
```

Expected: failures on key lookups (`KeyError` or assertion failures).

- [ ] **Step 3: Rename keys in `server.py`**

In `src/llm_wiki/daemon/server.py`, `_read_issues_block` (around line 818):

```python
# OLD
return {
    "open_count": len(page_issues),
    "by_severity": by_severity,
    "items": items,
}

# NEW
return {
    "n": len(page_issues),
    "sev": by_severity,
    "items": items,
}
```

In `_read_talk_block` (around line 845), the empty dict:

```python
# OLD
empty = {
    "entry_count": 0,
    "open_count": 0,
    "by_severity": _empty_severity_counts(_TALK_SEVERITIES),
    "summary": "",
    "recent_critical": [],
    "recent_moderate": [],
}

# NEW
empty = {
    "cnt": 0,
    "open": 0,
    "sev": _empty_severity_counts(_TALK_SEVERITIES),
    "summary": "",
    "crit": [],
    "mod": [],
}
```

In `_read_talk_block`, the return dict (around line 882):

```python
# OLD
return {
    "entry_count": len(all_entries),
    "open_count": len(open_entries),
    "by_severity": by_severity,
    "summary": summary_text,
    "recent_critical": recent_critical,
    "recent_moderate": recent_moderate,
}

# NEW
return {
    "cnt": len(all_entries),
    "open": len(open_entries),
    "sev": by_severity,
    "summary": summary_text,
    "crit": recent_critical,
    "mod": recent_moderate,
}
```

Also update the local variable names inside `_read_talk_block` for clarity (optional but recommended):

```python
# OLD
recent_critical = [...]
recent_moderate = [...]

# NEW
crit = [...]
mod = [...]
```

And update the references two lines below:

```python
# OLD
"recent_critical": recent_critical,
"recent_moderate": recent_moderate,

# NEW
"crit": crit,
"mod": mod,
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_daemon/test_server.py tests/test_mcp/test_tools.py tests/test_mcp/test_compact_json.py -q 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 5: Update `docs/gallery.md`**

Replace all occurrences of old field names in `docs/gallery.md` with new names. Use sed for reliability:

```bash
sed -i \
  -e 's/"open_count"/"n"/g' \
  -e 's/"by_severity"/"sev"/g' \
  -e 's/"entry_count"/"cnt"/g' \
  -e 's/open_count/open/g' \
  -e 's/recent_critical/"crit"/g' \
  -e 's/recent_moderate/"mod"/g' \
  docs/gallery.md
```

**Important:** `open_count` appears in both `issues` (→ `n`) and `talk` (→ `open`) contexts. The sed above handles `"open_count"` first (→ `"n"`) and then `open_count` without quotes. Verify manually:

```bash
grep -n "open_count\|by_severity\|entry_count\|recent_critical\|recent_moderate" docs/gallery.md
```

Expected: no output. Fix any remaining occurrences by hand.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py tests/test_mcp/test_tools.py tests/test_mcp/test_compact_json.py docs/gallery.md
git commit -m "feat: L2 — rename verbose response fields to compact keys (n, sev, cnt, open, crit, mod)"
```

---

## Task 2: L3 — Tool description updates

**Files:**
- Modify: `src/llm_wiki/mcp/tools.py`

No tests needed — descriptions are string constants with no behavioural logic.

- [ ] **Step 1: Rewrite `WIKI_MANIFEST` description**

Current:
```python
description=(
    "Return a hierarchical, budget-aware manifest of the whole vault. "
    "Use this to get an overview of what the wiki contains before "
    "diving into specific pages."
),
```

New:
```python
description=(
    "Return a hierarchical manifest of the vault: all pages, clusters, "
    "section names, and token counts. Call this first when you don't know "
    "where to look — it costs nothing to read and tells you section sizes "
    "before you commit to a wiki_read. Use wiki_search when you have a "
    "specific term; use wiki_query when you have a question and want a "
    "compiled answer."
),
```

- [ ] **Step 2: Rewrite `WIKI_SEARCH` description**

Current:
```python
description=(
    "Keyword-search the wiki and return ranked manifest entries with "
    "line-numbered match snippets. Use this to find which pages might "
    "be relevant before deciding which to read in full."
),
```

New:
```python
description=(
    "Keyword-search the wiki and return ranked manifest entries with "
    "match snippets. Use this when you have a specific term and want to "
    "find which pages cover it. Use wiki_manifest when you don't have a "
    "term yet; use wiki_query when you have a question and want a "
    "compiled answer without loading pages yourself."
),
```

- [ ] **Step 3: Rewrite `WIKI_QUERY` description**

Current:
```python
description=(
    "Ask the wiki a question. The daemon performs multi-turn traversal "
    "with budget management and returns a synthesized answer plus the "
    "citations it relied on. Your context only sees the final answer — "
    "the navigation log stays on the daemon side."
),
```

New:
```python
description=(
    "Ask the wiki a question and get a synthesized answer. The daemon "
    "traverses internally — your context only receives the final answer "
    "and citations, not the navigation log. Use this when you have a "
    "specific question and want an answer at near-zero context cost. "
    "Use wiki_manifest to orient; use wiki_search to find pages by term; "
    "use wiki_read when you need to reason over page content yourself."
),
```

- [ ] **Step 4: Rewrite `WIKI_READ` description**

Current:
```python
description=(
    "Read a wiki page with viewport control. The response also folds "
    "in any open issues for the page and a digest of unresolved talk "
    "entries — you cannot read the page without seeing what background "
    "workers and prior sessions have said about it."
),
```

New:
```python
description=(
    "Read a wiki page. The response folds in open issues and a digest "
    "of unresolved talk entries. Viewport options: 'top' to orient "
    "(default, reads overview + first section), 'section' with section= "
    "to read a named section, 'grep' with grep= to find specific content, "
    "'full' when you need the whole page (writing a patch, short page, "
    "structural analysis). Check manifest section sizes before reading — "
    "that tells you what 'full' costs before you commit."
),
```

- [ ] **Step 5: Add session reminder to `WIKI_CREATE` description**

Current:
```python
description=(
    "Create a new wiki page. Requires citations — every claim in the "
    "main wiki must be traceable to a primary source. If you cannot "
    "cite a source, post your idea to the talk page via wiki_talk_post "
    "instead. Pass force=true to override near-match warnings."
),
```

New:
```python
description=(
    "Create a new wiki page. Requires citations — every claim in the "
    "main wiki must be traceable to a primary source. If you cannot "
    "cite a source, post your idea to the talk page via wiki_talk_post "
    "instead. Pass force=true to override near-match warnings. "
    "Opens a session on first call; close explicitly with "
    "wiki_session_close when done — do not rely on the inactivity timer."
),
```

- [ ] **Step 6: Add session reminder to `WIKI_UPDATE` description**

Current:
```python
description=(
    "Apply a V4A-format patch to an existing page. The patch envelope is "
    "*** Begin Patch / *** Update File: <path> / @@ <context> @@ / "
    "context+/-/space lines / *** End Patch. On context drift, you'll get "
    "patch-conflict with the current file content so you can re-read and retry."
),
```

New:
```python
description=(
    "Apply a V4A-format patch to an existing page. The patch envelope is "
    "*** Begin Patch / *** Update File: <path> / @@ <context> @@ / "
    "context+/-/space lines / *** End Patch. On patch-conflict: re-read "
    "the page and retry — never rewrite the whole page from scratch. "
    "Opens a session on first call; close explicitly with "
    "wiki_session_close when done."
),
```

- [ ] **Step 7: Add session reminder to `WIKI_APPEND` description**

Find the `WIKI_APPEND` definition and update its description to end with:
```
Opens a session on first call; close explicitly with wiki_session_close when done.
```

- [ ] **Step 8: Run full test suite**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all PASS (descriptions are strings, no logic changed).

- [ ] **Step 9: Commit**

```bash
git add src/llm_wiki/mcp/tools.py
git commit -m "feat: L3 — tool description updates: disambiguation, viewport intent, session reminders"
```
