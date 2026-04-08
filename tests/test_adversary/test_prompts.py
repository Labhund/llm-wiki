from __future__ import annotations

from llm_wiki.adversary.claim_extractor import Claim
from llm_wiki.adversary.prompts import (
    compose_verification_messages,
    parse_verification,
)


def _claim() -> Claim:
    return Claim(
        page="srna-embeddings",
        section="method",
        text="The k-means algorithm uses k=10 clusters [[raw/smith-2026.pdf]].",
        citation="raw/smith-2026.pdf",
    )


def test_compose_verification_messages_includes_claim_and_source():
    messages = compose_verification_messages(_claim(), raw_text="Source text body here.")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "k=10 clusters" in user
    assert "Source text body here." in user
    assert "srna-embeddings" in user
    assert "raw/smith-2026.pdf" in user


def test_compose_verification_messages_truncates_long_source():
    """Very long raw text is truncated to fit within the prompt budget."""
    long_text = "x" * 100_000
    messages = compose_verification_messages(_claim(), raw_text=long_text, max_chars=4000)
    user = messages[1]["content"]
    # Should contain a truncated version, not the full text
    assert len(user) < 100_000
    assert "x" in user


def test_parse_verification_validated():
    text = '{"verdict": "validated", "confidence": 0.9, "explanation": "Source matches."}'
    verdict, confidence, explanation = parse_verification(text)
    assert verdict == "validated"
    assert confidence == 0.9
    assert explanation == "Source matches."


def test_parse_verification_contradicted():
    text = '{"verdict": "contradicted", "confidence": 0.85, "explanation": "Source says k=5, not k=10."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "contradicted"


def test_parse_verification_unsupported():
    text = '{"verdict": "unsupported", "confidence": 0.7, "explanation": "Claim not in source."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "unsupported"


def test_parse_verification_ambiguous():
    text = '{"verdict": "ambiguous", "confidence": 0.5, "explanation": "Source unclear."}'
    verdict, _, _ = parse_verification(text)
    assert verdict == "ambiguous"


def test_parse_verification_invalid_verdict_returns_none():
    text = '{"verdict": "maybe", "confidence": 0.5, "explanation": "x"}'
    verdict, _, _ = parse_verification(text)
    assert verdict is None


def test_parse_verification_garbage_returns_none():
    verdict, confidence, explanation = parse_verification("not JSON")
    assert verdict is None
    assert confidence == 0.0
    assert explanation == ""


def test_parse_verification_missing_fields_safe_defaults():
    text = '{"verdict": "validated"}'
    verdict, confidence, explanation = parse_verification(text)
    assert verdict == "validated"
    assert confidence == 0.0
    assert explanation == ""


def test_parse_verification_fenced_json():
    text = """```json
{"verdict": "validated", "confidence": 0.9, "explanation": "ok"}
```"""
    verdict, _, _ = parse_verification(text)
    assert verdict == "validated"
