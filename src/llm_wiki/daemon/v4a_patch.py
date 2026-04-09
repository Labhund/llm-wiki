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

import re
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


_BEGIN_MARKER = "*** Begin Patch"
_END_MARKER = "*** End Patch"
_UPDATE_HEADER_RE = re.compile(r"^\*\*\* Update File:\s*(?P<path>\S.*)$")
_ADD_HEADER_RE = re.compile(r"^\*\*\* Add File:\s*(?P<path>\S.*)$")
_DELETE_HEADER_RE = re.compile(r"^\*\*\* Delete File:\s*(?P<path>\S.*)$")
_HUNK_HEADER_RE = re.compile(r"^@@\s*(?P<hint>.*?)\s*@@\s*$")


def parse_patch(text: str) -> Patch:
    """Parse V4A patch text into a Patch object.

    Phase 6b supports only ``*** Update File:``. ``*** Add File:`` and
    ``*** Delete File:`` raise PatchParseError with a clear message — they
    are reserved for future expansion.
    """
    lines = text.splitlines()

    # Envelope checks
    if not any(line.strip() == _BEGIN_MARKER for line in lines):
        raise PatchParseError(f"Missing '{_BEGIN_MARKER}' marker")
    if not any(line.strip() == _END_MARKER for line in lines):
        raise PatchParseError(f"Missing '{_END_MARKER}' marker")

    # Slice between markers
    begin_idx = next(i for i, l in enumerate(lines) if l.strip() == _BEGIN_MARKER)
    end_idx = next(i for i, l in enumerate(lines) if l.strip() == _END_MARKER)
    if end_idx <= begin_idx:
        raise PatchParseError("End Patch appears before Begin Patch")
    body = lines[begin_idx + 1 : end_idx]

    if not body:
        raise PatchParseError("Patch body is empty")

    # First non-blank line must be a file-op header
    op_line_idx = 0
    while op_line_idx < len(body) and not body[op_line_idx].strip():
        op_line_idx += 1
    if op_line_idx >= len(body):
        raise PatchParseError("Patch body has no file-op header")

    op_line = body[op_line_idx]
    update_match = _UPDATE_HEADER_RE.match(op_line)
    if update_match is None:
        if _ADD_HEADER_RE.match(op_line):
            raise PatchParseError(
                "*** Add File: is not supported in Phase 6b — use wiki_create instead"
            )
        if _DELETE_HEADER_RE.match(op_line):
            raise PatchParseError(
                "*** Delete File: is not supported in Phase 6b — delete pages outside the daemon"
            )
        raise PatchParseError(f"Unrecognized file-op header: {op_line!r}")

    target_path = update_match.group("path").strip()
    if not target_path:
        raise PatchParseError("Update File: target path is empty")

    # Walk hunks. Each hunk starts with @@ and continues until the next @@
    # or end of body.
    hunks: list[Hunk] = []
    current_hunk: Hunk | None = None
    saw_any_hunk_header = False
    for line in body[op_line_idx + 1 :]:
        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match is not None:
            saw_any_hunk_header = True
            if current_hunk is not None:
                hunks.append(current_hunk)
            current_hunk = Hunk(
                context_hint=hunk_match.group("hint").strip(),
                lines=[],
            )
            continue

        if current_hunk is None:
            # Body content before the first @@ header
            if line.strip():
                raise PatchParseError(
                    f"Patch body has content before first @@ header: {line!r}"
                )
            continue

        # Body line within a hunk
        if not line:
            # Blank lines are treated as empty context lines (preserve them)
            current_hunk.lines.append(HunkLine(kind="context", text=""))
            continue
        prefix = line[0]
        rest = line[1:]
        if prefix == " ":
            current_hunk.lines.append(HunkLine(kind="context", text=rest))
        elif prefix == "+":
            current_hunk.lines.append(HunkLine(kind="add", text=rest))
        elif prefix == "-":
            current_hunk.lines.append(HunkLine(kind="remove", text=rest))
        else:
            raise PatchParseError(
                f"Hunk body line must start with ' ', '+', or '-': {line!r}"
            )

    if not saw_any_hunk_header:
        raise PatchParseError("Patch has no @@ hunk headers")
    if current_hunk is not None:
        hunks.append(current_hunk)

    return Patch(op="update", target_path=target_path, hunks=hunks)


# Applier follows in Tasks 5–6 below.
