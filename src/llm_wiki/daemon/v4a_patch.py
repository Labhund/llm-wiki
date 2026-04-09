"""V4A patch parser and applier.

The V4A format is the diff format used by OpenAI's codex and the cline tool.
This module is the daemon-side implementation that backs the `wiki_update`
MCP tool. Only `*** Update File:` is supported in Phase 6b — `*** Add File:`
and `*** Delete File:` are recognized only enough to return a clear error.

Format example:

    *** Begin Patch
    *** Update File: wiki/sRNA-tQuant.md
    @@ ## Methods @@
     context line
    -removed line
    +added line
     context line
    *** End Patch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PatchOp = Literal["update", "create", "delete"]
HunkLineKind = Literal["context", "add", "remove"]


@dataclass
class HunkLine:
    """One line within a hunk: context, addition, or removal."""
    kind: HunkLineKind
    text: str


@dataclass
class Hunk:
    """A contiguous block of changes within a patch.

    `context_hint` is the text after `@@` on the hunk header line — usually
    a heading or a section name. Empty string when the header is bare `@@`.
    The hint is used as a starting anchor for the applier when there are
    multiple plausible matches.
    """
    context_hint: str
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class Patch:
    """A complete V4A patch operating on one file."""
    op: PatchOp
    target_path: str
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Outcome of applying a patch."""
    additions: int
    removals: int
    applied_via: Literal["exact", "fuzzy"]


class PatchConflict(Exception):
    """Raised when patch context lines do not match the current file content.

    The `current_excerpt` field carries a few lines of the actual file content
    around the failed match site so the agent can re-read and regenerate
    the patch.
    """

    def __init__(self, message: str, current_excerpt: str = "") -> None:
        super().__init__(message)
        self.current_excerpt = current_excerpt


class PatchParseError(Exception):
    """Raised when patch text is malformed (missing markers, bad header, etc.)."""


# Parser and applier follow in Tasks 3–6 below.
