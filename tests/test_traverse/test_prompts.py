from __future__ import annotations

from pathlib import Path

from llm_wiki.traverse.prompts import (
    DEFAULT_SYNTHESIZE_PROMPT,
    DEFAULT_TRAVERSE_PROMPT,
    compose_synthesize_messages,
    compose_traverse_messages,
    load_prompt,
)
from llm_wiki.traverse.working_memory import WorkingMemory


def test_compose_traverse_messages_structure():
    mem = WorkingMemory.initial("How does X work?", budget=16000)
    messages = compose_traverse_messages(
        query="How does X work?",
        memory=mem,
        new_content="page-a: Summary of page A",
        system_prompt="You are a research assistant.",
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a research assistant."
    assert messages[1]["role"] == "user"
    assert "How does X work?" in messages[1]["content"]
    assert "page-a: Summary of page A" in messages[1]["content"]
    assert "16000" in messages[1]["content"]  # budget info


def test_compose_traverse_messages_includes_memory():
    mem = WorkingMemory.initial("q", budget=8000)
    mem.hypothesis = "X works via Y"
    messages = compose_traverse_messages("q", mem, "new stuff", "sys")
    assert "X works via Y" in messages[1]["content"]


def test_compose_traverse_messages_shows_turn():
    mem = WorkingMemory.initial("q", budget=8000)
    mem.turn = 3
    messages = compose_traverse_messages("q", mem, "content", "sys")
    assert "Turn 3" in messages[1]["content"]


def test_compose_synthesize_messages_structure():
    mem = WorkingMemory.initial("What is Y?", budget=16000)
    mem.hypothesis = "Y is a protein"
    messages = compose_synthesize_messages(
        query="What is Y?",
        memory=mem,
        system_prompt="Synthesize an answer.",
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "What is Y?" in messages[1]["content"]
    assert "Y is a protein" in messages[1]["content"]


def test_load_prompt_returns_default():
    prompt = load_prompt(None, "traverse")
    assert prompt == DEFAULT_TRAVERSE_PROMPT
    assert "Structural Contract" in prompt


def test_load_prompt_returns_synthesize_default():
    prompt = load_prompt(None, "synthesize")
    assert prompt == DEFAULT_SYNTHESIZE_PROMPT


def test_load_prompt_vault_override(tmp_path: Path):
    prompts_dir = tmp_path / "schema" / "prompts"
    prompts_dir.mkdir(parents=True)
    override_text = "Custom traverse prompt for this domain."
    (prompts_dir / "traverse.md").write_text(override_text)

    prompt = load_prompt(tmp_path, "traverse")
    assert prompt == override_text


def test_load_prompt_falls_back_when_no_override(tmp_path: Path):
    prompt = load_prompt(tmp_path, "traverse")
    assert prompt == DEFAULT_TRAVERSE_PROMPT


def test_load_prompt_unknown_name_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown prompt name"):
        load_prompt(None, "nonexistent")


def test_compose_synthesize_messages_handles_empty_memory():
    """Synthesize on a memory with no useful state still produces well-formed messages."""
    mem = WorkingMemory(query="q", budget_total=8000)
    mem.remaining_questions = []  # Force fully empty state
    messages = compose_synthesize_messages("q", mem, "sys")
    assert "No research notes available." in messages[1]["content"]


def test_compose_synthesize_messages_includes_synthesis_candidates():
    """When synthesis_candidates provided, messages include existing-page block."""
    memory = WorkingMemory.initial("how does boltz-2 work?", 2000)
    candidates = [("boltz-2-structure", "how does boltz-2 work?", "Boltz-2 uses diffusion.")]
    msgs = compose_synthesize_messages(
        "how does boltz-2 work?",
        memory,
        DEFAULT_SYNTHESIZE_PROMPT,
        synthesis_candidates=candidates
    )
    user_msg = msgs[-1]["content"]
    assert "boltz-2-structure" in user_msg
    assert "how does boltz-2 work?" in user_msg
    assert "Boltz-2 uses diffusion." in user_msg
    # Verify the system prompt includes action schema instructions
    sys_msg = msgs[0]["content"]
    assert "accept" in sys_msg
    assert "update" in sys_msg
    assert "create" in sys_msg


def test_compose_synthesize_messages_no_candidates_unchanged():
    """Without synthesis_candidates the message structure is unchanged."""
    memory = WorkingMemory.initial("q", 1000)
    msgs_without = compose_synthesize_messages("q", memory, "Sys.")
    msgs_with_empty = compose_synthesize_messages("q", memory, "Sys.", synthesis_candidates=[])
    assert msgs_without == msgs_with_empty
