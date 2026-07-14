"""PreToolUse hook on Edit|Write: block **-glob Read deny rules in settings.json.

On WSL2 with sandbox enabled, Read(**...) patterns in permissions.deny cause
Claude Code to recursively scan the base directory (via fs.readdirSync with
{recursive: true}) before every sandboxed bash command. With ~/projects
containing 1.4M+ files, each glob expansion takes ~5 seconds. Multiple
patterns compound to 10-30 second UI freezes.

This hook fires before any Edit or Write, reconstructs what the file would
look like after the proposed edit, and checks for dangerous patterns.
Exit code 2 blocks the edit before it is applied.

See the WSL2 sandbox-hang investigation for the full investigation.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("block_glob_deny_rules")


def reconstruct_proposed_content(data: dict) -> str | None:
    """Reconstruct what the file would contain after the proposed edit.

    For Write operations, tool_input.content is the full proposed file.
    For Edit operations, read the current file from disk and apply the
    proposed old_string -> new_string replacement.

    Returns the proposed file content as a string, or None if reconstruction
    fails (file not found, old_string not in file, etc.).  Never modifies
    the file on disk.
    """
    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Write operation: content is the full proposed file
    content = tool_input.get("content")
    if content is not None:
        return content

    # Edit operation: reconstruct from disk + old_string/new_string
    old_string = tool_input.get("old_string")
    new_string = tool_input.get("new_string")
    if old_string is None or new_string is None:
        return None

    try:
        with open(file_path) as f:
            current_content = f.read()
    except (FileNotFoundError, OSError):
        return None

    if old_string not in current_content:
        return None

    return current_content.replace(old_string, new_string, 1)


def find_glob_violations(settings: dict) -> list[str]:
    """Check settings dict for dangerous ** glob patterns.

    Returns a list of human-readable problem descriptions, one per violation.
    """
    problems: list[str] = []

    deny_rules: list[str] = settings.get("permissions", {}).get("deny", [])
    for rule in deny_rules:
        if rule.startswith("Read(") and "**" in rule:
            problems.append(f"  permissions.deny: {rule}")

    filesystem = settings.get("sandbox", {}).get("filesystem", {})
    glob_keys = [
        "allowRead",
        "denyRead",
        "allowWrite",
        "denyWrite",
        "denyOnly",
        "allowWithinDeny",
        "denyWithinAllow",
    ]
    for key in glob_keys:
        entries: list[str] = filesystem.get(key, [])
        for entry in entries:
            if "**" in entry:
                problems.append(f"  sandbox.filesystem.{key}: {entry}")

    return problems


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str):
        sys.exit(0)

    if "settings" not in file_path or not file_path.endswith(".json"):
        return 0

    if "/.claude/" not in file_path:
        return 0

    proposed_content = reconstruct_proposed_content(data)
    if proposed_content is None:
        return 0

    try:
        settings = json.loads(proposed_content)
    except json.JSONDecodeError:
        return 0

    problems = find_glob_violations(settings)

    if problems:
        print(
            f"""BLOCKED by ~/.claude/hooks/block_glob_deny_rules.py: dangerous ** glob pattern(s) found in {file_path}.

Found {len(problems)} pattern(s) that will cause sandbox hangs on WSL2:

"""
            + "\n".join(problems)
            + """

Why this is blocked:
  On Linux/WSL2, Claude Code's sandbox expands ** glob patterns in Read deny
  rules and allowRead/denyRead entries using fs.readdirSync(baseDir,
  {recursive: true}). If the base directory is large (e.g. ~/projects with
  1.4M+ files), each pattern takes ~5 seconds to expand. Multiple patterns
  cause 10-30 second freezes before every bash command.

  This was the root cause of a multi-session debugging effort. See:
  the WSL2 sandbox-hang investigation

How to fix:
  Replace ** glob patterns with specific file paths:
    BAD:  Read(**/.env)           — scans entire project tree
    GOOD: Read(/path/to/project/.env)  — no scanning

  For system paths, use // prefix (base dir / triggers the "too broad" guard
  and is skipped):
    OK:   Edit(//etc/**)          — skipped by sandbox expander

  For home-directory paths, use ~ without **:
    BAD:  Read(~/.aws/**)         — scans ~/.aws AND can fail bwrap mount
    OK:   Read(~/.bashrc)         — direct path, no glob

Do NOT work around this by:
  - Using nested single-* globs to simulate **
  - Adding allowRead carve-outs (these ALSO trigger recursive scanning)
  - Disabling the sandbox to avoid the issue

If you believe a ** glob is genuinely needed here, STOP and ask the user.""",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
