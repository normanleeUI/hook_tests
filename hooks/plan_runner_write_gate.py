#!/usr/bin/env python
"""DEPRECATED: Never wired in settings.json. Discovered 2026-06-13 during
hook audit. The plan runner concept was abandoned. Delete if not needed
by end of Q3 2026.

Original: PreToolUse hook to enforce write-path restrictions during plan runner phases.

When the plan runner dispatches a claude -p agent, it sets the
PLAN_RUNNER_ALLOWED_WRITE_PATTERN env var to restrict which files each phase
can modify (e.g., red phase can only write tests, green phase can only write
implementation files). This hook reads that env var and blocks writes to
files outside the allowed patterns.

If the env var is absent (interactive use), the hook is a no-op. If the env
var is set but empty, ALL writes are blocked — this is the safety default
for phases that should be read-only.

Exit codes: 0 = allow, 2 = block (matching the Claude Code hook convention).
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys


def _get_repo_root() -> str | None:
    """Return the git repo root, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def _normalize_path(file_path: str) -> str:
    """Convert an absolute file path to a repo-relative path for matching.

    If we can't determine the repo root (e.g., not in a git repo), fall back
    to the basename to avoid false blocks.
    """
    repo_root = _get_repo_root()
    if repo_root and os.path.isabs(file_path):
        return os.path.relpath(file_path, repo_root)
    return file_path


def main() -> int:
    # If no write pattern env var, this is interactive use — allow everything.
    pattern_str = os.environ.get("PLAN_RUNNER_ALLOWED_WRITE_PATTERN")
    if pattern_str is None:
        return 0

    # Empty string means "block ALL writes" — safety default for read-only phases.
    if pattern_str == "":
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0

        inp = payload.get("tool_input") or {}
        file_path = inp.get("file_path")
        phase = os.environ.get("PLAN_RUNNER_PHASE", "unknown")
        print(
            f"[write_gate] BLOCKED — {phase} phase does not allow any file writes.\n"
            f"  File: {file_path or '(unknown)'}",
            file=sys.stderr,
        )
        return 2

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    inp = payload.get("tool_input") or {}
    file_path = inp.get("file_path")

    # No file_path in the payload — not a write we can gate.
    if not file_path or not isinstance(file_path, str):
        return 0

    relative_path = _normalize_path(file_path)
    patterns = [p.strip() for p in pattern_str.split(",") if p.strip()]

    for pattern in patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return 0

    phase = os.environ.get("PLAN_RUNNER_PHASE", "unknown")
    print(
        f"[write_gate] BLOCKED — file is outside the allowed write paths "
        f"for the {phase} phase.\n"
        f"  File:             {relative_path}\n"
        f"  Allowed patterns: {', '.join(patterns)}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
