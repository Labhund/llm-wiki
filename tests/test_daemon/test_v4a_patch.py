from __future__ import annotations

import pytest


def test_hunk_line_dataclass():
    from llm_wiki.daemon.v4a_patch import HunkLine
    line = HunkLine(kind="context", text="some context")
    assert line.kind == "context"
    assert line.text == "some context"


def test_hunk_dataclass_default_context_hint():
    from llm_wiki.daemon.v4a_patch import Hunk
    hunk = Hunk(context_hint="", lines=[])
    assert hunk.context_hint == ""
    assert hunk.lines == []


def test_patch_dataclass():
    from llm_wiki.daemon.v4a_patch import Hunk, Patch
    patch = Patch(op="update", target_path="wiki/foo.md", hunks=[Hunk("", [])])
    assert patch.op == "update"
    assert patch.target_path == "wiki/foo.md"
    assert len(patch.hunks) == 1


def test_patch_conflict_carries_excerpt():
    from llm_wiki.daemon.v4a_patch import PatchConflict
    exc = PatchConflict("context drift", current_excerpt="actual line")
    assert "context drift" in str(exc)
    assert exc.current_excerpt == "actual line"


def test_apply_result_dataclass():
    from llm_wiki.daemon.v4a_patch import ApplyResult
    result = ApplyResult(additions=2, removals=1, applied_via="exact")
    assert result.additions == 2
    assert result.removals == 1
    assert result.applied_via == "exact"
