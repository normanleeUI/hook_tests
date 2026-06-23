"""Tests for the semgrep_check hook: shell wrapper gate logic, parser, and integration.

Derived from the hook spec — semgrep_check is a PostToolUse Edit|Write hook that
runs Semgrep on .py files via inject_tool_findings.py's inline injection pattern.

Unlike bandit_check.sh, this hook does NOT skip test files — test code can contain
genuinely security-relevant patterns (real network calls, unsafe deserialization).
"""

from __future__ import annotations

import ast
import json
import os
import stat
import sys
import tempfile

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR, run_bash_hook

sys.path.insert(0, str(HOOKS_DIR))


def edit_payload(file_path: str) -> dict:
    """Build a PostToolUse payload with both jq-extractable path fields."""
    return {
        "tool_input": {"file_path": file_path, "new_string": "x = 1\n"},
        "tool_response": {"filePath": file_path},
    }


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


# ── Shell wrapper gate tests ────────────────────────────────────────────────


@pytest.fixture
def fake_tool_env(tmp_path):
    """Create fake tool stubs that log invocations instead of running real tools."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "tool_invocations.log"

    for tool_name in ("uvx", "semgrep"):
        stub = bin_dir / tool_name
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{tool_name} $@" >> {log_file}\nexit 0\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": os.environ["HOME"]}
    return env, log_file


def _read_log(log_file) -> str:
    if log_file.exists():
        return log_file.read_text()
    return ""


class TestSemgrepCheckShell:
    """Gate logic tests for semgrep_check.sh shell wrapper."""

    def test_fires_on_py_file(self, fake_tool_env):
        """TEST-SEMGREP-SH-P01: runs on .py files."""
        env, log_file = fake_tool_env
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            py_file = os.path.join(td, "app.py")
            with open(py_file, "w") as fh:
                fh.write("import os\n")

            rc, stderr, stdout = run_bash_hook(
                "semgrep_check.sh", edit_payload(py_file), env=env
            )

            assert rc == 0

    def test_does_not_skip_test_files(self, fake_tool_env):
        """TEST-SEMGREP-SH-P02: unlike bandit, semgrep runs on test files."""
        env, log_file = fake_tool_env
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            test_file = os.path.join(td, "test_app.py")
            with open(test_file, "w") as fh:
                fh.write("import subprocess\n")

            rc, stderr, stdout = run_bash_hook(
                "semgrep_check.sh", edit_payload(test_file), env=env
            )

            assert rc == 0

    def test_skips_non_python_file(self, fake_tool_env, tmp_path):
        """TEST-SEMGREP-SH-N01: non-.py files are silently skipped."""
        env, log_file = fake_tool_env
        js_file = tmp_path / "index.js"
        js_file.write_text("const x = 1;\n")

        rc, stderr, stdout = run_bash_hook(
            "semgrep_check.sh", edit_payload(str(js_file)), env=env
        )

        assert rc == 0
        log = _read_log(log_file)
        assert log == "", "semgrep should not run on .js files"

    def test_skips_claude_directory(self, fake_tool_env, tmp_path):
        """TEST-SEMGREP-SH-N02: files under .claude/ are skipped."""
        env, log_file = fake_tool_env
        claude_dir = tmp_path / ".claude" / "hooks"
        claude_dir.mkdir(parents=True)
        hook_file = claude_dir / "some_hook.py"
        hook_file.write_text("pass\n")

        rc, stderr, stdout = run_bash_hook(
            "semgrep_check.sh", edit_payload(str(hook_file)), env=env
        )

        assert rc == 0
        log = _read_log(log_file)
        assert log == "", "semgrep should skip files in .claude/"

    def test_handles_missing_file_path(self, fake_tool_env):
        """TEST-SEMGREP-SH-E01: gracefully handles missing file path in payload."""
        env, log_file = fake_tool_env
        payload = {"tool_input": {}, "tool_response": {}}

        rc, stderr, stdout = run_bash_hook("semgrep_check.sh", payload, env=env)

        assert rc == 0
        log = _read_log(log_file)
        assert log == "", "semgrep should not run with missing file path"


# ── Wiring validation ──────────────────────────────────────────────────────


class TestSemgrepWiring:
    """Verify hook is correctly registered and script exists."""

    def test_registered_in_settings(self):
        """TEST-SEMGREP-W01: hook entry exists in settings.json."""
        settings_path = HOOKS_DIR.parent / "settings.json"
        with open(settings_path) as f:
            settings = json.load(f)

        post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])
        semgrep_entries = []
        for group in post_tool_use:
            if group.get("matcher") == "Edit|Write":
                for hook in group.get("hooks", []):
                    if "semgrep_check" in hook.get("command", ""):
                        semgrep_entries.append(hook)

        assert len(semgrep_entries) == 1, (
            f"Expected exactly 1 semgrep_check entry, found {len(semgrep_entries)}"
        )

    def test_script_exists(self):
        """TEST-SEMGREP-W02: hook script file exists on disk."""
        script = HOOKS_DIR / "semgrep_check.sh"
        assert script.exists(), f"Script not found: {script}"

    def test_inject_tool_findings_has_semgrep_config(self):
        """TEST-SEMGREP-W03: TOOL_CONFIGS has a SEMGREP entry."""
        from inject_tool_findings import TOOL_CONFIGS

        assert "SEMGREP" in TOOL_CONFIGS
        config = TOOL_CONFIGS["SEMGREP"]
        assert "bin" in config
        assert "args" in config
        assert "parser" in config
        assert "env" in config


# ── Integration test (real semgrep) ─────────────────────────────────────────


@pytest.mark.slow
class TestSemgrepIntegration:
    """End-to-end: run real semgrep and verify inline injection."""

    def test_injects_comment_on_eval(self):
        """TEST-SEMGREP-INT01: eval() triggers a HOOK:SEMGREP: comment."""
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            src = os.path.join(td, "vuln.py")
            with open(src, "w") as fh:
                fh.write(
                    "import os\n"
                    'user_input = input("Enter: ")\n'
                    "result = eval(user_input)\n"
                )
            payload = {
                "tool_input": {"file_path": src},
                "tool_response": {"filePath": src},
            }
            run_bash_hook("semgrep_check.sh", payload, timeout=60)
            with open(src) as fh:
                content = fh.read()
            assert "# HOOK:SEMGREP:" in content
            ast.parse(content)

    def test_no_injection_when_clean(self):
        """Clean code produces no HOOK:SEMGREP: comments."""
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            src = os.path.join(td, "clean.py")
            original = "x = 1\ny = x + 2\n"
            with open(src, "w") as fh:
                fh.write(original)
            payload = {
                "tool_input": {"file_path": src},
                "tool_response": {"filePath": src},
            }
            run_bash_hook("semgrep_check.sh", payload, timeout=60)
            with open(src) as fh:
                content = fh.read()
            assert content == original

    def test_stale_comments_cleaned(self):
        """Stale HOOK:SEMGREP: comments are removed on re-run."""
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            src = os.path.join(td, "clean.py")
            with open(src, "w") as fh:
                fh.write("# HOOK:SEMGREP: old stale finding\nx = 1\ny = x + 2\n")
            payload = {
                "tool_input": {"file_path": src},
                "tool_response": {"filePath": src},
            }
            run_bash_hook("semgrep_check.sh", payload, timeout=60)
            with open(src) as fh:
                content = fh.read()
            assert "old stale finding" not in content
