"""Helpers for the synthesis cache feature.

Synthesis pages are first-class wiki pages (type: synthesis) written after
cited query answers. This module handles: query → slug, LLM action parsing,
page content building.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Match a JSON object starting at the very beginning of the (stripped) response.
_JSON_OBJECT_RE = re.compile(r"^\s*(\{[^}]*\})", re.DOTALL)


def slug_from_query(query: str) -> str:
    """Convert a query string to a filesystem-safe slug (max 60 chars)."""
    slug = _SLUG_RE.sub("-", query.lower()).strip("-")
    slug = slug[:60] if len(slug) > 60 else slug
    return slug or "query"


def parse_synthesis_action(response: str) -> dict | None:
    """Parse the optional JSON action envelope from the start of a synthesize response.

    Returns the action dict if present and valid, else None.
    Valid actions: "accept", "update", "create".
    """
    m = _JSON_OBJECT_RE.match(response.strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "action" not in data:
        return None
    if data["action"] not in ("accept", "update", "create"):
        return None
    return data


def extract_prose_after_action(response: str) -> str:
    """Return the prose answer that follows the JSON action block.

    If no JSON block is present, returns the full response.
    If the JSON block covers the entire response, returns it as-is (accept path).
    """
    stripped = response.strip()
    m = _JSON_OBJECT_RE.match(stripped)
    if not m:
        return stripped
    prose = stripped[m.end():].strip()
    return prose if prose else stripped


def build_synthesis_page_content(
    title: str,
    query: str,
    answer: str,
    sources: list[str],
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Build the full markdown text for a new or updated synthesis page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = created_at or now
    updated = updated_at or now

    if sources:
        sources_yaml = "\n".join(f"  - {s}" for s in sources)
        sources_block = f"sources:\n{sources_yaml}"
    else:
        sources_block = "sources: []"

    frontmatter = (
        f"---\n"
        f"title: {json.dumps(title)}\n"
        f"type: synthesis\n"
        f"query: {json.dumps(query)}\n"
        f"created_by: query\n"
        f"created_at: {created}\n"
        f"updated_at: {updated}\n"
        f"{sources_block}\n"
        f"---\n"
    )
    body = f"\n%% section: answer %%\n\n{answer}\n"
    return frontmatter + body
