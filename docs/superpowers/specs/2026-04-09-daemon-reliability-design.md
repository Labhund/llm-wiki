# Daemon Reliability — Design Spec

**Date:** 2026-04-09
**Status:** Draft — pending implementation plan
**Author:** Markus Williams

---

## Overview

Three urgent bugs discovered in live operation, grouped into one phase:

1. **CLI vault resolution & silent daemon failures** — wrong default vault path, `Vault.scan()` crashes on directories named `*.md`, daemon startup errors are swallowed
2. **Background agent resilience & observability** — LLM-dependent workers fail silently, no retry, no health check, no visible failure state
3. **Daemon mutual exclusion** — no pidfile; two daemons can race on the same vault

Implementation is split into two groups by code area: startup/init first, runtime second.

---

## Group 1 — Startup/Init Reliability

### `vault.py` — `Vault.scan()`

Two fixes:

1. **Skip non-file paths.** Before calling `Page.parse(md_file)`, add `if not md_file.is_file(): continue`. This is the direct cause of the `IsADirectoryError` crash when walking home directories that contain directories named `*.md` (e.g. inside mamba envs).

2. **Validate vault structure before walking.** At the start of `Vault.scan()`, check that the target path looks like a vault: `schema/config.yaml` exists, or at least one of `raw/`, `wiki/` exists. If neither check passes, raise immediately with:
   > "Path X does not appear to be an llm-wiki vault. Pass --vault <path> or run from inside a vault directory."
   This is a two-check guard on entry — not a deep scan.

### `cli/main.py` — `_get_client()` and default vault path

Two fixes:

1. **Default vault resolution.** Change the default path resolution order to:
   - `LLM_WIKI_VAULT` env var (already used by MCP config)
   - `~/wiki` as fallback
   - `.` (current directory) only if neither exists

2. **Daemon startup error visibility.** Replace `stderr=DEVNULL` with `stderr=<NamedTemporaryFile>`. After each poll iteration, check `proc.poll() is not None` — if the child has already exited, read the temp file and report the error immediately rather than waiting the full 30-second timeout.

### `lifecycle.py` — Per-vault pidfile (mutual exclusion)

Pidfile path: `~/.llm-wiki/vaults/<vault_hash>/daemon.pid` (one pidfile per vault; multiple vaults on one machine is valid).

**On startup (before socket bind):**
- If pidfile exists: call `os.kill(pid, 0)` to check liveness
  - Process alive → refuse with `"Daemon already running for this vault (PID N). Use llm-wiki stop or kill the process first."`
  - Process dead (stale pidfile) → remove pidfile and proceed
- Write pidfile with current PID

**On shutdown:** Remove pidfile in all exit paths — SIGTERM handler, SIGINT handler, and `finally` block. Verify all paths are covered.

**Test fixtures:** Teardown must remove pidfile after daemon stop, even on test failure. This also fixes the 18 zombie test daemons found in `/tmp/pytest-*/`.

---

## Group 2 — Runtime Resilience & Observability

### `LLMClient` — Retry with backoff

Wrap `litellm.acompletion()` in a retry loop inside `LLMClient.complete()`:

- **3 attempts max**
- **Exponential backoff:** 5s → 15s → 45s between retries
- **Retry on:** transient errors — connection refused, timeout, HTTP 502/503/504
- **Fail immediately on:** permanent errors — HTTP 401, 403, 400, model not found

One change point; all workers and MCP tool handlers benefit automatically.

### `Scheduler` / worker base class — Health probe + failure tracking

**Health probe:** Before each worker run, probe the configured backend with a lightweight request (e.g. `GET /v1/models`). If unreachable, log `"[worker-name] backend unreachable, skipping run"` and return without incrementing `consecutive_failures`. A skipped run is not a failure.

**Failure tracking:** Each worker gains two new state fields:
- `last_attempt`: timestamp of every run attempt (success or skip or failure) — currently missing; `maintenance status` only shows `last_run`
- `consecutive_failures`: int; reset to 0 on success; incremented on failure (not on skip)

`last_run` (last success) semantics are unchanged.

### `Scheduler` — Escalation on repeated failure

When `consecutive_failures` reaches the configured threshold (default: 3), file a wiki issue via the existing issue queue:
- **Severity:** `moderate`
- **Title:** `"[worker-name] has failed N consecutive runs"`
- **Body:** last error type, last error message, last attempt timestamp

Filed **once per threshold crossing**, not on every subsequent failure (avoids issue queue flooding). Resets — and the issue is auto-resolved — when the worker next succeeds.

Threshold is configurable in `schema/config.yaml` under `maintenance.failure_escalation_threshold`.

### Daemon status response — `health` field

Extend the `wiki_status` MCP tool response and `llm-wiki maintenance status` CLI output with a `health` key. Per-worker structure:

```json
{
  "health": {
    "librarian": {
      "last_run": "2026-04-09T10:00:00Z",
      "last_attempt": "2026-04-09T14:00:00Z",
      "consecutive_failures": 2,
      "backend_reachable": false
    },
    ...
  }
}
```

This surfaces all the information an operator needs to diagnose silent failures without tailing logs.

---

## What Is Not In This Spec

- **Circuit breaker** — marked nice-to-have in TODO; excluded. Retry-with-backoff covers 90% of the value with a fraction of the complexity.
- **Structured log field changes** — the escalation issue carries structured error info; separate log format changes are not needed.
- **Source reading status, research-mode ingest, skills** — separate phases.

---

## Files Touched

| File | Change |
|------|--------|
| `src/llm_wiki/vault.py` | Vault validation guard + skip non-file paths |
| `src/llm_wiki/cli/main.py` | Default vault path order + stderr capture |
| `src/llm_wiki/daemon/lifecycle.py` | Per-vault pidfile, startup check, shutdown cleanup |
| `src/llm_wiki/llm/client.py` | Retry with backoff |
| `src/llm_wiki/daemon/scheduler.py` | Health probe, failure tracking, escalation |
| `src/llm_wiki/daemon/workers/*.py` | Surface `last_attempt` + `consecutive_failures` per worker |
| `src/llm_wiki/mcp/tools/status.py` | Add `health` field to status response |
| `tests/` | Fixture teardown — pidfile cleanup on test failure |
