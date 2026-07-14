"""PreToolUse hook on Bash: block dependency operations when unresolved
vulnerabilities exist.

Checks for .hook_state/pip_audit/report.json (written by the companion
PostToolUse hook pip_audit_check.py). If the state file exists, exits 2
to block the operation. Does NOT re-run pip-audit -- trusts the state file.

Fast-exits (exit 0) for commands that don't match uv add/sync/pip install.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import get_state_dir
from hook_log import log_hook

log_hook("pip_audit_guard")


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
    if not isinstance(command, str):
        return 0

    if not ("uv add" in command or "uv sync" in command or "uv pip install" in command):
        return 0

    report_file = get_state_dir() / "pip_audit" / "report.json"
    if not report_file.exists():
        return 0

    try:
        data = json.loads(report_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[pip-audit-guard] Warning: could not read state file {report_file}: {exc}",
            file=sys.stderr,
        )
        return 0

    summary = data.get("summary", "unknown vulnerabilities")
    vulns = data.get("vulns", "")
    print(
        f"[pip-audit-guard] BLOCKED: unresolved vulnerabilities from previous audit.\n"
        f"Summary: {summary}\n"
        f"Details:\n{vulns}\n"
        f"Fix the vulnerabilities before adding new dependencies.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
