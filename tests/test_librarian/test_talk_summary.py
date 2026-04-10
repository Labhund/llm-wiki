from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.librarian.talk_summary import (
    TalkSummaryStore,
    summarize_open_entries,
)
from llm_wiki.talk.page import TalkEntry


def test_store_load_missing_file_returns_empty(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "missing.json")
    assert store.get("any-page") is None


def test_store_set_and_get_round_trip(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "store.json")
    store.set("p1", summary="Two unresolved findings.", last_max_index=5,
              last_summary_ts="2026-04-08T10:00:00+00:00")
    store.save()

    reloaded = TalkSummaryStore.load(tmp_path / "store.json")
    record = reloaded.get("p1")
    assert record is not None
    assert record.summary == "Two unresolved findings."
    assert record.last_max_index == 5
    assert record.last_summary_ts == "2026-04-08T10:00:00+00:00"


def test_store_save_writes_atomically(tmp_path):
    """The store uses temp-file-rename so a partial write can't corrupt it."""
    store = TalkSummaryStore.load(tmp_path / "s.json")
    store.set("a", summary="x", last_max_index=1, last_summary_ts="t")
    store.save()
    assert (tmp_path / "s.json").exists()
    payload = json.loads((tmp_path / "s.json").read_text())
    assert "a" in payload


def test_store_delete_removes_entry(tmp_path):
    store = TalkSummaryStore.load(tmp_path / "s.json")
    store.set("a", summary="x", last_max_index=1, last_summary_ts="t")
    store.delete("a")
    assert store.get("a") is None


@pytest.mark.asyncio
async def test_summarize_open_entries_calls_llm(tmp_path):
    """summarize_open_entries() formats entries and asks the LLM for 2 sentences."""
    entries = [
        TalkEntry(1, "2026-04-08T10:00:00+00:00", "@adversary", "First.", severity="critical"),
        TalkEntry(2, "2026-04-08T10:01:00+00:00", "@compliance", "Second.", severity="moderate"),
    ]

    captured: dict = {}

    class MockLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance", **kwargs):
            from llm_wiki.traverse.llm_client import LLMResponse
            captured["messages"] = messages
            captured["priority"] = priority
            return LLMResponse(
                content="Two unresolved entries: an adversary contradiction and a compliance flag.",
                input_tokens=20,
                output_tokens=0,
            )

    summary = await summarize_open_entries(entries, MockLLM())
    assert "unresolved" in summary.lower()
    assert captured["priority"] == "maintenance"
    # The prompt should mention each entry's body
    flat = " ".join(m["content"] for m in captured["messages"])
    assert "First." in flat
    assert "Second." in flat


@pytest.mark.asyncio
async def test_summarize_open_entries_falls_back_on_llm_error():
    """If the LLM raises, return a deterministic summary based on counts."""
    entries = [
        TalkEntry(1, "t", "@a", "first", severity="critical"),
        TalkEntry(2, "t", "@b", "second", severity="moderate"),
    ]

    class FailingLLM:
        async def complete(self, messages, temperature=0.0, priority="maintenance", **kwargs):
            raise RuntimeError("model unreachable")

    summary = await summarize_open_entries(entries, FailingLLM())
    assert "2" in summary or "two" in summary.lower()
    assert "critical" in summary.lower() or "moderate" in summary.lower()


@pytest.mark.asyncio
async def test_summarize_open_entries_empty_returns_empty_string():
    summary = await summarize_open_entries([], llm=None)
    assert summary == ""


def test_deterministic_summary_orders_by_severity_rank():
    """P6A-M4: deterministic fallback sorts severities by rank, not alphabet.

    Alphabetical order produces "critical, minor, moderate, ..." which
    misleads readers about precedence. Rank order is:
    critical → moderate → minor → suggestion → new_connection.
    """
    from llm_wiki.librarian.talk_summary import _deterministic_summary

    entries = [
        TalkEntry(1, "t", "@a", "x", severity="minor"),
        TalkEntry(2, "t", "@a", "x", severity="suggestion"),
        TalkEntry(3, "t", "@a", "x", severity="critical"),
        TalkEntry(4, "t", "@a", "x", severity="new_connection"),
        TalkEntry(5, "t", "@a", "x", severity="moderate"),
        TalkEntry(6, "t", "@a", "x", severity="critical"),
    ]
    summary = _deterministic_summary(entries)
    # Expected: 6 unresolved talk entries: 2 critical, 1 moderate, 1 minor,
    # 1 suggestion, 1 new_connection.
    assert summary.startswith("6 unresolved talk entries: ")
    body = summary[len("6 unresolved talk entries: "):].rstrip(".")
    parts = [p.strip() for p in body.split(",")]
    severities_in_order = [p.split()[1] for p in parts]
    assert severities_in_order == [
        "critical", "moderate", "minor", "suggestion", "new_connection",
    ]


def test_deterministic_summary_rank_skips_unknown_severities():
    """Unknown severities sort after known ones, alphabetically among themselves."""
    from llm_wiki.librarian.talk_summary import _deterministic_summary

    entries = [
        TalkEntry(1, "t", "@a", "x", severity="critical"),
        TalkEntry(2, "t", "@a", "x", severity="zzz_unknown"),
        TalkEntry(3, "t", "@a", "x", severity="aaa_unknown"),
    ]
    summary = _deterministic_summary(entries)
    # Known first (critical), then unknowns alphabetically (aaa, zzz).
    body = summary[len("3 unresolved talk entries: "):].rstrip(".")
    parts = [p.strip() for p in body.split(",")]
    severities_in_order = [p.split()[1] for p in parts]
    assert severities_in_order == ["critical", "aaa_unknown", "zzz_unknown"]
