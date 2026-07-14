"""Tests for pip_audit_check.py hook.

Verifies that the PostToolUse hook runs ``uvx pip-audit`` after
dependency-changing commands (``uv add``, ``uv sync``, ``uv pip install``)
run, and silently exits for non-matching commands or interrupted commands.

Payload schema note (regression): real PostToolUse Bash payloads carry a
``tool_response`` key (NOT ``tool_result``), and that response has NO
``exitCode`` field -- its keys are ``stdout``, ``stderr``, ``interrupted``,
``isImage``, ``noOutputExpected``. The hook once gated on
``tool_result["exitCode"] == 0``, which never matched the real schema, so the
audit never ran. It now audits whenever a matching uv command ran and was not
interrupted. See ``TestPipAuditCheckRealPayloadSchema`` for the load-bearing
regression tests.

Exit codes: always 0 on PostToolUse (exit codes are cosmetic). When vulns
are found, findings are persisted to ``.hook_state/pip_audit/report.json``
so the companion guard hook (``pip_audit_guard.py``) can block future ops.

The hook has two sequential gates:
  1. Command substring check -- must contain ``uv add``, ``uv sync``, or
     ``uv pip install``.
  2. Interruption check -- ``tool_response.interrupted`` must be falsy.

Only when both gates pass does the hook engage and run ``uvx pip-audit``.
"""

import json as json_mod
import os
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_hook

HOOK = "pip_audit_check.py"


@pytest.fixture
def post_tool_payload():
    """Build a realistic PostToolUse Bash payload.

    Mirrors the real Claude Code schema: a ``tool_response`` key (not
    ``tool_result``) whose Bash payload has NO ``exitCode`` -- keys are
    ``stdout``, ``stderr``, ``interrupted``, ``isImage``, ``noOutputExpected``.
    """

    def _make(command: str, interrupted: bool = False) -> dict:
        return {
            "tool_input": {"command": command},
            "tool_response": {
                "stdout": "",
                "stderr": "",
                "interrupted": interrupted,
                "isImage": False,
                "noOutputExpected": False,
            },
        }

    return _make


def _run_hook_expecting_engagement(payload: dict, timeout: int = 15) -> str:
    """Run the hook and return stderr, tolerating TimeoutExpired.

    The engagement tests only need to verify that the hook printed
    ``[pip-audit]`` to stderr before spawning ``uvx pip-audit``. The
    subprocess may time out if pip-audit is slow, but the engagement
    message is written *before* the subprocess call, so it will appear
    in stderr either way.
    """
    try:
        _, stderr, _ = run_hook(HOOK, payload, timeout=timeout)
        return stderr
    except subprocess.TimeoutExpired as exc:
        stderr_bytes = exc.stderr or b""
        if isinstance(stderr_bytes, bytes):
            return stderr_bytes.decode("utf-8", errors="replace")
        return stderr_bytes


def _run_hook_raw_stdin(raw_stdin: str, timeout: int = 10) -> tuple[int, str, str]:
    """Invoke the hook with a raw string on stdin (bypasses json.dumps).

    Used for testing invalid-JSON handling, since the standard run_hook
    helper serializes the payload via json.dumps.
    """
    script = HOOKS_DIR / HOOK
    if not script.exists():
        pytest.skip(f"Hook script not found: {script}")

    result = subprocess.run(
        ["python3", str(script)],
        input=raw_stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ,
    )
    return result.returncode, result.stderr, result.stdout


class TestPipAuditCheckExamples:
    """Explicit test cases from the test matrix."""

    def test_non_matching_command_exits_zero(self, post_tool_payload):
        """A command without uv add/sync/pip install exits 0 immediately."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload("git status"))
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_invalid_json_exits_zero(self):
        """Invalid JSON on stdin triggers the JSONDecodeError handler, exit 0."""
        code, stderr, _ = _run_hook_raw_stdin("not json")
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_empty_payload_exits_zero(self):
        """Empty payload {} has no tool_input, so command is '' -- no match."""
        code, stderr, _ = run_hook(HOOK, {})
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_interrupted_uv_add_skips_audit(self, post_tool_payload):
        """A matching uv command whose tool_response.interrupted is True skips.

        An interrupted command may have left deps half-changed; the hook
        early-returns without engaging pip-audit.
        """
        payload = post_tool_payload("uv add requests", interrupted=True)
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 0
        assert "[pip-audit]" not in stderr

    @pytest.mark.network
    def test_uv_add_with_exit_zero_engages(self, post_tool_payload):
        """uv add with exitCode=0 passes both gates and runs pip-audit.

        The hook engages and prints to stderr before spawning ``uvx pip-audit``.
        We use a short timeout and accept either normal completion or a
        TimeoutExpired -- in both cases the engagement message must appear
        in stderr.
        """
        payload = post_tool_payload("cd /project && uv add requests")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"

    @pytest.mark.network
    def test_uv_pip_install_with_exit_zero_engages(self, post_tool_payload):
        """uv pip install with exitCode=0 passes both gates and runs pip-audit."""
        payload = post_tool_payload("uv pip install requests")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"

    @pytest.mark.network
    def test_uv_sync_frozen_with_exit_zero_engages(self, post_tool_payload):
        """uv sync --frozen with exitCode=0 passes both gates and runs pip-audit."""
        payload = post_tool_payload("uv sync --frozen")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"


class TestPipAuditCheckProperties:
    """Hypothesis property tests for gate logic."""

    non_matching_cmds = st.from_regex(
        r"[a-z][a-z0-9 _/.-]{0,50}", fullmatch=True
    ).filter(
        lambda c: "uv add" not in c and "uv sync" not in c and "uv pip install" not in c
    )
    pkg_names = st.from_regex(r"[a-z][a-z0-9_-]{0,20}", fullmatch=True)

    @given(command=non_matching_cmds)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_matching_commands_exit_zero(self, post_tool_payload, command):
        """Any command not containing the trigger substrings exits 0."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload(command))
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_add_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv add whose tool_response.interrupted is True skips the audit."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv add {pkg}", interrupted=True)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_sync_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv sync whose tool_response.interrupted is True skips the audit."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload("uv sync", interrupted=True))
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_pip_install_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv pip install whose tool_response.interrupted is True skips."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv pip install {pkg}", interrupted=True)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr


class TestPipAuditCheckKnownBugs:
    """Known bugs in pip_audit_check.py (Step 0d)."""

    def test_non_dict_json_should_not_crash(self):
        """Step 0d fail-safe: non-dict JSON payload should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, ["not", "a", "dict"])
        assert code == 0

    def test_null_command_should_not_crash(self):
        """Step 0d fail-safe: null command should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, {"tool_input": {"command": None}})
        assert code == 0


class TestPipAuditCheckGateEngagement:
    """Each trigger command should independently engage the hook.

    The hook should run pip-audit after any of: 'uv add', 'uv sync',
    or 'uv pip install'. These tests verify each command engages the
    hook on its own.
    """

    @pytest.mark.network
    def test_uv_add_alone_engages(self, post_tool_payload):
        """'uv add' alone (without 'uv sync' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv add requests")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv add' command"

    @pytest.mark.network
    def test_uv_sync_alone_engages(self, post_tool_payload):
        """'uv sync' alone (without 'uv add' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv sync")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv sync' command"

    @pytest.mark.network
    def test_uv_pip_install_alone_engages(self, post_tool_payload):
        """'uv pip install' alone should engage."""
        payload = post_tool_payload("uv pip install requests")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, (
            "Hook should engage for 'uv pip install' command"
        )


class TestPipAuditCheckSubprocessResults:
    """The hook should pass through pip-audit's exit code.

    Clean audit (exit 0) means no vulnerabilities — hook should exit 0.
    Vulnerabilities found (exit nonzero) — hook should exit nonzero.
    Uses a fake ``uvx`` script to test without network access.
    """

    @staticmethod
    def _make_fake_uvx(tmp_path, exit_code: int, stdout: str = "", stderr: str = ""):
        """Create a fake 'uvx' script that returns the given exit code."""
        fake_uvx = tmp_path / "uvx"
        fake_uvx.write_text(
            f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n"
        )
        fake_uvx.chmod(0o755)
        return str(tmp_path)

    def test_audit_clean_returns_zero(self, post_tool_payload, tmp_path):
        """When pip-audit exits 0 (clean), hook should return 0."""
        fake_bin = self._make_fake_uvx(tmp_path, 0, stdout="pkg1\npkg2\npkg3")
        # PATH: fake bin first (for uvx), then real bins (for python)
        real_path = os.environ.get("PATH", "")
        payload = post_tool_payload("uv add requests")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"PATH": f"{fake_bin}:{real_path}"},
            timeout=15,
        )
        assert code == 0
        assert "[pip-audit]" in stderr

    def test_audit_vuln_found_exits_zero_and_reports(self, post_tool_payload, tmp_path):
        """When pip-audit finds vulns, hook exits 0 (PostToolUse is cosmetic) but reports to stderr.

        Blocking is handled by the companion guard hook (pip_audit_guard.py)
        via the state file, not by exit code on PostToolUse.
        """
        fake_bin = self._make_fake_uvx(
            tmp_path,
            1,
            stdout="pkg1  1.0  CVE-2024-1234",
            stderr="VULNERABILITIES FOUND",
        )
        real_path = os.environ.get("PATH", "")
        payload = post_tool_payload("uv add requests")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"PATH": f"{fake_bin}:{real_path}"},
            timeout=15,
        )
        assert code == 0, (
            "PostToolUse hook should always exit 0 (guard handles blocking)"
        )
        assert "[pip-audit]" in stderr


class TestPipAuditCheckStateFile:
    """State-file gating: pip_audit_check.py writes/clears .hook_state/pip_audit/report.json.

    When pip-audit finds vulnerabilities, the hook persists findings to a state
    file so the companion guard hook (pip_audit_guard.py) can block future
    dependency operations. When the audit is clean, any existing state file is
    deleted so the guard stops blocking.
    """

    @staticmethod
    def _make_fake_uvx(tmp_path, exit_code: int, stdout: str = "", stderr: str = ""):
        """Create a fake 'uvx' script that returns the given exit code."""
        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir(exist_ok=True)
        fake_uvx = fake_bin / "uvx"
        fake_uvx.write_text(
            f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n"
        )
        fake_uvx.chmod(0o755)
        return str(fake_bin)

    def _run_with_state_dir(
        self,
        post_tool_payload,
        tmp_path,
        exit_code,
        stdout="",
        stderr_text="",
        command="uv add requests",
    ):
        """Run the hook with a fake uvx and HOOK_STATE_DIR set."""
        fake_bin = self._make_fake_uvx(tmp_path, exit_code, stdout, stderr_text)
        real_path = os.environ.get("PATH", "")
        state_dir = str(tmp_path / ".hook_state")
        payload = post_tool_payload(command)
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={
                "PATH": f"{fake_bin}:{real_path}",
                "HOOK_STATE_DIR": state_dir,
            },
            timeout=15,
        )
        return code, stderr, Path(state_dir)

    def test_vuln_found_creates_state_file(self, post_tool_payload, tmp_path):
        """When pip-audit finds vulns (fake uvx exits 1), state file is created."""
        _, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=1,
            stdout="pkg1  1.0  CVE-2024-1234",
            stderr_text="VULNERABILITIES FOUND",
        )
        report = state_dir / "pip_audit" / "report.json"
        assert report.exists(), "State file should be created when vulns found"

    def test_clean_audit_clears_state_file(self, post_tool_payload, tmp_path):
        """When pip-audit is clean (fake uvx exits 0), existing state file is deleted."""
        state_dir = tmp_path / ".hook_state"
        report = state_dir / "pip_audit" / "report.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"vulns": "old data", "summary": "stale"}')

        _, _, state_dir_result = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=0,
            stdout="pkg1\npkg2\npkg3",
        )
        report = state_dir_result / "pip_audit" / "report.json"
        assert not report.exists(), "State file should be deleted when audit is clean"

    def test_clean_audit_no_state_file_is_noop(self, post_tool_payload, tmp_path):
        """When pip-audit is clean and no state file exists, nothing crashes."""
        code, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=0,
            stdout="pkg1\npkg2",
        )
        report = state_dir / "pip_audit" / "report.json"
        assert not report.exists()
        assert code == 0

    def test_state_file_contains_summary(self, post_tool_payload, tmp_path):
        """The state file content includes vulnerability details from pip-audit."""
        vuln_output = "pkg1  1.0  CVE-2024-1234"
        _, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=1,
            stdout=vuln_output,
            stderr_text="Found 1 vulnerability",
        )
        report = state_dir / "pip_audit" / "report.json"
        data = json_mod.loads(report.read_text())
        assert "vulns" in data, "State file must have 'vulns' key"
        assert "summary" in data, "State file must have 'summary' key"
        assert "CVE-2024-1234" in data["vulns"], "Vuln details should be in state file"


class TestPipAuditCheckRealPayloadSchema:
    """Regression tests locking in the tool_response schema fix.

    Real PostToolUse Bash payloads carry ``tool_response`` (not ``tool_result``)
    and that response has NO ``exitCode`` -- keys are stdout/stderr/interrupted/
    isImage/noOutputExpected. The hook previously gated on
    ``tool_result["exitCode"] == 0`` (defaulting a missing exitCode to 1), so
    against the real schema it always early-returned and never audited.

    These tests exercise the REAL schema via the ``post_tool_payload`` fixture.
    The engage-and-report test is load-bearing: it fails against the old
    exitCode gate (no engagement, no report file) and passes with the fix. A
    fake ``uvx`` on PATH stands in for the real network call.
    """

    @staticmethod
    def _make_fake_uvx(tmp_path, exit_code: int, stdout: str = "", stderr: str = ""):
        """Create a fake 'uvx' on PATH that returns the given exit code."""
        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir(exist_ok=True)
        fake_uvx = fake_bin / "uvx"
        fake_uvx.write_text(
            f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n"
        )
        fake_uvx.chmod(0o755)
        return str(fake_bin)

    def _run(self, payload, tmp_path, exit_code, stdout="", stderr_text=""):
        fake_bin = self._make_fake_uvx(tmp_path, exit_code, stdout, stderr_text)
        real_path = os.environ.get("PATH", "")
        state_dir = str(tmp_path / ".hook_state")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"PATH": f"{fake_bin}:{real_path}", "HOOK_STATE_DIR": state_dir},
            timeout=15,
        )
        return code, stderr, Path(state_dir)

    def test_real_response_has_no_exitcode_key(self, post_tool_payload):
        """Sanity guard: the realistic payload must NOT carry an exitCode.

        If a future edit reintroduces exitCode, the load-bearing test below
        would stop distinguishing old from new behavior -- this pins the schema.
        """
        payload = post_tool_payload("uv add requests")
        assert "tool_result" not in payload
        assert "exitCode" not in payload["tool_response"]

    def test_real_response_vuln_found_engages_and_writes_report(
        self, post_tool_payload, tmp_path
    ):
        """LOAD-BEARING: real tool_response (no exitCode) + uv add engages the
        audit and, on a vulnerable result, writes report.json.

        Old code gated on tool_result["exitCode"] == 0; a real payload has no
        tool_result/exitCode, so it early-returned -- no [pip-audit] message and
        no report file. This asserts the fixed behavior.
        """
        payload = post_tool_payload("uv add requests")
        code, stderr, state_dir = self._run(
            payload,
            tmp_path,
            exit_code=1,
            stdout="pkg1  1.0  CVE-2024-1234",
            stderr_text="VULNERABILITIES FOUND",
        )
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" in stderr, "Hook must engage on the real payload schema"
        assert report.exists(), "Vulnerable audit must write report.json"
        assert "CVE-2024-1234" in json_mod.loads(report.read_text())["vulns"]

    def test_real_response_clean_audit_writes_no_report(
        self, post_tool_payload, tmp_path
    ):
        """Real tool_response + uv add engages, and a clean audit leaves no
        report.json (companion guard then stops blocking)."""
        payload = post_tool_payload("uv sync")
        code, stderr, state_dir = self._run(
            payload, tmp_path, exit_code=0, stdout="pkg1\npkg2\npkg3"
        )
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" in stderr, "Hook must engage on the real payload schema"
        assert not report.exists(), "Clean audit must not leave a report file"

    def test_real_response_interrupted_skips_audit(self, post_tool_payload, tmp_path):
        """interrupted=True on the real schema skips the audit entirely."""
        payload = post_tool_payload("uv add requests", interrupted=True)
        code, stderr, state_dir = self._run(payload, tmp_path, exit_code=1)
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" not in stderr
        assert not report.exists()

    def test_non_uv_command_skips_audit(self, post_tool_payload, tmp_path):
        """A non-uv command (git status) with a normal response is filtered out."""
        payload = post_tool_payload("git status")
        code, stderr, state_dir = self._run(payload, tmp_path, exit_code=1)
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" not in stderr
        assert not report.exists()
