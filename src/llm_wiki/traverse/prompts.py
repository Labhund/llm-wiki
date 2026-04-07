from __future__ import annotations

from pathlib import Path

from llm_wiki.traverse.working_memory import WorkingMemory

DEFAULT_TRAVERSE_PROMPT = """\
You are a research assistant navigating a wiki to answer a specific question. \
Each turn, you receive new content (search results or a page) and decide what \
to keep, what to ignore, and where to look next.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No other text outside the JSON. Required fields:

- "salient_points": string — the SPECIFIC facts, claims, or quotes from the new \
content that materially help answer the question. Cite source pages with \
[[page-name]]. Be SELECTIVE: short bullet-style is fine, paragraphs are fine, but \
do not summarize the whole page. If the content was not useful for this question, \
return an empty string "" — that is a valid and meaningful answer (it tells the \
librarian this page surfaced but did not help).

- "remaining_questions": list of strings — sub-questions still unanswered. Refine \
this as you learn; the goal is to reach an empty list.

- "next_candidates": list of {"name": "page-name", "reason": "why this page", \
"priority": float 0-1} — wiki pages to read next, ordered by expected value. \
Empty list if you are done or if nothing else looks promising. Only suggest pages \
you actually saw mentioned in the manifest, page links, or search results.

- "hypothesis": string — your current working theory for the answer. Update as \
evidence accumulates. This is your "best guess so far."

- "answer_complete": boolean — true ONLY when remaining_questions is empty AND you \
are confident the hypothesis fully answers the original question. False if uncertain.

Selectivity matters more than thoroughness. Generic summaries waste tokens and \
dilute signal. Sharp, specific findings — or an honest empty string — are best."""

DEFAULT_SYNTHESIZE_PROMPT = """\
Synthesize a clear, well-organized answer from your research notes.

## Structural Contract (Non-Negotiable)

- Every factual claim MUST cite a wiki page: [[page-name]] or [[page-name#section]]
- If information is incomplete, state what is missing explicitly
- Do not invent information not in your research notes
- Be concise but thorough"""


def load_prompt(vault_root: Path | None, name: str) -> str:
    """Load a prompt from vault override or return the built-in default.

    Checks {vault_root}/schema/prompts/{name}.md for an override.
    Falls back to built-in defaults.
    """
    if vault_root is not None:
        override = vault_root / "schema" / "prompts" / f"{name}.md"
        if override.exists():
            return override.read_text(encoding="utf-8")

    defaults = {
        "traverse": DEFAULT_TRAVERSE_PROMPT,
        "synthesize": DEFAULT_SYNTHESIZE_PROMPT,
    }
    if name not in defaults:
        raise ValueError(
            f"Unknown prompt name {name!r}. Known: {sorted(defaults)}"
        )
    return defaults[name]


def compose_traverse_messages(
    query: str,
    memory: WorkingMemory,
    new_content: str,
    system_prompt: str,
) -> list[dict[str, str]]:
    """Build the message list for a traversal turn."""
    memory_text = memory.to_context_text() or "No pages read yet."

    user_content = (
        f"## Question\n{query}\n\n"
        f"## Working Memory\n{memory_text}\n\n"
        f"## New Content\n{new_content}\n\n"
        f"## Budget\n{memory.budget_remaining} tokens remaining "
        f"of {memory.budget_total}. Turn {memory.turn}."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def compose_synthesize_messages(
    query: str,
    memory: WorkingMemory,
    system_prompt: str,
) -> list[dict[str, str]]:
    """Build the message list for final synthesis."""
    notes = memory.to_context_text() or "No research notes available."
    user_content = (
        f"## Question\n{query}\n\n"
        f"## Research Notes\n{notes}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
