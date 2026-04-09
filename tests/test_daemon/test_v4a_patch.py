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


SIMPLE_PATCH = """\
*** Begin Patch
*** Update File: wiki/sRNA-tQuant.md
@@ ## Methods @@
 We trained on 50k sequences using k-means
-with cosine similarity, learning rate 1e-4.
+with cosine similarity, learning rate 3e-4.
 The clustering converged in 12 epochs.
*** End Patch
"""


def test_parse_patch_simple_update():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    assert patch.op == "update"
    assert patch.target_path == "wiki/sRNA-tQuant.md"
    assert len(patch.hunks) == 1


def test_parse_patch_extracts_context_hint():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    assert patch.hunks[0].context_hint == "## Methods"


def test_parse_patch_extracts_hunk_lines_in_order():
    from llm_wiki.daemon.v4a_patch import parse_patch
    patch = parse_patch(SIMPLE_PATCH)
    lines = patch.hunks[0].lines
    assert len(lines) == 4
    assert lines[0].kind == "context"
    assert lines[0].text == "We trained on 50k sequences using k-means"
    assert lines[1].kind == "remove"
    assert lines[1].text == "with cosine similarity, learning rate 1e-4."
    assert lines[2].kind == "add"
    assert lines[2].text == "with cosine similarity, learning rate 3e-4."
    assert lines[3].kind == "context"
    assert lines[3].text == "The clustering converged in 12 epochs."


def test_parse_patch_missing_begin_marker_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = "*** Update File: wiki/foo.md\n@@ x @@\n context\n*** End Patch\n"
    with pytest.raises(PatchParseError, match="Begin Patch"):
        parse_patch(text)


def test_parse_patch_missing_end_marker_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = "*** Begin Patch\n*** Update File: wiki/foo.md\n@@ x @@\n context\n"
    with pytest.raises(PatchParseError, match="End Patch"):
        parse_patch(text)


def test_parse_patch_unknown_op_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Add File: wiki/foo.md\n"
        "@@ x @@\n"
        "+ new line\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchParseError, match="Add File"):
        parse_patch(text)


def test_parse_patch_no_hunk_header_raises():
    from llm_wiki.daemon.v4a_patch import PatchParseError, parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        " just some line\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchParseError):
        parse_patch(text)


def test_parse_patch_bare_at_at_header():
    """A `@@ @@` header with no context hint is valid; hint is empty string."""
    from llm_wiki.daemon.v4a_patch import parse_patch
    text = (
        "*** Begin Patch\n"
        "*** Update File: wiki/foo.md\n"
        "@@ @@\n"
        " context\n"
        "+added\n"
        "*** End Patch\n"
    )
    patch = parse_patch(text)
    assert patch.hunks[0].context_hint == ""
