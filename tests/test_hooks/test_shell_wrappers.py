"""Gate-logic tests for the wired shell hooks (ruff_format, ruff_lint,
git_pull_on_start, check_dep_freshness) plus unit tests for the shared
inject_tool_findings.py parsers and the batch_checks.sh Stop hook.

The wired shell hooks are thin wrappers that parse JSON, extract file paths,
filter by extension/location, and delegate to external tools (ruff, git). We
verify the GATE logic — does the hook correctly decide whether to invoke the
tool? — not the external tool's behavior.

(The old per-file pyright_check.sh / bandit_check.sh wrappers were removed
2026-07-17 — pyright/bandit/semgrep now run through the Stop hook batch_checks.sh
→ inject_tool_findings.py --batch. Their parser logic is still covered here by
TestParsePyrightNoStopgap / TestBatchParsePyright.)

Approach: create fake tool stubs that log invocations, inject them into PATH,
run the hook, then check the log to see whether/how the tool was called.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_bash_hook

sys.path.insert(0, str(HOOKS_DIR))


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

    def test_skips_collab_project_file(self, fake_tool_env, tmp_path):
        """Files under a collab/ path component are never auto-formatted
        (global rule: don't reformat code you don't own). Mirrors
        project_health_check.detect_category(): 'collab' in path parts."""
        env, log_file = fake_tool_env
        proj = tmp_path / "collab" / "team_project"
        proj.mkdir(parents=True)
        py_file = proj / "module.py"
        py_file.write_text("x=1\n")

        rc, stderr, stdout = run_bash_hook(
            "ruff_format.sh", edit_payload(str(py_file)), env=env
        )

        log = _read_log(log_file)
        assert log == "", "ruff should not be invoked for collab-project files"
        assert rc == 0

    def test_formats_solo_project_file(self, fake_tool_env, tmp_path):
        """Files under solo/ (or any non-collab path) keep current behavior."""
        env, log_file = fake_tool_env
        proj = tmp_path / "solo" / "my_project"
        proj.mkdir(parents=True)
        py_file = proj / "module.py"
        py_file.write_text("x=1\n")

        run_bash_hook("ruff_format.sh", edit_payload(str(py_file)), env=env)

        log = _read_log(log_file)
        assert "uvx ruff format" in log
        assert str(py_file) in log

    def test_collab_substring_dir_still_formatted(self, fake_tool_env, tmp_path):
        """A dir merely containing 'collab' as a substring is NOT a collab
        project — only an exact path component gates, matching detect_category."""
        env, log_file = fake_tool_env
        proj = tmp_path / "collaboration_tools"
        proj.mkdir()
        py_file = proj / "module.py"
        py_file.write_text("x=1\n")

        run_bash_hook("ruff_format.sh", edit_payload(str(py_file)), env=env)

        log = _read_log(log_file)
        assert "uvx ruff format" in log


# ── ruff_lint.sh ────────────────────────────────────────────────────────────


class TestRuffLint:
    """ruff_lint.sh: lint-fix changed .py files at session end."""

    def test_runs_on_changed_files(self, fake_tool_env, tmp_path):
        """When there are changed .py files, .venv/bin/ruff check --fix should run."""
        env, log_file = fake_tool_env
        _init_git_repo(tmp_path)

        # ruff_lint.sh calls .venv/bin/ruff directly, not uvx
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        ruff_stub = venv_bin / "ruff"
        ruff_stub.write_text(
            f'#!/usr/bin/env bash\necho "ruff $@" >> {log_file}\nexit 0\n'
        )
        ruff_stub.chmod(ruff_stub.stat().st_mode | stat.S_IEXEC)

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
        # T20 (flake8-print) added 2026-08-03 to close the print-vs-logging gap
        assert "ruff check --extend-select T20 --fix" in log
        assert "app.py" in log, "ruff should have been passed the changed file"

    def test_noops_without_git(self, fake_tool_env, tmp_path):
        """Outside a git repo, the hook should silently exit."""
        env, log_file = fake_tool_env

        run_bash_hook("ruff_lint.sh", {}, env=env, cwd=str(tmp_path))

        log = _read_log(log_file)
        assert log == "", "ruff_lint should no-op outside a git repo"

    def _setup_repo_with_real_ruff(self, tmp_path):
        """Init a repo whose .venv/bin/ruff is the REAL ruff, not a stub.

        E722 blocking depends on real ruff output (file:line + rule code),
        so a logging stub would only test our own assumptions.
        """
        _init_git_repo(tmp_path)
        real_ruff = shutil.which("ruff")
        if real_ruff is None:
            pytest.skip("real ruff not found on PATH")
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "ruff").symlink_to(real_ruff)

    def _commit_then_rewrite(self, tmp_path, content):
        """Commit a clean app.py, then rewrite it so it shows in git diff."""
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
        py_file.write_text(content)

    def test_blocks_on_bare_except(self, fake_tool_env, tmp_path):
        """A remaining bare except (E722) blocks with exit 2 (Tier-2 #8)."""
        env, _ = fake_tool_env
        self._setup_repo_with_real_ruff(tmp_path)
        self._commit_then_rewrite(tmp_path, "try:\n    x = 1\nexcept:\n    pass\n")

        rc, stderr, stdout = run_bash_hook(
            "ruff_lint.sh", {}, env=env, cwd=str(tmp_path)
        )

        assert rc == 2, f"bare except should block, got rc={rc} (stderr: {stderr})"
        assert "E722" in stderr
        assert "app.py" in stderr, "block message should name the offending file"

    def test_clean_file_not_blocked(self, fake_tool_env, tmp_path):
        """A changed file with no E722 violation exits 0 (no false block)."""
        env, _ = fake_tool_env
        self._setup_repo_with_real_ruff(tmp_path)
        self._commit_then_rewrite(
            tmp_path, "try:\n    x = 1\nexcept ValueError:\n    pass\n"
        )

        rc, stderr, stdout = run_bash_hook(
            "ruff_lint.sh", {}, env=env, cwd=str(tmp_path)
        )

        assert rc == 0, f"clean file should not block (stderr: {stderr})"


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
    """check_dep_freshness.sh: warn if deps haven't been checked recently.

    Intent (TESTING.md, "SESSION-STDOUT"): when deps are stale the hook must
    surface a warning through the SessionStart stdout channel as a JSON
    ``systemMessage`` — the same convention git_pull_on_start.sh uses. Probes
    proved stderr-on-exit-0 is invisible, so a warning written only to stderr
    reaches nobody. These tests assert the *intended* channel, not whatever the
    hook happens to do; the earlier versions asserted ``"WARNING" in stderr``,
    which codified the bug fixed during wiring test 1.1.
    """

    @staticmethod
    def _run_stale_check(tmp_path, env, *, marker_age_days=None, uvlock_age_days=None):
        """Set up a temp repo and run the hook. Ages in days; None = don't create.

        Returns (rc, stderr, stdout).
        """
        _init_git_repo(tmp_path)
        if uvlock_age_days is not None:
            lock = tmp_path / "uv.lock"
            lock.write_text("")
            t = time.time() - (uvlock_age_days * 86400)
            os.utime(str(lock), (t, t))
        if marker_age_days is not None:
            marker = tmp_path / ".last_dep_check"
            marker.write_text("")
            t = time.time() - (marker_age_days * 86400)
            os.utime(str(marker), (t, t))
        return run_bash_hook(
            "check_dep_freshness.sh",
            {},
            env={**env, "DEP_CHECK_MAX_DAYS": "30"},
            cwd=str(tmp_path),
        )

    def test_stale_marker_warns_via_stdout_not_stderr(self, fake_tool_env, tmp_path):
        """Stale marker → warning delivered as a stdout systemMessage.

        This is the regression guard for wiring test 1.1: the warning MUST be on
        stdout (the working SessionStart channel), and MUST NOT be confined to
        stderr (the old, invisible channel).
        """
        env, _ = fake_tool_env
        rc, stderr, stdout = self._run_stale_check(
            tmp_path, env, marker_age_days=60, uvlock_age_days=1
        )

        assert rc == 0
        payload = json.loads(stdout)  # stdout must be valid SessionStart JSON
        message = payload["systemMessage"]
        assert "depend" in message.lower(), (
            f"systemMessage should mention dependencies, got: {message!r}"
        )
        # Old behavior gone: the user-facing warning is not stranded on stderr.
        assert message not in stderr

    def test_fresh_marker_produces_no_warning(self, fake_tool_env, tmp_path):
        """Recent marker → no warning emitted on stdout at all."""
        env, _ = fake_tool_env
        rc, stderr, stdout = self._run_stale_check(
            tmp_path, env, marker_age_days=0, uvlock_age_days=1
        )

        assert rc == 0
        assert stdout.strip() == "", (
            f"Fresh deps should emit no stdout warning, got: {stdout!r}"
        )

    def test_missing_marker_stale_uvlock_warns_via_fallback(
        self, fake_tool_env, tmp_path
    ):
        """No marker + old uv.lock → warning, using uv.lock mtime as fallback."""
        env, _ = fake_tool_env
        rc, stderr, stdout = self._run_stale_check(
            tmp_path, env, marker_age_days=None, uvlock_age_days=60
        )

        assert rc == 0
        payload = json.loads(stdout)
        assert "depend" in payload["systemMessage"].lower()

    def test_missing_marker_fresh_uvlock_no_crash_no_warning(
        self, fake_tool_env, tmp_path
    ):
        """No marker + fresh uv.lock → graceful: exit 0, no false warning.

        This is wiring test 1.3: a missing marker must fall back to uv.lock
        without crashing or emitting a spurious staleness warning.
        """
        env, _ = fake_tool_env
        rc, stderr, stdout = self._run_stale_check(
            tmp_path, env, marker_age_days=None, uvlock_age_days=1
        )

        assert rc == 0, "Missing marker must not crash the hook"
        assert stdout.strip() == "", (
            f"Fresh uv.lock fallback should emit no warning, got: {stdout!r}"
        )

    def test_no_uvlock_exits_silently(self, fake_tool_env, tmp_path):
        """No uv.lock at all → hook is a no-op (only applies to uv projects)."""
        env, _ = fake_tool_env
        rc, stderr, stdout = self._run_stale_check(
            tmp_path, env, marker_age_days=None, uvlock_age_days=None
        )

        assert rc == 0
        assert stdout.strip() == "", "Non-uv projects should get no output"


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


# ── Pyright venv discovery (Addendum A) ──────────────────────────────────


class TestParsePyrightNoStopgap:
    """_parse_pyright should NOT filter reportMissingImports after venv discovery fix."""

    @pytest.fixture(autouse=True)
    def _import_parser(self):
        from inject_tool_findings import _parse_pyright

        self.parse = _parse_pyright

    def test_includes_reportMissingImports(self):
        """reportMissingImports findings should no longer be filtered out."""
        output = (
            '/tmp/test.py:1:8 - error: Import "requests" '
            "could not be resolved (reportMissingImports)\n"
            '/tmp/test.py:3:10 - error: Type "str" is not assignable '
            'to declared type "int" (reportAssignmentType)\n'
        )
        findings = self.parse(output, "/tmp/test.py")
        assert len(findings) == 2
        assert any("reportMissingImports" in msg for _, msg in findings)
        assert any("reportAssignmentType" in msg for _, msg in findings)


class TestFindVenvPython:
    """_find_venv_python walks up from the file to locate .venv/bin/python."""

    @pytest.fixture(autouse=True)
    def _import_fn(self):
        from inject_tool_findings import _find_venv_python

        self.find = _find_venv_python

    def test_finds_venv_in_same_dir(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/sh\n")
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IEXEC)

        src = tmp_path / "app.py"
        src.write_text("x = 1\n")

        result = self.find(str(src))
        assert result == str(fake_python)

    def test_finds_venv_in_parent_dir(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/sh\n")
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IEXEC)

        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        src = subdir / "module.py"
        src.write_text("x = 1\n")

        result = self.find(str(src))
        assert result == str(fake_python)

    def test_returns_none_when_no_venv(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text("x = 1\n")

        result = self.find(str(src))
        assert result is None


class TestBatchParsePyright:
    """_batch_parse_pyright must key findings by the un-indented file path.

    Pyright indents each diagnostic line by two spaces. If the path capture
    group keeps that indent, the findings dict is keyed "  /abs/path" while
    batch_main() looks up the un-indented "/abs/path" — so every pyright
    finding is silently dropped. Batch mode is the only mode the Stop hook
    uses, so this made # HOOK:PYRIGHT: injection fully non-functional.
    """

    @pytest.fixture(autouse=True)
    def _import(self):
        from inject_tool_findings import _batch_parse_pyright

        self.parse = _batch_parse_pyright

    def test_path_key_has_no_leading_whitespace(self):
        output = (
            "/abs/path/app.py\n"
            '  /abs/path/app.py:6:12 - error: Type "Literal[42]" is not '
            'assignable to return type "str"\n'
            '  /abs/path/app.py:11:12 - error: Operator "+" not supported '
            "(reportOperatorIssue)\n"
            "2 errors, 0 warnings, 0 informations\n"
        )
        findings = self.parse(output)
        assert "/abs/path/app.py" in findings
        assert "  /abs/path/app.py" not in findings
        assert [ln for ln, _ in findings["/abs/path/app.py"]] == [6, 11]


class TestBatchChecksStdinDrain:
    """batch_checks.sh must run docstring+seed checks on EVERY changed .py file.

    check_docstrings.py/check_random_seeds.py run inside a `while read` loop
    fed by a stdin pipe; both call log_hook(), which does sys.stdin.read().
    Without a </dev/null redirect the first child drains the file list and the
    loop stops after one file. This is the regression guard: two changed files,
    both must be annotated.
    """

    def test_docstring_and_seed_checks_run_on_all_files(self, fake_tool_env, tmp_path):
        env, _ = (
            fake_tool_env  # fakes uvx/pyright/bandit so only docstring+seed do real work
        )
        _init_git_repo(tmp_path)

        # File 1 (sorts first): triggers the docstring check only.
        doc_file = tmp_path / "a_needs_docstring.py"
        doc_file.write_text(
            "def compute(x, y, z):\n    a = x + y\n    b = a * z\n    return b\n"
        )
        # File 2 (sorts second): triggers the seed check only. Before the fix,
        # the stdin drain meant this file was never reached.
        seed_file = tmp_path / "b_needs_seed.py"
        seed_file.write_text(
            "import random\n\n\ndef pick(items):\n    chosen = random.choice(items)\n"
            "    return chosen\n"
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "add",
                "a_needs_docstring.py",
                "b_needs_seed.py",
            ],
            check=True,
            capture_output=True,
        )

        run_bash_hook("batch_checks.sh", {}, env=env, cwd=str(tmp_path), timeout=30)

        assert "# HOOK:DOCSTRING:" in doc_file.read_text(), (
            "first file should be annotated"
        )
        assert "# HOOK:SEED:" in seed_file.read_text(), (
            "second file must also be annotated — stdin drain regression"
        )
