"""Automated config validation: verify all 19 hooks are correctly wired.

Parses ~/.claude/settings.json and checks that every canonical hook is
present with the correct event type, matcher, and if-condition. Also
detects orphaned scripts on disk, deprecated hooks that were re-added,
and invalid matcher/if syntax.
"""

import json
import os
import re
from pathlib import Path

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

VALID_TOOL_NAMES = {
    "Read",
    "Write",
    "Edit",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Agent",
    "NotebookEdit",
}
VALID_MCP_PATTERN = re.compile(r"^mcp__[a-zA-Z0-9_.*]+$")
IF_PATTERN = re.compile(r"^(\w+\([^)]*\))(\|\w+\([^)]*\))*$")

CANONICAL_HOOKS: dict[str, dict[str, str | None]] = {
    # SessionStart
    "project_health_check.py": {
        "event": "SessionStart",
        "interpreter": "python3",
        "matcher": None,
        "if_condition": None,
    },
    "git_pull_on_start.sh": {
        "event": "SessionStart",
        "interpreter": "bash",
        "matcher": None,
        "if_condition": None,
    },
    "check_dep_freshness.sh": {
        "event": "SessionStart",
        "interpreter": "bash",
        "matcher": None,
        "if_condition": None,
    },
    # PreToolUse
    "block_read_env.py": {
        "event": "PreToolUse",
        "interpreter": "python3",
        "matcher": "Read",
        "if_condition": None,
    },
    "block_bare_pip.py": {
        "event": "PreToolUse",
        "interpreter": "python3",
        "matcher": "Bash",
        "if_condition": None,
    },
    "scan_secrets_on_commit.py": {
        "event": "PreToolUse",
        "interpreter": "python3",
        "matcher": "Bash",
        "if_condition": "Bash(git commit*)",
    },
    "block_git_add_env.py": {
        "event": "PreToolUse",
        "interpreter": "python3",
        "matcher": "Bash",
        "if_condition": "Bash(git add*)",
    },
    # PostToolUse -- Edit|Write group
    "block_glob_deny_rules.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "ruff_format.sh": {
        "event": "PostToolUse",
        "interpreter": "bash",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "pyright_check.sh": {
        "event": "PostToolUse",
        "interpreter": "bash",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "check_docstrings.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "check_dependency_pins.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "check_random_seeds.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "block_suppressions.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    "bandit_check.sh": {
        "event": "PostToolUse",
        "interpreter": "bash",
        "matcher": "Edit|Write",
        "if_condition": None,
    },
    # PostToolUse -- Write group
    "check_test_pair.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Write",
        "if_condition": None,
    },
    # PostToolUse -- Bash group
    "pip_audit_check.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "Bash",
        "if_condition": "Bash(*uv add*)|Bash(*uv sync*)|Bash(*uv pip install*)",
    },
    # PostToolUse -- WebFetch|mcp__.*
    "scan_prompt_injection.py": {
        "event": "PostToolUse",
        "interpreter": "python3",
        "matcher": "WebFetch|mcp__.*",
        "if_condition": None,
    },
    # Stop
    "ruff_lint.sh": {
        "event": "Stop",
        "interpreter": "bash",
        "matcher": None,
        "if_condition": None,
    },
}

DEPRECATED_HOOKS: dict[str, str] = {
    "mypy_check.sh": "Unwired 2026-06-13: pyright preferred",
    "log_new_dependency.py": "Unwired 2026-06-13: redundant with pip_audit_check",
    "r_style_check.sh": "Unwired 2026-06-13: R not actively used",
    "plan_runner_write_gate.py": "Never wired: plan runner concept abandoned",
}


def load_settings() -> dict:
    """Load and parse ~/.claude/settings.json."""
    return json.loads(SETTINGS_PATH.read_text())


def extract_wired_scripts(settings: dict) -> list[dict[str, str | None]]:
    """Extract all hook entries from settings.json with event/matcher/if metadata."""
    results: list[dict[str, str | None]] = []
    for event_type, groups in settings.get("hooks", {}).items():
        for group in groups:
            matcher = group.get("matcher")
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                parts = cmd.split()
                results.append(
                    {
                        "event": event_type,
                        "matcher": matcher,
                        "if_condition": hook.get("if"),
                        "command": cmd,
                        "script_name": Path(parts[-1]).name if parts else None,
                        "interpreter": parts[0] if parts else None,
                    }
                )
    return results


class TestScriptExistence:
    """Verify that every script referenced in settings.json exists on disk."""

    def test_all_wired_scripts_exist(self):
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            script_path = Path(entry["command"].split()[-1])
            assert script_path.exists(), (
                f"Wired script missing: {script_path} "
                f"(event={entry['event']}, matcher={entry['matcher']})"
            )

    def test_all_wired_scripts_readable(self):
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            script_path = Path(entry["command"].split()[-1])
            assert os.access(script_path, os.R_OK), f"Not readable: {script_path}"


class TestCanonicalList:
    """Verify settings.json matches the canonical hook registry exactly."""

    def test_all_canonical_hooks_wired(self):
        """Every hook in CANONICAL_HOOKS appears in settings.json."""
        settings = load_settings()
        wired = {e["script_name"] for e in extract_wired_scripts(settings)}
        for name in CANONICAL_HOOKS:
            assert name in wired, f"Canonical hook not wired: {name}"

    def test_wired_hooks_match_canonical_config(self):
        """Each wired hook has the expected event type, matcher, and if condition."""
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            name = entry["script_name"]
            if name not in CANONICAL_HOOKS:
                continue
            expected = CANONICAL_HOOKS[name]
            assert entry["event"] == expected["event"], (
                f"{name}: event {entry['event']} != expected {expected['event']}"
            )
            assert entry["matcher"] == expected["matcher"], (
                f"{name}: matcher {entry['matcher']} != expected {expected['matcher']}"
            )
            assert entry["if_condition"] == expected["if_condition"], (
                f"{name}: if {entry['if_condition']} != expected {expected['if_condition']}"
            )
            assert entry["interpreter"] == expected["interpreter"], (
                f"{name}: interpreter {entry['interpreter']} != expected {expected['interpreter']}"
            )

    def test_no_unknown_wired_hooks(self):
        """Every wired hook is in CANONICAL_HOOKS (catches unexpected additions)."""
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            assert entry["script_name"] in CANONICAL_HOOKS, (
                f"Unknown hook wired: {entry['script_name']}"
            )

    def test_deprecated_hooks_not_wired(self):
        """Deprecated hooks must not appear in settings.json."""
        settings = load_settings()
        wired = {e["script_name"] for e in extract_wired_scripts(settings)}
        for name in DEPRECATED_HOOKS:
            assert name not in wired, (
                f"Deprecated hook still wired: {name} -- {DEPRECATED_HOOKS[name]}"
            )


class TestOrphanDetection:
    """Detect scripts on disk that are not in the canonical or deprecated lists."""

    def test_no_unknown_scripts_on_disk(self):
        """Every script in ~/.claude/hooks/ is canonical or deprecated."""
        known = set(CANONICAL_HOOKS) | set(DEPRECATED_HOOKS)
        on_disk = {
            f.name
            for f in HOOKS_DIR.iterdir()
            if f.suffix in (".py", ".sh") and not f.name.startswith(".")
        }
        unknown = on_disk - known
        assert not unknown, f"Unknown scripts on disk: {unknown}"


class TestMatcherValidity:
    """Validate matcher and if-condition syntax in settings.json."""

    def test_matchers_reference_valid_tools(self):
        """All matcher tool names are recognized Claude Code tools or MCP patterns."""
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            matcher = entry["matcher"]
            if matcher is None:
                continue
            for tool in matcher.split("|"):
                assert tool in VALID_TOOL_NAMES or VALID_MCP_PATTERN.match(tool), (
                    f"Unknown tool in matcher: '{tool}' (hook: {entry['script_name']})"
                )

    def test_if_conditions_syntactically_valid(self):
        """All if: conditions follow the ToolName(glob*) pattern."""
        settings = load_settings()
        for entry in extract_wired_scripts(settings):
            cond = entry["if_condition"]
            if cond is None:
                continue
            assert IF_PATTERN.match(cond), (
                f"Invalid if: pattern: '{cond}' (hook: {entry['script_name']})"
            )

    def test_no_duplicate_hooks_in_same_group(self):
        """No script appears twice in the same event+matcher group."""
        settings = load_settings()
        for event_type, groups in settings.get("hooks", {}).items():
            for group in groups:
                matcher = group.get("matcher")
                scripts = [
                    Path(h["command"].split()[-1]).name for h in group.get("hooks", [])
                ]
                dupes = [s for s in scripts if scripts.count(s) > 1]
                assert not dupes, (
                    f"Duplicate hooks in {event_type}/{matcher}: {set(dupes)}"
                )


class TestIfPatternRegex:
    """Unit tests for the IF_PATTERN regex used to validate if: conditions."""

    @pytest.mark.parametrize(
        "pattern",
        [
            "Bash(git commit*)",
            "Bash(git add*)",
            "Bash(*uv add*)|Bash(*uv sync*)|Bash(*uv pip install*)",
            "Read(.env*)",
            "Write(*.py)",
        ],
    )
    def test_valid_if_patterns_match(self, pattern: str) -> None:
        assert IF_PATTERN.match(pattern), f"Should match: {pattern!r}"

    @pytest.mark.parametrize(
        "pattern",
        [
            "git commit",
            "git commit*",
            "Bash",
            "(git commit*)",
            "Bash git commit*",
            "",
        ],
    )
    def test_invalid_if_patterns_rejected(self, pattern: str) -> None:
        assert not IF_PATTERN.match(pattern), f"Should NOT match: {pattern!r}"
