"""Multi-trigger test fixture for inline injection integration tests.

This file deliberately contains issues that trigger multiple PostToolUse
hooks simultaneously, used to verify parallel execution + file locking.

Triggers: pyright (type error), bandit+semgrep (eval, shell=True),
          check_docstrings (missing docstring), check_random_seeds (unseeded random)
"""

import random
import subprocess


def compute_total(prices: list[float]) -> float:
    """Sum prices and return the total."""
    return "not-a-float"


def run_report(user_input: str) -> int:
    """Execute a report command (INSECURE: shell injection)."""
    result = subprocess.call(user_input, shell=True)
    return result


def transform_data(records, threshold):
    filtered = [r for r in records if r > threshold]
    return filtered


def pick_sample(data: list) -> list:
    """Select a random 10% sample (unseeded — non-reproducible)."""
    k = max(1, len(data) // 10)
    return random.sample(data, k)


def load_user_config(raw: str) -> dict:
    """Parse user-provided config string (INSECURE: arbitrary code exec)."""
    return eval(raw)
