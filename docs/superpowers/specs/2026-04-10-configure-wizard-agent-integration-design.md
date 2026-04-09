# Configure Wizard — Model Context + Agent Framework Integration

**Date:** 2026-04-10  
**Status:** Approved  
**Scope:** Two improvements to `llm-wiki configure`: (1) model picker UX, (2) agent framework section (Hermes + Claude Code)

---

## Problem

The current `configure` wizard has two gaps:

1. **Model picker gives no context.** The user sees "Select model:" with no explanation that llm-wiki routes tasks to two tiers (smart/fast) or what each tier is used for. "Other (type manually)" drops them into a bare prompt with no format hint.

2. **No path to agent framework integration.** After configuring LLM backends, the user still has to manually register the MCP server and install companion skills. For Hermes users this was a multi-step manual process (Sakura session: 7 phases, 25 steps). Should be zero-friction.

---

## Design

### Part 1 — Model Picker UX

Add a framing paragraph before any model selection:

> *llm-wiki routes tasks across two model tiers. Your **smart model** handles depth work — research queries, document ingestion, adversarial fact-checking. Your **fast model** handles high-frequency background tasks — librarian, compliance, commit summaries — where throughput matters more than reasoning depth. You can use the same model for both.*

Prompt labels change:
- `"Select model:"` → `"Choose your smart model:"`
- Fast model prompt already has good context; minor wording tightening only.

When user selects "other (type manually)", show format hint before the prompt:
```
  LiteLLM format examples:
    openai/gpt-4o           (OpenAI)
    anthropic/claude-haiku-4-5  (Anthropic)
    openrouter/google/gemini-2.5-pro
    openai/my-local-model   (local endpoint)
```

---

### Part 2 — Agent Framework Section

New final section in `run_wizard()`, after embeddings. Header: **"Agent Framework Integration"**.

```
Which agent framework are you using?

→ Hermes
  Claude Code
  Skip (I'll register manually)
```

#### If Hermes

**Detect home:**
- Check `HERMES_HOME` env var, then `~/.hermes`. Confirm path with user.

**Vault setup:**
- Prompt for vault path (default: `LLM_WIKI_VAULT` env var, then `~/wiki`).
- If vault dirs missing: create `raw/`, `wiki/`, `schema/`, `inbox/`.
- Run `Vault.scan(vault_path)` (same as `llm-wiki init`) automatically — no separate CLI call needed.

**Skill installation:**
- Source: `skills/llm-wiki/` directory from the installed package (resolved via `importlib.resources` or `__file__`-relative path from `src/llm_wiki/`).
- Mapping: skill `name` field uses slash notation (`llm-wiki/research`) → maps to `<hermes_home>/skills/llm-wiki/research/SKILL.md`. Top-level skill (`name: llm-wiki`) → `<hermes_home>/skills/llm-wiki/SKILL.md`.
- Overwrite existing files (idempotent).
- Compute MD5 of each written file; append/update entries in `<hermes_home>/skills/.bundled_manifest` (format: `skillname:hash` one per line).

**Legacy skill patching:**
- Scan `<hermes_home>/skills/research/` for dirs matching `llm-wiki*` that do NOT already contain the MCP supersession banner.
- Prepend banner after frontmatter:
  ```markdown
  > **MCP supersedes this skill.** If `wiki_search`, `wiki_read`, `wiki_query` tools are
  > available (llm-wiki MCP server connected), use those instead. This skill is retained
  > as conceptual reference only.
  ```
- Only patch; never delete.

**MCP registration:**
- Load `<hermes_home>/config.yaml`.
- Merge under `mcp_servers:`:
  ```yaml
  llm-wiki:
    command: llm-wiki
    args:
      - mcp
    env:
      LLM_WIKI_VAULT: "<vault_path>"
    timeout: 120
    connect_timeout: 30
  ```
- Write back with `yaml.dump(..., sort_keys=False)` to preserve key order.

**Config check:**
- If `<vault>/schema/config.yaml` missing or empty: print warning:
  > "No LLM backend configured. Run `llm-wiki configure` in an interactive session to set up your models before starting the daemon."

**Final message:**
> "Restart Hermes to load the new skills."

---

#### If Claude Code

**Vault setup:** same as Hermes (prompt, create dirs, run `Vault.scan`).

**MCP registration:**
- Target file: `.claude/mcp.json` in the current working directory (project-local), OR `~/.claude/mcp.json` (global) — ask user which.
- Merge entry:
  ```json
  {
    "mcpServers": {
      "llm-wiki": {
        "command": "llm-wiki",
        "args": ["mcp"],
        "env": {
          "LLM_WIKI_VAULT": "<vault_path>"
        }
      }
    }
  }
  ```
- If file doesn't exist: create it. If it exists: merge `mcpServers` key without touching other keys.

**No skill installation** — Claude Code uses the MCP tool surface directly; no separate skill files needed.

**Config check:** same warning as Hermes if no `schema/config.yaml`.

**Final message:**
> "Reload Claude Code (or restart your IDE) to connect the MCP server."

---

#### If Skip

Print the manual MCP snippet for both Hermes and Claude Code formats, vault path placeholder included.

---

### Summary Screen

After agent framework section, summary shows all configured items:

```
  ✓ Smart model  (anthropic/claude-sonnet-4-6)
  ✓ Fast model   (openai/gpt-4o-mini)
  ✓ Embeddings   (openai/text-embedding-3-small)
  ✓ Hermes MCP   registered + 10 skills installed
  ⚠ LLM config  not yet written — run llm-wiki configure
```

---

## File Changes

| File | Change |
|------|--------|
| `src/llm_wiki/cli/configure.py` | Add model tier framing text; fix "other" hint; add `_setup_agent_framework()` section; update summary |
| `skills/llm-wiki/` | No changes — existing skill files are the install source |

No new dependencies. `importlib.resources` and `yaml` are already present. Vault init reuses `Vault.scan()`.

---

## Out of Scope

- OpenClaw wizard integration (later)
- Daemon systemd service setup (stays agent-guided with explicit consent)
- Non-Hermes agent frameworks beyond Claude Code

---

## Open Questions

None — scope is closed.
