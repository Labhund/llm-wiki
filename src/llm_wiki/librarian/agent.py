from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue
from llm_wiki.librarian.authority import compute_authority
from llm_wiki.librarian.log_reader import PageUsage, aggregate_logs
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.vault import Vault, _state_dir_for

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class LibrarianResult:
    """Outcome of one LibrarianAgent.run() invocation."""
    pages_refined: list[str] = field(default_factory=list)
    authorities_updated: int = 0
    issues_filed: list[str] = field(default_factory=list)


class LibrarianAgent:
    """Refines manifest entries from usage signals.

    Two operations:
      - run() — full refresh: re-aggregate logs, refine tags/summary for
        pages above threshold, then recompute authority.
      - recalc_authority() — programmatic, no LLM. Recompute authority for
        every page from current usage + link graph.

    Both write through ManifestOverrides. The librarian and authority_recalc
    workers may run on different cadences (config.maintenance.librarian_interval
    vs authority_recalc).
    """

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: "LLMClient",
        queue: IssueQueue,
        config: WikiConfig,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._queue = queue
        self._config = config
        self._state_dir = _state_dir_for(vault_root)
        self._overrides_path = self._state_dir / "manifest_overrides.json"
        self._log_path = self._state_dir / "traversal_logs" / "traversal_logs.jsonl"

    async def recalc_authority(self) -> int:
        """Recompute authority for every entry and persist via overrides.

        Returns:
            The number of authority values written.
        """
        entries = self._vault.manifest_entries()
        if not entries:
            return 0

        usage = aggregate_logs(self._log_path)
        scores = compute_authority(entries, usage)

        overrides = ManifestOverrides.load(self._overrides_path)
        for name, score in scores.items():
            existing = overrides.get(name) or PageOverride()
            existing.authority = score
            # Persist read_count + usefulness alongside authority for the next refresh
            pu = usage.get(name)
            if pu is not None:
                existing.read_count = pu.read_count
                existing.usefulness = min(1.0, pu.avg_relevance)
            overrides.set(name, existing)

        overrides.prune(set(entries))
        overrides.save()

        return len(scores)
