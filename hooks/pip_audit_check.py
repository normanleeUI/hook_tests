"""PostToolUse hook on Bash(uv add*|uv sync*): run pip-audit after
dependency changes to check for known vulnerabilities.

When vulnerabilities are found, writes findings to
.hook_state/pip_audit/report.json so the companion PreToolUse guard
(pip_audit_guard.py) can block future dependency operations. When the
audit is clean, deletes any existing state file so the guard stops blocking.

Always exits 0 on PostToolUse (exit 2 on PostToolUse is cosmetic --
the guard hook handles actual blocking).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import ensure_state_dir, get_state_dir
from hook_log import log_hook

log_hook("pip_audit_check")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    if not ("uv add" in command or "uv sync" in command or "uv pip install" in command):
        return 0

    # PostToolUse payloads use `tool_response` (not `tool_result`), and the Bash
    # tool_response carries no exit code (keys: stdout, stderr, interrupted,
    # isImage, noOutputExpected). We can't gate on command success, so audit
    # whenever a uv dependency command ran and wasn't interrupted — a failed
    # `uv add` leaves deps unchanged, so auditing the current state is harmless.
    tool_response = payload.get("tool_response") or {}
    if tool_response.get("interrupted"):
        return 0

    print(
        "[pip-audit] Scanning dependencies for known vulnerabilities...",
        file=sys.stderr,
    )

    tmpdir = os.environ.get("TMPDIR", "/tmp")
    env = {
        **os.environ,
        "XDG_CACHE_HOME": tmpdir,
        "UV_TOOL_DIR": os.path.join(tmpdir, "uv-tools"),
    }
    result = subprocess.run(
        ["uvx", "pip-audit", "--progress-spinner=off"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    state_dir = get_state_dir()
    report_dir = state_dir / "pip_audit"
    report_file = report_dir / "report.json"

    if result.returncode != 0:
        print(
            f"[pip-audit] VULNERABILITIES FOUND:\n{result.stdout}\n{result.stderr}",
            file=sys.stderr,
        )
        ensure_state_dir(report_dir)
        report_file.write_text(
            json.dumps(
                {
                    "vulns": result.stdout,
                    "summary": result.stderr.strip(),
                }
            )
        )
        return 0

    if report_file.exists():
        report_file.unlink()

    pkg_count = result.stdout.strip().count("\n")
    print(
        f"[pip-audit] All dependencies clean ({pkg_count} packages audited).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
