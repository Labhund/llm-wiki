"""Error translation between daemon responses and MCP tool errors.

The daemon returns ``{"status": "error", "code": "...", "message": "..."}``.
The MCP SDK surfaces tool errors as exceptions raised by the tool handler.
This module bridges the two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolError(Exception):
    """Raised by an MCP tool handler when the daemon returned an error.

    The MCP SDK turns this into a structured error response. The ``code``
    field is the daemon's error code (e.g. 'patch-conflict',
    'missing-citations') so the agent can act on it programmatically.
    """

    code: str | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)


def format_error(exc: McpToolError) -> str:
    """Build a human-readable error message that carries the code + details.

    The MCP SDK doesn't have a structured-error type per se — errors are
    rendered as strings. We pack everything into a JSON blob inside the
    string so the agent can parse it back if it wants the details.
    """
    payload: dict[str, Any] = {"message": exc.message}
    if exc.code is not None:
        payload["code"] = exc.code
    if exc.details:
        payload["details"] = exc.details
    return json.dumps(payload, indent=2)


def translate_daemon_response(response: dict) -> dict:
    """Pass through ok responses; raise McpToolError on daemon errors.

    The daemon's error responses carry ``status="error"``, ``code``
    (sometimes), ``message``, and arbitrary additional fields. We pack
    the additional fields into ``details`` for the agent.
    """
    if response.get("status") == "error":
        code = response.get("code")
        message = response.get("message", "Unknown daemon error")
        details = {
            k: v
            for k, v in response.items()
            if k not in ("status", "code", "message")
        }
        raise McpToolError(code=code, message=message, details=details)
    return response
