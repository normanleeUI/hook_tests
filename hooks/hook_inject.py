"""Shared infrastructure for hook inline injection and state-file management.

Provides utilities for:
- Injecting '# HOOK:NAME: message' comments into source files at specific lines
- Cleaning stale hook comments on re-runs
- Managing .hook_state/ directories for persistent hook findings

Used by Steps 4-9 of the channel redesign plan. Not a hook itself.
"""

import fcntl
import os
import subprocess
from pathlib import Path

HOOK_PREFIX = "# HOOK:"


def remove_hook_comments(lines: list[str], hook_name: str) -> list[str]:
    """Remove lines whose stripped form starts with '# HOOK:{hook_name}:'."""
    prefix = f"# HOOK:{hook_name}:"
    return [line for line in lines if not line.strip().startswith(prefix)]


def inject_at_line(lines: list[str], line_num: int, hook_name: str, msg: str) -> None:
    """Insert a hook comment before the given 1-based line number. Mutates in place."""
    comment = f"# HOOK:{hook_name}: {msg}\n"
    lines.insert(line_num - 1, comment)


def read_clean_write(file_path: str, hook_name: str, analyze_fn) -> None:
    """Orchestrator: read file, remove stale HOOK comments for this hook_name,
    call analyze_fn(clean_content_str, clean_lines_list) -> list[(line_num, message)],
    inject new findings at correct lines, write back.

    Uses fcntl.flock to serialize concurrent hook writes on the same file.
    The lock is held across read-analyze-write to prevent parallel hooks
    from overwriting each other's injected comments.
    """
    lock_path = file_path + ".hook_lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(file_path, "r") as f:
                original_lines = f.readlines()

            clean_lines = remove_hook_comments(original_lines, hook_name)
            had_stale = len(clean_lines) != len(original_lines)

            clean_content = "".join(clean_lines)
            findings = analyze_fn(clean_content, clean_lines)

            if not had_stale and not findings:
                return

            for line_num, msg in sorted(findings, reverse=True):
                inject_at_line(clean_lines, line_num, hook_name, msg)

            with open(file_path, "w") as f:
                f.writelines(clean_lines)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    try:
        os.unlink(lock_path)
    except OSError:
        pass


def get_state_dir() -> Path:
    """Return the .hook_state/ directory path.

    Uses HOOK_STATE_DIR env var if set (for testing), otherwise
    falls back to `git rev-parse --show-toplevel` / .hook_state,
    or cwd / .hook_state if not in a git repo.
    """
    env_override = os.environ.get("HOOK_STATE_DIR")
    if env_override:
        return Path(env_override)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip()) / ".hook_state"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd() / ".hook_state"


def ensure_state_dir(path: Path) -> Path:
    """Create the directory (and parents) if it doesn't exist. Returns path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
