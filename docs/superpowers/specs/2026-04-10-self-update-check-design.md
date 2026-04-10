# Self-Update Check Design

**Date:** 2026-04-10
**Branch:** feat/self-update-check

## Overview

`llm-wiki` checks for updates on every CLI invocation. If the installed version is behind upstream (Labhund/llm-wiki), it prints a warning. `llm-wiki update` applies the update.

Two components:
1. **Update checker** — detects installed location, compares commit to upstream
2. **Update command** — pulls and applies pending updates

## Motivation

Users running `llm-wiki` from source or editable installs want to know when they're behind. The warning should be silent on every command, explicit only when there's something to act on.

## Component A: Update Checker

### Location Detection

Three-tier detection, in priority order:

1. **Git directory walk** — Start from `__file__` (available at import time), walk up the tree looking for `.git`. First match is the repo root. Works for editable installs, source installs, and venv installs.

2. **pip direct_url** — `pip show --format=json llm-wiki` includes `direct_url` if installed from a VCS. Extract `url` and `commit_hash`.

3. **Editable install markers** — `pip show -f llm-wiki` lists files; look for `Location` + `Requires-Dist` metadata. Extract the source path from the editable install marker.

If none succeed, skip update check (assume non-git install or pip install).

### Upstream URL Resolution

- Read `git remote get-url origin` from the detected repo. If `origin` doesn't exist, use `https://github.com/Labhund/llm-wiki` as fallback.
- This allows forks/mirrors to work without hardcoding the upstream URL.

### Network Model: Cached Check with TTL

**Cache location:** `~/.cache/llm-wiki/update-check.json`

**Cache schema:**
```json
{
  "repo_path": "/absolute/path/to/llm-wiki",
  "local_commit": "abc123...",
  "upstream_commit": "def456...",
  "commit_distance": 42,
  "checked_at": "2026-04-10T12:00:00Z"
}
```

**TTL:** 24 hours. If `checked_at` is older than 24h, refetch.

**Cache invalidation:**
- On `llm-wiki update` success: clear cache
- On `llm-wiki update` failure: keep cache (user can retry later)
- On network error: keep cache, don't write new stale data

**Flow:**
1. Load cache if exists
2. If cache is < 24h old: use cached value, show warning if behind
3. If cache is stale or missing: `git fetch` upstream, compare commits, write new cache, show warning if behind
4. Silent failure: if git fetch fails, use cached value (or no warning if cache is missing)

### Warning Message

If behind:
```
llm-wiki is behind by N commits. Run llm-wiki update to get up to date.
```

If ahead or equal: no warning.

### Integration Point

Checker runs at the start of every CLI command (in `cli/main.py` before command dispatch). Silent failure — if check fails (no git, network error, cache missing), suppress warning.

## Component B: Update Command

### CLI Command

```python
@cli.command()
def update() -> None:
    """Pull and apply the latest llm-wiki updates."""
```

### Implementation

1. Detect installed location (same logic as checker)
2. `git pull` from upstream
3. Reinstall package if editable: `pip install -e .`
4. Print success message with commit hashes

### Error Handling

- If not a git repo: "llm-wiki is not installed from a git repo — cannot update."
- If git pull fails: "Update failed: <error>. Run manually: git pull && pip install -e ."
- If pip install fails: "Reinstall failed: <error>. Run manually: pip install -e ."

## Backward Compatibility

- No changes to existing CLI commands
- Warning is opt-in (only if behind)
- `llm-wiki update` is a new command, no conflicts

## Out of Scope

- Auto-update (user must run `llm-wiki update`)
- Check for new releases/tags (commit-based only)
- Update dependencies (user runs `pip install --upgrade` separately)
