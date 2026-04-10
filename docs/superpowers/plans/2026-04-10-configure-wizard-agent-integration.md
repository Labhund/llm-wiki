# Configure Wizard — Model Context + Agent Framework Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `llm-wiki configure` with smart/fast model framing and a final agent-framework section that installs Hermes skills, patches legacy skills, and registers the MCP server — all in one wizard run.

**Architecture:** All new logic lives in `src/llm_wiki/cli/configure.py`. Skills are moved from the repo root into the Python package so `importlib.resources` can locate them after a pip install. Hermes and Claude Code integration are pure file operations (yaml merge, file copy, manifest update) — no new dependencies.

**Tech Stack:** Python stdlib (`hashlib`, `importlib.resources`, `pathlib`), PyYAML (already present), Click (already present).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `skills/llm-wiki/` | **Move** → `src/llm_wiki/skills/llm-wiki/` | Bundle skills with the pip package |
| `skills/llm-wiki/autonomous/` | Moves with parent | Autonomous skill variants |
| `pyproject.toml` | Modify | Declare skill .md files as package data |
| `src/llm_wiki/cli/configure.py` | Modify | All new wizard logic |
| `tests/test_cli/test_configure_wizard.py` | Create | Tests for pure logic helpers |

---

## Task 1: Move skills into package + declare as package data

**Files:**
- Move: `skills/llm-wiki/` → `src/llm_wiki/skills/llm-wiki/`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write a failing test that confirms skills are findable via importlib**

Create `tests/test_cli/test_configure_wizard.py`:

```python
"""Tests for configure wizard helper functions."""
from pathlib import Path


def test_skills_source_returns_directory():
    """Skills must be locatable from the installed package."""
    from llm_wiki.cli.configure import _skills_source
    src = _skills_source()
    assert src.is_dir(), f"Skills dir not found at {src}"
    md_files = list(src.rglob("*.md"))
    assert len(md_files) >= 5, f"Expected at least 5 skill files, found {len(md_files)}"
```

Run: `PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v`
Expected: FAIL — `_skills_source` not defined yet.

- [ ] **Step 2: Move skill files**

```bash
mkdir -p src/llm_wiki/skills
git mv skills/llm-wiki src/llm_wiki/skills/llm-wiki
# Keep skills/setup/SKILL.md where it is — that's a Hermes skill, not a bundled asset
```

Verify: `ls src/llm_wiki/skills/llm-wiki/` should show index.md, research.md, write.md, ingest.md, maintain.md, autonomous/

- [ ] **Step 3: Add package data to pyproject.toml**

In `pyproject.toml`, the existing `[tool.hatch.build.targets.wheel]` section already has `packages = ["src/llm_wiki"]`. Hatchling includes all files in that tree for editable installs. Add an explicit `artifacts` declaration so non-editable wheels include the .md files:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/llm_wiki"]
artifacts = ["src/llm_wiki/skills/**/*.md"]
```

- [ ] **Step 4: Add `_skills_source()` to configure.py**

Add after the existing imports in `src/llm_wiki/cli/configure.py`:

```python
def _skills_source() -> Path:
    """Locate the bundled skills/llm-wiki/ directory.

    Works for both editable (pip install -e .) and non-editable installs.
    Raises RuntimeError with a clear message if the package is broken.
    """
    # Editable install: src/llm_wiki/cli/configure.py → ../../skills/llm-wiki
    candidate = Path(__file__).parent.parent / "skills" / "llm-wiki"
    if candidate.is_dir():
        return candidate
    # Non-editable install: use importlib.resources
    try:
        import importlib.resources
        ref = importlib.resources.files("llm_wiki") / "skills" / "llm-wiki"
        with importlib.resources.as_file(ref) as p:
            if Path(p).is_dir():
                return Path(p)
    except Exception:
        pass
    raise RuntimeError(
        "Could not locate bundled skills directory.\n"
        "Run: pip install -e . to ensure the package is properly installed."
    )
```

- [ ] **Step 5: Run test — expect pass**

```bash
PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py::test_skills_source_returns_directory -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804 passed (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/skills/ pyproject.toml src/llm_wiki/cli/configure.py tests/test_cli/test_configure_wizard.py
git commit -m "feat: move skills into package + add _skills_source() helper"
```

---

## Task 2: Skill installation helpers

These are pure functions — no interactive prompts — so they're fully testable.

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`
- Modify: `tests/test_cli/test_configure_wizard.py`

- [ ] **Step 1: Write failing tests for the three helpers**

Append to `tests/test_cli/test_configure_wizard.py`:

```python
import hashlib
from pathlib import Path


def test_parse_skill_name_extracts_name(tmp_path):
    from llm_wiki.cli.configure import _parse_skill_name
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: llm-wiki/research\ndescription: test\n---\n\n# Body\n")
    assert _parse_skill_name(skill) == "llm-wiki/research"


def test_parse_skill_name_returns_none_for_missing(tmp_path):
    from llm_wiki.cli.configure import _parse_skill_name
    skill = tmp_path / "SKILL.md"
    skill.write_text("# No frontmatter\n")
    assert _parse_skill_name(skill) is None


def test_skill_dest_maps_slash_to_path(tmp_path):
    from llm_wiki.cli.configure import _skill_dest
    hermes = tmp_path / ".hermes"
    assert _skill_dest("llm-wiki/research", hermes) == hermes / "skills" / "llm-wiki" / "research" / "SKILL.md"
    assert _skill_dest("llm-wiki", hermes) == hermes / "skills" / "llm-wiki" / "SKILL.md"


def test_update_manifest_writes_entry(tmp_path):
    from llm_wiki.cli.configure import _update_manifest
    manifest = tmp_path / ".bundled_manifest"
    content = b"hello world"
    _update_manifest(manifest, "llm-wiki/research", content)
    expected_hash = hashlib.md5(content).hexdigest()
    lines = manifest.read_text().splitlines()
    assert f"llm-wiki/research:{expected_hash}" in lines


def test_update_manifest_replaces_existing_entry(tmp_path):
    from llm_wiki.cli.configure import _update_manifest
    manifest = tmp_path / ".bundled_manifest"
    manifest.write_text("llm-wiki/research:oldhash\nother:abc\n")
    _update_manifest(manifest, "llm-wiki/research", b"new content")
    lines = manifest.read_text().splitlines()
    assert not any("oldhash" in l for l in lines)
    assert any("llm-wiki/research:" in l for l in lines)
    assert "other:abc" in lines


def test_patch_legacy_skill_adds_banner(tmp_path):
    from llm_wiki.cli.configure import _patch_legacy_skill, _MCP_BANNER
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: llm-wiki\n---\n\n# Body text\n")
    patched = _patch_legacy_skill(skill)
    assert patched is True
    content = skill.read_text()
    assert _MCP_BANNER in content
    # Banner must be after frontmatter
    assert content.index(_MCP_BANNER) > content.index("---\n\n")


def test_patch_legacy_skill_idempotent(tmp_path):
    from llm_wiki.cli.configure import _patch_legacy_skill, _MCP_BANNER
    skill = tmp_path / "SKILL.md"
    skill.write_text(f"---\nname: llm-wiki\n---\n\n{_MCP_BANNER}\n# Body\n")
    patched = _patch_legacy_skill(skill)
    assert patched is False  # already patched
```

Run: `PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v`
Expected: All new tests FAIL.

- [ ] **Step 2: Implement the three helpers in configure.py**

Add after `_skills_source()`:

```python
import hashlib as _hashlib


_MCP_BANNER = (
    "> **MCP supersedes this skill.** If `wiki_search`, `wiki_read`, `wiki_query` tools are\n"
    "> available (llm-wiki MCP server connected), use those instead. This skill is retained\n"
    "> as conceptual reference only.\n"
)


def _parse_skill_name(md_path: Path) -> str | None:
    """Extract the 'name' field from YAML frontmatter. Returns None if absent."""
    content = md_path.read_text()
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end < 0:
        return None
    try:
        meta = yaml.safe_load(content[3:end].strip())
        return meta.get("name") if isinstance(meta, dict) else None
    except yaml.YAMLError:
        return None


def _skill_dest(name: str, hermes_home: Path) -> Path:
    """Map a skill name (slash-separated) to its SKILL.md path under hermes_home/skills/."""
    parts = name.split("/")
    return hermes_home / "skills" / Path(*parts) / "SKILL.md"


def _update_manifest(manifest_path: Path, skill_name: str, content: bytes) -> None:
    """Upsert a skillname:md5 entry in the Hermes bundled manifest."""
    md5 = _hashlib.md5(content).hexdigest()
    entry = f"{skill_name}:{md5}"
    if manifest_path.exists():
        lines = [l for l in manifest_path.read_text().splitlines()
                 if not l.startswith(f"{skill_name}:")]
    else:
        lines = []
    lines.append(entry)
    manifest_path.write_text("\n".join(lines) + "\n")


def _patch_legacy_skill(skill_path: Path) -> bool:
    """Prepend MCP supersession banner after frontmatter. Returns True if patched."""
    content = skill_path.read_text()
    if _MCP_BANNER in content:
        return False
    if not content.startswith("---"):
        return False
    end = content.find("---", 3)
    if end < 0:
        return False
    insert_at = end + 3
    new_content = content[:insert_at] + "\n\n" + _MCP_BANNER + "\n" + content[insert_at:].lstrip("\n")
    skill_path.write_text(new_content)
    return True
```

- [ ] **Step 3: Run tests — expect pass**

```bash
PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804+ passed.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/configure.py tests/test_cli/test_configure_wizard.py
git commit -m "feat: add skill install helpers — parse, dest mapping, manifest upsert, legacy patch"
```

---

## Task 3: Model picker UX — framing text + "other" hint

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`

- [ ] **Step 1: Update `_pick_or_type()` to show the LiteLLM hint and use the right label**

Find and replace the current `_pick_or_type` function:

```python
def _pick_or_type(choices: list[str], label: str = "Select model:", default: int = 0) -> str:
    """Choose from list; last entry is always 'other (type manually)'."""
    idx = _choice(label, choices, default)
    if idx == len(choices) - 1:
        _info("LiteLLM format examples:")
        _info("  openai/gpt-4o                     (OpenAI)")
        _info("  anthropic/claude-haiku-4-5         (Anthropic)")
        _info("  openrouter/google/gemini-2.5-pro   (OpenRouter)")
        _info("  openai/my-local-model              (local endpoint)")
        print()
        return _prompt("Model name")
    return choices[idx]
```

- [ ] **Step 2: Update all callers to pass the label**

In `_setup_local()`, `_setup_openai()`, `_setup_anthropic()`, `_setup_openrouter()` — all call `_pick_or_type(...)`. Update each to pass `label="Choose your smart model:"` (they're called for the smart model selection only; the fast model goes through a separate `_setup_fast_backend()` which also calls `_pick_or_type` — pass `label="Choose your fast model:"` there).

In `_setup_local()`:
```python
raw_model = _pick_or_type(_LOCAL_MODELS, label="Choose your smart model:")
```

In `_setup_openai()`:
```python
model = _pick_or_type(_OPENAI_MODELS, label="Choose your smart model:")
```

In `_setup_anthropic()`:
```python
model = _pick_or_type(_ANTHROPIC_MODELS, label="Choose your smart model:")
```

In `_setup_openrouter()`:
```python
model_short = _pick_or_type(_OPENROUTER_MODELS, label="Choose your smart model:")
```

In `_setup_custom()`: no model picker (user types directly) — no change needed.

In `_setup_fast_backend()`, the call goes through `_PROVIDER_SETUP[provider_idx]()` which will use "Choose your smart model:" — fix by threading a `label` parameter. Simplest: just add a module-level variable `_MODEL_PICK_LABEL = "Choose your smart model:"` and override it in `_setup_fast_backend()`.

Actually, simpler: rename the calls in `_setup_fast_backend` to use a dedicated `_setup_fast_provider()` wrapper that temporarily relabels. Or: just change the label strings inside each `_setup_*` function to accept a `label` argument and default appropriately.

Cleanest approach — add a `label` parameter to each `_setup_*` function and thread it through. Here's the complete updated set:

```python
def _setup_local(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Common base URLs:")
    _info("  ollama:       http://localhost:11434/v1")
    _info("  vllm:         http://localhost:8000/v1")
    _info("  LiteLLM proxy: http://localhost:4000/v1")
    print()
    api_base = _prompt("Base URL", "http://localhost:11434/v1")
    raw_model = _pick_or_type(_LOCAL_MODELS, label=label)
    model = raw_model.lstrip("openai/") if raw_model.startswith("openai/") else raw_model
    model_str = f"openai/{model}"
    api_key = _prompt("API key (press Enter to skip)")
    backend: dict[str, Any] = {"model": model_str, "api_base": api_base}
    if api_key:
        backend["api_key"] = api_key
    return "local", backend


def _setup_openai(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://platform.openai.com/api-keys")
    print()
    api_key = _prompt("OpenAI API key", password=True)
    model = _pick_or_type(_OPENAI_MODELS, label=label)
    return "openai", {"model": model, "api_key": api_key}


def _setup_anthropic(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://console.anthropic.com/")
    print()
    api_key = _prompt("Anthropic API key", password=True)
    model = _pick_or_type(_ANTHROPIC_MODELS, label=label)
    return "anthropic", {"model": model, "api_key": api_key}


def _setup_openrouter(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://openrouter.ai/keys")
    _info("OpenRouter gives you access to 200+ models with a single key.")
    print()
    api_key = _prompt("OpenRouter API key", password=True)
    model_short = _pick_or_type(_OPENROUTER_MODELS, label=label)
    if not model_short.startswith("openrouter/"):
        model_str = f"openrouter/{model_short}"
    else:
        model_str = model_short
    return "openrouter", {
        "model": model_str,
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": api_key,
    }
```

Update `_PROVIDER_SETUP` to use lambdas so label can be passed through:

```python
_PROVIDER_SETUP = [_setup_local, _setup_openai, _setup_anthropic, _setup_openrouter, _setup_custom]
```

In `_setup_fast_backend()`, call the setup function with `label="Choose your fast model:"`:

```python
def _setup_fast_backend(smart_name: str, smart_cfg: dict) -> tuple[str, dict] | None:
    _info("Background tasks (librarian, adversary, compliance, commit, talk_summary)")
    _info("don't need your most powerful model. A cheaper/faster one saves cost.")
    print()
    if not _yes_no("Configure a separate fast/cheap model for background tasks?", default=True):
        return None
    print()
    _header("Fast / Cheap Model")
    provider_idx = _choice("Provider:", _PROVIDERS, default=0)
    _header(_PROVIDERS[provider_idx].split("  ")[0].strip())
    _, backend_cfg = _PROVIDER_SETUP[provider_idx](label="Choose your fast model:")
    return "fast", backend_cfg
```

- [ ] **Step 3: Add model tier framing in `run_wizard()` before the LLM Backend section**

In `run_wizard()`, find the `# ── LLM Backend` comment and add framing above it:

```python
    # ── Model tier framing ────────────────────────────────────────────────────
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
    _info("Which provider do you want for your smart model?")
    print()
```

Remove the old `_header("LLM Backend")` and the two `_info` lines that follow it.

- [ ] **Step 4: Run full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804+ passed.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/configure.py
git commit -m "feat: model tier framing and smart/fast labels in configure wizard"
```

---

## Task 4: `_setup_hermes()` — vault init, skill install, MCP registration

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`
- Modify: `tests/test_cli/test_configure_wizard.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli/test_configure_wizard.py`:

```python
def test_install_skills_to_hermes(tmp_path):
    """Skills are copied and manifest is updated."""
    from llm_wiki.cli.configure import _install_skills_to_hermes
    hermes_home = tmp_path / ".hermes"
    (hermes_home / "skills").mkdir(parents=True)
    count = _install_skills_to_hermes(hermes_home)
    assert count > 0
    # At minimum the index skill should be installed
    assert (hermes_home / "skills" / "llm-wiki" / "SKILL.md").exists()
    assert (hermes_home / "skills" / ".bundled_manifest").exists()


def test_patch_legacy_skills_in_hermes(tmp_path):
    """Legacy llm-wiki* skills in research/ get the MCP banner."""
    from llm_wiki.cli.configure import _patch_legacy_skills, _MCP_BANNER
    hermes_home = tmp_path / ".hermes"
    legacy_dir = hermes_home / "skills" / "research" / "llm-wiki-legacy"
    legacy_dir.mkdir(parents=True)
    skill_file = legacy_dir / "SKILL.md"
    skill_file.write_text("---\nname: llm-wiki-legacy\n---\n\n# Old skill\n")
    patched = _patch_legacy_skills(hermes_home)
    assert patched == 1
    assert _MCP_BANNER in skill_file.read_text()


def test_merge_hermes_mcp_config(tmp_path):
    """MCP server block is merged into Hermes config without losing other keys."""
    from llm_wiki.cli.configure import _merge_hermes_mcp
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  max_turns: 90\nmcp_servers:\n  other-tool:\n    command: foo\n")
    vault_path = Path("/home/user/wiki")
    _merge_hermes_mcp(config_path, vault_path)
    content = yaml.safe_load(config_path.read_text())
    assert "llm-wiki" in content["mcp_servers"]
    assert content["mcp_servers"]["llm-wiki"]["command"] == "llm-wiki"
    assert content["mcp_servers"]["llm-wiki"]["env"]["LLM_WIKI_VAULT"] == str(vault_path)
    assert "other-tool" in content["mcp_servers"]   # existing entry preserved
    assert content["agent"]["max_turns"] == 90       # unrelated key preserved
```

Run: `PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v`
Expected: three new tests FAIL.

- [ ] **Step 2: Implement `_install_skills_to_hermes()`, `_patch_legacy_skills()`, `_merge_hermes_mcp()`**

Add to `src/llm_wiki/cli/configure.py`:

```python
def _install_skills_to_hermes(hermes_home: Path) -> int:
    """Copy all bundled skills to hermes_home/skills/. Returns count installed."""
    src_root = _skills_source()
    manifest_path = hermes_home / "skills" / ".bundled_manifest"
    count = 0
    for md_path in sorted(src_root.rglob("*.md")):
        name = _parse_skill_name(md_path)
        if not name:
            continue
        dest = _skill_dest(name, hermes_home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = md_path.read_bytes()
        dest.write_bytes(content)
        _update_manifest(manifest_path, name, content)
        count += 1
    return count


def _patch_legacy_skills(hermes_home: Path) -> int:
    """Patch all llm-wiki* skills in hermes_home/skills/research/ with MCP banner.
    Returns count of skills patched (0 if all already patched)."""
    research_dir = hermes_home / "skills" / "research"
    if not research_dir.is_dir():
        return 0
    patched = 0
    for skill_dir in research_dir.iterdir():
        if not skill_dir.name.startswith("llm-wiki"):
            continue
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and _patch_legacy_skill(skill_file):
            patched += 1
    return patched


def _merge_hermes_mcp(config_path: Path, vault_path: Path) -> None:
    """Merge llm-wiki MCP server entry into Hermes config.yaml."""
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    config.setdefault("mcp_servers", {})["llm-wiki"] = {
        "command": "llm-wiki",
        "args": ["mcp"],
        "env": {"LLM_WIKI_VAULT": str(vault_path)},
        "timeout": 120,
        "connect_timeout": 30,
    }
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
```

- [ ] **Step 3: Implement `_setup_hermes()`**

```python
def _setup_hermes() -> dict[str, Any] | None:
    """Interactive Hermes integration setup. Returns result dict or None on abort."""
    # ── Detect Hermes home ────────────────────────────────────────────────────
    import os
    default_hermes = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    hermes_home_str = _prompt("Hermes home directory", str(default_hermes))
    hermes_home = Path(hermes_home_str).expanduser()
    if not hermes_home.is_dir():
        _err(f"Directory not found: {hermes_home}")
        _info("Is Hermes installed? Check https://github.com/NousResearch/hermes-agent")
        return None

    # ── Vault path ────────────────────────────────────────────────────────────
    import os as _os
    env_vault = _os.environ.get("LLM_WIKI_VAULT", "")
    if env_vault:
        default_vault = env_vault
        vault_source_note = "  [from $LLM_WIKI_VAULT — override if stale]"
    else:
        default_vault = str(Path.home() / "wiki")
        vault_source_note = ""
    if vault_source_note:
        _info(vault_source_note)
    vault_str = _prompt("Vault path", default_vault)
    vault_path = Path(vault_str).expanduser()

    # ── Vault initialisation ──────────────────────────────────────────────────
    vault_created = False
    if not vault_path.exists():
        _info(f"Creating vault at {vault_path}…")
        for sub in ("raw", "wiki", "schema", "inbox"):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        vault_created = True

    if not (vault_path / "raw").is_dir():
        # Exists but missing required structure
        for sub in ("raw", "wiki", "schema", "inbox"):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        vault_created = True

    if vault_created:
        _info("Initialising vault index…")
        from llm_wiki.vault import Vault
        try:
            Vault.scan(vault_path)
            _ok("Vault initialised")
        except Exception as e:
            _warn(f"Vault init warning: {e}")

    # ── Skill installation ────────────────────────────────────────────────────
    _info("Installing companion skills…")
    try:
        count = _install_skills_to_hermes(hermes_home)
        _ok(f"{count} skills installed")
    except RuntimeError as e:
        _err(str(e))
        return None

    # ── Legacy skill patching ─────────────────────────────────────────────────
    patched = _patch_legacy_skills(hermes_home)
    if patched:
        _ok(f"{patched} legacy skill(s) patched with MCP routing banner")

    # ── MCP registration ──────────────────────────────────────────────────────
    hermes_config = hermes_home / "config.yaml"
    if hermes_config.exists():
        _merge_hermes_mcp(hermes_config, vault_path)
        _ok("MCP server registered in Hermes config")
    else:
        _warn("Hermes config.yaml not found — skipping MCP registration")
        _info(f"Add manually under mcp_servers: in {hermes_config}")

    # ── Config check ──────────────────────────────────────────────────────────
    wiki_config = vault_path / "schema" / "config.yaml"
    config_missing = not wiki_config.exists() or wiki_config.stat().st_size == 0

    _ok("Hermes integration complete")
    _info("Restart Hermes to load the new skills.")

    return {
        "framework": "hermes",
        "vault_path": vault_path,
        "skills_installed": count,
        "config_missing": config_missing,
    }
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v
```

Expected: All pass.

- [ ] **Step 5: Full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804+ passed.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/cli/configure.py tests/test_cli/test_configure_wizard.py
git commit -m "feat: _setup_hermes() — vault init, skill install, legacy patch, MCP registration"
```

---

## Task 5: `_setup_claude_code()` and `_setup_agent_framework()` routing

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`
- Modify: `tests/test_cli/test_configure_wizard.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli/test_configure_wizard.py`:

```python
def test_merge_claude_code_mcp_creates_file(tmp_path):
    """MCP entry is written to .claude/mcp.json if it doesn't exist."""
    import json
    from llm_wiki.cli.configure import _merge_claude_code_mcp
    mcp_path = tmp_path / "mcp.json"
    _merge_claude_code_mcp(mcp_path, Path("/home/user/wiki"))
    data = json.loads(mcp_path.read_text())
    assert "llm-wiki" in data["mcpServers"]
    assert data["mcpServers"]["llm-wiki"]["args"] == ["mcp"]


def test_merge_claude_code_mcp_preserves_existing(tmp_path):
    """Existing MCP servers are not overwritten."""
    import json
    from llm_wiki.cli.configure import _merge_claude_code_mcp
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}}}))
    _merge_claude_code_mcp(mcp_path, Path("/home/user/wiki"))
    data = json.loads(mcp_path.read_text())
    assert "other" in data["mcpServers"]
    assert "llm-wiki" in data["mcpServers"]
```

Run: `PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v`
Expected: two new tests FAIL.

- [ ] **Step 2: Implement `_merge_claude_code_mcp()` and `_setup_claude_code()`**

```python
def _merge_claude_code_mcp(mcp_path: Path, vault_path: Path) -> None:
    """Merge llm-wiki MCP entry into a Claude Code mcp.json file."""
    import json
    existing: dict = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            pass
    existing.setdefault("mcpServers", {})["llm-wiki"] = {
        "command": "llm-wiki",
        "args": ["mcp"],
        "env": {"LLM_WIKI_VAULT": str(vault_path)},
    }
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(existing, indent=2) + "\n")


def _setup_claude_code() -> dict[str, Any] | None:
    """Interactive Claude Code integration setup."""
    import os as _os
    # ── Vault path ────────────────────────────────────────────────────────────
    env_vault = _os.environ.get("LLM_WIKI_VAULT", "")
    if env_vault:
        default_vault = env_vault
        _info("  [from $LLM_WIKI_VAULT — override if stale]")
    else:
        default_vault = str(Path.home() / "wiki")
    vault_str = _prompt("Vault path", default_vault)
    vault_path = Path(vault_str).expanduser()

    # ── Vault initialisation ──────────────────────────────────────────────────
    if not (vault_path / "raw").is_dir():
        _info(f"Creating vault at {vault_path}…")
        for sub in ("raw", "wiki", "schema", "inbox"):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        _info("Initialising vault index…")
        from llm_wiki.vault import Vault
        try:
            Vault.scan(vault_path)
            _ok("Vault initialised")
        except Exception as e:
            _warn(f"Vault init warning: {e}")

    # ── MCP config location ───────────────────────────────────────────────────
    global_mcp = Path.home() / ".claude" / "mcp.json"
    use_global = _yes_no(f"Write to global config ({global_mcp})?", default=True)
    mcp_path = global_mcp if use_global else Path.cwd() / ".claude" / "mcp.json"

    _merge_claude_code_mcp(mcp_path, vault_path)
    _ok(f"MCP server registered in {mcp_path}")

    wiki_config = vault_path / "schema" / "config.yaml"
    config_missing = not wiki_config.exists() or wiki_config.stat().st_size == 0

    _info("Reload Claude Code (or restart your IDE) to connect the MCP server.")

    return {
        "framework": "claude_code",
        "vault_path": vault_path,
        "config_missing": config_missing,
    }
```

- [ ] **Step 3: Implement `_setup_agent_framework()`**

```python
_FRAMEWORK_CHOICES = [
    "Hermes",
    "Claude Code",
    "Let my agent figure it out",
    "Skip  (I'll register manually)",
]


def _setup_agent_framework() -> dict[str, Any] | None:
    """Prompt for agent framework and run appropriate setup. Returns result or None."""
    _info("Register the llm-wiki MCP server with your agent framework.")
    print()
    idx = _choice("Agent framework:", _FRAMEWORK_CHOICES, default=0)

    if idx == 0:
        _header("Hermes Integration")
        return _setup_hermes()

    if idx == 1:
        _header("Claude Code Integration")
        return _setup_claude_code()

    if idx == 2:
        print()
        _info("Tell your agent:")
        print()
        print(_col('    "Set up llm-wiki for me."', _C.YELLOW))
        print()
        _info("It will load the llm-wiki-setup skill and walk you through vault")
        _info("creation, daemon configuration, MCP registration, and skill")
        _info("installation interactively — with explicit consent at each step.")
        print()
        return {"framework": "agent_guided"}

    # Skip
    print()
    _info("Add this to your agent's MCP server config:")
    print()
    _info("  Hermes (~/.hermes/config.yaml under mcp_servers:):")
    print(_col(
        "    llm-wiki:\n"
        "      command: llm-wiki\n"
        "      args: [mcp]\n"
        "      env:\n"
        "        LLM_WIKI_VAULT: ~/wiki\n"
        "      timeout: 120\n"
        "      connect_timeout: 30",
        _C.DIM,
    ))
    print()
    _info("  Claude Code (~/.claude/mcp.json under mcpServers:):")
    print(_col(
        '    "llm-wiki": {\n'
        '      "command": "llm-wiki",\n'
        '      "args": ["mcp"],\n'
        '      "env": {"LLM_WIKI_VAULT": "~/wiki"}\n'
        '    }',
        _C.DIM,
    ))
    return {"framework": "manual"}
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src pytest tests/test_cli/test_configure_wizard.py -v
```

Expected: All pass.

- [ ] **Step 5: Full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804+ passed.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/cli/configure.py tests/test_cli/test_configure_wizard.py
git commit -m "feat: _setup_claude_code() and _setup_agent_framework() routing"
```

---

## Task 6: Wire agent framework into `run_wizard()` and update summary

**Files:**
- Modify: `src/llm_wiki/cli/configure.py`

- [ ] **Step 1: Add the agent framework section to `run_wizard()`**

In `run_wizard()`, after the embeddings write and before the summary, add:

```python
    # ── Agent framework integration ───────────────────────────────────────────
    print()
    _header("Agent Framework Integration")
    framework_result = _setup_agent_framework()
```

- [ ] **Step 2: Update the summary block**

Replace the existing `# ── Summary` section with:

```python
    # ── Summary ───────────────────────────────────────────────────────────────
    _header("Setup Complete")

    has_fast = "fast" in backends
    framework = framework_result.get("framework") if framework_result else "skipped"
    config_missing = framework_result.get("config_missing", False) if framework_result else False

    # Framework display label
    framework_label = {
        "hermes": f"Hermes  ({framework_result.get('skills_installed', 0)} skills installed)",
        "claude_code": "Claude Code  (MCP registered)",
        "agent_guided": "Agent-guided  (see instructions above)",
        "manual": "Manual  (see snippets above)",
        "skipped": "Not configured",
    }.get(framework, framework)

    features = [
        (f"Smart model  ({backend_cfg['model']})", True, None),
        (
            f"Fast model  ({backends['fast']['model']})" if has_fast
            else "Fast model  (using smart model for all tasks)",
            has_fast,
            "run wizard again to configure",
        ),
        ("Embeddings / semantic search", embed_enabled,
         "disable with search.embeddings_enabled: false"),
        (f"Agent framework  {framework_label}",
         framework not in ("skipped", "manual"),
         "run wizard again to configure"),
    ]

    enabled = sum(1 for _, ok, _ in features if ok)
    _info(f"{enabled}/{len(features)} configured:")
    print()
    for name, ok, hint in features:
        if ok:
            print(f"   {_col('✓', _C.GREEN)} {name}")
        else:
            dim = f"  {_col(f'({hint})', _C.DIM)}" if hint else ""
            print(f"   {_col('✗', _C.RED)} {name}{dim}")

    if config_missing:
        print()
        _warn("No LLM backend configured yet.")
        _info("Run: llm-wiki configure  to set up your models before starting the daemon.")

    print()
    _ok(f"Config written to {config_path}")
    print()
    print(_col("  Next steps:", _C.CYAN, _C.BOLD))
    _info(f"  llm-wiki serve {vault_path}    Start the wiki daemon")
    _info(f"  llm-wiki query \"...\"           Ask a question")
    _info(f"  llm-wiki ingest <file>         Add a document")
    print()
```

- [ ] **Step 2: Run full suite**

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: 804+ passed.

- [ ] **Step 3: Smoke test the wizard flow manually**

```bash
PYTHONPATH=src python -c "
from llm_wiki.cli.main import cli
from click.testing import CliRunner
r = CliRunner()
result = r.invoke(cli, ['configure', '--help'])
print(result.output)
assert 'configure' in result.output or 'wizard' in result.output.lower()
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/llm_wiki/cli/configure.py
git commit -m "feat: wire agent framework section into run_wizard() and update summary"
```

---

## Task 7: PR

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin feat/config-wizard-agent-integration
gh pr create \
  --title "feat: configure wizard — model framing + Hermes/Claude Code integration" \
  --body "Extends llm-wiki configure with smart/fast model tier framing, LiteLLM format hints, and a new agent framework section (Hermes skill install + MCP registration, Claude Code mcp.json, agent-guided pointer, skip)."
```

---

## Self-Review Notes

**Spec coverage check:**
- ✓ Model tier framing paragraph — Task 3
- ✓ LiteLLM format hint for "other" — Task 3
- ✓ Smart/fast label on model picker — Task 3
- ✓ Vault path shows `[from $LLM_WIKI_VAULT]` source — Tasks 4 + 5
- ✓ Skills moved to package + importlib.resources — Task 1
- ✓ importlib.resources failure gives clear error — Task 1
- ✓ Hermes: detect home, vault init, install skills, patch legacy, register MCP — Task 4
- ✓ Claude Code: vault init, global mcp.json default with Y/n override — Task 5
- ✓ "Let my agent figure it out" option with setup skill pointer — Task 5
- ✓ Skip option with manual snippets — Task 5
- ✓ config_missing warning in summary — Task 6
- ✓ Restart message per framework — Tasks 4 + 5

**Type consistency check:** All function signatures are defined before use. `_PROVIDER_SETUP` callers pass `label=` kwargs — checked. `framework_result` dict keys (`framework`, `skills_installed`, `config_missing`) are consistently set in both `_setup_hermes()` and `_setup_claude_code()`.
