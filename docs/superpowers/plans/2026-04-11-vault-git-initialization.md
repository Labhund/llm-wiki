# Vault Git Initialization Prompt

**Date:** 2026-04-11
**Branch:** `feat/vault-git-initialization`
**Status:** ✅ Complete

## Problem

The `llm-wiki init` and `llm-wiki configure` commands did not initialize git repositories, despite the system relying on git as an audit trail for all writes. This caused silent failures when:
- Wiki writes (`wiki_create`, `wiki_update`, `wiki_append`) attempted to commit
- Session settlement tried to create git commits with `Agent:` trailers
- Users expected git history but had no repository

The codebase assumed git was always available (all test fixtures manually run `git init`), but the CLI never asked users to set it up.

## Solution

Added interactive git initialization with clear user choice:

### 1. Helper Functions (`src/llm_wiki/cli/main.py`)

**`_is_git_repo(path: Path) -> bool`**
- Checks if a path is inside a git repository
- Uses `git rev-parse --git-dir` for reliable detection
- Returns boolean (no exceptions)

**`_ensure_git_repo(vault_path: Path, interactive: bool = True) -> bool`**
- If already a git repo: returns `True` (idempotent)
- If not a git repo:
  - Interactive mode: warns user, explains consequences, asks to initialize
    - If user accepts: initializes git, sets config, creates .gitignore, makes initial commit
    - If user declines: provides manual instructions, returns `False`
  - Non-interactive mode: initializes automatically, returns success status
- Returns `True` if git is ready, `False` if user declined or initialization failed

### 2. CLI Integration

**`llm-wiki init` command:**
- After creating vault directories (`wiki/`, `raw/`, `schema/`, `inbox/`)
- Calls `_ensure_git_repo(vault_path, interactive=True)`
- If git not initialized: warns "Proceeding without git — write operations will fail."
- Continues with vault scan regardless (allows existing vaults without git)

**`llm-wiki configure` wizard (both Hermes and Claude Code paths):**
- After creating vault structure
- Calls `_ensure_git_repo(vault_path, interactive=True)`
- Same warning if git not initialized
- Only scans vault if it was just created

### 3. Git Configuration

When initializing git:
```bash
git init -q
git config user.email "llm-wiki@local"
git config user.name "llm-wiki"
```

### 4. `.gitignore` Creation

Creates sensible `.gitignore`:
```
# llm-wiki state
.llm-wiki/
.DS_Store
```

Commits it with message: `chore: initialize git repo`

## User Experience

### Scenario 1: User accepts git initialization

```
$ llm-wiki init my-vault
Initialised new vault at my-vault.

⚠  This vault is not a git repository.

llm-wiki uses git as an audit trail for all writes. Without git:
  • Wiki writes (wiki_create, wiki_update, wiki_append) will fail
  • Session commits will fail
  • No attribution history (Agent: trailers, git log)

Initialize git repository now? [Y/n]: y
✓ Git repository initialized
Indexed 0 pages in 0 clusters.
```

### Scenario 2: User declines git initialization

```
$ llm-wiki init my-vault
Initialised new vault at my-vault.

⚠  This vault is not a git repository.

llm-wiki uses git as an audit trail for all writes. Without git:
  • Wiki writes (wiki_create, wiki_update, wiki_append) will fail
  • Session commits will fail
  • No attribution history (Agent: trailers, git log)

Initialize git repository now? [Y/n]: n
Skipping git initialization. To initialize manually:

  cd my-vault
  git init
  git config user.name "Your Name"
  git config user.email "your.email@example.com"

⚠  Proceeding without git — write operations will fail.
Indexed 0 pages in 0 clusters.
```

### Scenario 3: Existing vault with git

```
$ llm-wiki init existing-vault
Indexed 10 pages in 2 clusters.
```

No prompt - git check passes silently.

## Testing

All existing tests pass (1078 tests):
- `tests/test_cli/` - 60 tests (all pass)
- Full test suite - 1078 tests (all pass)

Manual testing verified:
- Interactive prompt works (both yes and no responses)
- Git repository created correctly
- `.gitignore` created and committed
- Non-interactive mode works for programmatic use
- Idempotent on already-initialized repos

## Files Changed

1. `src/llm_wiki/cli/main.py`
   - Added `_is_git_repo()` helper
   - Added `_ensure_git_repo()` helper
   - Updated `init()` command to call `_ensure_git_repo()`

2. `src/llm_wiki/cli/configure.py`
   - Updated `_setup_hermes()` to check git initialization
   - Updated `_setup_claude_code()` to check git initialization

## Backward Compatibility

✅ Fully backward compatible:
- Existing vaults with git: no change (detected, prompt skipped)
- Existing vaults without git: prompt appears (fixes the bug)
- New vaults: prompt appears (improves UX)
- Non-interactive mode: available for scripts/CI

## Future Enhancements

Possible improvements:
1. Detect and use global git config instead of setting defaults
2. Offer to initialize with user's name/email from git config
3. Add `--no-git` flag to skip prompt for automation
4. Provide better error messages if git is not installed
5. Check git availability before running commands

## Resolves

This resolves the issue where llm-wiki assumed git availability but never initialized it, causing silent failures on write operations.
