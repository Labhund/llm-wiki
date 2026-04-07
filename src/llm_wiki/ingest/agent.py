from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str        # URL-safe slug: "srna-embeddings"
    title: str       # Human-readable: "sRNA Embeddings"
    passages: list[str] = field(default_factory=list)


@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)   # concept slugs
    pages_updated: list[str] = field(default_factory=list)   # concept slugs

    @property
    def concepts_found(self) -> int:
        return len(self.pages_created) + len(self.pages_updated)
