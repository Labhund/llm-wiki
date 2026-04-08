from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_SAMPLE_CAP = 5


@dataclass
class PageUsage:
    """Per-page usage signals aggregated from traversal_logs.jsonl."""
    name: str
    read_count: int = 0              # distinct queries that read this page
    turn_appearances: int = 0        # total turn-level appearances
    total_relevance: float = 0.0
    salient_samples: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    @property
    def avg_relevance(self) -> float:
        if self.turn_appearances == 0:
            return 0.0
        return self.total_relevance / self.turn_appearances


def aggregate_logs(log_path: Path) -> dict[str, PageUsage]:
    """Walk a traversal_logs.jsonl file and produce per-page usage signals.

    Returns an empty dict if the file does not exist or is empty. The
    most recent SAMPLE_CAP salient_points and queries per page are kept.

    A page that appears in multiple turns of the same query is counted
    once toward read_count but its turn_appearances and total_relevance
    accumulate normally.
    """
    usage: dict[str, PageUsage] = {}
    if not log_path.exists():
        return usage

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            query = entry.get("query") or ""
            seen_in_query: set[str] = set()
            for turn in entry.get("turns") or []:
                for page in turn.get("pages_read") or []:
                    name = page.get("name")
                    if not name:
                        continue
                    pu = usage.setdefault(name, PageUsage(name=name))
                    if name not in seen_in_query:
                        pu.read_count += 1
                        seen_in_query.add(name)
                    pu.turn_appearances += 1
                    relevance = page.get("relevance")
                    if isinstance(relevance, (int, float)):
                        pu.total_relevance += float(relevance)
                    salient = page.get("salient_points")
                    if isinstance(salient, str) and salient.strip():
                        pu.salient_samples.append(salient)
            # Track which queries each page appeared in (for prompt context)
            for name in seen_in_query:
                if query:
                    usage[name].queries.append(query)

    for pu in usage.values():
        pu.salient_samples = pu.salient_samples[-_SAMPLE_CAP:]
        pu.queries = pu.queries[-_SAMPLE_CAP:]

    return usage
