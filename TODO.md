# TODO

%% section: urgent-background-agent-resilience-observability %%
## Urgent — CLI Vault Resolution & Silent Daemon Failures

**Discovered:** `llm-wiki status` (and all daemon-dependent CLI commands) default `--vault` to `.` (current working directory). When run from `~` or any non-vault directory, the auto-start path spawns a daemon that crashes immediately — but the crash is invisible because `_get_client` suppresses both stdout and stderr on the child process. The CLI then polls `is_running()` for 30 seconds before timing out with a generic "Daemon failed to start within 30 seconds" error. No information about what actually went wrong.

### Root cause chain

1. User runs `llm-wiki status` from `~` (no `--vault` flag)
2. `_get_client()` resolves vault_path to `/home/labhund`
3. Spawns daemon via `subprocess.Popen(..., stdout=DEVNULL, stderr=DEVNULL)`
4. Daemon calls `Vault.scan("/home/labhund")` which walks the entire home directory tree looking for markdown files
5. Crashes on `IsADirectoryError` when it encounters a directory named `jdk.internal.md` inside mamba envs
6. Socket is never created, so `client.is_running()` always returns False
7. CLI polls for 30s then gives up — user sees zero diagnostic output

### What needs to change

1. **Fail fast on non-vault directories.** `Vault.scan()` should validate the directory has vault structure (at minimum a `schema/config.yaml` or recognizable cluster dirs like `raw/`, `wiki/`) before walking files. If the directory looks like a home directory, bail immediately with: "Path X does not appear to be an llm-wiki vault. Pass --vault <path> or run from inside a vault directory."

2. **Vault.scan() must skip non-file paths.** `Page.parse()` calls `path.read_text()` on anything matching `*.md` in the tree, including directories that happen to end in `.md`. Add `if not md_file.is_file(): continue` before `Page.parse(md_file)`.

3. **Make daemon startup errors visible.** In `_get_client()` (cli/main.py line 34-38), stderr is sent to DEVNULL. Either:
   - Pipe stderr to a temp file and read it on timeout to show the user what went wrong, OR
   - Check the child process exit code after the first few poll iterations — if the process has already exited, read its stderr and report the error immediately instead of waiting the full 30s.

4. **Sensible default vault path.** The CLI should check for `LLM_WIKI_VAULT` env var first, then `~/wiki` as a fallback, then `.` only if neither exists. This matches the MCP config pattern where `LLM_WIKI_VAULT` is already used. With a systemd service managing the daemon, the CLI is just a client — it should find the vault the same way the daemon does.

## Urgent — Background Agent Resilience & Observability

**Discovered:** Daemon runs as persistent systemd service (good), but when the LLM backend (LiteLLM :4000, llama-server :8004) is down or unreachable, the maintenance workers fail silently. There is no retry, no escalation, no health check, and no way to distinguish a transient outage from a code bug from the `maintenance status` output.

%% section: current-behavior-when-api-calls-fail %%
### Current behavior when API calls fail

Call chain: `Scheduler._run_once()` → `agent.run()` → `LLMClient.complete()` → `LLMQueue.submit()` → `litellm.acompletion()`

- **`LLMClient.complete()`** — zero error handling. `litellm.acompletion()` exceptions propagate raw (connection refused, timeout, 500, model not found).
- **`LLMQueue.submit()`** — `try/finally` decrements active count, does NOT catch exceptions.
- **`Scheduler._run_once()` (line 115-124)** — safety net catches all `Exception`, logs with `logger.exception()`, continues. Worker retries on next interval. Does NOT distinguish failure types.
- **`last_run` only updates on success** — `maintenance status` shows last SUCCESSFUL run, not last attempt. A worker silently failing for days is invisible from the status command.
- **No retry logic** — if the API is down during the worker's execution window, it fails and waits 6-24h for next interval.
- **No health check** — workers don't ping the backend before attempting work.
- **No escalation** — failed runs don't create issues, talk posts, or notifications. No alerting mechanism exists.
- **No backoff** — workers fire every interval regardless of repeated failures (wasteful but not harmful).
- **No differentiation** — `logger.exception` catches everything equally: transient API failures, permanent config errors, code bugs.

%% section: what-needs-to-change %%
### What needs to change

1. **Health check before work.** Each LLM-dependent worker should probe its backend (lightweight `/v1/models` or `/health` call) before doing real work. Skip the run if backend is unreachable; log the skip clearly.

2. **Retry with backoff.** Transient API failures (connection refused, 502, timeout) should retry 2-3 times with exponential backoff (e.g., 5s, 15s, 45s) before giving up. Only permanent errors (auth failure, model not found, 400) should fail immediately.

3. **Track last ATTEMPT, not just last success.** `maintenance status` should show both `last_run` (success) and `last_attempt` (including failed). Add `consecutive_failures` counter. This makes silent failures visible.

4. **Escalation on repeated failure.** If a worker fails N consecutive runs (configurable, suggest 3), create a wiki issue or talk post: "Librarian has failed 3 consecutive runs (last error: Connection refused to localhost:4000). Backend may be down." This leverages the existing issue/talk infrastructure.

5. **Structured error logging.** Replace bare `logger.exception` with structured error info: worker name, backend targeted, error type, error message, timestamp. Makes `journalctl` filtering useful: `journalctl -u llm-wiki | grep "consecutive_failures"`.

6. **Circuit breaker (nice-to-have).** After N consecutive failures, skip the worker for a cooldown period (e.g., 1h) instead of attempting every interval. Reset on first successful health check. Prevents log spam when the inference stack is intentionally down.

7. **Daemon status endpoint.** Add a `health` field to the daemon's `status` response that includes: each worker's last success, last attempt, consecutive failures, and whether the backend was reachable at last check. MCP `wiki_status` already returns daemon info — extend it.

%% section: future-ideas %%
## Urgent — Daemon Mutual Exclusion

**Discovered:** No lockfile or pidfile-based mutual exclusion. Multiple `llm-wiki serve` instances for the same vault can start simultaneously. The second one wins the socket bind; the first becomes a zombie holding stale state. Two daemons running against the same vault could race on writes and corrupt the issue queue or session state. The `serve` command and the `_get_client` auto-start path both blindly spawn without checking if a daemon is already running for that vault. Note: multiple vaults on one machine is valid — mutual exclusion is per-vault only (each vault has its own state directory at `~/.llm-wiki/vaults/<hash>/`).

### What needs to change

1. **Per-vault pidfile on startup.** Write `daemon.pid` (containing PID) to the vault's state directory (`~/.llm-wiki/vaults/<hash>/`) on startup. Each vault gets its own pidfile — no global lock needed.
2. **Stale daemon detection.** On startup, check if pidfile exists for this vault. If the PID is alive, refuse to start with a clear error: "Daemon already running for this vault (PID N). Use `llm-wiki stop` or kill the process first." If the PID is dead (stale pidfile from crash), clean up and proceed.
3. **Atomic socket bind.** The socket bind already prevents two daemons from serving on the same socket, but the first daemon doesn't know it's been displaced. The pidfile check should happen BEFORE socket bind.
4. **Clean shutdown removes pidfile.** Already partially handled by `lifecycle.py` but verify the pidfile is removed in all exit paths (SIGTERM, SIGINT, exception).
5. **Test cleanup.** 18 zombie test daemons from pytest runs were found in `/tmp/pytest-*/test_full_workflow*`. Test fixtures should ensure daemon cleanup in teardown, even on test failure.

## Research-Mode Ingest — Design + Implementation

Emerged from live ingest session (2026-04-09). Core insight: attended ingest should be a **research tool that compounds**, not a supervised version of background extraction. Three distinct modes (queue / brief / deep), a persistent plan file format, synthesis claim markers, and eventual claim resonance matching across future ingests.

---

### 1. `inbox/` — New Standard Vault Directory

**What:** A fourth top-level directory alongside `raw/`, `wiki/`, `schema/`. Holds active ingest plan files — mutable, git-tracked, lifecycle-aware.

**Why not `raw/`:** `raw/` is for immutable source copies. Plan files are process artifacts — they get edited, checked off, appended to across sessions. Different nature.

**What needs to change:**
- `llm-wiki init` should create `inbox/` alongside `raw/wiki/schema/`
- `Vault.scan()` should recognise `inbox/` as a known directory (skip it for page scanning)
- Document `inbox/` in vault spec / setup skill

---

### 2. Ingest Plan File Format

**What:** A structured markdown file in `inbox/YYYY-MM-DD-slug-plan.md`, companion to a source in `raw/`. The persistent cursor for a multi-session deep ingest.

**Why a file, not talk pages:** Talk pages are append-only. A plan file is mutable — checkboxes get ticked, decisions get edited, the task list evolves. Committed to git after each session so it survives context compaction and restarts.

**Standard format:**

```markdown
---
source: raw/2026-04-09-silke-proteindj-2026.pdf
started: 2026-04-09
status: in-progress   # or: completed
sessions: 1
---

# ProteinDJ Ingest Plan

## Claims / Ideas
- [x] RFDiffusion — diffusion modes, contigs syntax, noise scale as design knob
- [x] BindCraft — hallucination-based design, induced-fit interfaces
- [ ] ProteinMPNN — sequence design, checkpoint variants, FastRelax integration  ← resume here
- [ ] FamPNN — side-chain iterative fitting, comparison to ProteinMPNN
- [ ] BindSweeper — parameter sweep, multi-dimensional YAML config
- [ ] Boltz-2 vs AF2 — contradicts existing benchmarks page (needs human call)
- [ ] Evaluation claims — success rate figures, flag for adversary

## Decisions
- rfdiffusion: new page (diffusion-models coverage insufficient)
- protein-design-pipelines comparison page: defer until 2+ pipeline sources ingested

## Session Notes

### 2026-04-09
Covered RFDiffusion and BindCraft. Boltz-2 section contradicts benchmarks page —
deferred for human review next session. Pages created: [[rfdiffusion]], [[bindcraft]].
```

**What needs to change:**
- Document this format in `skills/llm-wiki/ingest.md`
- `wiki_lint` should detect inbox/ files with `status: in-progress` and surface them as minor issues ("in-progress ingest: X, started DATE")
- No daemon code needed — this is convention + skill + lint rule

---

### 3. Synthesis Claim Markers

**What:** A `status: synthesis` frontmatter field on wiki pages (or per-claim markers within pages) that marks content as originating from analysis rather than direct source extraction.

**Why it matters:**
- Synthesis claims are the most valuable nodes in the knowledge graph — original thought that exists nowhere else
- They need different treatment from extracted claims: the adversary shouldn't try to verify them against their cited source (the source IS the analysis session); instead it should watch for corroborating sources
- Required infrastructure for claim resonance matching (item 4)

**What needs to change:**

1. **Frontmatter schema** — add `status` field with valid values: `extracted` (default), `synthesis`, `draft`. Document in schema.
2. **Compliance reviewer** — treat `status: synthesis` pages as valid; don't flag missing external citations if status is synthesis. The citation is the analysis session reference.
3. **Adversary** — skip verification pass for synthesis claims. Instead, flag synthesis pages in the resonance matching queue (item 4).
4. **Librarian** — synthesis pages get higher priority for cross-reference building (they're connection points, not endpoints).
5. **`wiki_lint`** — optionally flag synthesis claims older than N months without any resonance talk posts (configurable, default off).

---

### 4. Claim Resonance Matching (Phase 7)

**What:** A post-ingest pipeline step that compares newly ingested source claims against existing wiki claims — weighted toward synthesis claims — and files talk posts where meaningful overlap is found.

**The insight:** RAG cannot do this. The wiki accumulates claims over time. When a new source arrives that touches an existing synthesis claim, that connection should surface automatically — even if the human never thought to look. This is the compounding value made active.

**Direction:**
- Current adversary: `claim → cited source` (verify this claim against the source it came from)
- Resonance matching: `new source → existing claims` (when something new arrives, find what it touches)

**What needs to change:**

1. **New `IngestAgent` post-step** — after page creation, extract key claims from the ingested source and run semantic similarity against existing wiki claims (via the tantivy index + embedding comparison).

2. **Claim index** — claims need to be indexable for this to be efficient at scale. Either:
   - Tag claims with embeddings at write time (store in sidecar JSON)
   - Use the existing tantivy index with semantic search
   - Maintain a separate claim registry (heavier, more accurate)

3. **Resonance threshold** — configurable similarity threshold. Above threshold → file a `resonance` talk post. Start conservative (high threshold) to avoid noise.

4. **Target weighting** — weight matching toward `status: synthesis` claims first, then authority-scored pages, then everything else.

5. **New talk entry type: `resonance`** (item 5).

6. **Config keys:**
   ```yaml
   ingest:
     resonance_matching: true
     resonance_threshold: 0.82
     resonance_target: [synthesis, high_authority]
   ```

---

### 5. New Talk Entry Type: `resonance`

**What:** A distinct talk entry type for claim resonance findings, separate from adversary verdicts, suggestions, and connection notes.

**Format:**
```
type: resonance
source: raw/2026-04-09-new-paper.pdf
relevance: 0.87
note: "New source may corroborate/extend/contradict this claim. Review recommended."
```

**What needs to change:**

1. **`TalkEntry` model** — add `resonance` as a valid type alongside `suggestion`, `new_connection`, `adversary-finding`.
2. **Severity** — default `moderate` (surfaces inline but doesn't demand immediate action).
3. **`wiki_read` digest** — resonance entries appear in the inline talk digest when reading a page. Same surface as other talk entries.
4. **`wiki_lint`** — flag unreviewed resonance entries older than N weeks (configurable).
5. **Resolution path** — human reviews, either: promotes synthesis to main claim with the new source as corroborating citation, or dismisses the resonance as a false match.

---

### 6. PDF Extraction Pipeline — Configurable `pdf_extractor`

**What:** A config key controlling which tool is used to extract text from PDFs before ingest. Current default (`pdftotext`) is insufficient for academic papers with tables, figures, equations.

**Reference:** Qianfan-OCR (Abiray/Qianfan-OCR-GGUF on HuggingFace) — 4B VLM, Q4_K_M at 2.72GB, runs via llama.cpp on CPU/GPU. Layout-as-Thought approach: image→markdown in a single model pass.

**What needs to change:**

1. **Config schema** — add to `schema/config.yaml`:
   ```yaml
   ingest:
     pdf_extractor: pdftotext   # pdftotext | local-ocr | marker | nougat
     local_ocr_endpoint: "http://localhost:8006/v1"   # for local-ocr
     local_ocr_model: "qianfan-ocr"
   ```

2. **`extractor.py`** — dispatch to correct tool based on config:
   - `pdftotext`: current path (subprocess call)
   - `local-ocr`: send PDF page images to vision endpoint, collect markdown
   - `marker`: call marker API/subprocess
   - `nougat`: call nougat subprocess

3. **Extraction quality signal** — when extracted text appears mangled (heuristics: repeated short lines, excessive whitespace, low word/line ratio), emit a warning in the ingest response so the skill can flag it to the user before writing any pages.

4. **raw/ format note** — for PDFs, store the original file in `raw/` as `.pdf` (canonical), not just the extracted text. The `.pdf` is the immutable source; the extracted text is agent food.

---

### 7. Attended Ingest Skill Rewrite

**What:** Replace the current two-path (conversational / automated) attended ingest with three distinct modes that reflect actual use cases.

**Current problem:** "Conversational" mode is just automated extraction with a human watching. Both paths produce the same output (extracted pages). The attended agent's unique value — user context, memory, wiki knowledge, synthesis capability — is unused.

**Three modes:**

**Queue** — "I have a paper, ingest it, I don't have opinions."
- Agent copies source to `raw/`, hands off to `wiki_ingest` (background)
- Reports what was created
- No deep engagement required

**Brief** — "Read it for me with my context. Tell me what matters."
- Agent reads source with user memory + wiki context loaded
- Produces a briefing: what's genuinely new to *you*, what's already covered, what contradicts existing pages, which bits warrant reading yourself
- User decides: queue the rest / go deeper on specific claims / do nothing
- The briefing IS the value — not page creation

**Deep** — "Let's think through this together, claim by claim."
- Agent creates inbox plan file (item 2) before touching any wiki tool
- Task list = claims/ideas (not pages)
- Per-claim loop: agent analysis → human reaction → scratchpad (talk post on relevant page) → decision (create page now / defer / talk post only / skip)
- Session ends with checkpoint written to plan file, plan committed to git
- Multi-session: next session reads plan file, reconstructs task list, resumes

**What needs to change:**
- `skills/llm-wiki/ingest.md` — full rewrite of Act 2 and Act 3
- `skills/llm-wiki/autonomous/ingest.md` — unchanged (autonomous = queue mode only)
- Document the inbox plan file convention as part of deep mode

---

## Future Ideas

- **Vault → Managed migration tool**: Pre-packaged workflow that lets 4-8 local LLMs loose on an unstructured Obsidian vault over days/weeks to reorganize it into managed structure (raw sources separated, index built, cross-references added, provenance established). Automated "get it ship shape" pipeline.
