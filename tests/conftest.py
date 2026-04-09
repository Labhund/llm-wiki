import pytest
from pathlib import Path


SAMPLE_PAGE_WITH_MARKERS = """\
---
title: sRNA Embeddings Validation
source: "[[raw/smith-2026-srna.pdf]]"
---

%% section: overview, tokens: 45 %%
## Overview

sRNA embeddings are validated via PCA projection and k-means clustering.

%% section: method, tokens: 38 %%
## Method

We use PCA analysis to reduce dimensionality of embeddings before clustering.

%% section: clustering, tokens: 32 %%
## Clustering

Clustering is performed using k-means with k=10 clusters.

%% section: related, tokens: 52 %%
## Related Pages

For clustering metrics, see [[clustering-metrics]].
For variant analysis, see [[inter-rep-variant-analysis]].
"""

SAMPLE_PAGE_NO_MARKERS = """\
---
title: Clustering Metrics
---

# Clustering Metrics

Evaluation metrics for clustering algorithms.

## Silhouette Score

Silhouette score ranges from -1 to 1:
- > 0.5: Well-separated clusters
- 0.2 - 0.5: Moderate structure
- < 0.2: Poor or no structure

## Related Pages

For application to sRNA embeddings, see [[srna-embeddings]].
"""

SAMPLE_PAGE_NO_STRUCTURE = """\
A simple page with no headings and no markers.
Just plain text content that should be treated as one section.
It references [[some-other-page]] in passing.
"""


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample pages."""
    # Add wiki/ directory to pass vault validation guard
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    bio = tmp_path / "bioinformatics"
    bio.mkdir()
    (bio / "srna-embeddings.md").write_text(SAMPLE_PAGE_WITH_MARKERS)
    (bio / "inter-rep-variant-analysis.md").write_text(
        "---\ntitle: Inter-Rep Variant Analysis\n---\n\n"
        "%% section: overview, tokens: 30 %%\n"
        "## Overview\n\nVariant analysis across embedding representations.\n\n"
        "%% section: method, tokens: 35 %%\n"
        "## Method\n\nUses silhouette scores > 0.5 for quality.\n"
        "See [[srna-embeddings]] and [[clustering-metrics]].\n"
    )

    ml = tmp_path / "machine-learning"
    ml.mkdir()
    (ml / "clustering-metrics.md").write_text(SAMPLE_PAGE_NO_MARKERS)

    (tmp_path / "no-structure.md").write_text(SAMPLE_PAGE_NO_STRUCTURE)

    yield tmp_path

    # Clean up state dir under ~/.llm-wiki/vaults/ for this tmp vault
    import shutil
    from llm_wiki.vault import _state_dir_for
    state_dir = _state_dir_for(tmp_path)
    if state_dir.exists():
        shutil.rmtree(state_dir)
