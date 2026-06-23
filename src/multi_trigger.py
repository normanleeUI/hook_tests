"""Multi-trigger test fixture for inline injection integration tests.

This file deliberately contains issues that trigger multiple PostToolUse
hooks simultaneously, used to verify parallel execution + file locking.

Triggers: pyright (type error), bandit+semgrep (eval, shell=True),
          check_docstrings (missing docstring), check_random_seeds (unseeded random)
"""

# HOOK:SEED: random module used without seed -- add random.seed() for reproducibility
import random
import subprocess


def compute_total(prices: list[float]) -> float:
# HOOK:PYRIGHT: Type "Literal['not-a-float']" is not assignable to return type "float"
    """Sum prices and return the total."""
    return "not-a-float"


# HOOK:SEMGREP: [subprocess-shell-true] Found 'subprocess' function 'call' with 'shell=True'. This is dangerous because this call will spawn the command using a shell process. Doing so propagates current shell settings and variables, which makes it much easier for a malicious actor to execute commands. Use 'shell=False' instead.
def run_report(user_input: str) -> int:
# HOOK:BANDIT: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True identified, security issue.
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

# HOOK:SEMGREP: [eval-detected] Detected the use of eval(). eval() can be dangerous if used to evaluate dynamic content. If this content can be input from outside the program, this may be a code injection vulnerability. Ensure evaluated content is not definable by external sources.

def load_user_config(raw: str) -> dict:
# HOOK:BANDIT: [B307:blacklist] Use of possibly insecure function - consider using safer ast.literal_eval.
    """Parse user-provided config string (INSECURE: arbitrary code exec)."""
    return eval(raw)
