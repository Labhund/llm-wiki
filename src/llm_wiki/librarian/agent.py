from __future__ import annotations

import datetime
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

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
    index_regenerated: bool = False
    pages_backfilled: int = 0


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

        # Backfill missing frontmatter fields deterministically (no LLM).
        result.pages_backfilled = self._backfill_frontmatter()

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

        # Regenerate the index from the latest manifest state.
        result.index_regenerated = self._regenerate_index()

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

    def _regenerate_index(self) -> bool:
        """Regenerate wiki/index.md deterministically from the current manifest.

        Groups all ManifestEntry objects by cluster, sorts alphabetically within
        each group, and writes wiki/index.md atomically. The "root" cluster
        always appears last. The index page itself is excluded.

        Returns True if the file was written, False if there were no entries.
        """
        entries = self._vault.manifest_entries()
        if not entries:
            return False

        # Group by cluster, skipping the index page itself
        clusters: dict[str, list] = {}
        for entry in entries.values():
            if entry.name == "index":
                continue
            clusters.setdefault(entry.cluster, []).append(entry)

        if not clusters:
            return False

        # Sort cluster names alphabetically, with "root" last
        sorted_clusters = sorted(
            clusters.keys(),
            key=lambda c: (c == "root", c),
        )

        lines: list[str] = [
            "# Index",
            "",
            "<!-- auto-generated by librarian — do not edit manually -->",
            "",
        ]

        for cluster_name in sorted_clusters:
            heading = "Root" if cluster_name == "root" else cluster_name.replace("-", " ").title()
            lines.append(f"## {heading}")
            lines.append("")
            cluster_entries = sorted(clusters[cluster_name], key=lambda e: e.name)
            for entry in cluster_entries:
                description = entry.summary if entry.summary else entry.title
                lines.append(f"- [[{entry.name}]] \u2014 {description}")
            lines.append("")

        content = "\n".join(lines)

        index_path = self._vault_root / "wiki" / "index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to a temp file in the same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=index_path.parent, prefix=".index-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, index_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info("Librarian: regenerated wiki/index.md (%d clusters)", len(sorted_clusters))
        return True

    def _backfill_frontmatter(self) -> int:
        """Backfill missing frontmatter fields deterministically (no LLM call).

        For each page file that is missing any of the standard structural fields
        (created, updated, type, status, ingested), this method computes their
        values from filesystem / git metadata and writes them back to the file.

        Rules:
        - ``created``: git log mtime of the file, or stat().st_mtime fallback.
          Only written if absent.
        - ``updated``: same date as ``created`` for the initial backfill.
          Only written if absent.
        - ``type``: set to ``"concept"`` when the page has body content.
          Only written if absent.
        - ``status``: set to ``"stub"`` when ``created_by: ingest`` and absent.
        - ``ingested``: same date as ``created`` when ``created_by: ingest``
          and field is absent.

        Only touches pages where at least one field is actually missing.
        Does NOT modify the page body or fields that are already present.

        Returns:
            Number of page files that were modified.
        """
        wiki_dir = self._vault_root / "wiki"
        if not wiki_dir.exists():
            return 0

        md_files = sorted(wiki_dir.rglob("*.md"))
        md_files = [
            f for f in md_files
            if not any(p.startswith(".") for p in f.relative_to(wiki_dir).parts)
            and not f.name.endswith(".talk.md")
            and f.relative_to(wiki_dir) != Path("index.md")
        ]

        modified = 0
        for md_file in md_files:
            try:
                changed = self._backfill_page(md_file)
            except Exception:
                logger.exception("Librarian: backfill failed for %s", md_file)
                continue
            if changed:
                modified += 1

        logger.info("Librarian: backfilled frontmatter on %d page(s)", modified)
        return modified

    def _backfill_page(self, md_file: Path) -> bool:
        """Backfill one page file. Returns True if the file was modified."""
        from llm_wiki.page import _split_frontmatter

        raw = md_file.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)

        # Determine which fields are missing
        needs_created = "created" not in frontmatter
        needs_updated = "updated" not in frontmatter
        # type: backfill as "concept" if page has body content
        needs_type = "type" not in frontmatter and body.strip()
        # status/ingested are only relevant for ingest-created pages
        created_by_ingest = frontmatter.get("created_by") == "ingest"
        needs_status = "status" not in frontmatter and created_by_ingest
        needs_ingested = "ingested" not in frontmatter and created_by_ingest

        if not any([needs_created, needs_updated, needs_type, needs_status, needs_ingested]):
            return False

        # Compute the date to use for created/updated/ingested
        date_str = self._get_file_date(md_file)

        if needs_created:
            frontmatter["created"] = date_str
        if needs_updated:
            frontmatter["updated"] = date_str
        if needs_type:
            frontmatter["type"] = "concept"
        if needs_status:
            frontmatter["status"] = "stub"
        if needs_ingested:
            frontmatter["ingested"] = date_str

        # Reconstruct the file: YAML-dump new frontmatter + original body
        fm_text = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
        new_raw = f"---\n{fm_text}---\n\n{body}"
        md_file.write_text(new_raw, encoding="utf-8")
        logger.debug("Librarian: backfilled %s", md_file.name)
        return True

    @staticmethod
    def _get_file_date(path: Path) -> str:
        """Return a YYYY-MM-DD string for the given file.

        Tries ``git log --follow -1 --format=%ai`` first; falls back to
        ``stat().st_mtime`` if git is unavailable or produces no output.
        """
        try:
            result = subprocess.run(
                ["git", "log", "--follow", "-1", "--format=%ai", "--", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            line = result.stdout.strip()
            if line:
                # %ai format: "2024-01-15 12:34:56 +0000" — take the date part
                return line[:10]
        except Exception:
            pass

        # Fallback: filesystem mtime
        mtime = path.stat().st_mtime
        return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

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
                summary = await summarize_open_entries(open_entries, self._llm)
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
            messages, temperature=0.4, priority="maintenance"
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
