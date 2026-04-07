from __future__ import annotations

import json
import re

REQUIRED_TRAVERSE_FIELDS = {
    "salient_points",
    "remaining_questions",
    "next_candidates",
    "hypothesis",
    "answer_complete",
}


def parse_traverse_response(text: str) -> dict:
    """Extract and parse JSON from an LLM traverse response.

    Handles: raw JSON, JSON in markdown code blocks, JSON embedded in text.
    Raises ValueError if no valid JSON found.
    """
    # Try direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def validate_traverse_response(data: dict) -> list[str]:
    """Validate structural contracts. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    missing = REQUIRED_TRAVERSE_FIELDS - set(data.keys())
    if missing:
        errors.append(f"Missing required fields: {missing}")

    if "next_candidates" in data:
        candidates = data["next_candidates"]
        if not isinstance(candidates, list):
            errors.append("next_candidates must be a list")
        else:
            for i, c in enumerate(candidates):
                if not isinstance(c, dict):
                    errors.append(f"next_candidates[{i}] must be an object")
                elif "name" not in c:
                    errors.append(f"next_candidates[{i}] missing 'name'")

    if "answer_complete" in data and not isinstance(data["answer_complete"], bool):
        errors.append("answer_complete must be a boolean")

    return errors
