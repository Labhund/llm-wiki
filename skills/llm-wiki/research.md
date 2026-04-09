---
name: llm-wiki/research
description: "Use when researching a topic in an llm-wiki vault. Covers three traversal modes: daemon-delegated (wiki_query), sub-agent, and in-context manual. Attended mode."
---

# LLM-Wiki Research — Attended Traversal

## Hard Gate

Before any traversal, state out loud:
- What you are looking for
- Why you need it
- What you will do with the result

No exceptions. This keeps reasoning legible and prevents purposeless browsing.

## Mode Selection

After stating intent, offer the three modes to the user:

> "I can research this three ways:
> 1. **Daemon query** (`wiki_query`) — fast, low context cost, quality depends on the daemon's configured model
> 2. **Sub-agent** — I spawn a research agent using my framework's native sub-agent mechanism; my context stays clean; configurable model
> 3. **In-context manual** — I traverse step by step; you see each hop; costs more context
>
> Which do you prefer?"

Wait for a response; if none comes, apply the default recommendation: `wiki_query` for specific well-defined questions, sub-agent for broad exploratory research, in-context manual only when the user explicitly wants to see each hop.

## Mode 1: Daemon-Delegated (`wiki_query`)

Call `wiki_query` with a clear, specific query string derived from your stated intent. Return the synthesis to the user. If the result is empty or insufficient to answer the stated intent, fall back to sub-agent or in-context manual mode.

## Mode 2: Sub-Agent

Spawn a research agent using your framework's native sub-agent mechanism (e.g., `Agent` tool in Claude Code, `delegate_task` in Hermes). The prompt must include:
- The stated intent verbatim
- The vault path (from MCP connection context)
- A token budget hint (e.g., "stay under 20k tokens")
- Whether to return structured synthesis or raw findings

The sub-agent follows Mode 3 (in-context manual) discipline internally.

## Mode 3: In-Context Manual

1. `wiki_manifest` with a budget — orient before searching; understand the cluster landscape
2. `wiki_search` for entry points — do not start reading without a target
3. `wiki_read` viewport order: `top` → named section → `grep` → `full` (never `full` first)
4. Follow wikilinks with purpose — for each link, ask: does following this serve my stated intent?
5. Inline issue/talk digests in `wiki_read` responses are relevant findings — critical and moderate signals are part of the research result

## Exit Condition

Traversal ends when you can answer the stated intent. Not when pages run out.
