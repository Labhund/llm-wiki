---
name: llm-wiki-setup
description: "One-time integration: set up the llm-wiki daemon, configure backend profiles, register the MCP server with an agent framework, and handle conflicts with pre-MCP wiki skills. Triggers: 'set up llm wiki', 'integrate llm-wiki', 'configure wiki MCP', 'register wiki server'."
version: 1.0.0
---

# llm-wiki MCP Integration

One-time setup to connect the llm-wiki daemon to an agent framework via MCP.

This skill runs ONCE. After integration, wiki operations use the MCP tools directly.

---

## Prerequisites

Before starting, verify ALL of these:

1. **`llm-wiki` CLI installed** — `which llm-wiki` returns a path
2. **Inference backends running** — at minimum one LLM endpoint accessible
3. **Agent config writable** — wherever your agent registers MCP servers
4. **A vault location chosen** — default `~/wiki`

If any prerequisite is missing, tell the user what's needed and stop.

---

## The Skill Conflict

If the agent framework (e.g., Hermes) ships with pre-MCP wiki skills that assume raw file operations (read_file/write_file/search_files), these will contradict the MCP tool surface. Agents get mixed signals — old skills say "use search_files" while MCP provides wiki_search with manifest metadata, session management, and compliance review.

**Resolution:** After MCP registration, the old skills must be patched to detect MCP availability and delegate. See Step 5.

---

## Integration Procedure

### Step 1: Create Vault

```bash
mkdir -p ~/wiki/{raw,wiki,schema}
```

- `raw/` — immutable source material (papers, articles, transcripts)
- `wiki/` — compiled wiki pages (daemon-owned)
- `schema/` — configuration, prompts, agent definitions

### Step 2: Write Vault Config

Write `~/wiki/schema/config.yaml` with backend profiles and per-task routing:

```yaml
llm:
  backends:
    fast:
      model: "<lightweight-model>"
      api_base: "http://localhost:8004/v1"
      api_key: "sk-fake"
    deep:
      model: "<reasoning-model>"
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

vault:
  mode: "managed"
  raw_dir: "raw/"
  wiki_dir: "wiki/"
  watch: true

maintenance:
  librarian_interval: "6h"
  adversary_interval: "12h"
  adversary_claims_per_run: 5
  auditor_interval: "24h"
  authority_recalc: "12h"
  talk_pages_enabled: true
```

**Backend assignment rationale:**

| Role | Backend | Why |
|------|---------|-----|
| default | fast | Always-on baseline |
| query | fast | High volume, low reasoning need |
| commit | fast | One-liner summaries, zero stakes |
| talk_summary | fast | Summarizing talk entries, straightforward |
| librarian | deep | Tag refinement, authority scoring benefits from depth |
| compliance | deep | Heuristic but benefits from nuance |
| adversary | deep | Claim verification is the hardest reasoning task |
| ingest | deep | Concept extraction from papers, wants comprehension |

**Adjust model names and ports to match the user's actual inference setup.** The config above is a template.

### Step 3: Initialize Index

```bash
llm-wiki init ~/wiki/
```

Builds the tantivy search index and verifies vault structure. Must run after config is written but before the daemon starts.

### Step 4: Register MCP Server

**Hermes** — add to `~/.hermes/config.yaml` under `mcp_servers:`:

```yaml
  llm-wiki:
    command: llm-wiki
    args:
      - mcp
    env:
      LLM_WIKI_VAULT: "/home/<user>/wiki"
    timeout: 120
    connect_timeout: 30
```

**Claude Code** — add to `.claude/settings.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "llm-wiki": {
      "command": "llm-wiki",
      "args": ["mcp"],
      "env": {
        "LLM_WIKI_VAULT": "/home/<user>/wiki"
      }
    }
  }
}
```

**Other frameworks** — register a stdio MCP server with command `llm-wiki mcp` and env `LLM_WIKI_VAULT` pointing at the vault.

After registration, the agent gets 17 tools: `wiki_search`, `wiki_read`, `wiki_manifest`, `wiki_status`, `wiki_query`, `wiki_ingest`, `wiki_lint`, `wiki_create`, `wiki_update`, `wiki_append`, `wiki_issues_list`, `wiki_issues_get`, `wiki_issues_resolve`, `wiki_talk_read`, `wiki_talk_post`, `wiki_talk_list`, `wiki_session_close`.

### Step 5: Patch Pre-MCP Skills

If the agent framework has existing wiki skills based on raw file operations, add a routing banner after the frontmatter of each:

**Operational skills (llm-wiki, llm-wiki-traversal):**

```markdown
> **MCP supersedes this skill.** If wiki_search, wiki_read, wiki_query tools are
> available (llm-wiki MCP server connected), use those instead of ALL manual
> operations below. The MCP tools provide session management, compliance review,
> talk pages, issue tracking, and the daemon's traversal engine. This skill is
> retained as conceptual reference only.
```

**Conceptual skills (llm-wiki-architecture):**

```markdown
> **Note:** The architecture described here (Librarian/Worker, railroading, citation
> discipline) is now implemented in the llm-wiki daemon. If the MCP server is
> connected, these patterns are handled automatically by the daemon's agent roles.
> This document remains useful as conceptual background.
```

**Do not delete old skills.** Patch them. They're useful as conceptual reference and fallback if the MCP server goes down.

### Step 6: Verify

Run these checks in order:

1. **Vault status** — `llm-wiki status --vault ~/wiki` returns valid state
2. **MCP tools available** — call `wiki_status`, expect a response (daemon auto-starts on first connect)
3. **Search works** — `wiki_search("test")` returns results (empty is fine, just no errors)
4. **Ingest dry-run** — `wiki_ingest(source_path="/tmp/test.md", dry_run=True)` to verify the full pipeline without writing

If MCP connection fails, check:
- `llm-wiki` is on PATH
- `LLM_WIKI_VAULT` env var matches the actual vault path
- Agent config YAML/JSON is syntactically valid

### Step 7: Report

Tell the user:
- Vault location and structure
- Which backends are configured
- Which MCP tools are now available (list the 17)
- That old skills have been patched with MCP routing banners
- Suggest first actions: ingest a paper, run `wiki_lint`, or try a `wiki_query`

---

## Pitfalls

- **Config must exist before daemon starts.** The daemon loads `schema/config.yaml` on startup. Missing config → falls back to defaults which likely won't match the user's inference setup.
- **LLM_WIKI_VAULT must be an absolute path.** Relative paths won't resolve correctly when the daemon starts from the agent's working directory.
- **Port conflicts.** The ports in the config template are defaults. Always confirm the user's actual inference endpoints.
- **First MCP connect is slow.** The daemon starts on first connect — importing litellm, building the tantivy index. 30+ seconds on first call is normal. The 120s timeout handles this.
- **Backend model strings must match litellm format.** For local servers behind LiteLLM proxy, use `openai/<model-name>`. For direct llama-server, check what the `/v1/models` endpoint returns.
