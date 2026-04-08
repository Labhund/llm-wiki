from __future__ import annotations

import json
import re
from typing import Literal

from llm_wiki.adversary.claim_extractor import Claim

Verdict = Literal["validated", "contradicted", "unsupported", "ambiguous"]
_VALID_VERDICTS = {"validated", "contradicted", "unsupported", "ambiguous"}


_ADVERSARY_SYSTEM = """\
You are the adversary for a wiki. Your job is to verify whether a wiki claim \
is actually supported by the raw source it cites.

## Task

You will see ONE wiki claim and the text of the raw source it cites. Decide \
which of these verdicts applies:

- "validated"     — The source clearly supports the claim as written.
- "contradicted"  — The source clearly says something different, in a way that \
                    makes the claim wrong.
- "unsupported"   — The claim is not actually present in the source, even \
                    though the source is on-topic.
- "ambiguous"     — The source could be read either way, or you cannot tell.

Be strict. If the source says "X correlates with Y" but the claim says "X causes \
Y", that is "contradicted" — you must NOT extend the source's interpretation.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "verdict": "validated|contradicted|unsupported|ambiguous",
  "confidence": 0.85,
  "explanation": "One or two sentences explaining your verdict."
}"""


def compose_verification_messages(
    claim: Claim,
    raw_text: str,
    max_chars: int = 8000,
) -> list[dict[str, str]]:
    """Build the verification prompt for one claim against its raw source."""
    truncated = raw_text[:max_chars]
    user = (
        f"## Wiki Page\n{claim.page}\n\n"
        f"## Section\n{claim.section}\n\n"
        f"## Wiki Claim\n{claim.text}\n\n"
        f"## Cited Source\n{claim.citation}\n\n"
        f"## Source Text\n{truncated}"
    )
    return [
        {"role": "system", "content": _ADVERSARY_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
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


def parse_verification(text: str) -> tuple[Verdict | None, float, str]:
    """Parse an adversary LLM response into (verdict, confidence, explanation).

    Invalid verdicts return None. Missing confidence/explanation default to
    0.0 / empty string.
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None, 0.0, ""

    raw_verdict = data.get("verdict")
    if not isinstance(raw_verdict, str) or raw_verdict not in _VALID_VERDICTS:
        return None, 0.0, ""

    raw_confidence = data.get("confidence", 0.0)
    confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else 0.0

    raw_explanation = data.get("explanation", "")
    explanation = raw_explanation if isinstance(raw_explanation, str) else ""

    return raw_verdict, confidence, explanation  # type: ignore[return-value]
