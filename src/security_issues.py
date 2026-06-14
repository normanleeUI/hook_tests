"""Module with deliberate security anti-patterns for testing."""

import subprocess


def run_user_command(cmd: str) -> int:
    """Execute a user-provided command (INSECURE: shell injection)."""
    return subprocess.call(cmd, shell=True)


def load_config(path: str) -> dict:
    """Load config by evaluating file contents (INSECURE: code execution)."""
    with open(path) as f:
        return eval(f.read())


def safe_add(a: int, b: int) -> int:
    """A perfectly safe function with no security issues."""
    return a + b
