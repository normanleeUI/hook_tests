"""PostToolUse hook on Write: remind about missing test files.

When a new implementation file (.py or .R) is created, checks whether a
corresponding test file exists.  If not, outputs a non-blocking reminder
so the model can create one — supporting the TDD discipline required by
CLAUDE.md.

Why Write-only (not Edit)?
  Write means "create or fully overwrite a file" — i.e. the moment a new
  module enters the project.  That is the natural checkpoint for asking
  "where are the tests?"  Firing on every Edit would be noisy for files
  that were already flagged at creation time.

Exit codes:
  0 — always.  This hook informs, never blocks.  The reminder is printed
      to stdout so the model sees it and can act on it.

Heuristics for locating test files:
  Python:  tests/test_<name>.py  (pytest convention)
  R:       tests/testthat/test_<name>.R  (testthat convention)
  Also checks the same directory and up to two parent levels.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("check_test_pair")

data = json.loads(sys.stdin.read())
file_path: str = data.get("tool_response", {}).get("filePath") or data.get(
    "tool_input", {}
).get("file_path", "")
if not file_path:
    sys.exit(0)

p = Path(file_path)

# ── Only check .py and .R files ──────────────────────────────────────
if p.suffix not in (".py", ".R", ".r"):
    sys.exit(0)

# ── Skip if this IS already a test file ──────────────────────────────
name_lower = p.name.lower()
if (
    name_lower.startswith("test_")
    or name_lower.startswith("test-")
    or name_lower.endswith("_test.py")
    or "tests" in p.parts
    or "testthat" in p.parts
):
    sys.exit(0)

# ── Skip boilerplate / config files that don't need tests ───────────
SKIP_NAMES = {
    "__init__.py",
    "conftest.py",
    "setup.py",
    "manage.py",
    "__main__.py",
}
if p.name in SKIP_NAMES:
    sys.exit(0)

# ── Skip hooks / config scripts inside ~/.claude/ ───────────────────
if ".claude" in p.parts:
    sys.exit(0)

# ── Build list of conventional test-file locations ───────────────────
candidates: list[Path] = []

if p.suffix == ".py":
    test_name = f"test_{p.name}"
    # Walk up to two parents looking for a tests/ directory
    for ancestor in (p.parent, p.parent.parent, p.parent.parent.parent):
        candidates.append(ancestor / "tests" / test_name)
    # Same directory (flat layout)
    candidates.append(p.parent / test_name)

elif p.suffix.lower() == ".r":
    test_name = f"test_{p.stem}.R"
    for ancestor in (p.parent, p.parent.parent, p.parent.parent.parent):
        candidates.append(ancestor / "tests" / "testthat" / test_name)
    candidates.append(p.parent / test_name)

# ── If any candidate exists, all good ───────────────────────────────
if any(c.exists() for c in candidates):
    sys.exit(0)

# ── No test file found — emit a non-blocking reminder ───────────────
# Pick the most conventional location as the suggestion.
suggested = candidates[0]
lang = "pytest" if p.suffix == ".py" else "testthat"

print(
    f"TDD reminder for {p.name}: no matching test file found.\n"
    f"\n"
    f"  Suggested location: {suggested}\n"
    f"\n"
    f"CLAUDE.md prefers test-driven development ({lang}).\n"
    f"Consider creating the test file before (or immediately after) the\n"
    f"implementation, with at least one test case covering the happy path."
)
