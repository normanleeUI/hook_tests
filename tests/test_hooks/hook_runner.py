"""Hook invocation helpers shared across test modules.

Separated from conftest.py so that test modules can import run_hook()
and HOOKS_DIR directly -- pytest's conftest is auto-loaded but not
importable as a regular module.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"


def run_hook(
    script_name: str,
    payload: Any,
    interpreter: str = "python3",
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int = 10,
) -> tuple[int, str, str]:
    """Invoke a hook script via subprocess, piping a JSON payload on stdin.

    Returns (returncode, stderr, stdout). Skips the test if the script
    does not exist on disk -- this lets the test suite run even if some
    hooks have been removed.
    """
    script = HOOKS_DIR / script_name
    if not script.exists():
        pytest.skip(f"Hook script not found: {script}")

    run_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [interpreter, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
        cwd=cwd,
    )
    return result.returncode, result.stderr, result.stdout


def run_bash_hook(
    script_name: str,
    payload: Any,
    **kwargs: Any,
) -> tuple[int, str, str]:
    """Convenience wrapper: invoke a hook with bash as the interpreter."""
    return run_hook(script_name, payload, interpreter="bash", **kwargs)
