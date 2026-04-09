"""Severity vocabulary shared across Phase 6a's visibility surfaces.

Issues use the strict subset {critical, moderate, minor}. Talk entries
add {suggestion, new_connection}. The full vocabulary is the union;
both sites annotate against it so a static type-checker catches typos
at the call site.
"""

from typing import Literal

Severity = Literal[
    "critical",
    "moderate",
    "minor",
    "suggestion",
    "new_connection",
]

# Severity ordering for user-facing display. Lower rank = more important.
# Used by deterministic summary fallbacks and any other surface that
# needs to sort severity-tagged items in a meaningful order (rather than
# alphabetical, which puts "critical" between "aaa" and "minor").
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "moderate": 1,
    "minor": 2,
    "suggestion": 3,
    "new_connection": 4,
}


def severity_sort_key(sev: str) -> tuple[int, str]:
    """Sort key putting known severities in rank order, unknowns after.

    Unknown severities sort to the end, alphabetically among themselves.
    Returns a tuple `(rank, name)` so callers can use it directly with
    `sorted(items, key=lambda x: severity_sort_key(x.severity))`.
    """
    if sev in SEVERITY_RANK:
        return (SEVERITY_RANK[sev], sev)
    # Unknowns: rank past the largest known rank, then alphabetical.
    return (len(SEVERITY_RANK), sev)
