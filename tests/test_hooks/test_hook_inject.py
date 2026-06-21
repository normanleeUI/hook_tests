"""Tests for hook_inject.py shared infrastructure module.

Tests inline injection (remove/inject comments), the read_clean_write
orchestrator, and state-file management utilities.
"""

import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
from hook_inject import (
    ensure_state_dir,
    get_state_dir,
    inject_at_line,
    read_clean_write,
    remove_hook_comments,
)


class TestRemoveHookComments:
    """Verify stale hook comment removal."""

    def test_remove_hook_comments_cleans_matching_lines(self):
        lines = [
            "def foo():\n",
            "# HOOK:DOCSTRING: missing docstring\n",
            "    pass\n",
            "    return 1\n",
        ]
        cleaned = remove_hook_comments(lines, "DOCSTRING")
        assert len(cleaned) == 3
        assert not any("HOOK:DOCSTRING:" in line for line in cleaned)

    def test_remove_hook_comments_preserves_non_matching(self):
        lines = [
            "def foo():\n",
            "# HOOK:BANDIT: security issue\n",
            "    pass\n",
            "    return 1\n",
        ]
        cleaned = remove_hook_comments(lines, "DOCSTRING")
        assert len(cleaned) == 4
        assert any("HOOK:BANDIT:" in line for line in cleaned)


class TestInjectAtLine:
    """Verify comment insertion at 1-based line numbers."""

    def test_inject_at_line_inserts_before_target(self):
        lines = ["line1\n", "line2\n", "line3\n"]
        inject_at_line(lines, 2, "TEST", "found issue")
        assert lines[1] == "# HOOK:TEST: found issue\n"
        assert lines[2] == "line2\n"

    def test_inject_at_line_preserves_all_content(self):
        original = ["line1\n", "line2\n", "line3\n"]
        lines = list(original)
        inject_at_line(lines, 2, "TEST", "found issue")
        non_hook = [l for l in lines if not l.startswith("# HOOK:")]
        assert non_hook == original


class TestReadCleanWrite:
    """Verify the full read-clean-analyze-inject-write orchestrator."""

    def test_read_clean_write_no_change_no_write(self, tmp_path):
        """Clean file + analyzer returns [] -> mtime unchanged."""
        f = tmp_path / "clean.py"
        f.write_text("def foo():\n    pass\n")
        mtime_before = f.stat().st_mtime
        # Small sleep to ensure mtime would differ if file were written
        time.sleep(0.05)

        read_clean_write(str(f), "TEST", lambda content, lines: [])

        mtime_after = f.stat().st_mtime
        assert mtime_before == mtime_after

    def test_read_clean_write_cleanup_only(self, tmp_path):
        """File with stale comment + analyzer returns [] -> stale comment removed."""
        f = tmp_path / "stale.py"
        f.write_text("def foo():\n# HOOK:TEST: old finding\n    pass\n")

        read_clean_write(str(f), "TEST", lambda content, lines: [])

        result = f.read_text()
        assert "HOOK:TEST:" not in result
        assert "def foo():\n    pass\n" == result

    def test_read_clean_write_multi_finding_line_order(self, tmp_path):
        """3-line file, findings at lines 1 and 3 -> comments at correct positions."""
        f = tmp_path / "multi.py"
        f.write_text("aaa\nbbb\nccc\n")

        def analyzer(content, lines):
            return [(1, "issue at line 1"), (3, "issue at line 3")]

        read_clean_write(str(f), "TEST", analyzer)

        result_lines = f.read_text().splitlines(keepends=True)
        assert result_lines[0] == "# HOOK:TEST: issue at line 1\n"
        assert result_lines[1] == "aaa\n"
        assert result_lines[3] == "# HOOK:TEST: issue at line 3\n"
        assert result_lines[4] == "ccc\n"
        assert len(result_lines) == 5

    def test_read_clean_write_analyzer_sees_clean_content(self, tmp_path):
        """File has stale comment -> analyzer receives content without it."""
        f = tmp_path / "has_stale.py"
        f.write_text("def foo():\n# HOOK:TEST: old\n    pass\n")
        seen_content = []

        def analyzer(content, lines):
            seen_content.append(content)
            return []

        read_clean_write(str(f), "TEST", analyzer)

        assert "HOOK:TEST:" not in seen_content[0]
        assert seen_content[0] == "def foo():\n    pass\n"


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
