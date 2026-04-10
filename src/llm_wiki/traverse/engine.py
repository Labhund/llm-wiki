from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.traverse.llm_client import LLMClient
from llm_wiki.traverse.log import TraversalLog, TurnLog
from llm_wiki.traverse.parsing import parse_traverse_response, validate_traverse_response
from llm_wiki.traverse.prompts import (
    compose_synthesize_messages,
    compose_traverse_messages,
    load_prompt,
)
from llm_wiki.traverse.working_memory import NextCandidate, PageRead, WorkingMemory
from llm_wiki.vault import Vault

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[\[([^\]|]+?)(?:#[^\]]+?)?\]\]")


@dataclass
class TraversalResult:
    answer: str
    citations: list[str]
    outcome: str  # "complete", "budget_exceeded", "candidates_exhausted", "turn_limit"
    needs_more_budget: bool
    log: TraversalLog


class TraversalEngine:
    """Multi-turn traversal: search -> read -> update memory -> repeat -> synthesize.

    Optionally persists each TraversalLog to log_dir as JSONL for the librarian.
    """

    def __init__(
        self,
        vault: Vault,
        llm: LLMClient,
        config: WikiConfig,
        vault_root: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._vault = vault
        self._llm = llm
        self._config = config
        self._vault_root = vault_root
        self._log_dir = log_dir

    async def query(self, question: str, budget: int | None = None) -> TraversalResult:
        budget = budget if budget is not None else self._config.budgets.default_query
        max_turns = self._config.budgets.max_traversal_turns
        ceiling = budget * self._config.budgets.hard_ceiling_pct

        memory = WorkingMemory.initial(question, budget)
        log = TraversalLog(query=question, budget=budget)

        traverse_prompt = load_prompt(self._vault_root, "traverse")
        synthesize_prompt = load_prompt(self._vault_root, "synthesize")

        try:
            # -- Turn 0: Search -> manifest -> LLM picks starting pages --
            search_results = self._vault.search(question, limit=10)
            if not search_results:
                # Fallback: if the vault has multiple pages, use the full manifest so the
                # LLM can still orient itself (search may have filtered stop-word queries).
                # If the vault is a single trivial page with no matching content, bail out.
                if self._vault.page_count <= 1:
                    log.outcome = "candidates_exhausted"
                    self._persist_log(log)
                    return TraversalResult(
                        answer="No relevant pages found in the wiki.",
                        citations=[],
                        outcome="candidates_exhausted",
                        needs_more_budget=False,
                        log=log,
                    )
                manifest_text = self._vault.manifest_text(budget=budget // 2)
            else:
                manifest_text = "\n\n".join(
                    r.entry.to_manifest_text() for r in search_results
                )
            # Capture tokens_used for the Turn 0 call as a delta (consistent with Turn N).
            tokens_before_turn0 = memory.budget_used
            turn_data = await self._llm_turn(
                question, memory, manifest_text, traverse_prompt
            )
            self._apply_turn(memory, turn_data)
            log.add_turn(TurnLog(
                turn=0,
                pages_read=[],  # Turn 0 doesn't read a page, just manifest
                tokens_used=memory.budget_used - tokens_before_turn0,
                hypothesis=memory.hypothesis,
                remaining_questions=list(memory.remaining_questions),
                next_candidates=[c.name for c in memory.next_candidates],
            ))

            outcome = self._check_done(memory, ceiling)
            # After turn 0 we've only seen search results / the manifest — no page
            # has been read yet. If the model declares "complete" but there are
            # candidates worth reading, don't trust it: fall through to the read
            # loop so at least the top page is examined. If candidates is empty the
            # model has nothing left to look at, so honour the early exit.
            turn0_premature = (
                outcome == "complete"
                and not memory.pages_read
                and bool(memory.next_candidates)
            )
            if outcome and not turn0_premature:
                return await self._finish(
                    question, memory, outcome, log, synthesize_prompt
                )

            # -- Turns 1..max_turns: Read -> update -> decide --
            visited: set[str] = {p.name for p in memory.pages_read}
            for turn_num in range(1, max_turns + 1):
                memory.turn = turn_num

                candidate = self._pick_candidate(memory, visited)
                if candidate is None:
                    outcome = "candidates_exhausted"
                    break

                visited.add(candidate.name)
                content = self._vault.read_viewport(candidate.name, viewport="top")
                if content is None:
                    logger.warning("Page not found: %s", candidate.name)
                    continue

                # Compact working memory if getting large
                remaining_turns = max(1, max_turns - turn_num + 2)
                memory.compact(budget // remaining_turns)

                tokens_before = memory.budget_used
                turn_data = await self._llm_turn(
                    question, memory, content, traverse_prompt
                )

                page_read = PageRead(
                    name=candidate.name,
                    sections_read=["top"],
                    salient_points=turn_data.get("salient_points", ""),
                    relevance=candidate.priority,
                )
                memory.pages_read.append(page_read)
                self._apply_turn(memory, turn_data)

                tokens_this_turn = memory.budget_used - tokens_before
                log.add_turn(TurnLog(
                    turn=turn_num,
                    pages_read=[page_read],  # Rich PageRead with salient_points
                    tokens_used=tokens_this_turn,
                    hypothesis=memory.hypothesis,
                    remaining_questions=list(memory.remaining_questions),
                    next_candidates=[c.name for c in memory.next_candidates],
                ))

                outcome = self._check_done(memory, ceiling)
                if outcome:
                    break
            else:
                outcome = "turn_limit"

            return await self._finish(
                question, memory, outcome, log, synthesize_prompt
            )
        except Exception:
            if not log.outcome:
                log.outcome = "error"
                log.total_tokens_used = memory.budget_used
                log.pages_visited = [p.name for p in memory.pages_read]
                self._persist_log(log)
            raise

    # -- Internal helpers --

    async def _llm_turn(
        self,
        question: str,
        memory: WorkingMemory,
        content: str,
        system_prompt: str,
    ) -> dict:
        """Run one LLM traversal turn. Returns parsed turn data dict."""
        messages = compose_traverse_messages(
            question, memory, content, system_prompt
        )
        response = await self._llm.complete(messages)
        memory.budget_used += response.tokens_used

        try:
            data = parse_traverse_response(response.content)
            errors = validate_traverse_response(data)
            if errors:
                logger.warning("Validation errors: %s", errors)
        except ValueError:
            logger.warning("Failed to parse LLM response")
            data = {}

        # Fill defaults for missing fields
        data.setdefault("salient_points", "")
        data.setdefault("remaining_questions", list(memory.remaining_questions))
        data.setdefault("next_candidates", [])
        data.setdefault("hypothesis", memory.hypothesis)
        data.setdefault("answer_complete", False)
        return data

    @staticmethod
    def _apply_turn(memory: WorkingMemory, data: dict) -> None:
        """Apply LLM turn output to working memory."""
        memory.remaining_questions = data.get("remaining_questions", [])
        memory.next_candidates = [
            NextCandidate(
                name=c["name"],
                reason=c.get("reason", ""),
                priority=c.get("priority", 0.5),
            )
            for c in data.get("next_candidates", [])
            if isinstance(c, dict) and "name" in c
        ]
        memory.hypothesis = data.get("hypothesis", memory.hypothesis)
        memory.answer_complete = data.get("answer_complete", False)

    @staticmethod
    def _check_done(memory: WorkingMemory, ceiling: float) -> str | None:
        """Check termination criteria. Returns outcome or None to continue."""
        if memory.answer_complete:
            return "complete"
        if memory.budget_used >= ceiling:
            return "budget_exceeded"
        if not memory.next_candidates:
            return "candidates_exhausted"
        return None

    @staticmethod
    def _pick_candidate(
        memory: WorkingMemory, visited: set[str]
    ) -> NextCandidate | None:
        """Pick the highest-priority unvisited candidate."""
        for c in sorted(memory.next_candidates, key=lambda x: -x.priority):
            if c.name not in visited:
                return c
        return None

    async def _finish(
        self,
        question: str,
        memory: WorkingMemory,
        outcome: str,
        log: TraversalLog,
        synthesize_prompt: str,
    ) -> TraversalResult:
        """Synthesize final answer, persist log, build result."""
        messages = compose_synthesize_messages(question, memory, synthesize_prompt)
        response = await self._llm.complete(messages, temperature=0.3)
        memory.budget_used += response.tokens_used

        answer = response.content
        citations = _extract_citations(answer)

        log.outcome = outcome
        log.total_tokens_used = memory.budget_used
        log.pages_visited = [p.name for p in memory.pages_read]

        self._persist_log(log)

        return TraversalResult(
            answer=answer,
            citations=citations,
            outcome=outcome,
            needs_more_budget=(outcome == "budget_exceeded"),
            log=log,
        )

    def _persist_log(self, log: TraversalLog) -> None:
        """Save log to disk if log_dir was configured. Failures are logged, not raised."""
        if self._log_dir is None:
            return
        try:
            log.save(self._log_dir)
        except OSError as exc:
            logger.warning("Failed to persist traversal log: %s", exc)


def _extract_citations(text: str) -> list[str]:
    """Extract unique [[page-name]] citations from text."""
    matches = _CITATION_RE.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        name = m.split("/")[-1]
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result
