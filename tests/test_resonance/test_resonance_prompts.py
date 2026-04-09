from llm_wiki.resonance.prompts import compose_resonance_messages, parse_resonance


def test_compose_includes_both_claims():
    msgs = compose_resonance_messages(
        new_claim="Diffusion noise scale controls structural diversity.",
        new_source="raw/2026-04-10-rfdiffusion.pdf",
        existing_claim="Diffusion models produce diverse outputs via noise injection.",
        existing_page="diffusion-models",
    )
    assert len(msgs) == 2
    user_text = msgs[1]["content"]
    assert "diffusion-models" in user_text
    assert "rfdiffusion" in user_text


def test_parse_resonance_yes():
    response = "VERDICT: YES\nRELATION: corroborates\nNOTE: Both discuss noise as a diversity control."
    verdict = parse_resonance(response)
    assert verdict.resonates is True
    assert verdict.relation == "corroborates"
    assert "noise" in verdict.note.lower()


def test_parse_resonance_no():
    response = "VERDICT: NO"
    verdict = parse_resonance(response)
    assert verdict.resonates is False
    assert verdict.relation is None
    assert verdict.note is None


def test_parse_resonance_extends():
    response = "VERDICT: YES\nRELATION: extends\nNOTE: Adds empirical data to the theoretical claim."
    verdict = parse_resonance(response)
    assert verdict.relation == "extends"


def test_parse_resonance_contradicts():
    response = "VERDICT: YES\nRELATION: contradicts\nNOTE: New source disputes the benchmark."
    verdict = parse_resonance(response)
    assert verdict.relation == "contradicts"


def test_parse_resonance_malformed_returns_no():
    verdict = parse_resonance("I cannot determine this.")
    assert verdict.resonates is False
