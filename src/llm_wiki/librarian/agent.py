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

    async def run(self) -> LibrarianResult:
        """Full librarian pass: refresh candidates above threshold, then recalc authority."""
        result = LibrarianResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        threshold = self._config.budgets.manifest_refresh_after_traversals
        usage = aggregate_logs(self._log_path)
        overrides = ManifestOverrides.load(self._overrides_path)

        # Identify refresh candidates: read_count - last_refreshed_read_count >= threshold
        candidates: list[str] = []
        for name, pu in usage.items():
            if name not in entries:
                continue
            existing = overrides.get(name)
            last_refreshed = existing.last_refreshed_read_count if existing else 0
            if pu.read_count - last_refreshed >= threshold:
                candidates.append(name)

        # Refresh each candidate via LLM
        for name in candidates:
            try:
                refreshed = await self.refresh_page(name)
            except Exception:
                logger.exception("Librarian: refresh_page failed for %s", name)
                continue
            if refreshed:
                result.pages_refined.append(name)

        # Recalculate authority for everything afterwards (uses the latest overrides).
        # Reuse the already-aggregated usage so we don't scan the log file twice.
        result.authorities_updated = await self.recalc_authority(usage=usage)
        return result

    async def recalc_authority(
        self, usage: dict[str, PageUsage] | None = None
    ) -> int:
        """Recompute authority for every entry and persist via overrides.

        Args:
            usage: Optional pre-aggregated log usage. When ``None`` (the
                default used by the ``authority_recalc`` scheduled worker),
                this method loads logs itself. ``LibrarianAgent.run`` passes
                its own aggregation to avoid re-scanning the log file.

        Returns:
            The number of authority values written.
        """
        entries = self._vault.manifest_entries()
        if not entries:
            return 0

        if usage is None:
            usage = aggregate_logs(self._log_path)
        scores = compute_authority(
            entries,
            usage,
            synthesis_boost=self._config.maintenance.synthesis_authority_boost,
        )

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

    async def refresh_talk_summaries(self) -> int:
        """Refresh stale talk-page summaries.

        For each `*.talk.md` in the wiki, load entries and compute the open
        set. Summarize via the cheap maintenance LLM iff:
          - the number of OPEN entries with `index > last_max_index` (the
            high-water mark from the last summary) is at least
            `config.maintenance.talk_summary_min_new_entries`. This counts
            new arrivals that are still unresolved, so closures of older
            entries between runs do not mask new arrivals.
          - at least `config.maintenance.talk_summary_min_interval_seconds`
            have passed since the last summary.

        After summarizing, the store's `last_max_index` is set to the
        highest entry index in the file (open or resolved) — that becomes
        the high-water mark for the next run.

        Returns the number of pages whose summary was refreshed.
        """
        import datetime as _dt
        from llm_wiki.librarian.talk_summary import (
            TalkSummaryStore,
            summarize_open_entries,
        )
        from llm_wiki.talk.page import compute_open_set, iter_talk_pages

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        if not wiki_dir.exists():
            return 0

        store = TalkSummaryStore.load(self._state_dir / "talk_summaries.json")
        threshold = self._config.maintenance.talk_summary_min_new_entries
        min_interval = self._config.maintenance.talk_summary_min_interval_seconds
        now = _dt.datetime.now(_dt.timezone.utc)
        refreshed = 0
        live_page_names: set[str] = set()

        for page_name, talk in iter_talk_pages(wiki_dir):
            # Track every live talk file (even ones that won't be summarized
            # this round) so the prune step at the bottom doesn't drop them.
            live_page_names.add(page_name)

            entries = talk.load()
            if not entries:
                continue
            open_entries = compute_open_set(entries)

            current_max_index = max(e.index for e in entries)

            existing = store.get(page_name)
            high_water = existing.last_max_index if existing else 0

            # Count NEW unresolved entries: open AND index > high_water.
            # Resilient to closures: a closure between runs only removes
            # entries from open_entries; new arrivals are still counted.
            new_unresolved = sum(1 for e in open_entries if e.index > high_water)
            if new_unresolved < threshold:
                continue

            # Rate limit: don't re-summarize a page within min_interval seconds
            if existing is not None:
                try:
                    last_ts = _dt.datetime.fromisoformat(existing.last_summary_ts)
                except ValueError:
                    last_ts = None
                if last_ts is not None:
                    elapsed = (now - last_ts).total_seconds()
                    if elapsed < min_interval:
                        continue

            try:
                summary = await summarize_open_entries(open_entries, self._llm, page_name=page_name)
            except Exception:
                logger.exception("Failed to summarize talk page %s", page_name)
                continue
            if not summary:
                continue

            store.set(
                page_name,
                summary=summary,
                last_max_index=current_max_index,
                last_summary_ts=now.isoformat(),
            )
            refreshed += 1

        # Prune entries for talk files that no longer exist on disk.
        # Mirrors the `overrides.prune(set(entries))` discipline applied to
        # ManifestOverrides above. Save when either pruning or refreshing
        # produced a change.
        pruned = store.prune(live_page_names)
        if refreshed > 0 or pruned > 0:
            store.save()
        return refreshed

    async def refresh_page(self, page_name: str) -> bool:
        """Refine tags + summary for a single page via LLM.

        Returns True if the override was updated, False if the page is
        unknown or the LLM response could not be parsed.
        """
        from llm_wiki.librarian.prompts import (
            compose_refinement_messages,
            parse_refinement,
        )

        page = self._vault.read_page(page_name)
        if page is None:
            return False

        usage = aggregate_logs(self._log_path).get(page_name) or PageUsage(name=page_name)

        messages = compose_refinement_messages(
            page_name=page_name,
            page_title=page.title,
            page_content=page.raw_content,
            usage=usage,
        )

        response = await self._llm.complete(
            messages, temperature=0.4, priority="maintenance",
            label="librarian:refine-manifest",
        )
        tags, summary = parse_refinement(response.content)

        if not tags and summary is None:
            logger.info("Librarian: empty refinement for %s, skipping write", page_name)
            return False

        overrides = ManifestOverrides.load(self._overrides_path)
        existing = overrides.get(page_name) or PageOverride()
        if tags:
            existing.tags = tags
        if summary is not None:
            existing.summary_override = summary
        existing.read_count = usage.read_count
        existing.usefulness = min(1.0, usage.avg_relevance)
        existing.last_refreshed_read_count = usage.read_count
        overrides.set(page_name, existing)
        overrides.save()
        return True
