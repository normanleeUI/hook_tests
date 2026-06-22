"""Tests for pip_audit_guard.py hook.

Verifies the PreToolUse guard that blocks dependency operations (``uv add``,
``uv sync``, ``uv pip install``) when a pip-audit state file exists at
``.hook_state/pip_audit/report.json``. The guard trusts the state file --
it does NOT re-run pip-audit. State files are managed by the companion
PostToolUse hook ``pip_audit_check.py``.

Exit codes: 0 = allow operation, 2 = block (vulnerabilities previously found).
"""

import json
import os
from pathlib import Path

import pytest

from tests.test_hooks.hook_runner import run_hook

HOOK = "pip_audit_guard.py"


@pytest.fixture
def pre_tool_payload():
    """Build a PreToolUse payload with tool_input.command."""

    def _make(command: str) -> dict:
        return {"tool_input": {"command": command}}

    return _make


@pytest.fixture
def state_dir(tmp_path):
    """Provide a temporary HOOK_STATE_DIR and return the Path to it."""
    d = tmp_path / ".hook_state"
    return d


def _create_state_file(state_dir: Path) -> Path:
    """Create a pip_audit report.json state file with sample vulnerability data."""
    report = state_dir / "pip_audit" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "vulns": "pkg1  1.0  CVE-2024-1234",
                "summary": "Found 1 known vulnerability",
            }
        )
    )
    return report


class TestPipAuditGuard:
    """PreToolUse guard blocks dependency ops when pip-audit state file exists."""

    def test_guard_blocks_when_state_file_exists(
        self, pre_tool_payload, state_dir, tmp_path
    ):
        """With a state file present, 'uv add httpx' should exit 2 with error message."""
        _create_state_file(state_dir)
        payload = pre_tool_payload("uv add httpx")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 2, (
            f"Guard should block (exit 2) when state file exists, got {code}"
        )
        assert "vulnerabilit" in stderr.lower(), (
            f"Error message should mention vulnerabilities, got: {stderr}"
        )

    def test_guard_allows_when_no_state_file(
        self, pre_tool_payload, state_dir, tmp_path
    ):
        """Without a state file, 'uv add httpx' should exit 0 (allow)."""
        payload = pre_tool_payload("uv add httpx")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 0, f"Guard should allow (exit 0) when no state file, got {code}"

    def test_guard_fast_exit_on_non_matching_command(
        self, pre_tool_payload, state_dir, tmp_path
    ):
        """Non-matching command ('echo hello') exits 0 even with state file present."""
        _create_state_file(state_dir)
        payload = pre_tool_payload("echo hello")
        code, _, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 0, "Guard should fast-exit 0 for non-matching commands"

    def test_guard_blocks_for_uv_sync(self, pre_tool_payload, state_dir, tmp_path):
        """'uv sync' should also be blocked when state file exists."""
        _create_state_file(state_dir)
        payload = pre_tool_payload("uv sync")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 2, f"Guard should block 'uv sync', got exit {code}"
        assert "vulnerabilit" in stderr.lower()

    def test_guard_blocks_for_uv_pip_install(
        self, pre_tool_payload, state_dir, tmp_path
    ):
        """'uv pip install requests' should also be blocked when state file exists."""
        _create_state_file(state_dir)
        payload = pre_tool_payload("uv pip install requests")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 2, f"Guard should block 'uv pip install', got exit {code}"
        assert "vulnerabilit" in stderr.lower()

    def test_guard_allows_after_state_cleared(
        self, pre_tool_payload, state_dir, tmp_path
    ):
        """State file created then deleted -- guard should allow (exit 0)."""
        report = _create_state_file(state_dir)
        report.unlink()
        payload = pre_tool_payload("uv add httpx")
        code, _, _ = run_hook(
            HOOK,
            payload,
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 0, "Guard should allow when state file has been cleared"
