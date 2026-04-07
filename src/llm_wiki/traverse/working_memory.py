from __future__ import annotations

from dataclasses import dataclass, field

from llm_wiki.tokens import count_tokens


@dataclass
class PageRead:
    """A page that the engine read during traversal.

    `salient_points` is the model's selective extract — the specific facts
    that materially help answer the question. Empty string is valid and
    meaningful: "I looked at this page and it didn't help." The librarian
    uses these signals to refine manifests and authority scores.
    """
    name: str
    sections_read: list[str]
    salient_points: str
    relevance: float


@dataclass
class NextCandidate:
    name: str
    reason: str
    priority: float


@dataclass
class WorkingMemory:
    query: str
    pages_read: list[PageRead] = field(default_factory=list)
    remaining_questions: list[str] = field(default_factory=list)
    next_candidates: list[NextCandidate] = field(default_factory=list)
    hypothesis: str = ""
    budget_total: int = 16000
    budget_used: int = 0
    turn: int = 0
    answer_complete: bool = False

    @classmethod
    def initial(cls, query: str, budget: int) -> WorkingMemory:
        return cls(
            query=query,
            remaining_questions=[query],
            budget_total=budget,
        )

    @property
    def budget_remaining(self) -> int:
        return max(0, self.budget_total - self.budget_used)

    def to_context_text(self) -> str:
        """Render working memory as text for LLM context."""
        lines: list[str] = []

        if self.pages_read:
            lines.append("## Pages Read")
            for i, p in enumerate(self.pages_read, 1):
                sections = ", ".join(p.sections_read) if p.sections_read else "full"
                points = p.salient_points if p.salient_points else "(no relevant content)"
                lines.append(
                    f"{i}. [[{p.name}]] (sections: {sections}) — {points}"
                )
            lines.append("")

        if self.remaining_questions:
            lines.append("## Remaining Questions")
            for q in self.remaining_questions:
                lines.append(f"- {q}")
            lines.append("")

        if self.hypothesis:
            lines.append("## Current Hypothesis")
            lines.append(self.hypothesis)
            lines.append("")

        return "\n".join(lines)

    def compact(self, target_tokens: int) -> None:
        """Truncate older page salient_points to fit within target_tokens.

        Iterates pages_read in order (oldest first), truncating each page
        whose salient_points exceeds 80 characters down to 77 chars + "...".
        Stops as soon as the rendered context fits within target_tokens.

        Note: this is a best-effort reduction. If all pages are already at
        the minimum length but the context still exceeds target_tokens,
        compact() returns without further action — the engine's budget
        ceiling check is the authoritative backstop for runaway memory.
        """
        for page in self.pages_read:
            if count_tokens(self.to_context_text()) <= target_tokens:
                break
            if len(page.salient_points) > 80:
                page.salient_points = page.salient_points[:77] + "..."

    def to_dict(self) -> dict:
        # answer_complete is transient engine state and is not persisted.
        return {
            "query": self.query,
            "pages_read": [
                {
                    "name": p.name,
                    "sections_read": p.sections_read,
                    "salient_points": p.salient_points,
                    "relevance": p.relevance,
                }
                for p in self.pages_read
            ],
            "remaining_questions": self.remaining_questions,
            "next_candidates": [
                {"name": c.name, "reason": c.reason, "priority": c.priority}
                for c in self.next_candidates
            ],
            "hypothesis": self.hypothesis,
            "budget_total": self.budget_total,
            "budget_used": self.budget_used,
            "turn": self.turn,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkingMemory:
        return cls(
            query=data["query"],
            pages_read=[PageRead(**p) for p in data.get("pages_read", [])],
            remaining_questions=data.get("remaining_questions", []),
            next_candidates=[
                NextCandidate(**c) for c in data.get("next_candidates", [])
            ],
            hypothesis=data.get("hypothesis", ""),
            budget_total=data.get("budget_total", 16000),
            budget_used=data.get("budget_used", 0),
            turn=data.get("turn", 0),
        )
