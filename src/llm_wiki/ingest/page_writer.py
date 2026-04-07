from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageSection:
    """One section of a wiki page."""
    name: str      # slug: "overview"
    heading: str   # display text: "Overview"
    content: str   # markdown body


@dataclass
class WrittenPage:
    """Result of writing a page."""
    path: Path
    was_update: bool
