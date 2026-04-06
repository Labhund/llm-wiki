# LLM Wiki

Personal knowledge base built and maintained by LLMs.

## Structure

- `raw/` — Immutable source documents
- `wiki/` — Compiled knowledge articles (index.md, log.md)
- `docs/` — Documentation and pattern notes

## Documentation

- [LLM Wiki - Knowledge Base Pattern](docs/LLM%20Wiki%20-%20Knowledge%20Base%20Pattern.md) — The full pattern description with architecture, operations, and critical analysis
- [Multi-Turn Traversal Pattern](docs/Multi-Turn%20Traversal%20Pattern.md) — How agents navigate the wiki without reading all pages at once

## Quick Start

1. Ingest a source:
   ```
   Raw source → raw/<topic>/YYYY-MM-DD-slug.md
   LLM compiles → wiki/<topic>/concept.md
   Update index + log
   ```

2. Query (using multi-turn traversal):
   ```
   Search index → read page → summarize internally → decide next page
   → read next page → repeat until answer synthesized
   Emit answer with full citation path
   ```

3. Lint:
   ```
   Check consistency → fix broken links → flag contradictions
   ```

## Philosophy

> The LLM writes and maintains the wiki; the human reads and asks questions.
>
> — Andrej Karpathy

The wiki is a persistent, compounding artifact. Knowledge is compiled once and kept current, not re-derived on every query.
