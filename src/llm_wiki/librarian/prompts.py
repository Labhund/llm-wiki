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


def compose_talk_summary_messages(entries: "list[TalkEntry]") -> list[dict[str, str]]:
    """Build a 2-message prompt asking for a 2-sentence digest of open talk entries.

    The librarian uses this when refreshing a talk-page summary. Entries are
    formatted compactly so the cheap maintenance model can read them all in
    a single small prompt.
    """
    from llm_wiki.talk.page import TalkEntry  # local import to avoid cycles

    body_lines = []
    for e in entries:
        body_lines.append(
            f"[#{e.index} {e.severity} by {e.author}] {e.body.strip()}"
        )
    body_text = "\n".join(body_lines)

    return [
        {
            "role": "system",
            "content": (
                "You are summarizing the unresolved discussion on a wiki talk page. "
                "Produce a single 2-sentence digest that an active reader can use "
                "to decide whether to investigate further. Do not list individual "
                "entries — synthesize."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Unresolved entries on this talk page:\n\n{body_text}\n\n"
                f"Write a 2-sentence summary."
            ),
        },
    ]


def parse_talk_summary(text: str) -> str:
    """Extract a clean 2-sentence summary from the LLM response.

    The cheap model often wraps its output in quotes or prefixes. Strip
    common decoration. Returns an empty string if the response is empty.
    """
    if not text:
        return ""
    cleaned = text.strip()
    # Strip surrounding quotes
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    # Strip a leading "Summary:" prefix
    for prefix in ("Summary:", "summary:", "SUMMARY:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned


def compose_commit_summary_messages(
    author: str,
    entries: "list[JournalEntry]",
) -> list[dict[str, str]]:
    """Build a 2-message prompt asking for a commit summary.

    The cheap maintenance LLM gets the journal entries and produces a
    one-line subject (≤60 chars) plus 2-5 bullet points. The settle
    pipeline parses this into the commit body.
    """
    body_lines = []
    for e in entries:
        intent = e.intent or ""
        body_lines.append(
            f"- {e.tool} {e.path}: {e.summary}"
            + (f" — {intent}" if intent else "")
        )
    body_text = "\n".join(body_lines)

    return [
        {
            "role": "system",
            "content": (
                "You write git commit messages for wiki edits made by AI agents. "
                "Format: a single one-line subject (max 60 characters), then a "
                "blank line, then 2-5 bullet points describing what changed and "
                "why. Use the intent field when present."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Here are {len(entries)} wiki edits from one session by agent {author}:\n\n"
                f"{body_text}\n\n"
                f"Produce the commit message."
            ),
        },
    ]


def parse_commit_summary(text: str) -> tuple[str, list[str]]:
    """Split LLM commit-message output into (subject, bullets).

    Returns ("", []) if the response is empty or unparseable. The subject
    is truncated to 60 characters; bullets are taken from lines starting
    with `-` or `*`.
    """
    if not text or not text.strip():
        return "", []
    cleaned = text.strip()
    parts = cleaned.split("\n\n", 1)
    subject_block = parts[0].strip()
    rest = parts[1] if len(parts) > 1 else ""

    # Subject is the first non-empty line of the subject block
    subject_lines = [l for l in subject_block.splitlines() if l.strip()]
    subject = subject_lines[0].strip() if subject_lines else ""
    if len(subject) > 60:
        subject = subject[:57] + "..."

    bullets: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            bullets.append(stripped[1:].strip())

    return subject, bullets
