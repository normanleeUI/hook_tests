"""PostToolUse hook on Edit|Write: warn when randomness is used without
setting an explicit seed.

Scans .py and .R files for imports or calls that produce random numbers,
then checks whether the same file sets a seed.  If not, injects inline
'# HOOK:SEED:' comments at the relevant lines so the model can see the
warning in context and add a seed immediately.

Why this matters:
  the user's CLAUDE.md says: "Set random seeds anywhere randomness is used."
  Without a fixed seed, results that depend on random number generation
  cannot be exactly reproduced -- making verification, peer review, and
  debugging effectively impossible for research code.

Detection strategy:
  Python -- AST-based import detection for high-confidence modules
    (random, numpy.random, torch, tensorflow, scipy.stats, sklearn),
    plus regex fallback for seed-setting patterns that the AST walk
    might miss (e.g. `random_state=42` in keyword arguments).
  R -- regex-based detection of common RNG functions (sample, rnorm,
    runif, rbinom, etc.) and set.seed().

False-positive mitigation:
  - Files that import random modules purely for type hints or re-export
    may trigger this.  That's acceptable: a spurious reminder is cheap,
    a missing seed in real code is not.
  - sklearn usage counts as "randomness" because most estimators use
    internal RNG.  The hook accepts `random_state=` keyword arguments
    as proof of seed control.

Exit code: always 0 (informational, never blocks).
"""

import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import read_clean_write
from hook_log import log_hook

log_hook("check_random_seeds")


def _resolve_file_path() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool_response = data.get("tool_response")
    if not isinstance(tool_response, dict):
        tool_response = {}
    return tool_response.get("filePath") or tool_input.get("file_path", "")


file_path = _resolve_file_path()
if not file_path:
    sys.exit(0)

p = Path(file_path)

# -- Only check code files ----------------------------------------------------
if p.suffix not in (".py", ".R", ".r"):
    sys.exit(0)

# Skip test files (seeds in tests are optional -- fixtures handle repro)
# and ~/.claude/ config scripts.
if p.name.startswith("test_") or ".claude" in p.parts:
    sys.exit(0)

# Modules whose import means "this file uses randomness".
# Includes both top-level packages (numpy, torch) and specific
# submodules (numpy.random, scipy.stats) so that both
# `import numpy as np` and `from numpy.random import default_rng`
# are caught.
RANDOM_MODULES = frozenset(
    {
        "random",
        "numpy",
        "numpy.random",
        "torch",
        "tensorflow",
        "scipy.stats",
        "sklearn",
    }
)

# Regex fallback for seed-setting patterns that the AST walk might miss
SEED_PATTERNS = [
    r"random\.seed\s*\(",
    r"np\.random\.seed\s*\(",
    r"numpy\.random\.seed\s*\(",
    r"torch\.manual_seed\s*\(",
    r"torch\.cuda\.manual_seed(?:_all)?\s*\(",
    r"tf\.random\.set_seed\s*\(",
    r"tensorflow\.random\.set_seed\s*\(",
    r"PYTHONHASHSEED",
    r"random_state\s*=\s*\d+",  # sklearn convention
    r"RandomState\s*\(",
    r"default_rng\s*\(\s*\d+",  # numpy new-style RNG with explicit seed
    r"Generator\s*\(\s*\w+\s*\(\s*\d+",  # numpy Generator(PCG64(42))
]

# Functions that produce random output in base R and common packages
R_RANDOM_FUNCS = [
    r"\bsample\s*\(",
    r"\brnorm\s*\(",
    r"\brunif\s*\(",
    r"\brbinom\s*\(",
    r"\brpois\s*\(",
    r"\brgamma\s*\(",
    r"\brexp\s*\(",
    r"\brcauchy\s*\(",
    r"\brmultinom\s*\(",
    r"\brandom[Ff]orest\s*\(",
    r"\btrain\s*\(",  # caret::train uses internal RNG
    r"\bbootstrap\s*\(",
]


def _regex_has_seed(content: str) -> bool:
    """Check for seed-setting patterns using regex on raw content.

    This is the legacy detection method. It cannot distinguish real code from
    comments or strings, so it produces false negatives (seed in a comment
    suppresses the warning).  Used only as a fallback when AST parsing fails.
    """
    return any(re.search(pat, content) for pat in SEED_PATTERNS)


def _regex_uses_randomness(content: str) -> bool:
    """Check for randomness-related imports using regex on raw content.

    Fallback for when AST parsing fails.
    """
    import_pats = [r"^\s*(?:import|from)\s+" + re.escape(mod) for mod in RANDOM_MODULES]
    return any(re.search(pat, content, re.MULTILINE) for pat in import_pats)


def _ast_has_seed(tree: ast.AST) -> bool:
    """Walk the AST to detect seed-setting calls and patterns.

    Covers all the patterns that the regex fallback detects, but only matches
    actual executable code — comments and string literals are excluded by
    virtue of not appearing as code nodes in the AST.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # -- Method calls: .seed(), .manual_seed(), .set_seed(), etc. --
        if isinstance(func, ast.Attribute) and func.attr in (
            "seed",
            "manual_seed",
            "manual_seed_all",
            "set_seed",
        ):
            return True

        # -- Bare seed() call --
        if isinstance(func, ast.Name) and func.id == "seed":
            return True

        # -- default_rng(<int>) and RandomState(<int>) --
        if isinstance(func, ast.Name) and func.id in ("default_rng", "RandomState"):
            if node.args:  # called with at least one positional arg
                return True

        # -- Attribute form: np.random.default_rng(42), np.random.RandomState(42) --
        if isinstance(func, ast.Attribute) and func.attr in (
            "default_rng",
            "RandomState",
        ):
            if node.args:
                return True

        # -- random_state=<int> keyword argument (sklearn convention) --
        for kw in node.keywords:
            if kw.arg == "random_state" and isinstance(kw.value, ast.Constant):
                return True

    # -- PYTHONHASHSEED: look for the string literal used as a dict key or
    #    in subscript access (e.g. os.environ["PYTHONHASHSEED"] = "42") --
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "PYTHONHASHSEED":
            return True

    return False


def analyze_python_seeds(content: str, lines: list[str]) -> list[tuple[int, str]]:
    """Analyze Python source for randomness usage without an explicit seed.

    Receives clean file content (no stale HOOK comments) from read_clean_write.
    Returns a single finding at the first import line if randomness is used
    without seeding, or an empty list otherwise.

    Uses AST-based detection so that seed calls in comments or string literals
    are correctly ignored.  Falls back to regex when the file cannot be parsed
    (e.g. syntax errors), preserving the legacy behavior for non-Python files
    that happen to have a .py extension.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Fallback: regex-only detection (cannot distinguish code from
        # comments/strings, but better than silently skipping the file).
        if _regex_uses_randomness(content) and not _regex_has_seed(content):
            return [
                (
                    1,
                    "random module used without seed -- add random.seed() for reproducibility",
                )
            ]
        return []

    uses_randomness = False
    first_import_lineno: int | None = None

    for node in ast.walk(tree):
        # -- Detect randomness imports --
        if isinstance(node, ast.Import):
            for alias in node.names:
                for mod in RANDOM_MODULES:
                    if alias.name == mod or alias.name.startswith(mod + "."):
                        uses_randomness = True
                        if first_import_lineno is None:
                            first_import_lineno = node.lineno

        elif isinstance(node, ast.ImportFrom) and node.module:
            for mod in RANDOM_MODULES:
                if node.module == mod or node.module.startswith(mod + "."):
                    uses_randomness = True
                    if first_import_lineno is None:
                        first_import_lineno = node.lineno

    has_seed = _ast_has_seed(tree)

    if uses_randomness and not has_seed and first_import_lineno is not None:
        return [
            (
                first_import_lineno,
                "random module used without seed -- add random.seed() for reproducibility",
            )
        ]

    return []


def analyze_r_seeds(content: str, lines: list[str]) -> list[tuple[int, str]]:
    """Analyze R source for randomness usage without set.seed().

    Receives clean file content (no stale HOOK comments) from read_clean_write.
    Returns a finding at line 1 if randomness is detected without set.seed(),
    since regex detection does not give specific line numbers.
    """
    uses_randomness = any(re.search(pat, content) for pat in R_RANDOM_FUNCS)
    has_seed = bool(re.search(r"\bset\.seed\s*\(", content))

    if uses_randomness and not has_seed:
        return [
            (
                1,
                "randomness detected without set.seed() -- add set.seed() for reproducibility",
            )
        ]

    return []


if p.suffix == ".py":
    read_clean_write(str(p), "SEED", analyze_python_seeds)
elif p.suffix.lower() == ".r":
    read_clean_write(str(p), "SEED", analyze_r_seeds)
