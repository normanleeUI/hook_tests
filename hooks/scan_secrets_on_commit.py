"""PreToolUse hook on Bash (scoped via `if: Bash(git commit*)` in
settings.json): scan staged git changes for common secret patterns and
block the commit if any are found.

This is the LAST line of defense before secrets land in git history.
Once a secret is committed, it lives in the repo history forever (even if
removed in a later commit) and is harvested by bots within minutes if the
repo is ever pushed publicly. The error message is intentionally verbose to
make sure any future LLM understands the stakes and does not try to bypass
the hook.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("scan_secrets_on_commit")

# Scope guard: only scan on real `git commit` invocations. The settings
# `if: Bash(git commit*)` gate that used to enforce this is NOT honored by the
# current harness (Claude Code 2.1.201 runs the hook on every Bash command).
# Without this guard the hook shells out to `git diff --cached` on every single
# Bash command (wasteful) and could false-block an unrelated command if
# secret-looking content happens to be staged. Matches `git commit`,
# `git -C <path> commit`, and `git -c key=val commit`. See TESTING.md Known
# Issues: "if: hook gating regressed to a no-op".
try:
    _data = json.loads(sys.stdin.read())
    _cmd = _data["tool_input"]["command"] if isinstance(_data, dict) else ""
except (json.JSONDecodeError, ValueError, KeyError, TypeError):
    _cmd = ""
if not isinstance(_cmd, str) or not re.search(
    r"\bgit\s+(?:(?:-C|-c)\s+\S+\s+)*commit\b", _cmd
):
    sys.exit(0)

# Patterns are conservative — high-confidence prefixes only, to minimize
# false positives. We accept that this won't catch every secret; defense in
# depth means this hook is one of several layers (env vars, .gitignore,
# permissions deny rules on .env files, etc.).
PATTERNS: dict[str, str] = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI API key": r"sk-(?!ant-)[A-Za-z0-9]{20,}",
    "AWS access key ID": r"AKIA[0-9A-Z]{16}",
    "GitHub personal access token (classic)": r"ghp_[A-Za-z0-9]{36}",
    "GitHub fine-grained token": r"github_pat_[A-Za-z0-9_]{82}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "Generic private key block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
}

# Get staged diff. -U0 minimizes context lines (we only care about additions).
try:
    result = subprocess.run(
        ["git", "diff", "--cached", "-U0"],
        capture_output=True,
        text=True,
        check=False,
    )
except FileNotFoundError:
    # git not installed — let the commit proceed; that's a different problem.
    sys.exit(0)

if result.returncode != 0:
    print(
        "[scan_secrets_on_commit] BLOCKED — git diff failed (returncode "
        f"{result.returncode}); failing closed because staged changes cannot "
        "be verified as secret-free.",
        file=sys.stderr,
    )
    sys.exit(2)

# Only scan added-content lines (lines starting with '+'), skipping diff
# metadata headers (diff, ---, +++, @@) to avoid false positives from
# secret-like filenames appearing in header lines.
added_lines = []
for line in result.stdout.splitlines():
    if (
        line.startswith("diff ")
        or line.startswith("---")
        or line.startswith("+++")
        or line.startswith("@@")
    ):
        continue
    if line.startswith("+"):
        added_lines.append(line)
diff = "\n".join(added_lines)

for name, pattern in PATTERNS.items():
    match = re.search(pattern, diff)
    if not match:
        continue

    # Truncate match for display (don't print the whole secret).
    snippet = match.group(0)
    shown = snippet[:12] + "..." if len(snippet) > 12 else snippet

    print(
        f"""BLOCKED by ~/.claude/hooks/scan_secrets_on_commit.py: possible {name} in staged changes.

Why this rule exists:
  Committing API keys or private keys to git is one of the most damaging
  security mistakes a developer can make. Once a secret enters git history:
    1. It is permanent — even `git rm` + later commits leave it in history,
    2. It is harvested within MINUTES of any public push (bots scan GitHub,
       GitLab, etc. continuously for leaked credentials),
    3. Leaked LLM API keys (OpenAI, Anthropic) can result in thousands of
       dollars of fraudulent usage on the user's account before they notice,
    4. Leaked AWS keys can result in cryptomining on his account billed to
       his card,
    5. Rotating a leaked secret is the ONLY remediation, and it is painful.

  the user's CLAUDE.md mandates that secrets live in environment variables
  loaded from a `.env` file (which is `.gitignore`d and additionally blocked
  from being read by Claude via permission rules in settings.json).

What was detected:
  Pattern:    {pattern}
  Match name: {name}
  Snippet:    {shown}  (truncated; full secret not printed)

How to proceed:
  1. Unstage the offending file:
       git restore --staged <file>
  2. Move the secret value out of the source file into a `.env` file at the
     project root:
       MY_KEY=sk-ant-...
  3. Make sure `.env` is in `.gitignore` (it should be — the user's permissions
     deny rules also block reading it).
  4. In the source file, replace the literal with a runtime lookup:
       import os
       api_key = os.environ["MY_KEY"]
     Or with python-dotenv:
       from dotenv import load_dotenv; load_dotenv()
       api_key = os.environ["MY_KEY"]
  5. Re-stage the cleaned file and re-attempt the commit.

Do NOT attempt to work around this hook by:
  - Splitting the secret across string concatenations to fool the regex
  - Base64- or hex-encoding the secret in source
  - Adding `--no-verify` or otherwise bypassing the hook
  - Disabling this hook in settings.json
  - Telling the user this is a false positive without them explicitly confirming

If this is a genuine false positive (e.g. a test fixture string that happens
to match a pattern), STOP and ask the user before proceeding. Even then, the
right fix is usually to make the test fixture not look like a real key.""",
        file=sys.stderr,
    )
    sys.exit(2)

print(
    f"[scan_secrets_on_commit] PASSED — {len(PATTERNS)} patterns checked against staged diff",
    file=sys.stderr,
)
