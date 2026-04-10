# 10 — Gap-Filling Agent

**Status:** Idea  
**Depends on:** URL ingest (see below), web/arxiv search

---

## Problem

`llm-wiki query` surfaces "Missing Information" sections when the wiki lacks detail on something — e.g. a query about boltz-2 returns its role in a pipeline but not what kind of model it is, because no boltz-2 paper has been ingested. The gap is visible but nothing acts on it.

The adversary verifies existing claims; no agent finds and fills structural gaps.

---

## Proposed Feature

A **gap-filling agent** that closes the loop between identified gaps and new ingest. Two sub-features, each independently useful:

### Sub-feature A: URL ingest

`llm-wiki ingest <url>` — detect if the argument is a URL, download to a tempfile, dispatch to `extract_text` as normal. Covers:
- `https://arxiv.org/abs/...` → fetch `/pdf/` variant
- `https://www.biorxiv.org/content/.../v1` → fetch `.full.pdf`
- Any direct PDF URL

This is ~30 lines in `src/llm_wiki/cli/main.py` + `src/llm_wiki/ingest/extractor.py`:
- Detect URL prefix in the CLI command
- Download to `tempfile.NamedTemporaryFile(suffix=".pdf")`
- Pass temp path to existing `extract_text` pipeline unchanged

For arxiv/biorxiv, sniff the URL and rewrite to the PDF endpoint if needed (abstract URLs don't directly serve PDF).

### Sub-feature B: Gap-filling agent

A new background worker (or query-time suggestion) that:

1. **Collects gaps** — monitors traversal logs for turns where `salient_points` is empty or the synthesized answer contains "Missing Information". Logs the gap term + query context.

2. **Searches for sources** — given a gap term (e.g. "boltz-2 technical details"), queries an external source:
   - Arxiv API: `https://export.arxiv.org/api/query?search_query=...`
   - Optional: web search (requires external API key — Tavily, SerpAPI, etc.)
   - Returns candidate paper titles + URLs

3. **Prompts for consent** — does not ingest automatically. Posts candidates to a talk page on the relevant wiki page (or a dedicated `gap-queue` inbox entry) for human or agent review. Format:
   ```
   Gap detected: boltz-2 model architecture
   Candidate sources:
     - "Boltz-2: Towards Accurate..." https://biorxiv.org/...
     - ...
   Ingest? (run: llm-wiki ingest <url>)
   ```

4. **Ingest on approval** — human or connected agent runs `llm-wiki ingest <url>` (or a future `llm-wiki gap-fill approve <id>`). After ingest, the gap entry is resolved.

---

## Design Notes

- **No automatic ingest.** Gap-filling touches the knowledge graph; that requires explicit human or agent consent, consistent with the daemon's supervised-write model. The background worker only surfaces candidates, never writes pages.
- **Gap deduplication.** A gap term that has already been queued (or recently ingested) should not be re-surfaced.
- **Arxiv is the right first target.** It has a free structured API, covers most scientific content, and returns machine-readable metadata (authors, abstract, PDF URL). Web search requires an external API key and is harder to parse reliably.
- **URL ingest (Sub-feature A) is a prerequisite** and can ship independently. It's useful on its own and unblocks the rest.

---

## Out of Scope (for this idea)

- Automatic agent-driven ingest without human review
- Web scraping of non-PDF pages (HTML extraction is a separate problem)
- Semantic deduplication of ingested content (if the same paper is ingested twice, the existing idempotency logic handles page-level dedup but not content-level)
