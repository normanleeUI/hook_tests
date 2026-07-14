#!/usr/bin/env python
"""PreToolUse hook — block unjustified `# type: ignore` and `# noqa` comments.

Sessions must fix type errors and lint violations rather than suppress them.
This hook checks the new/modified text from Edit or Write operations and
hard-blocks (exit 2) if it contains unjustified suppression comments.

Merged from the former block_type_ignore.py and block_noqa.py.

Justification markers that bypass the block:
  - `# type: ignore[code]  # mypy-bug: <reason>`
  - `# type: ignore[code]  # known-issue: <reason>`
  - `# type: ignore[code]  # sqlmodel-metaclass: <reason>`
  - `# noqa: XXXX  # noqa-reason: <why this can't be fixed>`
  - `# noqa: E402` (import ordering after load_dotenv/sys.path — pre-approved)

Input contract: Claude Code passes the tool call JSON on stdin; we read
`tool_input.file_path` (or `tool_response.filePath`).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_suppressions")

EXEMPT_PARTS = {".venv", "spikes", "hooks"}

# --- type: ignore patterns ---
TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore", re.IGNORECASE)
TYPE_IGNORE_JUSTIFIED_RE = re.compile(
    r"#\s*(mypy-bug|known-issue|sqlmodel-metaclass):", re.IGNORECASE
)

# --- noqa patterns ---
NOQA_RE = re.compile(r"#\s*noqa\b", re.IGNORECASE)
NOQA_JUSTIFIED_RE = re.compile(r"#\s*noqa-reason:", re.IGNORECASE)
E402_ONLY_RE = re.compile(r"#\s*noqa:\s*E402\s*$", re.IGNORECASE)


def _extract_file_path(payload: dict) -> str | None:
    resp = payload.get("tool_response")
    if not isinstance(resp, dict):
        resp = {}
    inp = payload.get("tool_input")
    if not isinstance(inp, dict):
        inp = {}
    return resp.get("filePath") or inp.get("file_path")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    raw = _extract_file_path(payload)
    if not raw:
        return 0
    path = Path(raw)
    if path.suffix != ".py":
        return 0
    if any(part in EXEMPT_PARTS for part in path.parts):
        return 0

    inp = payload.get("tool_input")
    if not isinstance(inp, dict):
        inp = {}
    new_string = inp.get("new_string") or inp.get("content") or ""
    if not new_string:
        return 0

    lines = new_string.splitlines()

    type_ignore_violations: list[int] = []
    noqa_violations: list[int] = []

    for i, line in enumerate(lines, start=1):
        if TYPE_IGNORE_RE.search(line) and not TYPE_IGNORE_JUSTIFIED_RE.search(line):
            type_ignore_violations.append(i)

        if NOQA_RE.search(line):
            if not NOQA_JUSTIFIED_RE.search(line) and not E402_ONLY_RE.search(line):
                noqa_violations.append(i)

    if not type_ignore_violations and not noqa_violations:
        return 0

    messages: list[str] = []

    if type_ignore_violations:
        messages.append(
            f"Unjustified `# type: ignore` at line(s) "
            f"{', '.join(map(str, type_ignore_violations[:10]))} of the replacement.\n"
            f"Fix the underlying type error instead of suppressing it.\n"
            f"If this is a genuine third-party bug, add a justification marker: "
            f"`# type: ignore[code]  # sqlmodel-metaclass: <reason>` or "
            f"`# type: ignore[code]  # mypy-bug: <reason>`"
        )

    if noqa_violations:
        messages.append(
            f"Unjustified `# noqa` at line(s) "
            f"{', '.join(map(str, noqa_violations[:10]))} of the replacement.\n"
            f"Fix the underlying ruff violation instead of suppressing it.\n"
            f"If suppression is genuinely necessary, add a justification marker: "
            f"`# noqa: XXXX  # noqa-reason: <why this can't be fixed>`\n"
            f"Note: `# noqa: E402` (import order after load_dotenv/sys.path) is "
            f"pre-approved and does not need a marker."
        )

    print(
        f"[block_suppressions] New/modified text in {path} contains suppression comments:\n\n"
        + "\n\n".join(messages),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
