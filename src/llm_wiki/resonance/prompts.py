from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResonanceVerdict:
    resonates: bool
    relation: str | None  # corroborates | extends | contradicts
    note: str | None


def compose_resonance_messages(
    new_claim: str,
    new_source: str,
    existing_claim: str,
    existing_page: str,
) -> list[dict]:
    """Compose messages for an LLM resonance assessment call."""
    return [
        {
            "role": "system",
            "content": (
                "You determine whether a claim from a newly ingested source "
                "meaningfully connects to an existing wiki claim. "
                "Be conservative — minor vocabulary overlap does not count as resonance. "
                "Resonance means the claims are about the same phenomenon and one "
                "corroborates, extends, or contradicts the other."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Existing claim on wiki page [[{existing_page}]]:\n"
                f"> {existing_claim}\n\n"
                f"New claim from {new_source}:\n"
                f"> {new_claim}\n\n"
                "Do these claims meaningfully resonate?\n\n"
                "Answer format (exactly as shown):\n"
                "VERDICT: YES|NO\n"
                "RELATION: corroborates|extends|contradicts  (only if YES)\n"
                "NOTE: <one sentence>  (only if YES)"
            ),
        },
    ]


def parse_resonance(response: str) -> ResonanceVerdict:
    """Parse an LLM resonance assessment response.

    Returns a non-resonating verdict for any malformed response to avoid
    filing spurious talk posts.
    """
    for line in response.splitlines():
        if line.strip().startswith("VERDICT:"):
            verdict_value = line.split(":", 1)[1].strip().upper()
            if verdict_value != "YES":
                return ResonanceVerdict(resonates=False, relation=None, note=None)
            break
    else:
        return ResonanceVerdict(resonates=False, relation=None, note=None)

    relation: str | None = None
    note: str | None = None
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith("RELATION:"):
            relation = stripped.split(":", 1)[1].strip().lower()
        elif stripped.startswith("NOTE:"):
            note = stripped.split(":", 1)[1].strip()

    return ResonanceVerdict(resonates=True, relation=relation, note=note)
