"""Tests for the Semgrep parser and its wiring into batch_checks.sh.

The old per-file semgrep_check.sh PostToolUse wrapper was removed 2026-07-17.
Semgrep now runs via the Stop hook batch_checks.sh, which shells out to
inject_tool_findings.py --batch SEMGREP. These tests cover the surviving live
components: the JSON-output parser and the SEMGREP batch wiring.
"""

from __future__ import annotations

import json
import sys

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR

sys.path.insert(0, str(HOOKS_DIR))


# ── Parser unit tests ──────────────────────────────────────────────────────


class TestParseSemgrep:
    """Unit tests for _parse_semgrep in inject_tool_findings.py."""

    @pytest.fixture(autouse=True)
    def _import_parser(self):
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
        from inject_tool_findings import TOOL_CONFIGS

        assert "SEMGREP" in TOOL_CONFIGS
        config = TOOL_CONFIGS["SEMGREP"]
        assert "bin" in config
        assert "args" in config
        assert "parser" in config
        assert "env" in config
