"""PostToolUse hook on Edit|Write: check Python functions for missing docstrings.

Uses the `ast` module to parse the written/edited file and find function
and class definitions that lack docstrings.  Injects inline
'# HOOK:DOCSTRING:' comments at the relevant lines so the model can see
them in context and add docstrings in the same turn.

Policy (global CLAUDE.md + ponytail audit decision):
  Public API functions get a 1-3 line docstring covering *why* and gotchas.
  Drop Args/Returns when the type signature tells the story. Internal
  helpers (_foo) only need a docstring when the *why* is non-obvious.

Skips: private helpers (_foo), dunders (except __init__ with params),
  trivial functions (<=2 statements), test files, __init__.py, conftest.py.

Only checks .py files.  Skips test files (test_ prefix), __init__.py,
conftest.py, and files inside ~/.claude/.

Exit code: always 0 (informational, never blocks).
"""

import ast
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import read_clean_write
from hook_log import log_hook

log_hook("check_docstrings")


def _resolve_file_path() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool_response = data.get("tool_response")
    if not isinstance(tool_response, dict):
        tool_response = {}
    return tool_response.get("filePath") or tool_input.get("file_path", "")


file_path = _resolve_file_path()
if not file_path:
    sys.exit(0)

p = Path(file_path)

# -- Only check Python implementation files -----------------------------------
if p.suffix != ".py":
    sys.exit(0)

SKIP_NAMES = {"__init__.py", "conftest.py", "setup.py", "manage.py", "__main__.py"}
if p.name in SKIP_NAMES or p.name.startswith("test_"):
    sys.exit(0)

if ".claude" in p.parts:
    sys.exit(0)


def _has_docstring(node: ast.AST) -> bool:
    """Check whether a function/class node has a docstring as its first statement."""
    if not hasattr(node, "body") or not node.body:
        return False
    first = node.body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _is_trivial(node: ast.AST) -> bool:
    """Heuristic: a function is 'trivial' if it has <=2 statements (excluding docstring).

    Short property getters, one-liner wrappers, and simple assignments
    don't need full docstrings -- they clutter more than they help.
    """
    body = getattr(node, "body", [])
    # Subtract docstring from count if present
    effective = body[1:] if _has_docstring(node) else body
    return len(effective) <= 2


def analyze_docstrings(content: str, lines: list[str]) -> list[tuple[int, str]]:
    """Analyze Python source for missing docstrings on non-trivial public definitions.

    Receives clean file content (no stale HOOK comments) from read_clean_write.
    Returns (line_number, message) tuples for each definition missing a docstring.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        name: str = node.name
        kind = "class" if isinstance(node, ast.ClassDef) else "function"

        # Skip private helpers (_foo) -- convention says they're internal
        if name.startswith("_") and not name.startswith("__"):
            continue

        # Skip dunder methods other than __init__ -- well-known semantics
        if name.startswith("__") and name.endswith("__") and name != "__init__":
            continue

        # Skip trivial functions (<=2 statements)
        if kind == "function" and _is_trivial(node):
            continue

        # Classes always need a docstring (they define a public interface)
        # __init__ needs one if it has parameters beyond self
        if name == "__init__":
            args = node.args
            # Count params excluding 'self'
            param_count = len(args.args) - 1 + len(args.kwonlyargs)
            if param_count == 0:
                continue  # No-arg __init__ is trivial

        if _has_docstring(node):
            continue

        findings.append((node.lineno, f"missing docstring for {kind} '{name}'"))

    return findings


read_clean_write(file_path, "DOCSTRING", analyze_docstrings)
