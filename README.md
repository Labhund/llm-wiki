# LLM Wiki

Personal knowledge base built and maintained by LLMs.

## Structure

- `raw/` — Immutable source documents
- `wiki/` — Compiled knowledge articles
- `docs/` — Documentation and pattern notes

See [docs/LLM Wiki - Knowledge Base Pattern.md](docs/LLM%20Wiki%20-%20Knowledge%20Base%20Pattern.md) for the full pattern description.

## Quick Start

1. Ingest a source:
   ```
   Raw source → raw/<topic>/YYYY-MM-DD-slug.md
   LLM compiles → wiki/<topic>/concept.md
   Update index + log
   ```

2. Query:
   ```
   Search index → read pages → synthesize with citations
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
