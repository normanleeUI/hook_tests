"""SessionStart hook: check project health and inject a reminder into the
model's context if anything from CLAUDE.md's required infrastructure is
missing.

Output format (stdout, JSON):
  - systemMessage:  brief summary shown to the user in the UI
  - additionalContext: detailed instruction injected into the model's context
    so it acts proactively, not just if the user asks

Silently exits 0 (no output) when:
  - We're inside the ~/.claude config dir itself (not a real project), or
  - The directory looks like a home/generic folder (no Python/R files, no
    pyproject.toml, no .git — i.e. nothing that suggests active dev work), or
  - All checked items are present AND no CONTRIBUTING.md found.

Surfaces output when:
  - Any checked items are missing (gaps to fix), and/or
  - A CONTRIBUTING.md is found (informational — "read this before coding").
    Checked in: ./CONTRIBUTING.md, docs/CONTRIBUTING.md, .github/CONTRIBUTING.md.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("project_health_check")


def is_project_dir(cwd: Path, home: Path) -> bool:
    """Return True if cwd looks like an active project, not a home/system dir.

    Args:
        cwd:  current working directory
        home: user home directory

    Returns:
        bool: True if this looks like a project directory worth checking
    """
    # Always skip ~/.claude itself
    if cwd == home / ".claude" or str(cwd).startswith(str(home / ".claude")):
        return False
    # Skip the home directory itself — too noisy
    if cwd == home:
        return False
    # If there's a .git repo, definitely a project
    if (cwd / ".git").exists():
        return True
    # If there's a Python or R project file, definitely a project
    for marker in ("pyproject.toml", "setup.py", "setup.cfg", "renv.lock"):
        if (cwd / marker).exists():
            return True
    # If any .py or .R files live directly in the directory, treat as project
    for ext in ("*.py", "*.R", "*.Rmd", "*.qmd"):
        if any(cwd.glob(ext)):
            return True
    # If it has a src/ or R/ sub-directory (common project layouts), include it
    for subdir in ("src", "R", "notebooks"):
        if (cwd / subdir).is_dir():
            return True
    # Otherwise: new/empty directory — still worth checking so a fresh project
    # gets guided setup from the very first message. Only skip home + .claude
    # (already handled above).
    return True


def check_git_state(cwd: Path) -> list[str]:
    """Check for stale stashes and unpushed commits.

    Returns a list of warning strings (empty if nothing to report).
    Only runs in git repos — returns [] otherwise.
    """
    if not (cwd / ".git").exists():
        return []

    warnings: list[str] = []

    try:
        stash_result = subprocess.run(
            ["git", "stash", "list"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        stash_lines = [ln for ln in stash_result.stdout.strip().splitlines() if ln]
        if stash_lines:
            summary = "\n".join(f"    {ln}" for ln in stash_lines)
            warnings.append(
                f"Git stash has {len(stash_lines)} entry(ies) — "
                f"these may contain uncommitted work worth triaging:\n{summary}"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        unpushed_result = subprocess.run(
            ["git", "log", "--oneline", "@{upstream}..HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        unpushed_lines = [
            ln for ln in unpushed_result.stdout.strip().splitlines() if ln
        ]
        if unpushed_result.returncode == 0 and unpushed_lines:
            warnings.append(
                f"{len(unpushed_lines)} unpushed commit(s) on current branch."
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return warnings


def detect_category(cwd: Path) -> str:
    """Detect project category from directory path.

    Returns "collab", "oneOff", or "solo" based on whether the project
    lives under a collab/ or oneOff/ ancestor directory.
    """
    parts = cwd.parts
    if "collab" in parts:
        return "collab"
    if "oneOff" in parts:
        return "oneOff"
    return "solo"


def main() -> None:
    cwd = Path.cwd()
    home = Path.home()

    if not is_project_dir(cwd, home):
        raise SystemExit(0)

    category = detect_category(cwd)

    # --- checks ---
    # Each entry: human-readable label -> bool (True = present / OK)

    # Check for CONTRIBUTING.md in common locations. This is tracked
    # separately from the missing-items checks because finding one is
    # informational ("read this first"), not a gap to fix.
    contributing_paths = [
        cwd / "CONTRIBUTING.md",
        cwd / "docs" / "CONTRIBUTING.md",
        cwd / ".github" / "CONTRIBUTING.md",
    ]
    contributing_found = next((p for p in contributing_paths if p.exists()), None)

    # Light checks for collab/oneOff: just git + .gitignore.
    # Full checks only apply to solo projects.
    if category in ("collab", "oneOff"):
        checks: dict[str, bool] = {
            "git repository (.git/)": (cwd / ".git").exists(),
            ".gitignore": (cwd / ".gitignore").exists(),
        }
    else:
        checks = {
            "git repository (.git/)": (cwd / ".git").exists(),
            "virtual environment (.venv/, venv/, or renv/)": (
                (cwd / ".venv").exists()
                or (cwd / "venv").exists()
                or (cwd / "renv").exists()
            ),
            "dependency file (pyproject.toml or requirements.txt)": (
                (cwd / "pyproject.toml").exists()
                or (cwd / "requirements.txt").exists()
                or (cwd / "setup.py").exists()
            ),
            ".gitignore": (cwd / ".gitignore").exists(),
            "README.md": (cwd / "README.md").exists(),
            "project-level hooks (.claude/settings.json)": (
                (cwd / ".claude" / "settings.json").exists()
            ),
            "CI/CD workflow (.github/workflows/)": (
                (cwd / ".github" / "workflows").exists()
            ),
        }

    missing = [label for label, present in checks.items() if not present]
    git_warnings = check_git_state(cwd)

    if not missing and not contributing_found and not git_warnings:
        raise SystemExit(0)  # All good — stay silent

    # Build the context sections
    sections: list[str] = []
    sections.append(f"Directory: {cwd}")

    # CONTRIBUTING.md notice (informational, not a gap to fix)
    if contributing_found:
        rel_path = contributing_found.relative_to(cwd)
        sections.append(
            f"CONTRIBUTING.md found: {rel_path}\n"
            "  This is a collaborative project with contribution guidelines.\n"
            "  You MUST read this file before writing any code, creating branches,\n"
            "  or making commits. It may specify branch naming conventions, commit\n"
            "  message formats, PR processes, and code quality requirements that\n"
            "  override your default behavior and personal preferences.\n"
            "  Per the global CLAUDE.md 'Joining a collaborative project' section:\n"
            "  project conventions take priority over personal style."
        )

    if missing:
        missing_bullet = "\n".join(f"  - {m}" for m in missing)
        count = len(missing)
        noun = "item" if count == 1 else "items"
        sections.append(f"Missing ({count} {noun}):\n{missing_bullet}")
    else:
        count = 0
        noun = "items"

    if git_warnings:
        git_bullet = "\n".join(f"  - {w}" for w in git_warnings)
        sections.append(f"Git state warnings:\n{git_bullet}")

    # System message — compact summary of everything found
    parts: list[str] = []
    if contributing_found:
        parts.append("CONTRIBUTING.md found")
    if missing:
        parts.append(f"{count} setup {noun} missing")
    if git_warnings:
        parts.append(f"{len(git_warnings)} git warning(s)")
    category_label = f" [{category}]" if category != "solo" else ""
    sys_msg = (
        f"Project health check ({cwd.name}{category_label}): " + " + ".join(parts) + "."
    )

    sections_text = "\n\n".join(sections)

    if category in ("collab", "oneOff"):
        instructions = """
INSTRUCTIONS FOR YOU (the model):
This is a {category} project — LIGHT health-check mode applies.
Only git + .gitignore are checked. Do NOT suggest adding venvs, dependency
files, READMEs, project hooks, or CI/CD unless the user specifically asks.
Match existing project conventions; don't impose solo-project standards.
  1. Mention any missing items briefly near the start of the conversation.
  2. For git state warnings (stashes, unpushed commits): mention them early.
  3. If a CONTRIBUTING.md was found, read it before writing any code.
""".format(category=category)
    else:
        instructions = """
INSTRUCTIONS FOR YOU (the model):
These gaps were detected automatically at session start. You should:
  1. Mention the missing items briefly and naturally to the user near the
     start of the conversation — don't just silently ignore this.
  2. Offer to set them up before (or right after) tackling the main request.
  3. If the user's first message is urgent, complete that first, then circle
     back: "By the way, I noticed a few project setup items are missing..."
  4. For git state warnings (stashes, unpushed commits): mention them early.
     Stale stashes can contain lost work. Unpushed commits risk data loss.
     Offer to help triage stashes or push commits.

Do NOT silently skip this. The user has explicitly asked for best practices
to be enforced — they rely on you to notice gaps they might not know to
look for themselves.

WHAT EACH MISSING ITEM SHOULD LOOK LIKE:

git repository:
  `git init && git add . && git commit -m "Initial commit"`

virtual environment:
  `uv venv`  (then use `uv add <pkg>` to install dependencies)

dependency file:
  `uv init` creates pyproject.toml; or create requirements.txt manually.
  Always pin versions.

.gitignore — minimum contents:
  .env
  .venv/
  venv/
  __pycache__/
  *.pyc
  *.pyo
  .DS_Store
  dist/
  *.egg-info/
  .pytest_cache/
  .mypy_cache/
  .ruff_cache/

README.md — minimum sections:
  # Project name
  What it does, how to install it, how to run it, directory structure,
  inputs/outputs. See CLAUDE.md for details.

project-level hooks (.claude/settings.json):
  This project has no .claude/settings.json yet. Before implementing anything,
  BRAINSTORM which hooks would enforce the best practices in the global
  ~/.claude/CLAUDE.md for this specific project. Think through:
    - What languages/frameworks are in use? (Python → ruff, mypy, pytest;
      R → lintr, testthat; web → prettier, eslint)
    - What is the project type? (data pipeline, RAG system, web app, etc.)
    - Which CLAUDE.md sections are most at risk of being skipped under
      pressure? (testing, observability, reproducibility, security)
    - Are there project-specific patterns worth enforcing? (e.g. always log
      input shapes for a data pipeline; always set random seeds for ML)

  Then propose hooks ONE AT A TIME to the user for approval or denial before
  implementing each. Do not batch them all at once — the user needs to
  understand each hook before it runs silently in the background. For each
  hook, explain: (a) what it does, (b) which best practice it enforces, and
  (c) how intrusive it is (blocking vs. warning, slow vs. fast).

  Known useful starting points (still propose these individually):
    - PostToolUse Edit|Write → ruff format + check on .py files
    - PostToolUse Edit|Write → mypy or pyright on .py files
    - Stop → pytest (run tests before declaring a turn complete)
  Reference ~/.claude/hooks/ for script patterns already in place globally.
  Add .claude/ to .gitignore so local settings aren't committed.

CI/CD (.github/workflows/ci.yml):
  Minimal workflow: on push/PR, run `ruff check` and `pytest`.
  Suggest this once the project has at least one test."""

    output = {
        "systemMessage": sys_msg,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"""
=== PROJECT HEALTH CHECK (automated, from ~/.claude/hooks/project_health_check.py) ===

{sections_text}
{instructions}
=== END PROJECT HEALTH CHECK ===
""",
        },
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
