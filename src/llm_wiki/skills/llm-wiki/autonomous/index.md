---
name: llm-wiki/autonomous
description: Universal posture for autonomous (cron, swarm, unattended) llm-wiki agents. Read this before any autonomous subskill. Covers conservative defaults, error recovery, and exit report structure.
---

# LLM-Wiki Autonomous — Universal Posture

You are running without a user present. Every decision defaults to conservative. Surface nothing — escalate via talk pages.

## Universal Autonomous Defaults

- **Scope is predefined** — set by whoever scheduled this job; do not prompt for it
- **Ambiguity → `wiki_talk_post`** — never block waiting for input; post a clear note and move on
- **Judgment calls → talk page** — anything that would normally go to a user gets a talk post with a clear note
- **Hard write cap** — stop after the cap is reached; cap is passed via cron prompt or invocation parameter (e.g. `MAX_WRITES=10`); if unset, default to 10
- **Session close is mandatory** — no human will notice a drifting session; always call `wiki_session_close` at end of run
- **Exit with a structured report** — what was found, what was fixed, what was escalated to talk, what was left open

## Error Recovery

Infrastructure failures have defined responses — do not silently swallow errors or retry indefinitely:

| Failure | Response |
|---------|----------|
| `wiki_ingest` returns no concepts | Abort, report "no concepts extracted", do not write |
| `wiki_update` returns `patch-conflict` twice | `wiki_talk_post` noting the conflict, move on |
| Daemon unreachable | Abort entire run, emit error report, do not retry |
| Session expires mid-run | Start a new session for remaining writes, note the split in report |

## Research Quality Note

`wiki_query` quality is gated by the daemon's configured query backend, not the calling agent's model. For deep autonomous research, configure the daemon's query backend to use a capable model.

## Routing

- Autonomous research → `llm-wiki/autonomous/research`
- Autonomous writes → `llm-wiki/autonomous/write`
- Autonomous ingest → `llm-wiki/autonomous/ingest`
- Autonomous maintenance → `llm-wiki/autonomous/maintain`
