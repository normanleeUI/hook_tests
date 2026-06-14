"""Gate-logic tests for 6 shell-based hooks.

These hooks are thin wrappers that parse JSON, extract file paths, filter by
extension/location, and delegate to external tools (ruff, pyright, bandit).
We verify the GATE logic — does the hook correctly decide whether to invoke
the tool? — not the external tool's behavior.

Approach: create fake tool stubs that log invocations, inject them into PATH,
run the hook, then check the log to see whether/how the tool was called.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_bash_hook


def edit_payload(file_path: str) -> dict:
    """Build a PostToolUse payload with both jq-extractable path fields."""
    return {
        "tool_input": {"file_path": file_path, "new_string": "x = 1\n"},
        "tool_response": {"filePath": file_path},
    }


@pytest.fixture
def fake_tool_env(tmp_path):
    """Create fake uvx/ruff/pyright/bandit stubs that log invocations."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "tool_invocations.log"

    for tool_name in ("uvx", "ruff", "pyright", "bandit"):
        stub = bin_dir / tool_name
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{tool_name} $@" >> {log_file}\nexit 0\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": os.environ["HOME"]}
    return env, log_file


def _init_git_repo(path):
    """Initialize a minimal git repo at *path* with an initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    # Create an initial commit so HEAD exists
    placeholder = os.path.join(str(path), ".gitkeep")
    with open(placeholder, "w") as fh:
        fh.write("")
    subprocess.run(
        ["git", "-C", str(path), "add", ".gitkeep"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _read_log(log_file) -> str:
    """Return log contents, or empty string if no log was written."""
    if log_file.exists():
        return log_file.read_text()
    return ""


# ── ruff_format.sh ──────────────────────────────────────────────────────────


class TestRuffFormat:
    """ruff_format.sh: auto-format .py files after edit, skip everything else."""

    def test_formats_py_file(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        py_file = tmp_path / "module.py"
        py_file.write_text("x=1\n")

        rc, stderr, stdout = run_bash_hook(
            "ruff_format.sh", edit_payload(str(py_file)), env=env
        )

        log = _read_log(log_file)
        assert "uvx ruff format" in log
        assert str(py_file) in log

    def test_skips_txt_file(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("hello\n")

        run_bash_hook("ruff_format.sh", edit_payload(str(txt_file)), env=env)

        log = _read_log(log_file)
        assert log == "", "ruff should not be invoked for .txt files"

    def test_handles_spaces_in_path(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        spacy_dir = tmp_path / "my project"
        spacy_dir.mkdir()
        py_file = spacy_dir / "mod ule.py"
        py_file.write_text("x=1\n")

        run_bash_hook("ruff_format.sh", edit_payload(str(py_file)), env=env)

        log = _read_log(log_file)
        assert "uvx ruff format" in log
        # The full path (with spaces) should appear in the log
        assert "mod ule.py" in log

    def test_handles_missing_file_path(self, fake_tool_env):
        """When no file_path is present, the hook should silently no-op."""
        env, log_file = fake_tool_env
        payload = {"tool_input": {}, "tool_response": {}}

        rc, stderr, stdout = run_bash_hook("ruff_format.sh", payload, env=env)

        log = _read_log(log_file)
        assert log == "", "ruff should not be invoked when file_path is missing"


# ── pyright_check.sh ────────────────────────────────────────────────────────


class TestPyrightCheck:
    """pyright_check.sh: type-check .py project files, skip hooks/config and mypy projects."""

    def test_checks_py_file(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        py_file = tmp_path / "app.py"
        py_file.write_text("x: int = 1\n")

        run_bash_hook("pyright_check.sh", edit_payload(str(py_file)), env=env)

        log = _read_log(log_file)
        assert "uvx pyright" in log
        assert str(py_file) in log

    def test_skips_claude_dir(self, fake_tool_env, tmp_path):
        """Files under any .claude/ directory should be skipped."""
        env, log_file = fake_tool_env
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        hook_file = claude_dir / "my_hook.py"
        hook_file.write_text("pass\n")

        run_bash_hook("pyright_check.sh", edit_payload(str(hook_file)), env=env)

        log = _read_log(log_file)
        assert log == "", "pyright should skip files in .claude/"

    def test_skips_non_py(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        js_file = tmp_path / "index.js"
        js_file.write_text("const x = 1;\n")

        run_bash_hook("pyright_check.sh", edit_payload(str(js_file)), env=env)

        log = _read_log(log_file)
        assert log == "", "pyright should skip non-.py files"

    def test_skips_when_mypy_configured(self, fake_tool_env, tmp_path):
        """If pyproject.toml has [tool.mypy], skip pyright."""
        env, log_file = fake_tool_env
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        py_file = proj_dir / "app.py"
        py_file.write_text("x = 1\n")

        run_bash_hook("pyright_check.sh", edit_payload(str(py_file)), env=env)

        log = _read_log(log_file)
        assert log == "", "pyright should skip when mypy is configured"


# ── bandit_check.sh ─────────────────────────────────────────────────────────


class TestBanditCheck:
    """bandit_check.sh: security-scan production .py files, skip tests."""

    def test_scans_py_file(self, fake_tool_env):
        env, log_file = fake_tool_env
        # pytest's tmp_path includes the test name (e.g. "test_scans_py_file0")
        # which matches the hook's */test_* glob. That glob matches parent
        # directory components, not just filenames — arguably a hook bug
        # (spec says "skip test files", not "skip files under test-named dirs").
        with tempfile.TemporaryDirectory(prefix="proj_") as td:
            py_file = os.path.join(td, "app.py")
            with open(py_file, "w") as fh:
                fh.write("import os\n")

            run_bash_hook("bandit_check.sh", edit_payload(py_file), env=env)

            log = _read_log(log_file)
            assert "uvx bandit" in log
            assert py_file in log

    def test_skips_test_files(self, fake_tool_env, tmp_path):
        """Test files (test_* prefix, tests/ dir, _test.py suffix) should be skipped."""
        env, log_file = fake_tool_env

        # test_ prefix
        test_file = tmp_path / "test_app.py"
        test_file.write_text("pass\n")
        run_bash_hook("bandit_check.sh", edit_payload(str(test_file)), env=env)

        # tests/ directory
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file2 = tests_dir / "conftest.py"
        test_file2.write_text("pass\n")
        run_bash_hook("bandit_check.sh", edit_payload(str(test_file2)), env=env)

        # _test.py suffix
        test_file3 = tmp_path / "app_test.py"
        test_file3.write_text("pass\n")
        run_bash_hook("bandit_check.sh", edit_payload(str(test_file3)), env=env)

        log = _read_log(log_file)
        assert log == "", "bandit should skip all test file patterns"

    def test_skips_non_py(self, fake_tool_env, tmp_path):
        env, log_file = fake_tool_env
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        run_bash_hook("bandit_check.sh", edit_payload(str(rs_file)), env=env)

        log = _read_log(log_file)
        assert log == "", "bandit should skip non-.py files"


# ── ruff_lint.sh ────────────────────────────────────────────────────────────


class TestRuffLint:
    """ruff_lint.sh: lint-fix changed .py files at session end."""

    def test_runs_on_changed_files(self, fake_tool_env, tmp_path):
        """When there are changed .py files, ruff check --fix should run."""
        env, log_file = fake_tool_env
        _init_git_repo(tmp_path)

        # Create and commit a .py file, then modify it so it shows in git diff
        py_file = tmp_path / "app.py"
        py_file.write_text("x = 1\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "app.py"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add app"],
            check=True,
            capture_output=True,
        )
        py_file.write_text("x = 2\n")  # Now it's a modified file

        # ruff_lint.sh takes no meaningful JSON input; it reads git state
        run_bash_hook("ruff_lint.sh", {}, env=env, cwd=str(tmp_path))

        log = _read_log(log_file)
        assert "uvx ruff check --fix" in log
        assert "app.py" in log, "ruff should have been passed the changed file"

    def test_noops_without_git(self, fake_tool_env, tmp_path):
        """Outside a git repo, the hook should silently exit."""
        env, log_file = fake_tool_env

        run_bash_hook("ruff_lint.sh", {}, env=env, cwd=str(tmp_path))

        log = _read_log(log_file)
        assert log == "", "ruff_lint should no-op outside a git repo"


# ── git_pull_on_start.sh ───────────────────────────────────────────────────


class TestGitPullOnStart:
    """git_pull_on_start.sh: auto-pull if safe, warn on dirty tree."""

    def test_skips_non_git(self, fake_tool_env, tmp_path):
        """In a non-git directory, the hook should exit silently."""
        env, log_file = fake_tool_env

        rc, stderr, stdout = run_bash_hook(
            "git_pull_on_start.sh", {}, env=env, cwd=str(tmp_path)
        )

        assert rc == 0
        assert stdout == "", "should produce no output in a non-git dir"

    def test_skips_dirty_tree(self, fake_tool_env, tmp_path):
        """With uncommitted changes, the hook should warn (not pull)."""
        env, log_file = fake_tool_env

        # Set up a local bare repo as the remote so git ls-remote
        # succeeds instantly without network access.
        bare_dir = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare_dir)],
            check=True,
            capture_output=True,
        )

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        _init_git_repo(work_dir)

        subprocess.run(
            ["git", "-C", str(work_dir), "remote", "add", "origin", str(bare_dir)],
            check=True,
            capture_output=True,
        )

        # Push to create the remote branch so tracking works
        branch = subprocess.run(
            ["git", "-C", str(work_dir), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(work_dir), "push", "-u", "origin", branch],
            check=True,
            capture_output=True,
        )

        # Make the tree dirty
        dirty_file = work_dir / "dirty.txt"
        dirty_file.write_text("uncommitted\n")
        subprocess.run(
            ["git", "-C", str(work_dir), "add", "dirty.txt"],
            check=True,
            capture_output=True,
        )

        rc, stderr, stdout = run_bash_hook(
            "git_pull_on_start.sh", {}, env=env, cwd=str(work_dir)
        )

        assert rc == 0
        assert "skipped auto-pull" in stdout, (
            "Hook should warn about dirty working tree"
        )


# ── check_dep_freshness.sh ──────────────────────────────────────────────────


class TestCheckDepFreshness:
    """check_dep_freshness.sh: warn if deps haven't been checked recently."""

    def test_warns_when_stale(self, fake_tool_env, tmp_path):
        """When .last_dep_check is older than threshold, emit a warning."""
        env, log_file = fake_tool_env
        _init_git_repo(tmp_path)

        # Create uv.lock so the hook doesn't bail early
        (tmp_path / "uv.lock").write_text("")

        # Create .last_dep_check with old mtime (60 days ago)
        marker = tmp_path / ".last_dep_check"
        marker.write_text("")
        old_time = time.time() - (60 * 86400)
        os.utime(str(marker), (old_time, old_time))

        rc, stderr, stdout = run_bash_hook(
            "check_dep_freshness.sh",
            {},
            env={**env, "DEP_CHECK_MAX_DAYS": "30"},
            cwd=str(tmp_path),
        )

        assert "WARNING" in stderr, "Hook should warn when deps are stale"

    def test_no_warning_when_fresh(self, fake_tool_env, tmp_path):
        """When .last_dep_check is recent, no warning should appear."""
        env, log_file = fake_tool_env
        _init_git_repo(tmp_path)

        (tmp_path / "uv.lock").write_text("")

        # Create a fresh .last_dep_check (now)
        marker = tmp_path / ".last_dep_check"
        marker.write_text("")

        rc, stderr, stdout = run_bash_hook(
            "check_dep_freshness.sh",
            {},
            env={**env, "DEP_CHECK_MAX_DAYS": "30"},
            cwd=str(tmp_path),
        )

        assert "WARNING" not in stderr, "Hook should not warn when deps are fresh"

    def test_handles_missing_marker(self, fake_tool_env, tmp_path):
        """Without .last_dep_check, falls back to uv.lock mtime."""
        env, log_file = fake_tool_env
        _init_git_repo(tmp_path)

        # Create uv.lock with old mtime — no .last_dep_check exists
        lock = tmp_path / "uv.lock"
        lock.write_text("")
        old_time = time.time() - (60 * 86400)
        os.utime(str(lock), (old_time, old_time))

        rc, stderr, stdout = run_bash_hook(
            "check_dep_freshness.sh",
            {},
            env={**env, "DEP_CHECK_MAX_DAYS": "30"},
            cwd=str(tmp_path),
        )

        # Should warn because uv.lock (the fallback) is old
        assert "WARNING" in stderr, "Hook should warn using uv.lock mtime as fallback"


# ── Hypothesis property tests ──────────────────────────────────────────────


class TestRuffFormatProperties:
    """Property-based tests: any .py file gets formatted, non-.py never does."""

    @given(name=st.from_regex(r"[a-z_][a-z0-9_]{1,20}", fullmatch=True))
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_any_py_filename_gets_formatted(self, fake_tool_env, name):
        """Any validly-named .py file should trigger ruff format."""
        env, log_file = fake_tool_env
        with tempfile.TemporaryDirectory() as td:
            py_file = os.path.join(td, f"{name}.py")
            with open(py_file, "w") as fh:
                fh.write("x = 1\n")

            run_bash_hook("ruff_format.sh", edit_payload(py_file), env=env)

            log = _read_log(log_file)
            assert "uvx ruff format" in log, f"ruff format should run for {name}.py"

            # Clear log for next example
            if log_file.exists():
                log_file.write_text("")

    @given(
        ext=st.sampled_from(
            [".txt", ".js", ".rs", ".md", ".toml", ".yaml", ".json", ".sh"]
        )
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_py_never_formatted(self, fake_tool_env, ext):
        """Non-.py files should never trigger ruff format."""
        env, log_file = fake_tool_env
        with tempfile.TemporaryDirectory() as td:
            filepath = os.path.join(td, f"file{ext}")
            with open(filepath, "w") as fh:
                fh.write("content\n")

            run_bash_hook("ruff_format.sh", edit_payload(filepath), env=env)

            log = _read_log(log_file)
            assert log == "", f"ruff format should NOT run for {ext} files"

            # Clear log for next example
            if log_file.exists():
                log_file.write_text("")
