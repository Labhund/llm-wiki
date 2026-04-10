---
name: llm-wiki/autonomous/research
description: Use for autonomous (cron, swarm, unattended) research tasks against an llm-wiki vault. Uses wiki_query only — no manual traversal, no mode selection. Returns structured findings.
---

# LLM-Wiki Autonomous Research

## Mode: Daemon-Delegated Only

Use `wiki_query` exclusively. Do not attempt manual traversal — context management without a user present is not safe. Quality is gated by the daemon's configured query backend (see `llm-wiki/autonomous` for the research quality note).

## Protocol

1. Call `wiki_query` with a clear, specific query derived from the predefined scope
2. If `wiki_query` fails or returns empty results: note in report, do not retry with manual traversal
3. Return structured findings

## Output Format

```
## Research Report
**Query:** [the query used]
**Status:** [success / no results / error]
**Findings:** [synthesis of results, or "no results found"]
**Pages consulted:** [list of page names if available]
```
