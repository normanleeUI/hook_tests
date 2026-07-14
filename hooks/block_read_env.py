"""PreToolUse hook on Read and Bash: block reading .env files.

This replaces the old `Read(**/.env)` glob deny rules in permissions.deny,
which caused 30-second sandbox hangs on WSL2 by triggering recursive
directory scans of ~/projects (1.4M+ entries). See
the WSL2 sandbox-hang investigation for the full investigation.

The permissions.deny approach expanded globs via fs.readdirSync before every
sandboxed command. This hook runs only when the Read tool is actually
invoked, so there is zero sandbox overhead.

Dual-wired: fires on both Read (file_path payload) and Bash (command
payload). The Bash path catches circumvention via cat, head, base64,
source, python3 -c, etc. by scanning for .env filename references
anywhere in the command string.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_read_env")

env_re = re.compile(r"^\.env(?:\.[a-zA-Z0-9._-]+)?$")
template_re = re.compile(r"^\.env\.(example|sample|template|dist)$")

TEMPLATE_SUFFIXES = {".env.example", ".env.sample", ".env.template", ".env.dist"}

# Match .env filenames anywhere in the command string.
# (?<!\w) = not preceded by a word character (prevents matching "foo.env")
# (\.env(?:\.\w+)?) = .env optionally followed by .suffix
# (?=\s|['"\)]|$) = followed by whitespace, quote, paren, or end of string
ENV_IN_CMD_RE = re.compile(r'(?<!\w)(\.env(?:\.\w+)?)(?=\s|[\'"\)]|$)')


def check_read_path(file_path: str) -> None:
    """Check a Read tool invocation for .env file access."""
    basename = os.path.basename(file_path)

    if env_re.match(basename) and not template_re.match(basename):
        print(
            f"""BLOCKED by ~/.claude/hooks/block_read_env.py: {file_path!r}

Why this rule exists:
  .env files contain API keys, database credentials, and other secrets.
  Reading them is almost never necessary — the code loads them at runtime
  via python-dotenv or similar, and Claude should not need to see the
  actual secret values.

  the user's CLAUDE.md mandates that secrets live in environment variables
  loaded from .env files. These files must never be committed, and their
  contents should not be exposed to the assistant.

  Template files (.env.example, .env.sample, .env.template, .env.dist)
  are allowed because they contain only placeholder values and are meant
  to be committed as documentation.

How to proceed:
  - If you need to know which env vars the project uses, read .env.example
    or check the code for os.getenv() / os.environ calls.
  - If the user explicitly asks you to read a .env file, STOP and confirm
    the specific file before proceeding — do not attempt to work around
    this hook.
  - If you need to create or modify a .env file, use the Write or Edit
    tool (those have their own deny rules in permissions.deny).""",
            file=sys.stderr,
        )
        sys.exit(2)

    print(
        f"[block_read_env] PASSED — {basename!r} is not a .env file",
        file=sys.stderr,
    )


def check_bash_command(command: str) -> None:
    """Check a Bash tool invocation for .env file references."""
    matches = ENV_IN_CMD_RE.findall(command)

    for matched_name in matches:
        if matched_name in TEMPLATE_SUFFIXES:
            continue
        if env_re.match(matched_name) and not template_re.match(matched_name):
            print(
                f"""BLOCKED by ~/.claude/hooks/block_read_env.py: command references {matched_name!r}

Why this rule exists:
  .env files contain API keys, database credentials, and other secrets.
  Bash commands that read .env files (cat, head, source, base64, etc.)
  expose secret values to the assistant, just like the Read tool would.

  Template files (.env.example, .env.sample, .env.template, .env.dist)
  are allowed because they contain only placeholder values.

How to proceed:
  - If you need to know which env vars the project uses, read .env.example
    or check the code for os.getenv() / os.environ calls.
  - If the user explicitly asks you to read a .env file, STOP and confirm
    the specific file before proceeding.""",
                file=sys.stderr,
            )
            sys.exit(2)

    print(
        "[block_read_env] PASSED — command does not reference .env files",
        file=sys.stderr,
    )


def main() -> None:
    """Route to Read or Bash path based on payload keys."""
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    if "file_path" in tool_input and "command" not in tool_input:
        file_path = tool_input.get("file_path", "")
        if not isinstance(file_path, str):
            sys.exit(0)
        check_read_path(file_path)
    elif "command" in tool_input:
        command = tool_input.get("command", "")
        if not isinstance(command, str):
            sys.exit(0)
        check_bash_command(command)
    # else: unknown payload shape — pass silently


main()
