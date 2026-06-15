"""Tests for block_glob_deny_rules.py hook.

Verifies that the hook blocks dangerous ** glob patterns in Claude Code
settings files (exit 2), allows safe specific paths (exit 0), and correctly
skips non-settings files.  The hook reads file content from disk (not from
the JSON payload), so tests write settings to temp files before invoking.
"""

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_glob_deny_rules.py"


def _make_payload(file_path: str) -> dict:
    """Build a minimal PostToolUse payload with a file_path in tool_input."""
    return {"tool_input": {"file_path": file_path}}


def _write_settings(tmp_path: Path, content: dict) -> Path:
    """Write settings JSON to a .claude/settings.json under tmp_path.

    Returns the path to the settings file (satisfies all hook guards:
    contains 'settings', ends with '.json', contains '/.claude/').
    """
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text(json.dumps(content))
    return settings_file


class TestBlockGlobDenyRules:
    """Explicit examples covering the main test matrix."""

    def test_blocks_double_star_in_deny(self, tmp_path):
        """Read(**/.env) in permissions.deny triggers exit 2."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(**/.env)"]}}
        )
        code, stderr, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 2

    def test_allows_specific_paths(self, tmp_path):
        """Specific path in permissions.deny is allowed."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/home/user/.env)"]}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 0

    def test_skips_non_settings_files(self, tmp_path):
        """Payload with a non-settings file_path exits 0 immediately."""
        code, _, _ = run_hook(HOOK, _make_payload("/project/pyproject.toml"))
        assert code == 0

    def test_checks_sandbox_allowread(self, tmp_path):
        """** in sandbox.filesystem.allowRead triggers exit 2."""
        settings_file = _write_settings(
            tmp_path, {"sandbox": {"filesystem": {"allowRead": ["**/.aws"]}}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 2

    def test_checks_sandbox_denyread(self, tmp_path):
        """** in sandbox.filesystem.denyRead triggers exit 2."""
        settings_file = _write_settings(
            tmp_path, {"sandbox": {"filesystem": {"denyRead": ["**/.secrets"]}}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 2

    def test_skips_path_without_claude_dir(self, tmp_path):
        """Path not containing /.claude/ exits 0 (guard check)."""
        # Write a file that has 'settings' and '.json' but no /.claude/
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"permissions": {"deny": ["Read(**/.env)"]}})
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 0

    def test_nonexistent_file_allowed(self, tmp_path):
        """Path that doesn't exist on disk exits 0 (FileNotFoundError caught)."""
        fake_path = str(tmp_path / ".claude" / "settings.json")
        code, _, _ = run_hook(HOOK, _make_payload(fake_path))
        assert code == 0


class TestBlockGlobDenyRulesProperties:
    """Hypothesis property tests for glob pattern detection."""

    @given(pattern=st.from_regex(r"[A-Za-z0-9_./]{1,20}", fullmatch=True))
    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_double_star_always_blocked(self, pattern):
        """Any pattern with ** prefix in a Read() deny rule is blocked."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            settings_file = tmp / ".claude" / "settings.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps({"permissions": {"deny": [f"Read(**/{pattern})"]}})
            )
            code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
            assert code == 2

    @given(path=st.from_regex(r"/[a-z]{1,10}(/[a-z]{1,10}){1,3}", fullmatch=True))
    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_specific_paths_always_allowed(self, path):
        """Paths without ** in deny rules are always allowed."""
        assume("**" not in path)
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            settings_file = tmp / ".claude" / "settings.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps({"permissions": {"deny": [f"Read({path})"]}})
            )
            code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
            assert code == 0


class TestBlockGlobDenyRulesKnownBugs:
    """Tests for known bugs — these document expected failures."""

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hook only checks allowRead and denyRead, not write counterparts",
    )
    def test_double_star_in_deny_write_should_block(self, tmp_path):
        """** in sandbox.filesystem.denyWrite should block but doesn't."""
        settings_file = _write_settings(
            tmp_path, {"sandbox": {"filesystem": {"denyWrite": ["**/secrets"]}}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 2

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hook only checks allowRead and denyRead, not write counterparts",
    )
    def test_double_star_in_allow_write_should_block(self, tmp_path):
        """** in sandbox.filesystem.allowWrite should block but doesn't."""
        settings_file = _write_settings(
            tmp_path, {"sandbox": {"filesystem": {"allowWrite": ["**/src"]}}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 2


class TestBlockGlobDenyRulesEdgeCases:
    """Edge cases and boundary conditions."""

    def test_double_star_in_permissions_allow_not_blocked(self, tmp_path):
        """** in permissions.allow doesn't cause hangs, so it's not blocked."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"allow": ["Read(**/.env)"]}}
        )
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 0

    def test_invalid_json_on_disk_allowed(self, tmp_path):
        """Invalid JSON on disk exits 0 (JSONDecodeError caught)."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        settings_file.write_text("not valid json {{{")
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 0

    def test_empty_settings_allowed(self, tmp_path):
        """Empty settings object {} exits 0."""
        settings_file = _write_settings(tmp_path, {})
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file)))
        assert code == 0


class TestBlockGlobInputSource:
    """Verify the hook reads from disk, not from the payload."""

    def test_reads_from_disk_not_payload(self, tmp_path):
        """Safe content on disk + dangerous content in payload → exit 0.

        Proves the hook reads the file from disk, not from the payload.
        """
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/safe/specific/path)"]}}
        )
        # Payload includes dangerous content that would trigger exit 2
        # if the hook read from the payload instead of disk
        payload = {
            "tool_input": {"file_path": str(settings_file)},
            "tool_response": {
                "content": json.dumps({"permissions": {"deny": ["Read(**/.env)"]}})
            },
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_confirms_disk_read_with_dangerous_disk(self, tmp_path):
        """Dangerous content on disk → exit 2, confirming disk is the source."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(**/.env)"]}}
        )
        # Payload has safe content — but hook should read disk
        payload = {
            "tool_input": {"file_path": str(settings_file)},
            "tool_response": {
                "content": json.dumps({"permissions": {"deny": ["Read(/safe/path)"]}})
            },
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2


class TestBlockGlobDenyRulesGuardFilters:
    """The hook should only act on Claude Code settings JSON files.

    Both guard conditions must be met: the filename must contain
    'settings' AND end with '.json' AND the path must contain
    '/.claude/'. Files that fail any single condition should be skipped.
    """

    def test_non_matching_json_in_claude_dir_skipped(self):
        """A .json file without 'settings' in the name should be skipped.

        Uses tempfile directly to avoid pytest tmp_path embedding test
        name (which could contain 'settings') in the file path.
        """
        with tempfile.TemporaryDirectory(prefix="hookguard_") as tmp_str:
            tmp = Path(tmp_str)
            assert "settings" not in tmp_str, (
                f"temp dir path must not contain 'settings': {tmp_str}"
            )
            claude_dir = tmp / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            config_file = claude_dir / "hooks.json"
            config_file.write_text(
                json.dumps({"permissions": {"deny": ["Read(**/.env)"]}})
            )
            code, _, _ = run_hook(HOOK, _make_payload(str(config_file)))
            assert code == 0

    def test_yaml_with_matching_name_in_claude_dir_skipped(self):
        """A file with 'settings' in the name but not .json should be skipped."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            claude_dir = tmp / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            yaml_file = claude_dir / "settings.yaml"
            yaml_file.write_text("permissions:\n  deny:\n    - 'Read(**/.env)'")
            code, _, _ = run_hook(HOOK, _make_payload(str(yaml_file)))
            assert code == 0
