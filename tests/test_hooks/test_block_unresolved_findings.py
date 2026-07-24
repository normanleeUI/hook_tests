"""Tests for block_unresolved_findings.py -- the inline-finding commit gate.

Verifies the Strategy-C generalization: a PreToolUse guard on Bash that blocks
``git commit`` while any detector has recorded must-fix findings to
``.hook_state/blocking_findings/``. The guard trusts the state (no re-analysis),
mirroring pip_audit_guard.py.

Exit codes: 0 = allow, 2 = block (unresolved findings exist).
"""

import importlib

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR, run_hook

HOOK = "block_unresolved_findings.py"


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / ".hook_state"


@pytest.fixture
def hook_inject(monkeypatch, state_dir):
    """Import the shared infra module with state pointed at a temp dir."""
    monkeypatch.setenv("HOOK_STATE_DIR", str(state_dir))
    monkeypatch.syspath_prepend(str(HOOKS_DIR))
    mod = importlib.import_module("hook_inject")
    return importlib.reload(mod)


def _commit_payload(command="git commit -m 'x'"):
    return {"tool_input": {"command": command}}


class TestCommitGate:
    def test_blocks_commit_when_findings_recorded(self, hook_inject, state_dir):
        hook_inject.record_blocking_findings(
            "bandit", "/repo/auth.py", ["line 5: [B105] hardcoded secret"]
        )
        code, stderr, _ = run_hook(
            HOOK, _commit_payload(), env={"HOOK_STATE_DIR": str(state_dir)}
        )
        assert code == 2, f"commit should be blocked, got {code}"
        assert "auth.py" in stderr and "B105" in stderr
        assert "bandit" in stderr  # provenance: which detector flagged it

    def test_allows_commit_when_no_findings(self, state_dir):
        code, _, _ = run_hook(
            HOOK, _commit_payload(), env={"HOOK_STATE_DIR": str(state_dir)}
        )
        assert code == 0

    def test_ignores_non_commit_commands(self, hook_inject, state_dir):
        """Even with findings present, a non-commit command passes through."""
        hook_inject.record_blocking_findings("bandit", "/repo/auth.py", ["line 5: x"])
        code, _, _ = run_hook(
            HOOK,
            _commit_payload("git status"),
            env={"HOOK_STATE_DIR": str(state_dir)},
        )
        assert code == 0


class TestSelfClearingLoop:
    """The detector clears its own findings on a clean re-run -> guard unblocks."""

    def test_record_then_clear_unblocks(self, hook_inject, state_dir):
        hook_inject.record_blocking_findings("bandit", "/repo/auth.py", ["line 5: x"])
        code, _, _ = run_hook(
            HOOK, _commit_payload(), env={"HOOK_STATE_DIR": str(state_dir)}
        )
        assert code == 2  # blocked while dirty

        # detector re-runs clean on that file -> empty messages clears the entry
        hook_inject.record_blocking_findings("bandit", "/repo/auth.py", [])
        code, _, _ = run_hook(
            HOOK, _commit_payload(), env={"HOOK_STATE_DIR": str(state_dir)}
        )
        assert code == 0  # unblocked

    def test_read_clean_write_blocking_records_and_self_clears(
        self, hook_inject, tmp_path
    ):
        """End-to-end: blocking=True records a finding, then clears it when the
        source no longer trips the analyzer."""
        src = tmp_path / "auth.py"
        src.write_text('master_key = "sk_live_SECRET"\n')

        def analyze(content, lines):
            return [
                (i + 1, "[B105] hardcoded secret")
                for i, ln in enumerate(lines)
                if "sk_live_" in ln
            ]

        hook_inject.read_clean_write(str(src), "bandit", analyze, blocking=True)
        assert hook_inject.read_blocking_findings()  # recorded
        assert "# HOOK:bandit:" in src.read_text()  # and injected inline

        # fix the source; a clean run must clear the block
        src.write_text('master_key = os.environ["MASTER_KEY"]\n')
        hook_inject.read_clean_write(str(src), "bandit", analyze, blocking=True)
        assert hook_inject.read_blocking_findings() == {}
