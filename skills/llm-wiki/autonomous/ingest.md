---
name: llm-wiki/autonomous/ingest
description: Use for autonomous (cron, swarm, unattended) ingestion of external sources into an llm-wiki vault. Dry-run safety gate before executing wiki_ingest.
---

# LLM-Wiki Autonomous Ingest

## Protocol

1. **Read the source** — extract key concepts; understand what you are about to ingest before touching any wiki tool
2. **`wiki_ingest --dry-run`** — inspect what the daemon would create/update:
   - Zero concepts extracted → abort; report "no concepts extracted"; do not proceed to live ingest
   - All targets have open critical issues → `wiki_talk_post` flagging the conflict; abort
   - Otherwise → proceed
3. **`wiki_ingest`** — execute
4. **`wiki_session_close`** — mandatory
5. **Emit structured report**

No conversational path. No mode choice. The dry-run step is the autonomous safety gate — it replaces the human confirmation from the attended path.

## Error Recovery

- Daemon unreachable → abort, report
- `wiki_ingest` errors mid-run → report partial results, close session, do not retry
- Dry-run returns no concepts → abort; do not proceed to live ingest

## Output Format

```
## Ingest Report
**Source:** [path or name]
**Dry-run concepts found:** N
**Pages created:** [list]
**Pages updated:** [list]
**Errors:** [any, or "none"]
**Session closed:** yes
```
