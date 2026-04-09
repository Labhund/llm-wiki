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
