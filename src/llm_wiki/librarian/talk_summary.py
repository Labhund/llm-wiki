from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.talk.page import TalkEntry

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class TalkSummaryRecord:
    """One entry in the talk-summary sidecar.

    `last_max_index` is a high-water mark: the maximum entry index in the
    talk file at the moment of the last summary. The librarian uses it to
    count entries that arrived after the last summary by checking
    `entry.index > last_max_index`. This is robust to closures: if entries
    get resolved between runs, the open count drops but new arrivals are
    still counted, so the threshold is computed against arrivals not net
    state.
    """
    summary: str
    last_max_index: int
    last_summary_ts: str


class TalkSummaryStore:
    """JSON-backed sidecar of librarian-managed talk-page summaries.

    Atomic writes via temp-file-and-rename so concurrent workers cannot
    corrupt the file. Stored at `<state_dir>/talk_summaries.json` and
    rebuildable from the talk pages on rescan (the wiki itself is the
    source of truth — this is just cached LLM output).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, TalkSummaryRecord] = {}

    @classmethod
    def load(cls, path: Path) -> "TalkSummaryStore":
        store = cls(path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return store
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            store._entries[name] = TalkSummaryRecord(
                summary=str(raw.get("summary", "")),
                last_max_index=int(raw.get("last_max_index", 0) or 0),
                last_summary_ts=str(raw.get("last_summary_ts", "")),
            )
        return store

    def get(self, page_name: str) -> TalkSummaryRecord | None:
        return self._entries.get(page_name)

    def set(
        self,
        page_name: str,
        summary: str,
        last_max_index: int,
        last_summary_ts: str,
    ) -> None:
        self._entries[page_name] = TalkSummaryRecord(
            summary=summary,
            last_max_index=last_max_index,
            last_summary_ts=last_summary_ts,
        )

    def delete(self, page_name: str) -> None:
        self._entries.pop(page_name, None)

    def page_names(self) -> list[str]:
        """Return the list of page names currently tracked in the store."""
        return list(self._entries.keys())

    def prune(self, live_page_names: set[str]) -> int:
        """Drop entries for pages not in `live_page_names`.

        Returns the number of entries removed. The caller is responsible
        for calling `save()` afterwards if anything was pruned.
        """
        stale = [name for name in self._entries if name not in live_page_names]
        for name in stale:
            del self._entries[name]
        return len(stale)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(rec) for name, rec in self._entries.items()}
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise


async def summarize_open_entries(
    entries: list[TalkEntry],
    llm: "LLMClient | None",
    page_name: str = "",
) -> str:
    """Summarize a talk page's open (unresolved) entries in 2 sentences.

    Calls the cheap maintenance LLM via `priority="maintenance"`. Falls back
    to a deterministic count-based summary if the LLM is unreachable or
    raises. Returns "" for an empty input list.
    """
    if not entries:
        return ""

    if llm is None:
        return _deterministic_summary(entries)

    from llm_wiki.librarian.prompts import (
        compose_talk_summary_messages,
        parse_talk_summary,
    )

    try:
        messages = compose_talk_summary_messages(entries)
        response = await llm.complete(
            messages, temperature=0.0, priority="maintenance",
            label=f"librarian:talk-summary:{page_name}",
        )
        summary = parse_talk_summary(response.content)
        if summary:
            return summary
    except Exception:
        logger.warning("talk_summary LLM call failed; using deterministic fallback", exc_info=True)

    return _deterministic_summary(entries)


def _deterministic_summary(entries: list[TalkEntry]) -> str:
    """Build a one-line count-based summary as a fallback for LLM failures.

    Severity ordering is by rank (critical → moderate → minor → suggestion
    → new_connection), not alphabetical. P6A-M4 carryover.
    """
    from llm_wiki.severity import severity_sort_key

    by_severity: dict[str, int] = {}
    for e in entries:
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
    ordered = sorted(by_severity.items(), key=lambda kv: severity_sort_key(kv[0]))
    parts = [f"{count} {sev}" for sev, count in ordered]
    return f"{len(entries)} unresolved talk entries: " + ", ".join(parts) + "."
