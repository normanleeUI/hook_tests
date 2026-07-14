"""PreToolUse hook on Bash (scoped via `if: Bash(git add*)` in settings.json):
block staging .env files directly, as a second line of defense after
scan_secrets_on_commit.py.

The secrets scan checks staged *content* for known patterns, but a .env file
containing non-standard key names or formats may slip through. Blocking
`git add` on .env files entirely is cheaper and more reliable.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_git_add_env")

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

# Scope guard: only act on real `git add` invocations. The settings
# `if: Bash(git add*)` gate that used to enforce this is NOT honored by the
# current harness (Claude Code 2.1.201 runs the hook on every Bash command),
# so without this guard the .env regex below would block ANY command that
# merely mentions a bare `.env` token. Matches `git add`, `git -C <path> add`,
# and `git -c key=val add` (the config-override form). See TESTING.md Known
# Issues: "if: hook gating regressed to a no-op".
if not re.search(r"\bgit\s+(?:(?:-C|-c)\s+\S+\s+)*add\b", cmd):
    sys.exit(0)

# Match any git add command that includes a .env file (with optional suffix
# like .env.local, .env.production, etc.) or a bare `git add .` / `git add -A`
# which would pick up .env implicitly.
#
# We flag two cases:
#   1. The command explicitly names a .env* file.
#   2. The command uses a glob/wildcard that would capture everything (. or -A).
#
# We do NOT block `git add src/` or named non-env files — those are fine.
#
# Allow-list: template files that by convention contain ONLY placeholder
# values and are meant to be committed (e.g. `.env.example`, `.env.sample`,
# `.env.template`, and their `.dist` variant). These are documentation, not
# secrets. The scan_secrets_on_commit.py content scan still runs on them, so
# if a real key ever lands in one by mistake, it is still caught before the
# commit lands.

env_file_re = re.compile(r"\.env(?:\.[a-zA-Z0-9._-]+)?(?:[\s\"']|$)")
bulk_add_re = re.compile(
    r"git\s+(?:-C\s+\S+\s+)?add\s+(?:(?:-\w+|--[\w-]+)\s+)*(?:\.|--all|-A|-u|--update)(?:\s|$)"
)
template_suffix_re = re.compile(r"\.env\.(example|sample|template|dist)(?:[\s\"']|$)")

env_match = env_file_re.search(cmd)
# If every .env* token in the command is a known-safe template, don't block
# on rule 1. (Rule 2, the bulk `git add .`, still applies.)
if env_match:
    env_tokens = env_file_re.findall(cmd)
    # findall with a non-capturing group returns the full match only when
    # the regex has no capture groups; here the group is non-capturing, so
    # we need finditer to get full matches back.
    all_matches = [m.group(0) for m in env_file_re.finditer(cmd)]
    if all_matches and all(template_suffix_re.search(m) for m in all_matches):
        env_match = None

bulk_match = bulk_add_re.search(cmd)

if env_match or bulk_match:
    reason = (
        f"explicit `.env` file: {env_match.group(0).strip()!r}"
        if env_match
        else "bulk `git add .` / `git add -A` (would stage any .env files present)"
    )
    print(
        f"""BLOCKED by ~/.claude/hooks/block_git_add_env.py: {reason}

Why this rule exists:
  .env files contain API keys and other secrets. Once staged, they are one
  `git commit` away from living permanently in git history — and one `git push`
  away from being harvested by automated scanners within minutes.

  This hook is a second line of defense. The scan_secrets_on_commit.py hook
  catches secrets *in content*, but only for known key patterns. A .env file
  with non-standard variable names (e.g. MY_CUSTOM_TOKEN=...) would pass
  through undetected. Blocking the stage step entirely is safer.

  the user's CLAUDE.md mandates that secrets live in environment variables loaded
  from a .env file that is listed in .gitignore. The file should never be
  committed, even in a private repo.

How to proceed:
  - If you intended to stage everything EXCEPT .env, name files explicitly:
      git add src/ pyproject.toml README.md
  - If .env is already tracked by git (a past mistake), remove it:
      git rm --cached .env
      echo ".env" >> .gitignore
      git add .gitignore
  - If this is a false positive (e.g. a file called something.env.json that
    is not a secrets file), STOP and ask the user to confirm before proceeding.

Do NOT attempt to work around this hook by:
  - Staging via `git commit -a`
  - Using a different git plumbing command to add the file
  - Temporarily renaming the .env file, staging it, then renaming it back""",
        file=sys.stderr,
    )
    sys.exit(2)

print("[block_git_add_env] PASSED — no .env files in staging command", file=sys.stderr)
