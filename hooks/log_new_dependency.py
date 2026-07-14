"""DEPRECATED: Unwired 2026-06-13. The dependency logging this hook
provides is redundant — pip_audit_check.py covers the important case
(vulnerability scanning after dependency changes), and git diff on
pyproject.toml shows what was added. Re-enable only if a specific
need for session-scoped dependency logging emerges.

Original: PreToolUse hook on Bash(uv add*) that logged new dependency additions to a
session-scoped log file for end-of-session review.

This hook is NON-BLOCKING (exit 0 always). It captures each `uv add`
invocation so the developer can review what was added at the end of an
automated run. The log file is written to $TMPDIR/dependency_additions.log
(session-scoped temp directory).

The hook also emits a stderr warning so the orchestrator agent is aware
a dependency was added, without stopping work.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "")

    if "uv add" not in command:
        return 0

    # Extract package names from the uv add portion of the command.
    # Handles compound commands like "cd /path && uv add foo bar --dev"
    uv_add_idx = command.index("uv add")
    uv_add_part = command[uv_add_idx:]
    parts = uv_add_part.split()
    packages = [p for p in parts[2:] if not p.startswith("-")]
    if not packages:
        return 0

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    log_file = log_dir / "dependency_additions.log"

    entry = f"{timestamp} | {command} | packages: {', '.join(packages)}\n"

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass  # Don't block on logging failures

    print(
        f"[log_new_dependency] Dependency addition logged: {', '.join(packages)}. "
        f"Log file: {log_file}\n"
        f"  This will be included in the end-of-session review report.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
