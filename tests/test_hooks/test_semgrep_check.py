"""Tests for the vendored Semgrep ruleset and its staleness check.

Semgrep execution moved to the git pre-commit hook (claude-config
githooks/pre-commit) in 2026-08; the old batch_checks.sh / inject_tool_findings
path is retired, and its parser/offline-config/missing-rules tests now live in
tests/test_hooks/test_precommit_hook.py. What survives here is the ruleset
itself: the vendored rules dir, its refresh stamp, and the session-start
staleness warning.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR

# The claude-config repo (source of truth for the vendored semgrep ruleset).
CLAUDE_CONFIG = Path.home() / "projects" / "shared_resources" / "claude-config"
SEMGREP_RULES_DIR = CLAUDE_CONFIG / "semgrep_rules"

PENDING_REASON = "pending staging/semgrep-offline.patch apply"


# ── Vendored ruleset contents ──────────────────────────────────────────────


class TestVendoredRuleset:
    """semgrep_rules/ in claude-config: rules present, stamp dated, refresh
    script at least parses."""

    def test_rules_dir_has_yaml(self):
        rules = SEMGREP_RULES_DIR / "rules"
        if not rules.is_dir():
            pytest.xfail(PENDING_REASON)
        yamls = list(rules.glob("*.yaml")) + list(rules.glob("*.yml"))
        assert yamls, f"no YAML rule files in {rules}"

    def test_ruleset_stamp_first_line_has_iso_date(self):
        stamp = SEMGREP_RULES_DIR / "RULESET_STAMP"
        if not stamp.exists():
            pytest.xfail(PENDING_REASON)
        first_line = stamp.read_text().splitlines()[0]
        # refresh.sh writes "date: YYYY-MM-DD"; accept a bare ISO date too.
        m = re.match(r"(?:date:\s*)?(\d{4}-\d{2}-\d{2})", first_line)
        assert m, f"RULESET_STAMP first line has no ISO date: {first_line!r}"
        date.fromisoformat(m.group(1))  # raises if not a real date

    def test_refresh_script_parses(self):
        refresh = SEMGREP_RULES_DIR / "refresh.sh"
        if not refresh.exists():
            pytest.xfail(PENDING_REASON)
        result = subprocess.run(
            ["bash", "-n", str(refresh)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"bash -n failed: {result.stderr}"


# ── Staleness warning in the drift-check hook ──────────────────────────────


class TestRulesetStalenessCheck:
    """The session-start drift check must warn when RULESET_STAMP is >90 days
    old. String-assert the staged/deployed hook text (executing the live hook
    is not testable from here)."""

    def _drift_hook_with_stamp_check(self) -> str | None:
        candidates = [
            HOOKS_DIR / "config_drift_check.sh",
            CLAUDE_CONFIG / "hooks" / "config_drift_check.sh",
            CLAUDE_CONFIG / "staging" / "hooks" / "config_drift_check.sh",
        ]
        for path in candidates:
            if path.exists():
                text = path.read_text()
                if "RULESET_STAMP" in text:
                    return text
        return None

    def test_warns_at_90_days(self):
        text = self._drift_hook_with_stamp_check()
        if text is None:
            pytest.xfail(PENDING_REASON)
        assert "90" in text, "staleness threshold (90 days) not in drift hook"
        assert re.search(r"stale|old|refresh|warn", text, re.IGNORECASE), (
            "drift hook mentions RULESET_STAMP but no visible warning text"
        )
