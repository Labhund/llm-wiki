---
name: llm-wiki/autonomous/maintain
description: Use for autonomous (cron, swarm, unattended) maintenance passes on an llm-wiki vault. Conservative — lint, triage critical/moderate only, fix unambiguous issues, talk-post everything else.
---

# LLM-Wiki Autonomous Maintain

## Protocol

1. **`wiki_lint`** — get vault-wide attention map with issues by severity
2. **Triage** — critical first; move to moderate only once all in-scope critical issues are handled, subject to the cap; skip minor entirely
3. **For each issue (up to cap):**
   - `wiki_issues_get` — read the full issue
   - Unambiguously fixable → write tools + `wiki_issues_resolve`
   - Any doubt → `wiki_talk_post` with the issue ID and a clear note; leave the issue open; count toward cap
4. **Check talk pages** — `wiki_talk_list`; note open critical/moderate entries in report; do not contribute
5. **`wiki_session_close`** — mandatory even if no writes were made
6. **Emit structured report**

## Hard Cap

Stop processing after the write cap is reached (from invocation parameter or default 10). Note remaining open issues in the report — they are work for the next run.

## Error Recovery

- `wiki_lint` fails → abort, report
- `wiki_issues_get` fails for a specific issue → skip it, note in report, do not count toward cap
- Write fails → `wiki_talk_post` the intended fix, note in report, count toward cap

## Never

- Make judgment calls autonomously — when in doubt, talk post
- Fix minor issues while critical ones remain open
- Rewrite page body content — maintenance fixes are surgical

## Output Format

```
## Maintenance Report
**Issues found:** N (critical: X, moderate: Y, minor: Z)
**Issues fixed:** N
**Escalated to talk:** N
**Cap hit:** yes / no
**Talk pages with open critical/moderate:** [list, or "none"]
**Session closed:** yes
```
