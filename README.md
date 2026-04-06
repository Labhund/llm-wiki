# LLM Wiki

Personal knowledge base built and maintained by LLMs.

## Structure

- `raw/` — Immutable source documents
- `wiki/` — Compiled knowledge articles (index.md, log.md)
- `docs/` — Documentation and pattern notes

## Documentation

- [LLM Wiki - Knowledge Base Pattern](docs/LLM%20Wiki%20-%20Knowledge%20Base%20Pattern.md) — The full pattern description with architecture, operations, and critical analysis
- [Multi-Turn Traversal Pattern](docs/Multi-Turn%20Traversal%20Pattern.md) — How agents navigate wiki without reading all pages at once
- [Traversal Implementation](docs/Traversal%20Implementation.md) — Concrete tool contracts, working memory format, decision logic, and Python prototype
- **[Implementation Ideas](docs/implementation-ideas/README.md)** — Draft design documents for open questions and future development
- **[What is an Agent? - Identity and Soul](docs/what-is-an-agent-identity-and-soul.md)** — Philosophical question about what makes an agent "real" — simulated vs. persistent identity, the "genetic SOUL.md" concept, and the UI/UX parallel to the birth of the internet
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

## Prototype

A working prototype is available in `src/traversal.py`:

```bash
cd src
python3 traversal.py
```

The demo shows multi-turn traversal:
- Searches index for relevant pages
- Reads pages incrementally across 3 turns
- Maintains working memory of what was learned
- Returns answer with full citation path

**Note:** The prototype uses hardcoded mock LLM learning for demonstration. Real implementation would replace `_mock_learn_from_page()` with actual LLM calls for:
- Extracting learned information from pages
- Scoring candidate pages
- Deciding continue/stop
- Synthesizing final answers
