"""PreToolUse hook on Bash (no `if:` — fires on ALL Bash commands): block any
command containing `--no-verify`.

`git commit --no-verify` (and `git push --no-verify`) skip git-native hooks by
design, so the repo's pre-commit secret backstop cannot stop them. The only
place to catch `--no-verify` is BEFORE git runs — here. We match the flag
anywhere in the command string so `git -C . commit --no-verify` and compound
forms (`... && git commit --no-verify`) are all caught, mirroring the
content-scanning style of block_read_env.py.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_no_verify")

# A `git commit` invocation, captured up to the next shell separator so we only
# inspect that command's OWN arguments — not an unrelated `-n` in a chained
# command like `git commit -m x && sort -n file`.
#
# `commit` must be a whitespace-delimited TOKEN following `git` (and any global
# options like `-C /path` / `-c k=v`), not any substring: otherwise `git add
# .pre-commit-config.yaml` matches the "commit" inside the filename and its
# trailing `-config` is misread as a `-n` short flag. The lazy `(?:\s+…)*?`
# consumes intervening global-option tokens without crossing a shell separator.
_GIT_COMMIT_RE = re.compile(r"\bgit\b(?:\s+[^\s&|;]+)*?\s+commit\b([^&|;\n]*)")
# A single-dash short-flag cluster containing 'n' (e.g. -n, -nm, -an): for
# `git commit`, -n IS --no-verify. Double-dash long options are excluded by the
# lookbehind, so --no-verify is handled by the substring check, not here.
_SHORT_N_RE = re.compile(r"(?<![\w-])-[A-Za-z]*n[A-Za-z]*\b")


def _skips_git_hooks(command: str) -> bool:
    """True if the command tells git to skip hooks: an explicit --no-verify
    anywhere, or a `git commit` carrying a -n short flag (standalone or bundled).

    Tradeoff: a commit whose *message* literally contains a `-n` token can
    false-positive. Acceptable for a security backstop — it fails safe.
    """
    if "--no-verify" in command:
        return True
    m = _GIT_COMMIT_RE.search(command)
    return bool(m and _SHORT_N_RE.search(m.group(1)))


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        sys.exit(0)

    if _skips_git_hooks(command):
        print(
            """BLOCKED by ~/.claude/hooks/block_no_verify.py: command skips git hooks

Detected --no-verify or `git commit -n` (the short form of --no-verify, incl.
bundled forms like -nm).

Why this rule exists:
  Skipping git-native hooks bypasses this repo's pre-commit secret backstop
  (.git/hooks/pre-commit). That backstop is the LAST guaranteed line of defense
  before a secret lands in git history — where it lives forever and is harvested
  by bots within minutes of any public push. Bypassing it defeats the entire
  point of the layered secret protection.

How to proceed:
  - Never skip hooks to work around a block. If a hook is blocking a legitimate
    commit, fix the underlying issue (move the secret to a .gitignored .env
    file, unstage the .env file, etc.).
  - If a git hook is genuinely broken, STOP and ask the user before bypassing.""",
            file=sys.stderr,
        )
        sys.exit(2)

    print("[block_no_verify] PASSED — command does not skip git hooks", file=sys.stderr)


main()
