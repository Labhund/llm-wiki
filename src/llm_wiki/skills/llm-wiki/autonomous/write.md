---
name: llm-wiki/autonomous/write
description: Use for autonomous (cron, swarm, unattended) write tasks against an llm-wiki vault. Conservative — only write clearly sourced content, talk-post anything uncertain.
---

# LLM-Wiki Autonomous Write

## Conservative Default

Only write what is clearly and directly supported by an explicit source already in hand. If the justification for a write requires any inference or judgment, `wiki_talk_post` instead — never make autonomous judgment calls in writes.

## Protocol

1. For each intended write: confirm the source is explicit and in hand
2. If source is clear → proceed with write tool
3. If source requires inference → `wiki_talk_post` noting the intent and what source would be needed; do not write
4. Watch hard write cap (from invocation parameter or default 10) — stop when reached
5. `wiki_session_close` — mandatory
6. Emit structured report

## Tool Selection

Same tools as attended write — `wiki_create` (citations required), `wiki_update` (V4A patch, re-read first), `wiki_append` (heading-anchored, citations required). Session opens implicitly on first write.

**Wikilinks:** Link every salient noun, technical term, and named entity on its first mention. Same posture as attended write — aggressive linking is always correct, even in autonomous mode.

## Error Recovery

- `patch-conflict` on `wiki_update` twice → `wiki_talk_post` noting the conflict; do not rewrite the whole page; count toward cap
- Near-match rejection on `wiki_create` → `wiki_talk_post` noting the proposed page; do not use `force=true` autonomously; count toward cap

## Output Format

```
## Write Report
**Writes attempted:** N
**Writes completed:** N
**Escalated to talk:** N (pages: [list])
**Cap hit:** yes / no
**Session closed:** yes
```
