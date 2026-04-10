# 11 — Semantic Claim Identity and Adversary Sampling Diversity

**Status:** Idea  
**Depends on:** Adversary idle guard (claim verification store — see design discussion below)

---

## Background: How Claim Tracking Works Today

The adversary agent (`src/llm_wiki/adversary/`) verifies wiki claims against their cited raw sources. The current pipeline:

1. **Extraction** (`claim_extractor.py`) — scans every non-synthesis page for sentences ending with a `[[raw/...]]` citation. Each match becomes a `Claim(page, section, text, citation)`.
2. **Identity** — `claim.id` is `sha256(page|section|text)[:12]`. The location metadata (`page` name, `section` name) is part of the key.
3. **Sampling** (`sampling.py`) — weighted sample using `age_factor(last_corroborated) × (1.5 - authority)`. `last_corroborated` is tracked at the **page level** in `ManifestEntry` — a single timestamp updated whenever any claim on that page is verified, regardless of which specific claim was checked.
4. **Verification** — LLM call: does this raw source actually support this claim text?

---

## Problem 1: Identity Is Location-Sensitive

`sha256(page|section|text)` includes the page name and section name. This means:

- Page renamed → all its claim IDs change → all appear unverified in any external store
- Section restructured / heading renamed → same effect
- Two pages that make the same assertion (e.g. two concept pages both note that Boltz-2 uses diffusion) → two separate claim IDs, verified independently, no shared state

The semantic content of a claim hasn't changed, but its identity has. For the adversary's purposes — "has this assertion been checked against this source?" — what matters is the *assertion and its citation*, not where in the vault it lives.

**Near-term fix:** key the claim store by `sha256(text + "|" + citation)` instead of `sha256(page|section|text)`. The page/section are stored as metadata for display and pruning, but are not part of the identity. This is semantically stable across renames and restructuring.

---

## Problem 2: Verification State Is Per-Page, Not Per-Claim

`ManifestEntry.last_corroborated` is one timestamp per page. If the adversary checks 2 claims out of 20 on a page, all 20 claims inherit the "corroborated now" timestamp. The other 18 become low-priority for the next 90 days (`age_factor` decay) even though they've never been individually checked.

This makes coverage uneven and unpredictable. High-authority pages (many claims, low sampling weight) can go months with specific claims never verified.

**Fix:** a `ClaimVerificationStore` (flat JSON in `~/.llm-wiki/`) keyed by `sha256(text|citation)` — one record per claim, not per page. The adversary filters to stale claims (unverified, or last verified beyond a configurable `adversary_recheck_days`) before sampling. If no stale claims remain → return early, zero LLM calls. This also resolves the idle-guard problem: a stable vault eventually reaches a state where all claims are fresh and the adversary runs silently.

---

## The Deeper Question: Semantic Identity in Latent Space

During design discussion (2026-04-10), this question surfaced: if what we're verifying is the *semantic content* of a claim, should claim identity be tracked in *semantic* (embedding) space rather than text space?

The concrete problem: "Boltz-2 uses a diffusion model for structure prediction" and "Boltz-2 employs a diffusion-based approach to predict protein structures" are the same claim, expressed differently. Current text-hash approach treats them as unrelated — both get sampled and verified independently. In a large wiki where ingest produces paraphrased content across many pages, this is redundant verification spend.

### Why Embedding-Based Identity Is Hard Here

The adversary's task is not semantic search — it is adversarial verification of a specific `(claim_text, source)` pair. This requires high specificity:

- "Boltz-2 uses diffusion" and "Boltz-2 does NOT use diffusion" have high cosine similarity in most embedding models. Treating them as the same claim would be catastrophic.
- Two claims may cite *different sources*. Semantic similarity does not imply the same source supports both. Collapsing them in embedding space loses source attribution, which is load-bearing for the adversary.
- Verification of one claim does not entail verification of a "nearby" claim, even if semantically close. The adversary checks whether a *specific source* supports a *specific assertion* — generalizing that relationship is unsound.

### Where Embedding-Based Approaches Do Help: Sampling Diversity

The legitimate use of latent space in this pipeline is not identity — it is **sampling strategy**.

Current sampling weights by page-level age + authority. This does not account for *conceptual diversity* — a page with 15 claims about protein diffusion might dominate sampling even though those claims all cover the same conceptual ground.

An embedding-aware sampler would:
1. Embed each stale claim
2. Cluster by semantic similarity (e.g. k-means or agglomerative on cosine distance)
3. Sample proportionally from clusters — one representative per cluster per run

This ensures verification budget covers the *conceptual surface area* of the wiki rather than over-indexing on whatever pages happen to have the most raw-cited sentences. A wiki with 500 claims about protein folding and 3 claims about training data licensing would stop ignoring the licensing claims.

The tantivy index already exists; a separate lightweight embedding index (or even cheap BM25 pseudo-clusters on claim text) could approximate this without the full embedding infrastructure.

---

### The Inline Annotation Idea (Considered and Set Aside)

During the same design discussion, an alternative was considered: embedding a `%%sha256:hash%%` comment in the wiki page body after each verifiable claim, written by the adversary post-verification. This would make verification state travel with the content and self-heal when text changes.

**Why it was set aside:**

- The adversary currently never writes to wiki page bodies — only to talk pages. Making it a page writer triggers the file watcher, fires the git commit pipeline, and pollutes the audit trail with verification receipts on every run.
- A `%%` annotation in the body is semantically similar to a structural marker (`%% section: ... %%`), but structural markers are written once at ingest and never updated. Verification state changes on every run — a fundamentally different write pattern.
- An external JSON store (one file, one update per run) is cheaper and keeps pages as pure knowledge content.
- The state-travels-with-content property is appealing but the cost is disproportionate given that the store is keyed by the text hash anyway — if text changes, the store entry is automatically stale whether or not it's inline.

This idea may be worth revisiting if the wiki ever supports portable export (taking a vault snapshot to another machine, where the external store wouldn't travel with it). In that scenario, inline verification annotations would be the natural solution.

---

## Summary: What Should Be Built

| Problem | Solution | Where |
|---------|----------|-------|
| Claim ID fragile to page/section renames | Key by `sha256(text\|citation)` | `claim_extractor.py` |
| Per-page corroboration masks unchecked claims | `ClaimVerificationStore` keyed per-claim | new `adversary/claim_store.py` |
| Adversary wastes calls on fresh vault | Filter to stale claims before sampling; early-exit if empty | `adversary/agent.py` |
| `raw/` hardcoded in extractor and agent | Thread `config.vault.raw_dir` through | `claim_extractor.py`, `adversary/agent.py` |
| Sampling over-concentrates on high-claim pages | Cluster by semantic similarity, sample per cluster | `adversary/sampling.py` (future) |

The first four are the idle-guard feature. The last one is the latent-space idea worth tracking for future implementation.
