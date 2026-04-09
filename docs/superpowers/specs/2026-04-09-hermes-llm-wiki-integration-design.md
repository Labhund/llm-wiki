# Hermes + llm-wiki Integration

**Date:** 2026-04-09
**Status:** Draft
**Repo:** ~/repos/llm-wiki

## Scope

Two deliverables:

1. **llm-wiki code change** — backend profiles + per-task model routing in `LLMConfig`, updating all 7 LLM consumer sites in `server.py`
2. **Hermes integration** — register llm-wiki MCP server in Hermes config, init `~/wiki/` vault with proper config, end-to-end smoke test

Out of scope: Hermes skills (handled separately in Claude Code), content migration (empty vault start).

## Backend Profiles in LLMConfig

Replace the current flat `LLMConfig` with a two-tier structure:

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
            # Convert raw dicts to LLMBackend instances
            self.backends = {
                name: LLMBackend.from_dict(v) if isinstance(v, dict) else v
                for name, v in self.backends.items()
            }
        elif self._default_model:
            # Legacy mode: synthesize a single "default" backend from flat fields
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

**Key design decisions:**

- `default_backend` (not `default`) avoids semantic collision with the old model-string field. Old configs used `default: "openai/local-instruct"` — if we kept `default` as the field name, a legacy config would try to look up a backend named `"openai/local-instruct"` and KeyError.
- Legacy fields are prefixed with `_` and excluded from repr. They exist only for the `__post_init__` adapter path. YAML files should never set them directly.
- `__post_init__` handles both the dict-to-dataclass conversion for new configs AND the legacy synthesis for old configs. This is the single adaptation point — `_merge` stays untouched.
- `resolve()` uses `getattr(self, role, None)` with a default to avoid AttributeError on typos. If the backend name isn't in the dict, falls back to `default_backend`. If that's also missing, raises ValueError with available backends listed.
- `WikiConfig.load()` needs a small migration adapter: old YAML has `default`, `api_base`, `api_key` under `llm:`, but the new fields are `_default_model`, `_default_api_base`, `_default_api_key`. The `_merge` function maps YAML keys to dataclass field names, so old keys would be silently ignored. The adapter detects old-style LLM config (no `backends` key + `default` key present) and renames them before `_merge` runs:

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

## Vault Config at ~/wiki/schema/config.yaml

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

### Model Assignment Rationale

| Role | Backend | Why |
|------|---------|-----|
| default | fast (Gemma 4 E4B) | Always-on baseline |
| traversal | fast | High volume, low reasoning need |
| commit | fast | One-liner summaries, zero stakes |
| talk_summary | fast | Summarizing talk entries, straightforward |
| librarian | deep (35B thinking) | Tag refinement, authority scoring benefits from depth |
| compliance | deep | Heuristic but benefits from nuance |
| adversary | deep | Claim verification is the hardest reasoning task |
| ingest | deep | Concept extraction from papers, wants comprehension |

## Updating server.py Consumer Sites

All 7 sites in `server.py` that create `LLMClient` change from:

```python
llm = LLMClient(
    self._llm_queue,
    model=self._config.llm.default,
    api_base=self._config.llm.api_base,
    api_key=self._config.llm.api_key,
)
```

To:

```python
backend = self._config.llm.resolve("adversary")  # or "librarian", etc.
llm = LLMClient(
    self._llm_queue,
    model=backend.model,
    api_base=backend.api_base,
    api_key=backend.api_key,
)
```

The 7 sites and their roles:

| Site | Role | Line |
|------|------|------|
| CommitService init | commit | 84 |
| run_librarian | librarian | 255 |
| run_authority_recalc | librarian | 274 |
| run_adversary | adversary | 290 |
| run_talk_summary | talk_summary | 310 |
| _handle_query | query | 863 |
| _handle_ingest | ingest | 904 |

Note: `authority_recalc` and `librarian` share the same `LibrarianAgent` but can use the same role. If separate control is needed later, add a `authority` role.

## Hermes MCP Registration

Add to `~/.hermes/config.yaml` under `mcp_servers`:

```yaml
llm-wiki:
  command: llm-wiki
  args: ["mcp"]
  env:
    LLM_WIKI_VAULT: "/home/labhund/wiki"
  timeout: 120
  connect_timeout: 30
```

The MCP server auto-starts the daemon on first connect. Hermes gets 17 tools: wiki_search, wiki_read, wiki_manifest, wiki_status, wiki_query, wiki_ingest, wiki_lint, wiki_create, wiki_update, wiki_append, wiki_issues_list, wiki_issues_get, wiki_issues_resolve, wiki_talk_read, wiki_talk_post, wiki_talk_list, wiki_session_close.

## Vault Initialization

1. Create vault directory structure:
   ```
   mkdir -p ~/wiki/{raw,wiki,schema}
   ```
2. Write `~/wiki/schema/config.yaml` with the backend profiles (see above). This must happen before the daemon starts — the daemon loads config from `<vault>/schema/config.yaml` on startup.
3. Run `llm-wiki init ~/wiki/` to build the tantivy index and verify the vault is valid.

## Verification

1. All existing tests still pass (backward compat — no config file = legacy defaults work)
2. New unit tests for `LLMConfig`:
   - `resolve()` with explicit role override → returns correct backend
   - `resolve()` with no role override → falls back to `default_backend`
   - `resolve()` with unknown role string → falls back to `default_backend` (no crash)
   - `resolve()` with backend name not in dict → raises `ValueError` with available backends
   - `__post_init__` converts raw dicts to `LLMBackend` instances
   - Legacy mode: no `backends` + `_default_model` set → synthesizes "default" backend
   - Legacy mode: no `backends` + no `_default_model` → empty backends dict, `resolve()` raises
   - Legacy YAML migration: `WikiConfig.load()` with old-style `default`/`api_base`/`api_key` → populates `_default_*` fields, synthesizes backend
3. Integration: `llm-wiki status --vault ~/wiki` returns valid vault state with new config
4. MCP: server starts and responds to `wiki_status` through Hermes
