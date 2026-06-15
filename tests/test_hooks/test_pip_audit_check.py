"""Tests for pip_audit_check.py hook.

Verifies that the PostToolUse hook runs ``uvx pip-audit`` after
dependency-changing commands (``uv add``, ``uv sync``, ``uv pip install``)
succeed (exitCode == 0), and silently exits for non-matching commands or
failed commands (exitCode != 0).

Exit codes: 0 = no action or audit clean, 2 = vulnerabilities found (spec-correct; see known bugs).

The hook has two sequential gates:
  1. Command substring check -- must contain ``uv add``, ``uv sync``, or
     ``uv pip install``.
  2. Exit code check -- ``tool_result.exitCode`` must be 0.

Only when both gates pass does the hook engage and run ``uvx pip-audit``.
"""

import os
import subprocess

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_hook

HOOK = "pip_audit_check.py"


@pytest.fixture
def post_tool_payload():
    """Build a PostToolUse payload with both tool_input and tool_result."""

    def _make(command: str, exit_code: int = 0) -> dict:
        return {
            "tool_input": {"command": command},
            "tool_result": {"exitCode": exit_code},
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

    def test_missing_tool_result_exits_zero(self):
        """Payload with tool_input but no tool_result exits 0.

        The hook defaults exitCode to 1 via .get("exitCode", 1), so the
        exit-code gate (exitCode != 0) causes early return.
        """
        payload = {"tool_input": {"command": "uv add requests"}}
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
    nonzero_exit = st.integers(min_value=1, max_value=255)

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

    @given(pkg=pkg_names, exit_code=nonzero_exit)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_add_with_nonzero_exit_returns_zero(
        self, post_tool_payload, pkg, exit_code
    ):
        """uv add with a nonzero exitCode skips the audit."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv add {pkg}", exit_code=exit_code)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(exit_code=nonzero_exit)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_sync_with_nonzero_exit_returns_zero(self, post_tool_payload, exit_code):
        """uv sync with a nonzero exitCode skips the audit."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload("uv sync", exit_code=exit_code)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names, exit_code=nonzero_exit)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_pip_install_with_nonzero_exit_returns_zero(
        self, post_tool_payload, pkg, exit_code
    ):
        """uv pip install with a nonzero exitCode skips the audit."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv pip install {pkg}", exit_code=exit_code)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr


class TestPipAuditCheckKnownBugs:
    """Known bugs in pip_audit_check.py (Step 0d)."""

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hook uses exit(1) for vuln found, should be exit(2) for deliberate block",
    )
    @pytest.mark.network
    def test_vuln_found_should_exit_2(self):
        """Step 0d: exit 1 = hook error, not deliberate block. Should be exit 2."""
        payload = {
            "tool_input": {"command": "uv add requests"},
            "tool_result": {"exitCode": 0},
        }
        code, stderr, _ = run_hook(HOOK, payload, timeout=30)
        if "[pip-audit]" not in stderr or "vulnerability" not in stderr.lower():
            pytest.skip("No vulnerabilities found in current environment")
        assert code == 2, "Vuln found should exit 2 (block), not 1 (error)"

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: unhandled exception on malformed input",
    )
    def test_non_dict_json_should_not_crash(self):
        """Step 0d fail-safe: non-dict JSON payload should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, ["not", "a", "dict"])
        assert code == 0

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: unhandled exception on malformed input",
    )
    def test_null_command_should_not_crash(self):
        """Step 0d fail-safe: null command should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, {"tool_input": {"command": None}})
        assert code == 0


class TestPipAuditCheckGateEngagement:
    """Each trigger command should independently engage the hook.

    The hook should run pip-audit after any of: 'uv add', 'uv sync',
    or 'uv pip install'. These tests verify each command engages the
    hook on its own. Not marked @network because the engagement message
    is written before the subprocess call.
    """

    def test_uv_add_alone_engages(self, post_tool_payload):
        """'uv add' alone (without 'uv sync' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv add requests")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv add' command"

    def test_uv_sync_alone_engages(self, post_tool_payload):
        """'uv sync' alone (without 'uv add' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv sync")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv sync' command"

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

    def test_audit_vuln_found_returns_nonzero(self, post_tool_payload, tmp_path):
        """When pip-audit exits 1 (vulns found), hook should return nonzero."""
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
        assert code != 0, "Hook should return nonzero when pip-audit finds vulns"
        assert "[pip-audit]" in stderr
