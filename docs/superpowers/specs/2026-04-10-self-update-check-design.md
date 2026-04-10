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

1. **Editable install** — `pip show -f llm-wiki` lists files; look for `Location` + `Requires-Dist` metadata. Extract the source path from the editable install marker.

2. **Git directory** — Check if the package's `__file__` path contains `.git` or if `pkg_resources.get_distribution('llm-wiki').locate_file('.')` has a git repo.

3. **pip direct_url** — `pip show --format=json llm-wiki` includes `direct_url` if installed from a VCS. Extract `url` and `commit_hash`.

If none succeed, skip update check (assume non-git install or pip install).

### Upstream Comparison

- Clone/fetch upstream `https://github.com/Labhund/llm-wiki` (or use cached clone)
- Compare HEAD commit hash to installed commit hash
- Calculate commit distance (git merge-base + rev-list count)

### Warning Message

If behind:
```
llm-wiki is behind by N commits. Run llm-wiki update to get up to date.
```

If ahead or equal: no warning.

### Integration Point

Checker runs at the start of every CLI command (in `cli/main.py` before command dispatch). Silent failure — if check fails (no git, network error), suppress warning.

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
