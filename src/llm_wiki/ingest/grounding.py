from __future__ import annotations

import re
from dataclasses import dataclass


_VISUAL_RE = re.compile(
    r"\b(figure|fig\.|equation|eq\.|table|algorithm|listing)\s*\d",
    re.IGNORECASE,
)
_FORMULA_CHARS_RE = re.compile(r"[α-ωΑ-Ω∀∃∈∉⊂⊃∪∩∫∑∏±×÷≤≥≠≈∞Σσμλβγδεζηθικνξπρτυφχψ]")


@dataclass
class GroundingResult:
    """Grounding verification result for one passage."""
    passage: str
    score: float        # bigram F1 vs source text; 0.0 if unverifiable
    method: str = "ngram"
    verifiable: bool = True
    ocr_sourced: bool = False


def ground_passage(
    passage: str,
    source_text: str,
    ocr_sourced: bool = False,
) -> GroundingResult:
    """Compute bigram F1 between passage and source_text.

    Visual content (figures, equations, formulae) is marked unverifiable
    and assigned score 0.0 — the auditor treats these with a relaxed threshold.
    """
    if _is_visual_content(passage):
        return GroundingResult(
            passage=passage,
            score=0.0,
            verifiable=False,
            ocr_sourced=ocr_sourced,
        )
    score = _bigram_f1(passage, source_text)
    return GroundingResult(
        passage=passage,
        score=score,
        verifiable=True,
        ocr_sourced=ocr_sourced,
    )


def _bigram_f1(a: str, b: str) -> float:
    """Bigram F1 score between strings a and b (case-insensitive word bigrams)."""
    a_bigrams = _bigrams(a.lower())
    b_bigrams = _bigrams(b.lower())
    if not a_bigrams or not b_bigrams:
        return 0.0
    a_set = set(a_bigrams)
    b_set = set(b_bigrams)
    common = len(a_set & b_set)
    precision = common / len(a_set)
    recall = common / len(b_set)
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _bigrams(text: str) -> list[tuple[str, str]]:
    words = re.findall(r"\b\w+\b", text)
    return [(words[i], words[i + 1]) for i in range(len(words) - 1)]


def _is_visual_content(text: str) -> bool:
    """True if text references a figure, equation, table, or contains formula chars."""
    return bool(_VISUAL_RE.search(text) or _FORMULA_CHARS_RE.search(text))
