"""PostToolUse hook on Bash: run pip-audit after dependency changes to check
for known vulnerabilities.

Triggers on two paths:
  1. Explicit install commands (``uv add``/``uv sync``/``uv pip install``) --
     a guaranteed signal that deps moved; audits unconditionally.
  2. ``uv run`` -- which implicitly re-syncs the env from uv.lock. This runs on
     nearly every command, so we only audit it when uv.lock's *content* actually
     changed since the last audit (sha256 compare). Closes the coverage gap
     where a project's deps arrive via ``uv run`` and never a watched install
     command, so pip-audit never fired. (Empirically the common case: a project
     whose whole session used only ``uv run``.)

When vulnerabilities are found, writes findings to
.hook_state/pip_audit/report.json so the companion PreToolUse guard
(pip_audit_guard.py) can block future dependency operations. When the
audit is clean, deletes any existing state file so the guard stops blocking.

Always exits 0 on PostToolUse (exit 2 on PostToolUse is cosmetic --
the guard hook handles actual blocking).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import ensure_state_dir, get_state_dir
from hook_log import log_hook

log_hook("pip_audit_check")

_HASH_FILE = "last_lock_hash"


def _project_lock() -> Path | None:
    """uv.lock at the project root (the state dir's parent -- git root in
    production, the HOOK_STATE_DIR parent under test). None if absent."""
    lock = get_state_dir().parent / "uv.lock"
    return lock if lock.exists() else None


def _current_lock_hash() -> str | None:
    lock = _project_lock()
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock else None


def _lock_changed_since_last_audit(state_dir: Path) -> bool:
    """True if uv.lock differs from the last audited state (or was never
    audited). Gates the ``uv run`` path so a full network pip-audit is only
    paid when the lockfile genuinely moved."""
    current = _current_lock_hash()
    if current is None:
        return False  # no lockfile -> nothing to audit
    hash_file = state_dir / "pip_audit" / _HASH_FILE
    try:
        return hash_file.read_text().strip() != current
    except OSError:
        return True  # no prior record -> audit once


def _record_lock_hash(state_dir: Path) -> None:
    """Remember the lock state we just audited so a later ``uv run`` can skip
    cheaply. Recorded after every audit, including the explicit-command path."""
    current = _current_lock_hash()
    if current is None:
        return
    hash_dir = state_dir / "pip_audit"
    ensure_state_dir(hash_dir)
    (hash_dir / _HASH_FILE).write_text(current)


def _audit_locked_deps(project_dir: Path, env: dict, tmpdir: str):
    """Audit the project's *resolved* dependencies.

    Bare ``uvx pip-audit`` audits uvx's isolated tool env -- 0 project packages,
    so it silently passes everything (verified: a known-vulnerable pin came back
    "All dependencies clean, 0 packages audited"). Instead export the locked deps
    and audit that file, so pip-audit sees the project's actual dependency set.

    Returns the pip-audit CompletedProcess, or None when there is nothing to
    audit (no lockfile / empty export) -- the caller treats that as "clean".
    """
    export = subprocess.run(
        [
            "uv",
            "export",
            "--format",
            "requirements-txt",
            "--no-emit-project",
            "--no-hashes",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(project_dir),
        env=env,
    )
    if export.returncode != 0 or not export.stdout.strip():
        return None
    reqs = os.path.join(tmpdir, "pip_audit_reqs.txt")
    with open(reqs, "w") as f:
        f.write(export.stdout)
    return subprocess.run(
        ["uvx", "pip-audit", "--progress-spinner=off", "-r", reqs],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def main() -> int:
    """Audit dependencies when a uv command implies they may have moved.

    Reads the PostToolUse payload on stdin; returns 0 always (blocking is the
    guard hook's job). Side effect: writes/clears the vuln report state file.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    is_explicit = (
        "uv add" in command or "uv sync" in command or "uv pip install" in command
    )
    is_uv_run = "uv run" in command
    if not (is_explicit or is_uv_run):
        return 0

    # PostToolUse payloads use `tool_response` (not `tool_result`), and the Bash
    # tool_response carries no exit code (keys: stdout, stderr, interrupted,
    # isImage, noOutputExpected). We can't gate on command success, so audit
    # whenever a uv dependency command ran and wasn't interrupted — a failed
    # `uv add` leaves deps unchanged, so auditing the current state is harmless.
    tool_response = payload.get("tool_response") or {}
    if tool_response.get("interrupted"):
        return 0

    state_dir = get_state_dir()

    # `uv run` fires on nearly every command; only pay for a full audit when the
    # lockfile actually moved. Explicit install commands always proceed.
    if is_uv_run and not is_explicit and not _lock_changed_since_last_audit(state_dir):
        return 0

    print(
        "[pip-audit] Scanning dependencies for known vulnerabilities...",
        file=sys.stderr,
    )

    tmpdir = os.environ.get("TMPDIR", "/tmp")
    env = {
        **os.environ,
        "XDG_CACHE_HOME": tmpdir,
        "UV_TOOL_DIR": os.path.join(tmpdir, "uv-tools"),
    }
    result = _audit_locked_deps(state_dir.parent, env, tmpdir)
    if result is None:
        print(
            "[pip-audit] No exportable lockfile; nothing to audit.",
            file=sys.stderr,
        )
        _record_lock_hash(state_dir)
        return 0

    report_dir = state_dir / "pip_audit"
    report_file = report_dir / "report.json"

    if result.returncode != 0:
        print(
            f"[pip-audit] VULNERABILITIES FOUND:\n{result.stdout}\n{result.stderr}",
            file=sys.stderr,
        )
        ensure_state_dir(report_dir)
        report_file.write_text(
            json.dumps(
                {
                    "vulns": result.stdout,
                    "summary": result.stderr.strip(),
                }
            )
        )
    else:
        if report_file.exists():
            report_file.unlink()
        pkg_count = result.stdout.strip().count("\n")
        print(
            f"[pip-audit] All dependencies clean ({pkg_count} packages audited).",
            file=sys.stderr,
        )

    # Record the lock state we just audited so a later `uv run` skips cheaply.
    _record_lock_hash(state_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
