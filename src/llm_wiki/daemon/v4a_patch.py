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
            # Tolerate one optional separator space after '+' so that
            # LLM-generated patches writing "+ added" parse the same as
            # "+added". Symmetric for '-'. The cost is a literal added
            # line beginning with a space loses one space — acceptable
            # for a wiki where indented body lines are rare.
            if rest.startswith(" "):
                rest = rest[1:]
            current_hunk.lines.append(HunkLine(kind="add", text=rest))
        elif prefix == "-":
            if rest.startswith(" "):
                rest = rest[1:]
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


def apply_patch(
    patch: Patch,
    current_content: str,
    fuzzy_threshold: float = 0.85,
) -> tuple[str, ApplyResult]:
    """Apply a parsed Patch to file content. Returns (new_content, result).

    Two-stage matching:
      1. Exact: every context/remove line must appear verbatim.
      2. Fuzzy: trailing whitespace tolerated; per-line normalized
         Levenshtein similarity must be >= ``fuzzy_threshold``.

    Raises:
        PatchConflict: if neither stage can locate the hunk.
    """
    if patch.op != "update":
        raise PatchConflict(
            f"apply_patch only supports op='update', got {patch.op!r}"
        )

    lines = current_content.splitlines(keepends=True)
    cursor = 0  # Index into `lines` — we walk forward as we apply hunks.
    additions = 0
    removals = 0
    used_fuzzy = False

    for hunk in patch.hunks:
        try:
            new_cursor, new_lines, h_adds, h_rems = _apply_hunk_exact(
                hunk, lines, start=cursor,
            )
        except PatchConflict:
            new_cursor, new_lines, h_adds, h_rems = _apply_hunk_fuzzy(
                hunk, lines, start=cursor, threshold=fuzzy_threshold,
            )
            used_fuzzy = True
        lines = new_lines
        cursor = new_cursor
        additions += h_adds
        removals += h_rems

    return "".join(lines), ApplyResult(
        additions=additions,
        removals=removals,
        applied_via="fuzzy" if used_fuzzy else "exact",
    )


def _apply_hunk_exact(
    hunk: Hunk,
    lines: list[str],
    start: int,
) -> tuple[int, list[str], int, int]:
    """Apply one hunk to `lines` starting at index `start`.

    Returns (cursor_after_hunk, new_lines, additions, removals).
    Raises PatchConflict if the hunk cannot be matched exactly.
    """
    # Build the sequence of "expected file lines" — context + remove, in order.
    expected: list[str] = [
        l.text for l in hunk.lines if l.kind in ("context", "remove")
    ]

    # Search forward from `start` for a window of `lines` that matches `expected`.
    match_start = _find_window(lines, expected, search_from=start)
    if match_start is None:
        excerpt = _excerpt_around(lines, start, hunk.context_hint)
        raise PatchConflict(
            f"Could not locate hunk context: {hunk.context_hint or '<no hint>'}",
            current_excerpt=excerpt,
        )

    # Build the replacement: walk hunk.lines, emit context+add, drop remove.
    replacement: list[str] = []
    for hl in hunk.lines:
        if hl.kind == "remove":
            continue
        replacement.append(hl.text + "\n")

    additions = sum(1 for l in hunk.lines if l.kind == "add")
    removals = sum(1 for l in hunk.lines if l.kind == "remove")

    new_lines = (
        lines[:match_start]
        + replacement
        + lines[match_start + len(expected) :]
    )
    new_cursor = match_start + len(replacement)
    return new_cursor, new_lines, additions, removals


def _find_window(
    lines: list[str],
    expected: list[str],
    search_from: int = 0,
) -> int | None:
    """Find the index in `lines` where `expected` appears verbatim.

    Compares stripped trailing newlines so the patch's "context line text"
    matches the file's "line including trailing newline."
    """
    if not expected:
        return None
    n = len(expected)
    for i in range(search_from, len(lines) - n + 1):
        match = True
        for j in range(n):
            file_line = lines[i + j].rstrip("\n").rstrip("\r")
            if file_line != expected[j]:
                match = False
                break
        if match:
            return i
    return None


def _excerpt_around(lines: list[str], start: int, hint: str) -> str:
    """Return ~6 lines of context around the failed match site."""
    lo = max(0, start - 2)
    hi = min(len(lines), start + 6)
    return "".join(lines[lo:hi])


def _apply_hunk_fuzzy(
    hunk: Hunk,
    lines: list[str],
    start: int,
    threshold: float,
) -> tuple[int, list[str], int, int]:
    """Fuzzy fallback: tolerate trailing whitespace and per-line typos."""
    expected: list[str] = [
        l.text for l in hunk.lines if l.kind in ("context", "remove")
    ]

    match_start = _find_window_fuzzy(
        lines, expected, search_from=start, threshold=threshold,
    )
    if match_start is None:
        excerpt = _excerpt_around(lines, start, hunk.context_hint)
        raise PatchConflict(
            f"Could not locate hunk context (fuzzy): {hunk.context_hint or '<no hint>'}",
            current_excerpt=excerpt,
        )

    replacement: list[str] = []
    for hl in hunk.lines:
        if hl.kind == "remove":
            continue
        replacement.append(hl.text + "\n")

    additions = sum(1 for l in hunk.lines if l.kind == "add")
    removals = sum(1 for l in hunk.lines if l.kind == "remove")

    new_lines = (
        lines[:match_start]
        + replacement
        + lines[match_start + len(expected) :]
    )
    new_cursor = match_start + len(replacement)
    return new_cursor, new_lines, additions, removals


def _find_window_fuzzy(
    lines: list[str],
    expected: list[str],
    search_from: int,
    threshold: float,
) -> int | None:
    """Like ``_find_window`` but tolerates trailing whitespace and per-line drift."""
    if not expected:
        return None
    n = len(expected)
    for i in range(search_from, len(lines) - n + 1):
        all_match = True
        for j in range(n):
            file_line = lines[i + j].rstrip("\n").rstrip("\r").rstrip()
            patch_line = expected[j].rstrip()
            sim = _line_similarity(file_line, patch_line)
            if sim < threshold:
                all_match = False
                break
        if all_match:
            return i
    return None


def _line_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0.0, 1.0]. 1.0 means identical."""
    if a == b:
        return 1.0
    if not a and not b:
        return 1.0
    distance = levenshtein(a, b)
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - (distance / longest)


def levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein edit distance, iterative DP.

    Public so ``name_similarity.py`` (Phase 6b Task 10) can reuse it
    without dipping into private symbols across modules.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = curr
    return prev[-1]
