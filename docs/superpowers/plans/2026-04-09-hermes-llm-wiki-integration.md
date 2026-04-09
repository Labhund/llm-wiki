# Hermes + llm-wiki Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend profiles with per-task model routing to llm-wiki, then wire it into Hermes via MCP.

**Architecture:** Replace flat `LLMConfig` with a two-tier structure (named `LLMBackend` profiles + role-based resolution). A migration adapter in `WikiConfig.load()` handles old YAML configs. All 7 LLM consumer sites in `server.py` switch from direct field access to `config.llm.resolve(role)`. Hermes gets the MCP server registered in its config.

**Tech Stack:** Python 3.11+, dataclasses, YAML, pytest, llm-wiki daemon, MCP SDK

**Spec:** `docs/superpowers/specs/2026-04-09-hermes-llm-wiki-integration-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/llm_wiki/config.py` | Modify | `LLMBackend` dataclass, `LLMConfig` rewrite, `WikiConfig.load()` migration adapter |
| `src/llm_wiki/daemon/server.py` | Modify | 7 consumer sites → `resolve(role)` |
| `tests/test_config.py` | Modify | Update existing tests for new field names, add legacy compat tests |
| `tests/test_config_backend_profiles.py` | Create | New unit tests for `LLMBackend`, `resolve()`, `__post_init__`, legacy migration |
| `~/wiki/schema/config.yaml` | Create | Production vault config with fast/deep backends |
| `~/.hermes/config.yaml` | Modify | Add `llm-wiki` MCP server entry |

---

### Task 1: Add LLMBackend dataclass and rewrite LLMConfig

**Files:**
- Modify: `src/llm_wiki/config.py:22-29` (replace `LLMConfig` class)
- Test: `tests/test_config_backend_profiles.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_backend_profiles.py`:

```python
from pathlib import Path

import pytest
import yaml

from llm_wiki.config import LLMBackend, LLMConfig


class TestLLMBackend:
    def test_from_dict_all_fields(self):
        b = LLMBackend.from_dict({
            "model": "openai/gpt-4",
            "api_base": "http://localhost:4000/v1",
            "api_key": "sk-fake",
        })
        assert b.model == "openai/gpt-4"
        assert b.api_base == "http://localhost:4000/v1"
        assert b.api_key == "sk-fake"

    def test_from_dict_extra_keys_ignored(self):
        b = LLMBackend.from_dict({
            "model": "openai/gpt-4",
            "garbage": "ignored",
        })
        assert b.model == "openai/gpt-4"
        assert not hasattr(b, "garbage")

    def test_from_dict_model_required(self):
        with pytest.raises(TypeError):
            LLMBackend.from_dict({"api_base": "http://localhost"})


class TestLLMConfigResolve:
    def _make_config(self, **overrides):
        defaults = {
            "backends": {
                "fast": LLMBackend(model="openai/gemma"),
                "deep": LLMBackend(model="openai/qwen35"),
            },
            "default_backend": "fast",
        }
        defaults.update(overrides)
        return LLMConfig(**defaults)

    def test_resolve_default_no_role(self):
        cfg = self._make_config()
        backend = cfg.resolve()
        assert backend.model == "openai/gemma"

    def test_resolve_role_override(self):
        cfg = self._make_config(adversary="deep")
        backend = cfg.resolve("adversary")
        assert backend.model == "openai/qwen35"

    def test_resolve_falls_back_on_unknown_role(self):
        cfg = self._make_config()
        backend = cfg.resolve("nonexistent_role")
        assert backend.model == "openai/gemma"

    def test_resolve_falls_back_on_none_role_value(self):
        cfg = self._make_config(adversary=None)
        backend = cfg.resolve("adversary")
        assert backend.model == "openai/gemma"

    def test_resolve_raises_on_missing_backend(self):
        cfg = self._make_config(default_backend="nonexistent")
        with pytest.raises(ValueError, match="backend 'nonexistent' not found"):
            cfg.resolve()

    def test_resolve_error_lists_available(self):
        cfg = self._make_config(default_backend="missing")
        with pytest.raises(ValueError, match="fast.*deep"):
            cfg.resolve()


class TestLLMConfigPostInit:
    def test_dicts_converted_to_llm_backend(self):
        cfg = LLMConfig(
            backends={
                "fast": {"model": "openai/gemma", "api_base": "http://localhost:8004/v1"},
            },
            default_backend="fast",
        )
        assert isinstance(cfg.backends["fast"], LLMBackend)
        assert cfg.backends["fast"].model == "openai/gemma"
        assert cfg.backends["fast"].api_base == "http://localhost:8004/v1"

    def test_already_llm_backend_passthrough(self):
        b = LLMBackend(model="openai/gemma")
        cfg = LLMConfig(backends={"fast": b}, default_backend="fast")
        assert cfg.backends["fast"] is b


class TestLLMConfigLegacyCompat:
    def test_legacy_synthesizes_default_backend(self):
        cfg = LLMConfig(
            _default_model="openai/local-instruct",
            _default_api_base="http://localhost:4000",
            _default_api_key="sk-fake",
        )
        assert "default" in cfg.backends
        assert cfg.backends["default"].model == "openai/local-instruct"
        assert cfg.backends["default"].api_base == "http://localhost:4000"
        assert cfg.default_backend == "default"

    def test_legacy_resolve(self):
        cfg = LLMConfig(_default_model="openai/local-instruct")
        backend = cfg.resolve()
        assert backend.model == "openai/local-instruct"

    def test_no_backends_no_legacy_raises(self):
        cfg = LLMConfig()
        with pytest.raises(ValueError, match="not found"):
            cfg.resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config_backend_profiles.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLMBackend' from 'llm_wiki.config'`

- [ ] **Step 3: Implement LLMBackend and rewrite LLMConfig**

In `src/llm_wiki/config.py`, replace lines 22-29 (the current `LLMConfig` class) with:

```python
@dataclass
class LLMBackend:
    model: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "LLMBackend":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class LLMConfig:
    # New-style: named backend profiles
    backends: dict[str, LLMBackend] = field(default_factory=dict)
    default_backend: str = "fast"  # backend name, not model string

    # Legacy fields — kept for backward compat, used only when backends is empty
    _default_model: Optional[str] = field(default=None, repr=False)
    _default_api_base: Optional[str] = field(default=None, repr=False)
    _default_api_key: Optional[str] = field(default=None, repr=False)

    embeddings: str = "openai/text-embedding-3-small"

    # Per-task role overrides (each resolves to a backend name, falls back to default_backend)
    adversary: Optional[str] = None
    ingest: Optional[str] = None
    librarian: Optional[str] = None
    compliance: Optional[str] = None
    talk_summary: Optional[str] = None
    query: Optional[str] = None
    commit: Optional[str] = None

    def __post_init__(self):
        """Build LLMBackend objects from raw dicts (YAML loads everything as dicts).
        If backends is empty and legacy fields are present, synthesize a single backend."""
        if self.backends:
            self.backends = {
                name: LLMBackend.from_dict(v) if isinstance(v, dict) else v
                for name, v in self.backends.items()
            }
        elif self._default_model:
            self.backends = {
                "default": LLMBackend(
                    model=self._default_model,
                    api_base=self._default_api_base,
                    api_key=self._default_api_key,
                )
            }
            self.default_backend = "default"

    def resolve(self, role: Optional[str] = None) -> LLMBackend:
        """Resolve a role to its backend config.
        Falls back to default_backend if role is unset, unknown, or backend name missing."""
        backend_name = getattr(self, role, None) if role else None
        if not backend_name or backend_name not in self.backends:
            backend_name = self.default_backend
        if backend_name not in self.backends:
            raise ValueError(
                f"LLMConfig: backend '{backend_name}' not found. "
                f"Available: {list(self.backends.keys())}"
            )
        return self.backends[backend_name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config_backend_profiles.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/llm-wiki
git add src/llm_wiki/config.py tests/test_config_backend_profiles.py
git commit -m "feat: LLMBackend dataclass + per-task model routing in LLMConfig"
```

---

### Task 2: Add legacy YAML migration adapter to WikiConfig.load()

**Files:**
- Modify: `src/llm_wiki/config.py:125-133` (replace `WikiConfig.load()`)
- Modify: `tests/test_config_backend_profiles.py` (add migration tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_backend_profiles.py`:

```python
class TestWikiConfigLoadMigration:
    def test_legacy_yaml_migrated(self, tmp_path: Path):
        """Old-style YAML with default/api_base/api_key is migrated to internal fields."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n"
            "  default: ollama/llama3\n"
            "  api_base: http://localhost:11434\n"
            "  api_key: sk-123\n"
        )
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(cfg_file)
        assert cfg.llm.default_backend == "default"
        assert cfg.llm.backends["default"].model == "ollama/llama3"
        assert cfg.llm.backends["default"].api_base == "http://localhost:11434"
        assert cfg.llm.backends["default"].api_key == "sk-123"
        # resolve works through legacy backend
        backend = cfg.llm.resolve()
        assert backend.model == "ollama/llama3"

    def test_new_style_yaml_with_backends(self, tmp_path: Path):
        """New-style YAML with backends dict works directly."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "llm": {
                "backends": {
                    "fast": {"model": "openai/gemma"},
                    "deep": {"model": "openai/qwen35", "api_base": "http://localhost:4000/v1"},
                },
                "default_backend": "fast",
                "adversary": "deep",
            }
        }))
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(cfg_file)
        assert isinstance(cfg.llm.backends["fast"], LLMBackend)
        assert cfg.llm.backends["deep"].model == "openai/qwen35"
        assert cfg.llm.resolve("adversary").model == "openai/qwen35"
        assert cfg.llm.resolve().model == "openai/gemma"

    def test_missing_file_still_works(self):
        """No config file = default LLMConfig (no backends, resolve raises)."""
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(Path("/nonexistent/config.yaml"))
        with pytest.raises(ValueError):
            cfg.llm.resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config_backend_profiles.py::TestWikiConfigLoadMigration -v`
Expected: FAIL — old-style YAML keys are silently ignored by `_merge`, `default_backend` stays "fast", resolve raises

- [ ] **Step 3: Add migration adapter to WikiConfig.load()**

In `src/llm_wiki/config.py`, replace the `load` classmethod (lines 125-133):

```python
    @classmethod
    def load(cls, path: Path) -> "WikiConfig":
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data:
            return cls()
        # Migrate old LLM config keys to new internal names
        if "llm" in data and isinstance(data["llm"], dict):
            llm = data["llm"]
            if "backends" not in llm and "default" in llm:
                llm["_default_model"] = llm.pop("default")
                if "api_base" in llm:
                    llm["_default_api_base"] = llm.pop("api_base")
                if "api_key" in llm:
                    llm["_default_api_key"] = llm.pop("api_key")
        return _merge(cls, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config_backend_profiles.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/llm-wiki
git add src/llm_wiki/config.py tests/test_config_backend_profiles.py
git commit -m "feat: legacy YAML migration adapter in WikiConfig.load()"
```

---

### Task 3: Fix existing config tests for new field names

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Identify broken tests**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config.py -v`
Expected: FAIL on `test_default_config` (accesses `config.llm.default`) and `test_load_from_yaml` (accesses `config.llm.default`) and `test_load_missing_file` (same).

- [ ] **Step 2: Update test_default_config**

Replace the `test_default_config` function in `tests/test_config.py`:

```python
def test_default_config():
    config = WikiConfig()
    # New LLMConfig has no backends by default, so resolve() raises
    with pytest.raises(ValueError):
        config.llm.resolve()
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.llm.default_backend == "fast"
    assert config.search.backend == "tantivy"
    assert config.budgets.default_query == 16000
    assert config.budgets.hard_ceiling_pct == 0.8
    assert config.vault.mode == "vault"
```

- [ ] **Step 3: Update test_load_from_yaml**

Replace the `test_load_from_yaml` function:

```python
def test_load_from_yaml(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "llm:\n"
        "  default: ollama/llama3\n"
        "budgets:\n"
        "  default_query: 8192\n"
        "vault:\n"
        "  mode: managed\n"
    )
    config = WikiConfig.load(config_file)
    # Old-style "default" is migrated to legacy backend
    assert config.llm.backends["default"].model == "ollama/llama3"
    assert config.budgets.default_query == 8192
    assert config.vault.mode == "managed"
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.search.backend == "tantivy"
```

- [ ] **Step 4: Update test_load_missing_file and test_load_empty_file**

Replace `test_load_missing_file`:

```python
def test_load_missing_file():
    config = WikiConfig.load(Path("/nonexistent/config.yaml"))
    # No backends, no legacy — resolve raises
    with pytest.raises(ValueError):
        config.llm.resolve()
```

Replace `test_load_empty_file`:

```python
def test_load_empty_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    config = WikiConfig.load(config_file)
    with pytest.raises(ValueError):
        config.llm.resolve()
```

- [ ] **Step 5: Run all config tests**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_config.py tests/test_config_backend_profiles.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd ~/repos/llm-wiki
git add tests/test_config.py
git commit -m "test: update existing config tests for new LLMConfig field names"
```

---

### Task 4: Update server.py consumer sites to use resolve()

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:84-88, 254-260, 273-279, 289-295, 309-315, 862-868, 903-909`

- [ ] **Step 1: Run full test suite to get baseline**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/ -q --ignore=tests/test_mcp/`
Expected: 573 passed (baseline — all tests use WikiConfig() with no backends, but server.py sites construct LLMClient from flat fields that no longer exist)

Note: if server.py tests fail due to `config.llm.default` / `config.llm.api_base` / `config.llm.api_key` access, that confirms the breakage we're fixing.

- [ ] **Step 2: Update CommitService init (line 84)**

In `src/llm_wiki/daemon/server.py`, replace:

```python
        commit_llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
```

With:

```python
        commit_backend = self._config.llm.resolve("commit")
        commit_llm = LLMClient(
            self._llm_queue,
            model=commit_backend.model,
            api_base=commit_backend.api_base,
            api_key=commit_backend.api_key,
        )
```

- [ ] **Step 3: Update run_librarian (line 254)**

Replace:

```python
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
```

With:

```python
            backend = self._config.llm.resolve("librarian")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
```

- [ ] **Step 4: Update run_authority_recalc (line 273)**

Replace the LLMClient construction with:

```python
            backend = self._config.llm.resolve("librarian")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
            )
```

- [ ] **Step 5: Update run_adversary (line 289)**

Replace with:

```python
            backend = self._config.llm.resolve("adversary")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
            )
```

- [ ] **Step 6: Update run_talk_summary (line 309)**

Replace with:

```python
            backend = self._config.llm.resolve("talk_summary")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
            )
```

- [ ] **Step 7: Update _handle_query (line 862)**

Replace with:

```python
            query_backend = self._config.llm.resolve("query")
            llm = LLMClient(
                self._llm_queue,
                model=query_backend.model,
                api_base=query_backend.api_base,
                api_key=query_backend.api_key,
            )
```

- [ ] **Step 8: Update _handle_ingest (line 903)**

Replace with:

```python
            ingest_backend = self._config.llm.resolve("ingest")
            llm = LLMClient(
                self._llm_queue,
                model=ingest_backend.model,
                api_base=ingest_backend.api_base,
                api_key=ingest_backend.api_key,
            )
```

- [ ] **Step 9: Run full test suite**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/ -q --ignore=tests/test_mcp/`
Expected: ALL PASS — existing daemon tests construct `WikiConfig()` directly (no backends), but the server.py sites that use `self._config.llm` are only hit in integration tests that provide config. The unit tests that instantiate `WikiConfig()` without backends and then call server methods will need the config to have a backend. If any fail, the test fixtures need `_default_model` set.

If tests fail because `WikiConfig()` has no backends:

- [ ] **Step 10: Fix test fixtures that instantiate WikiConfig for server tests**

For any test that creates a `WikiConfig()` and hits a server route that calls `resolve()`, add the legacy model:

```python
config = WikiConfig(
    llm=LLMConfig(_default_model="openai/local-instruct")
)
```

Or, if the test already passes a custom LLMConfig, add `_default_model` to it. Search for `WikiConfig(` in test files and add the legacy field where needed.

- [ ] **Step 11: Run full test suite again**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/ -q`
Expected: ALL PASS (including MCP tests)

- [ ] **Step 12: Commit**

```bash
cd ~/repos/llm-wiki
git add src/llm_wiki/daemon/server.py tests/
git commit -m "feat: switch all 7 LLM consumer sites to config.llm.resolve(role)"
```

---

### Task 5: Initialize ~/wiki vault with backend profiles

**Files:**
- Create: `~/wiki/schema/config.yaml`

- [ ] **Step 1: Create vault directory structure**

```bash
mkdir -p ~/wiki/{raw,wiki,schema}
```

- [ ] **Step 2: Write vault config**

Create `~/wiki/schema/config.yaml`:

```yaml
llm:
  backends:
    fast:
      model: "openai/gemma-4-E4B-it-IQ4_XS.gguf"
      api_base: "http://localhost:8004/v1"
      api_key: "sk-fake"
    deep:
      model: "openai/qwen35-apex-thinking"
      api_base: "http://localhost:4000/v1"
      api_key: "sk-fake"
  default_backend: "fast"
  adversary: "deep"
  ingest: "deep"
  librarian: "deep"
  compliance: "deep"
  query: "fast"
  commit: "fast"
  talk_summary: "fast"
  embeddings: "openai/text-embedding-3-small"
```

- [ ] **Step 3: Initialize vault index**

```bash
llm-wiki init ~/wiki/
```

Expected: Output confirming index built, 0 pages.

- [ ] **Step 4: Verify vault status**

```bash
llm-wiki status --vault ~/wiki/
```

Expected: Shows vault with 0 pages, config loaded.

- [ ] **Step 5: Commit**

```bash
cd ~/wiki
git init
git add -A
git commit -m "init: llm-wiki vault with fast/deep backend profiles"
```

---

### Task 6: Register llm-wiki MCP server in Hermes config

**Files:**
- Modify: `~/.hermes/config.yaml` (add under `mcp_servers`)

- [ ] **Step 1: Add MCP server entry**

Add to `~/.hermes/config.yaml` under the existing `mcp_servers:` block, after `mcp-email`:

```yaml
  llm-wiki:
    command: llm-wiki
    args: ["mcp"]
    env:
      LLM_WIKI_VAULT: "/home/labhund/wiki"
    timeout: 120
    connect_timeout: 30
```

- [ ] **Step 2: Verify MCP server starts**

```bash
LLM_WIKI_VAULT=/home/labhund/wiki llm-wiki mcp &
# Send an MCP initialize request to verify it responds
# Kill the background process after verification
```

Or simpler — check that the command doesn't error:

```bash
LLM_WIKI_VAULT=/home/labhund/wiki timeout 5 llm-wiki mcp 2>&1 || true
```

Expected: No import errors, no crash. The MCP server waits for stdio input (which the timeout kills).

- [ ] **Step 3: Restart Hermes to pick up new MCP server**

This happens on next Hermes session start. No manual action needed — the config change is persistent.

---

### Task 7: End-to-end smoke test

**Files:** None (verification only)

- [ ] **Step 1: Verify wiki_status through MCP**

After Hermes restarts, the `llm-wiki` MCP tools should be available. Verify by running:

```bash
llm-wiki status --vault ~/wiki/
```

Expected: Valid vault status output.

- [ ] **Step 2: Verify search on empty vault**

```bash
llm-wiki search "test" --vault ~/wiki/
```

Expected: Returns empty results (vault is empty), no errors.

- [ ] **Step 3: Verify config loads with backends**

```bash
cd ~/repos/llm-wiki && python -c "
from llm_wiki.config import WikiConfig
from pathlib import Path
cfg = WikiConfig.load(Path('/home/labhund/wiki/schema/config.yaml'))
print('default:', cfg.llm.resolve().model)
print('adversary:', cfg.llm.resolve('adversary').model)
print('commit:', cfg.llm.resolve('commit').model)
"
```

Expected:
```
default: openai/gemma-4-E4B-it-IQ4_XS.gguf
adversary: openai/qwen35-apex-thinking
commit: openai/gemma-4-E4B-it-IQ4_XS.gguf
```

- [ ] **Step 4: Run full test suite one final time**

```bash
cd ~/repos/llm-wiki && python -m pytest tests/ -q
```

Expected: ALL PASS

- [ ] **Step 5: Final commit**

```bash
cd ~/repos/llm-wiki
git add -A
git commit -m "test: full test suite passes with backend profiles + Hermes MCP integration"
```
