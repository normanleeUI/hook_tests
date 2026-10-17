"""Tests for hook_inject.py: the state-dir helpers the pip-audit hooks import."""

import os
import sys

# HOOKS_DIR points the import at another hooks dir; defaults to the live
# ~/.claude/hooks, which is what this suite tests.
sys.path.insert(0, os.environ.get("HOOKS_DIR", os.path.expanduser("~/.claude/hooks")))
from hook_inject import (
    ensure_state_dir,
    get_state_dir,
)


class TestStateDir:
    """Verify state directory management."""

    def test_hook_state_dir_created(self, tmp_path):
        """ensure_state_dir creates nested directory."""
        nested = tmp_path / "deep" / "nested" / ".hook_state"
        result = ensure_state_dir(nested)
        assert result == nested
        assert nested.is_dir()

    def test_get_state_dir_uses_env_override(self, tmp_path, monkeypatch):
        """With HOOK_STATE_DIR env var set, returns that path."""
        custom_dir = str(tmp_path / "custom_state")
        monkeypatch.setenv("HOOK_STATE_DIR", custom_dir)
        result = get_state_dir()
        assert str(result) == custom_dir

    def test_get_state_dir_falls_back_to_git_root(self, tmp_path, monkeypatch):
        """Without env var, in a dir with .git/, returns <dir>/.hook_state."""
        monkeypatch.delenv("HOOK_STATE_DIR", raising=False)
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        monkeypatch.chdir(git_dir)
        result = get_state_dir()
        assert result == git_dir / ".hook_state"
