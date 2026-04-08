from __future__ import annotations

import json
import re

from llm_wiki.librarian.log_reader import PageUsage


_LIBRARIAN_SYSTEM = """\
You are the librarian for a wiki, refining a page's manifest entry based on \
how the page is actually being used.

## Task

Given the page content and recent traversal usage signals, propose:
1. Updated tags — 3 to 7 lowercase hyphenated tags that reflect what queries \
this page actually answers
2. A one-sentence summary that describes what the page covers, prioritizing \
how it has been used over the page's stated topic

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "tags": ["tag-a", "tag-b", "tag-c"],
  "summary": "One sentence describing the page."
}"""


def compose_refinement_messages(
    page_name: str,
    page_title: str,
    page_content: str,
    usage: PageUsage,
    page_content_chars: int = 4000,
) -> list[dict[str, str]]:
    """Build the message list for tag/summary refinement."""
    truncated = page_content[:page_content_chars]

    usage_lines: list[str] = []
    if usage.queries:
        usage_lines.append("## Recent Queries")
        for q in usage.queries:
            usage_lines.append(f"- {q}")
    if usage.salient_samples:
        usage_lines.append("\n## Recent Salient Points")
        for s in usage.salient_samples:
            usage_lines.append(f"- {s}")
    if not usage_lines:
        usage_lines.append("## Usage")
        usage_lines.append("(no recent traversal data)")

    usage_section = "\n".join(usage_lines)

    user = (
        f"## Page\n{page_name}\n\n"
        f"## Title\n{page_title}\n\n"
        f"## Page Content\n{truncated}\n\n"
        f"{usage_section}"
    )
    return [
        {"role": "system", "content": _LIBRARIAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from an LLM response (handles fenced blocks)."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    bare = re.search(r"\{.*\}", text, re.DOTALL)
    if bare:
        try:
            return json.loads(bare.group(0))
        except json.JSONDecodeError:
            pass
    return None


def parse_refinement(text: str) -> tuple[list[str], str | None]:
    """Parse a librarian LLM response into (tags, summary)."""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return [], None

    raw_tags = data.get("tags")
    if isinstance(raw_tags, list):
        tags = [t for t in raw_tags if isinstance(t, str) and t]
    else:
        tags = []

    raw_summary = data.get("summary")
    summary = raw_summary if isinstance(raw_summary, str) and raw_summary.strip() else None

    return tags, summary
