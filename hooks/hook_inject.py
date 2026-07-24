"""Shared infrastructure for hook inline injection and state-file management.

Provides utilities for:
- Injecting '# HOOK:NAME: message' comments into source files at specific lines
- Cleaning stale hook comments on re-runs
- Managing .hook_state/ directories for persistent hook findings

Used by Steps 4-9 of the channel redesign plan. Not a hook itself.
"""

import fcntl
import json
import os
import subprocess
from pathlib import Path

HOOK_PREFIX = "# HOOK:"


def remove_hook_comments(lines: list[str], hook_name: str) -> list[str]:
    """Remove lines whose stripped form starts with '# HOOK:{hook_name}:'."""
    prefix = f"# HOOK:{hook_name}:"
    return [line for line in lines if not line.strip().startswith(prefix)]


def inject_at_line(lines: list[str], line_num: int, hook_name: str, msg: str) -> None:
    """Insert a hook comment before the given 1-based line number. Mutates in place.

    The comment is framed as an addressed, provenance-tagged directive rather than
    a bare diagnostic: the heed-rate probe (probes/HEED_PROBE.md) found agents
    ignore bare `# HOOK:` lines as pre-existing churn (0/3 acknowledged) but
    acknowledge the reframed form (3/3). Keeps the `# HOOK:{hook_name}:` prefix so
    remove_hook_comments() still self-cleans it. Single line by contract -- a
    continuation line would not start with the prefix and would leak as stale.
    """
    comment = (
        f"# HOOK:{hook_name}: [automated guardrail] {msg} "
        f"-- fix or explicitly acknowledge before continuing; re-inserted until resolved\n"
    )
    lines.insert(line_num - 1, comment)


def read_clean_write(
    file_path: str, hook_name: str, analyze_fn, blocking: bool = False
) -> None:
    """Orchestrator: read file, remove stale HOOK comments for this hook_name,
    call analyze_fn(clean_content_str, clean_lines_list) -> list[(line_num, message)],
    inject new findings at correct lines, write back.

    Uses fcntl.flock to serialize concurrent hook writes on the same file.
    The lock is held across read-analyze-write to prevent parallel hooks
    from overwriting each other's injected comments.

    When ``blocking`` is True, the findings are ALSO recorded to blocking state
    so the commit guard (block_unresolved_findings.py) can enforce them -- an
    inline comment alone is passive (agents acknowledge but decline to fix it,
    per the heed-rate probe); the gate makes committing-without-fixing impossible.
    Flip this on only for must-fix findings (secrets, security), not advisory ones.
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

            # Sync blocking state inside the lock so it stays consistent with the
            # file's injected comments, even when the file write is skipped below.
            if blocking:
                messages = [f"line {ln}: {msg}" for ln, msg in sorted(findings)]
                record_blocking_findings(hook_name, file_path, messages)

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


def _blocking_state_file(hook_name: str) -> Path:
    return get_state_dir() / "blocking_findings" / f"{hook_name}.json"


def record_blocking_findings(
    hook_name: str, file_path: str, messages: list[str]
) -> None:
    """Persist this hook's must-fix findings for file_path so the commit guard
    can enforce them. Empty ``messages`` clears the file's entry (the detector
    ran clean). Keyed by hook_name so each detector owns and clears only its own
    findings; the guard aggregates across all detectors' state files.
    """
    state_file = _blocking_state_file(hook_name)
    data: dict = {}
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}

    if messages:
        data[file_path] = messages
    else:
        data.pop(file_path, None)

    if data:
        ensure_state_dir(state_file.parent)
        state_file.write_text(json.dumps(data, indent=2))
    elif state_file.exists():
        state_file.unlink()


def read_blocking_findings() -> dict[str, list[str]]:
    """Aggregate all detectors' unresolved blocking findings, keyed by file path.
    Trusted by the commit guard -- no re-analysis (matches pip_audit_guard)."""
    findings_dir = get_state_dir() / "blocking_findings"
    if not findings_dir.is_dir():
        return {}

    merged: dict[str, list[str]] = {}
    for state_file in sorted(findings_dir.glob("*.json")):
        try:
            data = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        hook_name = state_file.stem
        for path, messages in data.items():
            for msg in messages:
                merged.setdefault(path, []).append(f"[{hook_name}] {msg}")
    return merged


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
