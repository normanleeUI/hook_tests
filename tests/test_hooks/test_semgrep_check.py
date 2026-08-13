"""Tests for the Semgrep parser and its wiring into batch_checks.sh.

The old per-file semgrep_check.sh PostToolUse wrapper was removed 2026-07-17.
Semgrep now runs via the Stop hook batch_checks.sh, which shells out to
inject_tool_findings.py --batch SEMGREP. These tests cover the surviving live
components: the JSON-output parser and the SEMGREP batch wiring.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR

sys.path.insert(0, str(HOOKS_DIR))

# The claude-config repo (source of truth for the vendored semgrep ruleset).
CLAUDE_CONFIG = Path.home() / "projects" / "shared_resources" / "claude-config"
SEMGREP_RULES_DIR = CLAUDE_CONFIG / "semgrep_rules"

PENDING_REASON = "pending staging/semgrep-offline.patch apply"


def _offline_hook_path() -> Path | None:
    """Locate the inject_tool_findings.py that carries the offline semgrep
    config. Prefer the deployed hooks/ copy; fall back to staging/hooks/
    (hooks/ in claude-config is sandbox-read-only, so the patch lands in
    staging/ first)."""
    candidates = [
        HOOKS_DIR / "inject_tool_findings.py",
        CLAUDE_CONFIG / "hooks" / "inject_tool_findings.py",
        CLAUDE_CONFIG / "staging" / "hooks" / "inject_tool_findings.py",
    ]
    for path in candidates:
        if path.exists() and "--metrics=off" in path.read_text():
            return path
    return None


def _load_module(path: Path):
    """Import a hook file under a private name so it can't collide with the
    live copy already importable via sys.path."""
    spec = importlib.util.spec_from_file_location("inject_tool_findings_offline", path)
# HOOK:PYRIGHT: [automated guardrail] Argument of type "ModuleSpec | None" cannot be assigned to parameter "spec" of type "ModuleSpec" in function "module_from_spec" -- fix or explicitly acknowledge before continuing; re-inserted until resolved
    mod = importlib.util.module_from_spec(spec)
# HOOK:PYRIGHT: [automated guardrail] "exec_module" is not a known attribute of "None" (reportOptionalMemberAccess) -- fix or explicitly acknowledge before continuing; re-inserted until resolved
# HOOK:PYRIGHT: [automated guardrail] "loader" is not a known attribute of "None" (reportOptionalMemberAccess) -- fix or explicitly acknowledge before continuing; re-inserted until resolved
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def offline_mod():
    path = _offline_hook_path()
    if path is None:
        pytest.xfail(PENDING_REASON)
    return _load_module(path)


# ── Parser unit tests ──────────────────────────────────────────────────────


class TestParseSemgrep:
    """Unit tests for _parse_semgrep in inject_tool_findings.py."""

    @pytest.fixture(autouse=True)
    def _import_parser(self):
# HOOK:PYRIGHT: [automated guardrail] Import "inject_tool_findings" could not be resolved (reportMissingImports) -- fix or explicitly acknowledge before continuing; re-inserted until resolved
        from inject_tool_findings import _parse_semgrep

        self.parse = _parse_semgrep

    def test_single_finding(self):
        """TEST-SEMGREP-P01: parse one finding from JSON output."""
        output = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.audit.eval-detected.eval-detected",
                        "start": {"line": 4, "col": 1},
                        "end": {"line": 4, "col": 20},
                        "extra": {
                            "message": "Detected the use of eval(). eval() can be dangerous.",
                        },
                    }
                ]
            }
        )
        findings = self.parse(output, "/tmp/fake.py")
        assert len(findings) == 1
        assert findings[0][0] == 4
        assert "eval-detected" in findings[0][1]
        assert "eval()" in findings[0][1]

    def test_multiple_findings(self):
        """TEST-SEMGREP-P02: parse multiple findings from JSON output."""
        output = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.audit.eval-detected",
                        "start": {"line": 4, "col": 1},
                        "end": {"line": 4, "col": 20},
                        "extra": {"message": "eval is dangerous"},
                    },
                    {
                        "check_id": "python.lang.security.deserialization.pickle",
                        "start": {"line": 7, "col": 1},
                        "end": {"line": 7, "col": 30},
                        "extra": {"message": "Avoid using pickle"},
                    },
                    {
                        "check_id": "python.lang.security.deserialization.pyyaml",
                        "start": {"line": 10, "col": 1},
                        "end": {"line": 10, "col": 25},
                        "extra": {"message": "Avoid yaml.load"},
                    },
                ]
            }
        )
        findings = self.parse(output, "/tmp/fake.py")
        assert len(findings) == 3
        assert findings[0][0] == 4
        assert findings[1][0] == 7
        assert findings[2][0] == 10

    def test_empty_results(self):
        """TEST-SEMGREP-N01: empty results array returns empty list."""
        output = json.dumps({"results": []})
        findings = self.parse(output, "/tmp/fake.py")
        assert findings == []

    def test_malformed_json(self):
        """TEST-SEMGREP-E01: garbage input returns empty list without crashing."""
        findings = self.parse("not valid json {{{", "/tmp/fake.py")
        assert findings == []

    def test_missing_results_key(self):
        """TEST-SEMGREP-E02: valid JSON missing 'results' returns empty list."""
        output = json.dumps({"version": "1.0", "errors": []})
        findings = self.parse(output, "/tmp/fake.py")
        assert findings == []

    def test_finding_with_long_check_id_truncated_in_message(self):
        """Parser should include enough of check_id to be useful."""
        output = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.audit.eval-detected.eval-detected",
                        "start": {"line": 1, "col": 1},
                        "end": {"line": 1, "col": 10},
                        "extra": {"message": "Detected eval()"},
                    }
                ]
            }
        )
        findings = self.parse(output, "/tmp/fake.py")
        assert len(findings) == 1
        _line, msg = findings[0]
        assert "eval-detected" in msg


# ── Wiring validation ──────────────────────────────────────────────────────


class TestSemgrepWiring:
    """Verify Semgrep is wired into the batch Stop hook, not a standalone script."""

    def test_called_by_batch_checks(self):
        """TEST-SEMGREP-W01: semgrep runs from batch_checks.sh (Stop hook)."""
        batch_script = HOOKS_DIR / "batch_checks.sh"
        assert batch_script.exists(), "batch_checks.sh not found"
        content = batch_script.read_text()
        assert "SEMGREP" in content, "batch_checks.sh does not reference SEMGREP"

    def test_inject_tool_findings_has_semgrep_config(self):
        """TEST-SEMGREP-W03: TOOL_CONFIGS has a SEMGREP entry."""
# HOOK:PYRIGHT: [automated guardrail] Import "inject_tool_findings" could not be resolved (reportMissingImports) -- fix or explicitly acknowledge before continuing; re-inserted until resolved
        from inject_tool_findings import TOOL_CONFIGS

        assert "SEMGREP" in TOOL_CONFIGS
        config = TOOL_CONFIGS["SEMGREP"]
        assert "bin" in config
        assert "args" in config
        assert "parser" in config
        assert "env" in config


# ── Offline (pinned local ruleset) invocation ──────────────────────────────


class TestSemgrepOfflineConfig:
    """The SEMGREP entry must use the vendored local ruleset, never `--config
    auto` (which phones home and drifts). Runs against whichever copy carries
    the patch — hooks/ once deployed, staging/hooks/ before that."""

    def test_no_config_auto(self, offline_mod):
        args = offline_mod.TOOL_CONFIGS["SEMGREP"]["args"]
        assert "auto" not in args

    def test_metrics_off(self, offline_mod):
        assert "--metrics=off" in offline_mod.TOOL_CONFIGS["SEMGREP"]["args"]

    def test_config_points_at_vendored_rules(self, offline_mod):
        args = offline_mod.TOOL_CONFIGS["SEMGREP"]["args"]
        assert "--config" in args
        config_value = str(args[args.index("--config") + 1])
        assert config_value.endswith("semgrep_rules/rules") or config_value.endswith(
            "semgrep_rules\\rules"
        ), f"--config should target semgrep_rules/rules, got {config_value}"

    def test_core_flags_survive(self, offline_mod):
        args = offline_mod.TOOL_CONFIGS["SEMGREP"]["args"]
        assert "--disable-version-check" in args
        assert "--json" in args
        assert args[0] == "scan"


class TestMissingRulesGuard:
    """A missing/empty rules dir must fail LOUDLY (stderr), not silently
    return zero findings — the 'silent gap' bug class from docs/LEDGER.md."""

    @pytest.fixture
    def hook_copy(self, tmp_path):
        """Copy the patched hook (plus its hook_inject dependency) into a tmp
        tree with NO semgrep_rules/, so the __file__-relative rules path is
        missing."""
        src = _offline_hook_path()
        if src is None:
            pytest.xfail(PENDING_REASON)
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        shutil.copy(src, hooks_dir / "inject_tool_findings.py")
        # hook_inject.py may not sit next to a staging/ copy; fall back to live.
        dep = src.parent / "hook_inject.py"
        if not dep.exists():
            dep = HOOKS_DIR / "hook_inject.py"
        shutil.copy(dep, hooks_dir / "hook_inject.py")
        return tmp_path

    def _run_batch(self, root: Path, target: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(root / "hooks" / "inject_tool_findings.py"),
                "--batch",
                "SEMGREP",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_missing_rules_dir_is_loud(self, hook_copy, tmp_path):
        target = tmp_path / "victim.py"
        target.write_text("x = 1\n")
        result = self._run_batch(hook_copy, target)
        assert result.stderr.strip(), (
            "missing semgrep_rules/rules dir produced no stderr — "
            "silent empty-findings return"
        )
        assert "rules" in result.stderr.lower()

    def test_empty_rules_dir_is_loud(self, hook_copy, tmp_path):
        (hook_copy / "semgrep_rules" / "rules").mkdir(parents=True)
        target = tmp_path / "victim.py"
        target.write_text("x = 1\n")
        result = self._run_batch(hook_copy, target)
        assert result.stderr.strip(), (
            "empty semgrep_rules/rules dir produced no stderr — "
            "silent empty-findings return"
        )
        assert "rules" in result.stderr.lower()


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
