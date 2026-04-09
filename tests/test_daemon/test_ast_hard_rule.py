"""The hard-rule test: background workers never reach the write surface.

This test enforces PHILOSOPHY.md Principle 3 mechanically. It walks every
module in the background-worker subtree (daemon/, audit/, librarian/,
adversary/, talk/) and fails if any of them references the four MCP-only
write routes or the PageWriteService that implements them.

If this test fails, do NOT add an exception. Refactor the offending code
so the background worker doesn't even import the forbidden symbol.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Forbidden symbol names. Any AST node referencing these from a
# background-worker module is a hard-rule violation.
FORBIDDEN_NAMES = {
    "PageWriteService",
    "_handle_page_create",
    "_handle_page_update",
    "_handle_page_append",
    "_handle_session_close",
    # Phase 6c: the MCP layer is for supervised paths only.
    "MCPServer",
    "WIKI_TOOLS",
}
FORBIDDEN_ROUTE_STRINGS = {
    "page-create",
    "page-update",
    "page-append",
    "session-close",
}

# Phase 6c: forbidden import modules. Background workers must never
# import anything from llm_wiki.mcp — that whole package is the
# supervised-write surface and must not be reachable from cron-driven
# code paths.
FORBIDDEN_IMPORT_MODULES = {
    "llm_wiki.mcp",
    "llm_wiki.mcp.server",
    "llm_wiki.mcp.tools",
    "llm_wiki.mcp.errors",
}

# Modules that are background-worker code paths. The scheduler reaches
# all of these via run_auditor / run_librarian / run_authority_recalc /
# run_adversary / run_talk_summary in daemon/server.py.
BACKGROUND_MODULE_DIRS = [
    "src/llm_wiki/audit",
    "src/llm_wiki/librarian",
    "src/llm_wiki/adversary",
    "src/llm_wiki/talk",
]

# Modules in daemon/ that are themselves the write surface (NOT background).
# These are explicitly allowed to import PageWriteService etc. The hard-rule
# test must not flag them.
#
# Kept deliberately tight: only files that legitimately need to reference
# the forbidden symbols are exempted. `commit.py`, `sessions.py`,
# `v4a_patch.py`, `name_similarity.py`, and `writer.py` are NOT exempted —
# they don't import PageWriteService or _handle_page_*, and walking them
# anyway gives us defense in depth against future regressions where one of
# them ends up importing the write surface.
DAEMON_WRITE_SURFACE_FILES = {
    "src/llm_wiki/daemon/server.py",
    "src/llm_wiki/daemon/writes.py",
}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _walk_files(dir_relative: str) -> list[pathlib.Path]:
    base = _repo_root() / dir_relative
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*.py") if p.is_file())


def _violations_in_file(path: pathlib.Path) -> list[str]:
    """Return human-readable violation messages for a single file."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"AST parse failed for {path}: {exc}")

    violations: list[str] = []

    for node in ast.walk(tree):
        # Plain identifier reference
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(
                f"{path}:{node.lineno}: references forbidden symbol {node.id!r}"
            )
        # Attribute access (e.g. server._handle_page_create)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(
                f"{path}:{node.lineno}: references forbidden attribute {node.attr!r}"
            )
        # ImportFrom
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{path}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
            if node.module in FORBIDDEN_IMPORT_MODULES:
                violations.append(
                    f"{path}:{node.lineno}: imports from forbidden module {node.module!r}"
                )
        # Plain Import (e.g. `import llm_wiki.mcp.tools`)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_MODULES:
                    violations.append(
                        f"{path}:{node.lineno}: imports forbidden module {alias.name!r}"
                    )
        # String literals containing forbidden route names
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in FORBIDDEN_ROUTE_STRINGS:
                violations.append(
                    f"{path}:{node.lineno}: contains forbidden route string {node.value!r}"
                )

    return violations


def test_background_workers_never_reference_write_surface():
    """No file in audit/, librarian/, adversary/, talk/ references the write surface."""
    all_violations: list[str] = []
    for dir_rel in BACKGROUND_MODULE_DIRS:
        for path in _walk_files(dir_rel):
            all_violations.extend(_violations_in_file(path))

    if all_violations:
        pytest.fail(
            "Hard-rule violation: background-worker code references the write "
            "surface. Refactor so the import never happens — do NOT add the "
            "file to an allowlist.\n\n" + "\n".join(all_violations)
        )


def test_daemon_write_surface_files_are_known():
    """Sanity check: every file in daemon/ is either write-surface or excluded.

    This catches the case where someone adds a new daemon/ file that
    imports PageWriteService and forgets to update DAEMON_WRITE_SURFACE_FILES.
    """
    daemon_dir = _repo_root() / "src/llm_wiki/daemon"
    actual_files = {
        str(p.relative_to(_repo_root()))
        for p in daemon_dir.rglob("*.py")
        if p.name != "__init__.py" and p.name != "__main__.py"
    }
    unknown = actual_files - DAEMON_WRITE_SURFACE_FILES
    # The remaining files are dispatcher, scheduler, llm_queue, etc. — none of
    # which should reference the write surface either. Walk them too.
    background_daemon_violations: list[str] = []
    for rel in unknown:
        path = _repo_root() / rel
        background_daemon_violations.extend(_violations_in_file(path))
    if background_daemon_violations:
        pytest.fail(
            "A daemon/ file outside the write-surface set references the write "
            "surface:\n\n" + "\n".join(background_daemon_violations)
        )


def _violations_in_function(
    func_node: ast.AST, source_label: str,
) -> list[str]:
    """Walk a single function (and any nested functions inside it).

    Returns violation strings for any FORBIDDEN_NAMES references,
    forbidden attribute accesses, forbidden ImportFrom names, or forbidden
    route-string constants found anywhere in the function's subtree.
    Used by `test_register_maintenance_workers_never_reach_write_surface`
    to surgically check the background-worker bodies inside `server.py`
    even though `server.py` is otherwise exempt from the directory walk.
    """
    violations: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(
                f"{source_label}:{node.lineno}: references forbidden symbol {node.id!r}"
            )
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(
                f"{source_label}:{node.lineno}: references forbidden attribute {node.attr!r}"
            )
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{source_label}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
            if node.module in FORBIDDEN_IMPORT_MODULES:
                violations.append(
                    f"{source_label}:{node.lineno}: imports from forbidden module {node.module!r}"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_MODULES:
                    violations.append(
                        f"{source_label}:{node.lineno}: imports forbidden module {alias.name!r}"
                    )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in FORBIDDEN_ROUTE_STRINGS:
                violations.append(
                    f"{source_label}:{node.lineno}: contains forbidden route string {node.value!r}"
                )
    return violations


def test_register_maintenance_workers_never_reach_write_surface():
    """Walk `_register_maintenance_workers` (and its nested closures) for violations.

    `daemon/server.py` is in `DAEMON_WRITE_SURFACE_FILES` because it
    legitimately holds the `_handle_page_*` route handlers. But it ALSO
    holds `_register_maintenance_workers`, which defines the background
    worker entry points (`run_auditor`, `run_librarian`, `run_authority_recalc`,
    `run_adversary`, future `run_talk_summary`) as nested async closures.
    Those closures are unsupervised code paths and MUST NOT reference the
    write surface — but the directory-level exemption would otherwise
    silently pass them.

    This test parses `server.py`, finds `_register_maintenance_workers`,
    and walks every node in its subtree (including nested FunctionDefs)
    for FORBIDDEN_NAMES / FORBIDDEN_ROUTE_STRINGS. If a future change
    adds `from llm_wiki.daemon.writes import PageWriteService` inside
    one of the closures, this test catches it where the directory walk
    cannot.
    """
    server_path = _repo_root() / "src/llm_wiki/daemon/server.py"
    text = server_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(server_path))
    except SyntaxError as exc:
        pytest.fail(f"AST parse failed for {server_path}: {exc}")

    target: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_register_maintenance_workers":
                target = node
                break

    if target is None:
        pytest.fail(
            "Could not find _register_maintenance_workers in "
            f"{server_path} — has it been renamed or removed? Update this "
            "test to point at the new background-worker registration site."
        )

    violations = _violations_in_function(
        target, source_label=str(server_path),
    )
    if violations:
        pytest.fail(
            "Hard-rule violation: a background worker registered in "
            "_register_maintenance_workers references the write surface. "
            "Refactor the worker so it never imports PageWriteService or "
            "reaches the page-create / page-update / page-append / "
            "session-close routes.\n\n" + "\n".join(violations)
        )
