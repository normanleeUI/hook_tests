"""PreToolUse hook on Bash: block bare `pip install` outside of uv/venv.

Reads the tool-call JSON on stdin. If the command is a bare `pip install`
(not `uv pip install`, not `./venv/bin/pip install`, not `python -m pip
install`), prints an explanatory message to stderr and exits 2 to block.

The error message is intentionally verbose: it explains *why* the rule
exists so a future LLM reading the block message understands the reasoning
and does not try to work around it (e.g. by renaming, piping, or splitting
the command).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_bare_pip")

try:
    data = json.loads(sys.stdin.read())
except (json.JSONDecodeError, ValueError):
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
tool_input = data.get("tool_input")
if not isinstance(tool_input, dict):
    sys.exit(0)
cmd = tool_input.get("command", "")
if not isinstance(cmd, str):
    sys.exit(0)

# Match `pip install` only when it's not preceded by a path component or
# word character. So `pip install x` matches; `uv pip install x` does not
# (preceded by space-then-word `uv`, the lookbehind via `[^./\w]` fails);
# `./venv/bin/pip install x` does not (preceded by `/`).
bare_pip_re = re.compile(r"(^|[^./\w-])pip3?\s+install\b")

matches = list(bare_pip_re.finditer(cmd))
# Filter out matches where "uv" immediately precedes the matched pip token.
# group(1) is the prefix char (or empty at ^), so pip starts at m.start() + len(group(1)).
bare_matches = [
    m for m in matches if not re.search(r"\buv\s+$", cmd[: m.start() + len(m.group(1))])
]
if bare_matches:
    print(
        """BLOCKED by ~/.claude/hooks/block_bare_pip.py: bare `pip install` is not allowed.

Why this rule exists:
  the user's global CLAUDE.md requires every Python project to use an isolated
  environment (uv, venv, or conda). Running `pip install` without first
  activating a project environment installs the package into the *global*
  Python interpreter. This:
    1. Pollutes the global Python with packages that belong to one project,
    2. Breaks reproducibility (no record of the install in pyproject.toml /
       requirements.txt / uv.lock),
    3. Causes hard-to-debug version conflicts across projects,
    4. Makes "works on my machine" failures the norm.

  This is a foundational best practice for Python development, not a
  preference. the user has explicitly asked for best practices to be
  enforced, not optional.

How to proceed:
  - PREFERRED: `uv add <package>`
      Adds the dependency to pyproject.toml AND installs it in the project
      env in one step. This is the modern best-practice path.
  - If you specifically need pip semantics: `uv pip install <package>`
      Project-aware pip wrapper; will not be blocked by this hook.
  - If working inside an already-activated venv and you really need raw pip:
      `./.venv/bin/pip install <package>`      (Linux/macOS)
      `./.venv/Scripts/pip install <package>`  (Windows)
      Both are explicit paths and will not be blocked.
  - If no project environment exists yet, create one first:
      `uv init` or `uv venv`, then add dependencies.

Do NOT attempt to work around this hook by:
  - Aliasing or renaming pip
  - Piping (e.g. `echo pip install x | sh`)
  - Spawning a subshell or using `eval`
  - Using `python -m pip install` to bypass the regex
  - Calling pip from inside a Python script

If you genuinely believe `pip install` is the right call here (extremely
rare), STOP and ask the user before proceeding. Do not just retry with a
clever workaround.""",
        file=sys.stderr,
    )
    sys.exit(2)
