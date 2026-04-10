# Configure Wizard Section Menu + Keep/Skip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When re-running `llm-wiki configure` with an existing config, let the user choose which section to update and keep existing values with a single keypress — no re-entering models you already have.

**Architecture:** Two changes to `src/llm_wiki/cli/configure.py`. (1) Two new pure helpers — `_show_existing_summary()` and `_section_choice()` — replace the bare "Continue?" prompt with a section menu. (2) `run_wizard()` grows `run_llm / run_embed / run_agent` flags; each section either runs interactively (with keep/skip prompts showing current values) or silently loads from `existing`. No new files.

**Tech Stack:** Python stdlib only — same as the rest of configure.py. Tests use `monkeypatch` and `capsys` (already in use in `tests/test_cli/test_configure_wizard.py`).

---

## File Map

| File | Action |
|------|--------|
| `src/llm_wiki/cli/configure.py` | Add `_show_existing_summary`, `_section_choice`, `_UPDATE_CHOICES`; refactor `run_wizard()` |
| `tests/test_cli/test_configure_wizard.py` | Add 6 new tests |

---

## Task 1: `_show_existing_summary` + `_section_choice` helpers

These are pure functions — no interactive prompts in `_show_existing_summary`, and `_section_choice` delegates interaction only to the already-tested `_choice` helper. Fully testable.

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`
- Modify: `tests/test_cli/test_configure_wizard.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli/test_configure_wizard.py`:

```python
def test_section_choice_everything(monkeypatch):
    monkeypatch.setattr("llm_wiki.cli.configure._choice", lambda *a, **kw: 0)
    monkeypatch.setattr("llm_wiki.cli.configure._show_existing_summary", lambda x: None)
    from llm_wiki.cli.configure import _section_choice
    assert _section_choice({}) == (True, True, True)


def test_section_choice_llm_only(monkeypatch):
    monkeypatch.setattr("llm_wiki.cli.configure._choice", lambda *a, **kw: 1)
    monkeypatch.setattr("llm_wiki.cli.configure._show_existing_summary", lambda x: None)
    from llm_wiki.cli.configure import _section_choice
    assert _section_choice({}) == (True, False, False)


def test_section_choice_embed_only(monkeypatch):
    monkeypatch.setattr("llm_wiki.cli.configure._choice", lambda *a, **kw: 2)
    monkeypatch.setattr("llm_wiki.cli.configure._show_existing_summary", lambda x: None)
    from llm_wiki.cli.configure import _section_choice
    assert _section_choice({}) == (False, True, False)


def test_section_choice_agent_only(monkeypatch):
    monkeypatch.setattr("llm_wiki.cli.configure._choice", lambda *a, **kw: 3)
    monkeypatch.setattr("llm_wiki.cli.configure._show_existing_summary", lambda x: None)
    from llm_wiki.cli.configure import _section_choice
    assert _section_choice({}) == (False, False, True)


def test_show_existing_summary_with_fast(capsys):
    from llm_wiki.cli.configure import _show_existing_summary
    existing = {
        "llm": {
            "backends": {
                "smart": {"model": "openai/gpt-4o"},
                "fast": {"model": "openai/gpt-4o-mini"},
            }
        },
        "search": {"embeddings_enabled": True},
    }
    _show_existing_summary(existing)
    out = capsys.readouterr().out
    assert "gpt-4o" in out
    assert "gpt-4o-mini" in out
    assert "enabled" in out


def test_show_existing_summary_no_fast(capsys):
    from llm_wiki.cli.configure import _show_existing_summary
    _show_existing_summary({
        "llm": {"backends": {"smart": {"model": "openai/x"}}},
        "search": {"embeddings_enabled": False},
    })
    out = capsys.readouterr().out
    assert "using smart" in out
    assert "disabled" in out
```

Run: `PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v`
Expected: 6 new tests FAIL with `ImportError` / `AttributeError`.

- [ ] **Step 2: Add `_UPDATE_CHOICES`, `_show_existing_summary`, `_section_choice` to configure.py**

Add after the `_setup_agent_framework` function (before `run_wizard`):

```python
_UPDATE_CHOICES = [
    "Everything",
    "LLM backends  (smart + fast model)",
    "Embeddings",
    "Agent framework",
]


def _show_existing_summary(existing: dict) -> None:
    """Print a compact summary of the current config to orient the user."""
    llm = existing.get("llm", {})
    backends = llm.get("backends", {})
    smart = backends.get("smart", {})
    fast = backends.get("fast", {})
    embed_enabled = existing.get("search", {}).get("embeddings_enabled", False)
    embed_model = llm.get("embeddings", "")

    _info("Current settings:")
    _info(f"  Smart model:  {smart.get('model', '(none)')}")
    if fast:
        _info(f"  Fast model:   {fast.get('model', '(none)')}")
    else:
        _info("  Fast model:   (using smart for all tasks)")
    embed_status = "enabled" if embed_enabled else "disabled"
    _info(f"  Embeddings:   {embed_status}" + (f"  ({embed_model})" if embed_model else ""))


def _section_choice(existing: dict) -> tuple[bool, bool, bool]:
    """Show section menu and return (run_llm, run_embed, run_agent)."""
    _show_existing_summary(existing)
    print()
    idx = _choice("What would you like to update?", _UPDATE_CHOICES, default=0)
    print()
    if idx == 0:
        return True, True, True
    if idx == 1:
        return True, False, False
    if idx == 2:
        return False, True, False
    return False, False, True  # Agent framework only
```

- [ ] **Step 3: Run tests — expect pass**

```bash
PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v
```

Expected: all 23 tests PASS.

- [ ] **Step 4: Full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 823 passed.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/configure.py tests/test_cli/test_configure_wizard.py
git commit -m "feat: _show_existing_summary and _section_choice helpers for wizard"
```

---

## Task 2: Wire section menu + keep/skip into `run_wizard()`

This task refactors `run_wizard()` only. No new helpers. The function currently starts at line 690 and ends at line 862. Read it in full before making changes — the structure and variable names matter.

**Files:**
- Modify: `src/llm_wiki/cli/configure.py` — `run_wizard()` only

- [ ] **Step 1: Replace the "Continue?" block with `_section_choice`**

Find this block in `run_wizard()` (around line 717–726):

```python
    # Load existing config
    existing: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            existing = yaml.safe_load(f) or {}
        _warn("Existing config found — wizard will update it.")
        if not _yes_no("Continue?", default=True):
            _info("Aborted.")
            return
        print()
```

Replace with:

```python
    # Load existing config
    existing: dict[str, Any] = {}
    run_llm = run_embed = run_agent = True

    if config_path.exists():
        with open(config_path) as f:
            existing = yaml.safe_load(f) or {}
        _warn("Existing config found.")
        print()
        run_llm, run_embed, run_agent = _section_choice(existing)
```

- [ ] **Step 2: Wrap the LLM Backends section in `if run_llm:` with keep/skip, else load from existing**

Find the current LLM backends section (from `# ── Model tier framing` to `_ok(f"Fast model configured...")`). Replace the entire block with:

```python
    # ── LLM Backends ─────────────────────────────────────────────────────────
    existing_llm = existing.get("llm", {})
    existing_backends = existing_llm.get("backends", {})

    if run_llm:
        _header("LLM Backends")
        _info("llm-wiki routes tasks across two model tiers:")
        _info("")
        _info("  Smart model — depth work: research queries, document ingestion,")
        _info("                adversarial fact-checking. Use your most capable model.")
        _info("")
        _info("  Fast model  — high-frequency background: librarian, compliance,")
        _info("                commit summaries. Throughput matters more than depth.")
        _info("")
        _info("You can use the same model for both — just skip the fast model step.")
        print()

        # Smart model — keep or reconfigure
        existing_smart = existing_backends.get("smart", {})
        if existing_smart and not _yes_no(
            f"Change smart model  (current: {existing_smart.get('model', '?')})?",
            default=False,
        ):
            backend_name = "local" if existing_smart.get("api_base") else "openai"
            backend_cfg = existing_smart
            _ok(f"Keeping smart model  ({backend_cfg['model']})")
        else:
            _info("Which provider do you want for your smart model?")
            print()
            provider_idx = _choice("Provider:", _PROVIDERS, default=0)
            _header(_PROVIDERS[provider_idx].split("  ")[0].strip())
            backend_name, backend_cfg = _PROVIDER_SETUP[provider_idx]()
            _ok(f"Smart model configured  ({backend_cfg['model']})")

        backends: dict[str, dict] = {"smart": backend_cfg}
        default_backend = "smart"

        # Capture OpenAI key for embeddings reuse (only for cloud OpenAI, not local proxy)
        openai_key = ""
        if backend_name == "openai":
            openai_key = backend_cfg.get("api_key", "")

        # Fast model — keep or reconfigure
        print()
        _header("Fast / Cheap Model  (optional)")
        existing_fast = existing_backends.get("fast", {})
        role_overrides: dict[str, str] = {}
        if existing_fast and not _yes_no(
            f"Change fast model  (current: {existing_fast.get('model', '?')})?",
            default=False,
        ):
            backends["fast"] = existing_fast
            role_overrides = {role: "smart" for role in _SMART_ROLES}
            role_overrides.update({role: "fast" for role in _FAST_ROLES})
            _ok(f"Keeping fast model  ({existing_fast['model']})")
        else:
            fast_result = _setup_fast_backend("smart", backend_cfg)
            if fast_result:
                fast_name, fast_cfg = fast_result
                backends[fast_name] = fast_cfg
                role_overrides = {role: "smart" for role in _SMART_ROLES}
                role_overrides.update({role: fast_name for role in _FAST_ROLES})
                _ok(f"Fast model configured  ({fast_cfg['model']})")

    else:
        # LLM section skipped — load from existing
        backends = {k: v for k, v in existing_backends.items() if k != "embeddings"}
        if not backends:
            backends = {"smart": {}}
        backend_cfg = backends.get("smart", {})
        default_backend = "smart"
        openai_key = ""
        role_overrides = {}
        if "fast" in backends:
            role_overrides = {role: "smart" for role in _SMART_ROLES}
            role_overrides.update({role: "fast" for role in _FAST_ROLES})
```

- [ ] **Step 3: Wrap the Embeddings section in `if run_embed:` with keep/skip, else load from existing**

Find the current embeddings section:

```python
    # ── Embeddings ────────────────────────────────────────────────────────────
    print()
    _header("Embeddings")
    embed_model, embed_enabled, embed_key = _setup_embeddings(openai_key)
```

Replace with:

```python
    # ── Embeddings ────────────────────────────────────────────────────────────
    if run_embed:
        print()
        _header("Embeddings")
        existing_embed_model = existing_llm.get("embeddings", "")
        existing_embed_enabled = existing.get("search", {}).get("embeddings_enabled", True)
        if existing_embed_model and not _yes_no(
            f"Change embeddings  (current: {existing_embed_model}, "
            f"{'enabled' if existing_embed_enabled else 'disabled'})?",
            default=False,
        ):
            embed_model = existing_embed_model
            embed_enabled = existing_embed_enabled
            embed_key = ""
            _ok(f"Keeping embeddings  ({embed_model})")
        else:
            embed_model, embed_enabled, embed_key = _setup_embeddings(openai_key)
    else:
        embed_model = existing_llm.get("embeddings", "")
        embed_enabled = existing.get("search", {}).get("embeddings_enabled", True)
        embed_key = ""
```

- [ ] **Step 4: Wrap the Agent Framework section in `if run_agent:`**

Find:

```python
    # ── Agent framework integration ───────────────────────────────────────────
    print()
    _header("Agent Framework Integration")
    framework_result = _setup_agent_framework()
```

Replace with:

```python
    # ── Agent framework integration ───────────────────────────────────────────
    if run_agent:
        print()
        _header("Agent Framework Integration")
        framework_result = _setup_agent_framework()
    else:
        framework_result = None
```

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 823 passed (no new tests in this task — the wizard is interactive and tested via helpers).

- [ ] **Step 6: Smoke test the wizard can be invoked**

```bash
PYTHONPATH=src python -c "
from click.testing import CliRunner
from llm_wiki.cli.main import cli
r = CliRunner()
result = r.invoke(cli, ['configure', '--help'])
assert result.exit_code == 0
print('OK')
"
```

Expected: OK printed, exit code 0.

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/cli/configure.py
git commit -m "feat: wizard section menu and keep/skip for existing config"
```

---

## Task 3: PR

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/wizard-section-menu
gh pr create \
  --title "feat: wizard section menu + keep/skip for existing config" \
  --base master \
  --body "Re-running llm-wiki configure no longer forces you to re-enter everything. When an existing config is found, a section menu lets you choose what to update (Everything / LLM backends / Embeddings / Agent framework). Within each section, existing values are shown with a 'Change it? [y/N]' prompt so you can skip with Enter."
```

---

## Self-Review

**Spec coverage:**
- ✓ Section menu — `_section_choice` with 4 options
- ✓ Keep/skip smart model — `_yes_no("Change smart model...")` defaulting to No
- ✓ Keep/skip fast model — `_yes_no("Change fast model...")` defaulting to No
- ✓ Keep/skip embeddings — `_yes_no("Change embeddings...")` defaulting to No
- ✓ Agent framework skippable — `if run_agent`
- ✓ Skipped sections load from existing — `else` branches populate all required variables

**Placeholder scan:** None found.

**Type consistency:**
- `_section_choice` returns `tuple[bool, bool, bool]` — consumed as `run_llm, run_embed, run_agent = ...` ✓
- `backends`, `backend_cfg`, `role_overrides`, `embed_model`, `embed_enabled`, `embed_key`, `openai_key` — all set in both `if` and `else` branches ✓
- `existing_llm` referenced in both the LLM `else` branch and the Embeddings section — both branches have access because `existing_llm` is assigned before both ✓

**Edge cases handled:**
- Fresh install (no existing config): `run_llm = run_embed = run_agent = True`, `_section_choice` never called — full wizard runs as before ✓
- Existing config but no `smart` backend (malformed): `backends.get("smart", {})` returns `{}`, keep/skip prompt is skipped (no `existing_smart`) — full provider selection runs ✓
- Agent skipped: `framework_result = None` — summary shows "Not configured" via the `None` key in the label dict ✓
