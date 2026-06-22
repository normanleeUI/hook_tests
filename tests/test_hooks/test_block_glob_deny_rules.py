"""Tests for block_glob_deny_rules.py hook.

Verifies that the hook blocks dangerous ** glob patterns in Claude Code
settings files (exit 2), allows safe specific paths (exit 0), and correctly
skips non-settings files.  The hook reconstructs proposed file content from
disk + the edit payload (PreToolUse), so tests write settings to temp files
and provide edit payloads before invoking.
"""

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_glob_deny_rules.py"


def _make_payload(file_path: str, content: dict | None = None) -> dict:
    """Build a minimal PreToolUse Write payload with file_path and content.

    When content is provided, it's serialized as the proposed file content
    (simulating a Write operation). When omitted, the payload has no
    reconstruction data, so the hook will fail-open.
    """
    payload: dict = {"tool_input": {"file_path": file_path}}
    if content is not None:
        payload["tool_input"]["content"] = json.dumps(content)
    return payload


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
        content = {"permissions": {"deny": ["Read(**/.env)"]}}
        settings_file = _write_settings(tmp_path, content)
        code, stderr, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 2

    def test_allows_specific_paths(self, tmp_path):
        """Specific path in permissions.deny is allowed."""
        content = {"permissions": {"deny": ["Read(/home/user/.env)"]}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 0

    def test_skips_non_settings_files(self, tmp_path):
        """Payload with a non-settings file_path exits 0 immediately."""
        code, _, _ = run_hook(HOOK, _make_payload("/project/pyproject.toml"))
        assert code == 0

    def test_checks_sandbox_allowread(self, tmp_path):
        """** in sandbox.filesystem.allowRead triggers exit 2."""
        content = {"sandbox": {"filesystem": {"allowRead": ["**/.aws"]}}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 2

    def test_checks_sandbox_denyread(self, tmp_path):
        """** in sandbox.filesystem.denyRead triggers exit 2."""
        content = {"sandbox": {"filesystem": {"denyRead": ["**/.secrets"]}}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
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
        """Path that doesn't exist on disk exits 0 — no edit data means fail-open."""
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
        content = {"permissions": {"deny": [f"Read(**/{pattern})"]}}
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            settings_file = tmp / ".claude" / "settings.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps(content))
            code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
            assert code == 2

    @given(path=st.from_regex(r"/[a-z]{1,10}(/[a-z]{1,10}){1,3}", fullmatch=True))
    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_specific_paths_always_allowed(self, path):
        """Paths without ** in deny rules are always allowed."""
        assume("**" not in path)
        content = {"permissions": {"deny": [f"Read({path})"]}}
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            settings_file = tmp / ".claude" / "settings.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps(content))
            code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
            assert code == 0


class TestBlockGlobDenyRulesKnownBugs:
    """Tests for known bugs — these document expected failures."""

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hook only checks allowRead and denyRead, not write counterparts",
    )
    def test_double_star_in_deny_write_should_block(self, tmp_path):
        """** in sandbox.filesystem.denyWrite should block but doesn't."""
        content = {"sandbox": {"filesystem": {"denyWrite": ["**/secrets"]}}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 2

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hook only checks allowRead and denyRead, not write counterparts",
    )
    def test_double_star_in_allow_write_should_block(self, tmp_path):
        """** in sandbox.filesystem.allowWrite should block but doesn't."""
        content = {"sandbox": {"filesystem": {"allowWrite": ["**/src"]}}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 2


class TestBlockGlobDenyRulesEdgeCases:
    """Edge cases and boundary conditions."""

    def test_double_star_in_permissions_allow_not_blocked(self, tmp_path):
        """** in permissions.allow doesn't cause hangs, so it's not blocked."""
        content = {"permissions": {"allow": ["Read(**/.env)"]}}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 0

    def test_invalid_json_on_disk_allowed(self, tmp_path):
        """Invalid JSON on disk exits 0 — Write with non-JSON content fails reconstruction."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        settings_file.write_text("not valid json {{{")
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "content": "also not valid json {{{",
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_empty_settings_allowed(self, tmp_path):
        """Empty settings object {} exits 0."""
        content: dict = {}
        settings_file = _write_settings(tmp_path, content)
        code, _, _ = run_hook(HOOK, _make_payload(str(settings_file), content))
        assert code == 0


class TestBlockGlobReconstructionSource:
    """Verify the hook uses file reconstruction (disk + proposed edit), not raw disk reads."""

    def test_no_edit_in_payload_exits_zero(self, tmp_path):
        """Payload with no old_string/new_string/content → fail-open (exit 0).

        After refactoring to PreToolUse, the hook reconstructs from disk + edit.
        If there's no edit to apply, it can't reconstruct, so it exits 0.
        """
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/safe/specific/path)"]}}
        )
        payload = {
            "tool_input": {"file_path": str(settings_file)},
            "tool_response": {
                "content": json.dumps({"permissions": {"deny": ["Read(**/.env)"]}})
            },
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_dangerous_on_disk_no_edit_exits_zero(self, tmp_path):
        """Dangerous content on disk but no edit in payload → fail-open (exit 0).

        Without an edit to reconstruct, the hook can't determine what the
        proposed file would look like, so it exits 0.
        """
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(**/.env)"]}}
        )
        payload = {
            "tool_input": {"file_path": str(settings_file)},
            "tool_response": {
                "content": json.dumps({"permissions": {"deny": ["Read(/safe/path)"]}})
            },
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0


class TestBlockGlobFileReconstruction:
    """File reconstruction tests for PreToolUse (Strategy A)."""

    def test_edit_introducing_glob_blocked(self, tmp_path):
        """Edit that changes specific path to ** pattern is blocked."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/home/user/.env)"]}}
        )
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "old_string": '"Read(/home/user/.env)"',
                "new_string": '"Read(**/.env)"',
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2
        assert "**" in stderr

    def test_edit_keeping_specific_path_allowed(self, tmp_path):
        """Edit that changes one specific path to another is allowed."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/home/user/.env)"]}}
        )
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "old_string": '"Read(/home/user/.env)"',
                "new_string": '"Read(/home/user/.env.local)"',
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_write_with_glob_blocked(self, tmp_path):
        """Write operation with content containing ** is blocked."""
        settings_file = _write_settings(tmp_path, {})
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "content": '{"permissions": {"deny": ["Read(**/.env)"]}}',
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2

    def test_edit_does_not_modify_original_file(self, tmp_path):
        """File on disk must not be changed by the hook."""
        settings_file = _write_settings(
            tmp_path, {"permissions": {"deny": ["Read(/home/user/.env)"]}}
        )
        original_on_disk = settings_file.read_text()
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "old_string": '"Read(/home/user/.env)"',
                "new_string": '"Read(**/.env)"',
            }
        }
        run_hook(HOOK, payload)
        assert settings_file.read_text() == original_on_disk

    def test_old_string_not_found_exits_zero(self, tmp_path):
        """If old_string doesn't match file content, fail-open (exit 0)."""
        settings_file = _write_settings(tmp_path, {"permissions": {}})
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "old_string": "this text does not exist",
                "new_string": '"Read(**/.env)"',
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_malformed_json_after_reconstruction_exits_zero(self, tmp_path):
        """If reconstructed content isn't valid JSON, fail-open."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        settings_file.write_text("not json")
        payload = {
            "tool_input": {
                "file_path": str(settings_file),
                "content": "also not json",
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0


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
