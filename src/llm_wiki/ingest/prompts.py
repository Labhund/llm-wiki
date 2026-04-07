from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_wiki.ingest.agent import ConceptPlan
    from llm_wiki.ingest.page_writer import PageSection


_CONCEPT_EXTRACTION_SYSTEM = """\
You are analyzing a document to identify its main concepts and entities for a \
knowledge wiki.

## Task

Identify the key concepts, techniques, and entities from the source text. For each:
1. Assign a URL-safe slug (lowercase, hyphens only — e.g. "srna-embeddings")
2. Give a human-readable title (e.g. "sRNA Embeddings")
3. Extract 1-3 short passages from the text that discuss this concept

Focus on concepts with enough substance to warrant their own wiki page. \
Exclude trivial mentions.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "concepts": [
    {
      "name": "concept-slug",
      "title": "Concept Title",
      "passages": ["relevant excerpt from source text"]
    }
  ]
}"""

_PAGE_CONTENT_SYSTEM = """\
You are writing content for a wiki page about a specific concept, based on a \
source document.

## Ingest Rules (Non-Negotiable)

1. Every factual claim MUST end with a citation: [[{{source_ref}}]]
2. Do NOT interpret beyond what the source states
3. If the source says "X correlates with Y", write exactly that — never "X causes Y"
4. Be concise and precise

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{{
  "sections": [
    {{
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown content with [[source]] citations."
    }}
  ]
}}"""


def compose_concept_extraction_messages(
    source_text: str,
    source_ref: str,
    budget: int = 8000,
) -> list[dict[str, str]]:
    """Build the message list for concept extraction."""
    truncated = source_text[: budget * 4]  # rough chars-per-token estimate
    user = (
        f"## Source Reference\n{source_ref}\n\n"
        f"## Source Text\n{truncated}"
    )
    return [
        {"role": "system", "content": _CONCEPT_EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_page_content_messages(
    concept_title: str,
    passages: list[str],
    source_ref: str,
) -> list[dict[str, str]]:
    """Build the message list for page content generation."""
    system = _PAGE_CONTENT_SYSTEM.format(source_ref=source_ref)
    passages_text = "\n\n".join(f"- {p}" for p in passages)
    user = (
        f"## Concept\n{concept_title}\n\n"
        f"## Source Reference\n{source_ref}\n\n"
        f"## Relevant Passages\n{passages_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response (handles fenced blocks)."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in response: {text[:200]}")


def parse_concept_extraction(text: str) -> list[ConceptPlan]:
    """Parse JSON concept extraction response → list of ConceptPlan."""
    from llm_wiki.ingest.agent import ConceptPlan  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        return [
            ConceptPlan(
                name=c["name"],
                title=c.get("title", c["name"]),
                passages=c.get("passages", []),
            )
            for c in data.get("concepts", [])
            if isinstance(c, dict) and "name" in c
        ]
    except (ValueError, KeyError):
        return []


def parse_page_content(text: str) -> list[PageSection]:
    """Parse JSON page content response → list of PageSection."""
    from llm_wiki.ingest.page_writer import PageSection  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        return [
            PageSection(
                name=s["name"],
                heading=s.get("heading", s["name"]),
                content=s.get("content", ""),
            )
            for s in data.get("sections", [])
            if isinstance(s, dict) and "name" in s
        ]
    except (ValueError, KeyError):
        return []
