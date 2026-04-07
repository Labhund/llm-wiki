from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.traverse.working_memory import PageRead


@dataclass
class TurnLog:
    """One turn of traversal. pages_read carries full PageRead with salient_points
    so the librarian can analyze what was actually useful per query.
    """
    turn: int
    pages_read: list[PageRead]
    tokens_used: int
    hypothesis: str
    remaining_questions: list[str]
    next_candidates: list[str]

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "pages_read": [
                {
                    "name": p.name,
                    "sections_read": p.sections_read,
                    "salient_points": p.salient_points,
                    "relevance": p.relevance,
                }
                for p in self.pages_read
            ],
            "tokens_used": self.tokens_used,
            "hypothesis": self.hypothesis,
            "remaining_questions": self.remaining_questions,
            "next_candidates": self.next_candidates,
        }


@dataclass
class TraversalLog:
    """Persistent record of one query → answer flow.

    Persisted as one JSON line per query in traversal_logs.jsonl. The librarian
    (Phase 5) consumes this to refine manifests, update authority scores, and
    identify pages that surface in search but don't actually help users.
    """
    query: str
    budget: int
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    turns: list[TurnLog] = field(default_factory=list)
    outcome: str = ""
    total_tokens_used: int = 0
    pages_visited: list[str] = field(default_factory=list)

    def add_turn(self, turn: TurnLog) -> None:
        self.turns.append(turn)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "budget": self.budget,
            "timestamp": self.timestamp,
            "turns": [t.to_dict() for t in self.turns],
            "outcome": self.outcome,
            "total_tokens_used": self.total_tokens_used,
            "pages_visited": self.pages_visited,
        }

    def save(self, log_dir: Path) -> None:
        """Append this log as one JSON line to traversal_logs.jsonl in log_dir."""
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "traversal_logs.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict()) + "\n")
