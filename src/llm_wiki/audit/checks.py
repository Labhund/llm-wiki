from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm_wiki.issues.queue import Issue
from llm_wiki.vault import Vault

# Page names that should never be flagged as orphans even if nothing links to them.
_ENTRY_POINT_NAMES = {"index", "readme", "home"}


@dataclass
class CheckResult:
    """Result of one structural check."""
    check: str
    issues: list[Issue]


def find_orphans(vault: Vault) -> CheckResult:
    """Pages with zero inlinks (excluding entry-point names).

    Each orphan becomes one Issue with the page slug as the affected page
    and an empty key (since the page itself is the unique identifier).
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        if name.lower() in _ENTRY_POINT_NAMES:
            continue
        if entry.links_from:
            continue
        issues.append(
            Issue(
                id=Issue.make_id("orphan", name, ""),
                type="orphan",
                status="open",
                title=f"Page '{name}' has no inbound links",
                page=name,
                body=(
                    f"The page [[{name}]] is not referenced by any other page in the vault. "
                    f"Either link to it from a related page or remove it if obsolete."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={},
            )
        )
    return CheckResult(check="orphans", issues=issues)
