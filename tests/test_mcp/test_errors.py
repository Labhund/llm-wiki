from __future__ import annotations

import pytest


def test_mcp_tool_error_carries_code_and_details():
    from llm_wiki.mcp.errors import McpToolError
    exc = McpToolError(
        code="missing-citations",
        message="wiki_create requires at least one citation",
        details={"page": "foo"},
    )
    assert exc.code == "missing-citations"
    assert "citation" in str(exc)
    assert exc.details == {"page": "foo"}


def test_format_error_includes_code_and_message():
    from llm_wiki.mcp.errors import McpToolError, format_error
    exc = McpToolError(
        code="patch-conflict",
        message="context drift",
        details={"current_excerpt": "actual content"},
    )
    formatted = format_error(exc)
    assert "patch-conflict" in formatted
    assert "context drift" in formatted
    assert "actual content" in formatted


def test_format_error_handles_missing_code():
    from llm_wiki.mcp.errors import McpToolError, format_error
    exc = McpToolError(code=None, message="something failed", details={})
    formatted = format_error(exc)
    assert "something failed" in formatted


def test_translate_daemon_response_passes_through_ok():
    from llm_wiki.mcp.errors import translate_daemon_response
    response = {"status": "ok", "page_path": "wiki/foo.md"}
    # No exception raised
    result = translate_daemon_response(response)
    assert result == response


def test_translate_daemon_response_raises_on_error():
    from llm_wiki.mcp.errors import McpToolError, translate_daemon_response
    response = {
        "status": "error",
        "code": "missing-citations",
        "message": "no citations",
        "page": "foo",
    }
    with pytest.raises(McpToolError) as exc_info:
        translate_daemon_response(response)
    assert exc_info.value.code == "missing-citations"
