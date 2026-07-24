"""PreToolUse hook on Bash: block ``git commit`` while must-fix inline findings
are unresolved.

Companion to the Strategy-B inline-injection detectors. Those inject
``# HOOK:<NAME>:`` comments into source files; when a detector opts in
(``read_clean_write(..., blocking=True)``) it also records the finding to
``.hook_state/blocking_findings/<hook>.json``. This guard trusts that state --
it does NOT re-analyze -- and exits 2 to block the commit until the findings
clear (the detector removes the entry on its next clean run).

Why a commit gate and not an edit gate: the agent must be free to edit the file
to *fix* the finding; gating the commit avoids blocking the very edit that
resolves it, and mirrors pip_audit_guard.py plus the git-native pre-commit
backstop. The heed-rate probe showed inline comments alone get acknowledged but
not fixed -- this gate is the forcing function for must-fix findings.

Fast-exits (exit 0) for any command that isn't a git commit.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import read_blocking_findings
from hook_log import log_hook

log_hook("block_unresolved_findings")


def _is_git_commit(command: str) -> bool:
    return "git commit" in command


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not _is_git_commit(command):
        return 0

    findings = read_blocking_findings()
    if not findings:
        return 0

    lines = ["[findings-guard] BLOCKED: unresolved must-fix findings from hooks.\n"]
    for path in sorted(findings):
        lines.append(f"  {path}")
        for msg in findings[path]:
            lines.append(f"    - {msg}")
    lines.append(
        "\nResolve them (the inline `# HOOK:` comments mark each spot); the "
        "detector clears the block on its next clean run. Do not bypass with "
        "--no-verify."
    )
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
