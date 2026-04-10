---
title: Adversary Idle Guard + raw_dir Config Threading
date: 2026-04-10
status: approved
---

# Adversary Idle Guard

## Motivation

A single paper ingest costs ~$AUD 0.10 on a mid-range model. Background maintenance
workers run on configurable intervals (default: hourly). Users who are not actively
ingesting — students on small API budgets, anyone on a paid tier between research
sessions — should not be charged for workers doing nothing useful.

LLM Wiki presents local model options precisely because cost matters. The daemon must
be safe to leave running without burning through a budget.

## Which Workers Actually Waste Money

Four of the five background workers are already well-guarded:

- **Auditor** — no LLM calls at all; pure structural checks
- **Librarian** — LLM only fires for pages above the `manifest_refresh_after_traversals`
  read-count threshold; a fresh or idle vault produces zero LLM calls
- **Talk summary** — gated on `new_unresolved >= threshold` AND minimum elapsed time;
  silent when talk pages have no new entries
- **Authority recalc** — no LLM calls; pure graph computation

The **adversary** is the problem. It samples and verifies claims against raw sources on
every interval regardless of whether the vault has changed since the last run. On a
stable vault it makes `adversary_claims_per_run` LLM calls every hour, every day, for
no new information.

## Solution: Vault Modification Guard

Before sampling claims, `AdversaryAgent.run()` checks whether any file in `wiki/` or
`raw/` has been modified since the last successful adversary run. If nothing is newer,
return immediately — zero LLM calls.

```
AdversaryAgent.run()
  ├── entries empty? → return  (existing guard)
  ├── vault unchanged since last run? → return  (new)
  ├── extract claims
  ├── all_claims empty? → return  (existing guard)
  ├── sample + verify (LLM calls happen here)
  └── record last-run timestamp
```

### Vault unchanged check

```python
def _vault_unchanged_since_last_run(self) -> bool:
    ts = self._load_last_run_ts()   # None on first run
    if ts is None:
        return False                 # always run on first invocation
    wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
    raw_dir  = self._vault_root / self._config.vault.raw_dir.rstrip("/")
    for search_dir in (wiki_dir, raw_dir):
        if not search_dir.exists():
            continue
        for f in search_dir.rglob("*"):
            if f.is_file() and f.stat().st_mtime > ts:
                return False
    return True
```

The timestamp is stored as a float (Unix epoch) in
`{state_dir}/adversary_last_run.txt`. Written atomically after a successful run.

### Force-recheck

A vault can go untouched for months. The adversary's purpose includes periodic
re-verification of existing claims even on stable content — sources can be retracted,
prior LLM assessments may have been wrong. A guard that fires indefinitely on a static
vault defeats this.

Config: `maintenance.adversary_force_recheck_days` (default: `30`). If this many days
have elapsed since the last adversary run, the mtime check is bypassed and the adversary
runs regardless. This is a single sampling pass (`adversary_claims_per_run` claims),
not a full-coverage sweep — the vault will be sampled again the next time the interval
fires, whether or not the mtime changes.

```python
def _vault_unchanged_since_last_run(self) -> bool:
    ts = self._load_last_run_ts()
    if ts is None:
        return False
    # Bypass guard if force-recheck window has elapsed
    force_days = self._config.maintenance.adversary_force_recheck_days
    if (time.time() - ts) > force_days * 86400:
        return False
    # mtime scan
    ...
```

## Bonus Fix: raw_dir Config Threading

`config.vault.raw_dir` exists (default `"raw/"`) but two places hardcode `"raw"`:

1. `claim_extractor.py:22` — regex `r"\[\[(raw/[^\]|]+)..."` — only matches claims
   citing the hardcoded prefix. If `raw_dir` is configured to anything else, no claims
   are extracted and the adversary silently does nothing.

2. `adversary/agent.py:95` — `raw_dir = self._vault_root / "raw"` — unread-sources
   upweighting scans the wrong directory if raw_dir is configured differently.

3. `adversary/agent.py:102–106` — **same bug, harder to spot.** After finding the
   raw dir, the code builds `unread_sources` keys as `f"raw/{md_file.name}"` and
   `f"raw/{binary.name}"`. These keys are compared against `claim.citation` values,
   which come from the regex match and carry whatever prefix is in the wiki content.
   If raw_dir is configured to e.g. `"sources"`, the regex fix (item 1) will match
   `[[sources/foo.pdf]]` and `claim.citation` will be `"sources/foo.pdf"` — but the
   unread_sources keys are still `"raw/foo.pdf"`. The upweighting silently stops
   working. Same class of silent failure as item 1, different line.

Fix: derive a `raw_prefix` string from config (e.g. `raw_prefix = config.vault.raw_dir.rstrip("/")`)
and substitute it everywhere `"raw"` appears as a string prefix in `agent.py`. Pass
`raw_dir: str` to `extract_claims()` and use it to build the regex dynamically.

```python
# claim_extractor.py
def extract_claims(page: "Page", raw_dir: str = "raw") -> list[Claim]:
    prefix = re.escape(raw_dir.rstrip("/"))
    pattern = re.compile(
        rf"\[\[({prefix}/[^\]|]+)(?:\|[^\]]+)?\]\]\s*[.!?]?\s*$"
    )
    ...
```

Every call site passes `config.vault.raw_dir.rstrip("/")`. The default preserves
backward compatibility for callers that don't have config available.

## Config Changes

```yaml
maintenance:
  adversary_force_recheck_days: 30   # bypass mtime guard after this many idle days
```

No changes to `vault.raw_dir` — it already exists, just not wired up.

## Files Changed

| File | Change |
|------|--------|
| `src/llm_wiki/adversary/agent.py` | `_vault_unchanged_since_last_run()`, `_load_last_run_ts()`, `_record_last_run_ts()`; thread `raw_dir` from config |
| `src/llm_wiki/adversary/claim_extractor.py` | `raw_dir` param on `extract_claims()`, dynamic regex |
| `src/llm_wiki/config.py` | Add `adversary_force_recheck_days: int = 30` to `MaintenanceConfig` |

No scheduler changes. The guard lives in the agent — it knows what "nothing to do"
means for its own domain.

## Testing

Unit:
- `_vault_unchanged_since_last_run()` returns `False` on first run (no stored timestamp)
- Returns `True` when no files modified since stored timestamp
- Returns `False` when a wiki file mtime is newer than stored timestamp
- Returns `False` when force-recheck window has elapsed, even with no new files
- `extract_claims()` with non-default `raw_dir` matches citations correctly and ignores `[[raw/...]]` if that's not the configured prefix

Integration:
- Adversary run on stable vault → result has `claims_checked = 0`, no LLM calls made
- Adversary run after a wiki file is touched → guard does not fire, sampling proceeds
- Adversary run after `force_recheck_days` elapsed → guard bypassed, sampling proceeds

## What This Does Not Change

- The per-claim verification tracking gap (claim IDs still keyed by `page|section|text`,
  `last_corroborated` still per-page) — tracked in
  `docs/implementation-ideas/11-semantic-claim-identity.md`. If a `ClaimVerificationStore`
  is added in a future phase, its "no stale claims" early-exit will be a strictly better
  guard than the mtime check here; the mtime guard can be removed at that point.
- Librarian, talk summary, auditor — already adequately guarded
- The adversary still verifies `adversary_claims_per_run` claims per run when it does fire
- Other raw_dir hardcodings outside the adversary path (`daemon/server.py`, `audit/checks.py`,
  `vault.py`, `cli/main.py`) — out of scope here, tracked as follow-on work
