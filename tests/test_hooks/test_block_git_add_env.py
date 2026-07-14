"""Tests for block_git_add_env.py hook.

Verifies that the hook blocks staging `.env` files (exit 2) and bulk
`git add` operations (exit 2), while allowing template files like
`.env.example` and specific non-env files (exit 0).

Exit codes: 2 = blocked, 0 = allowed.

Spec conflicts (hook regex limitations, not tested):
  - `git add -v .` / `git add --verbose .` / `git add -n .`: flags between
    `add` and `.` break bulk_add_re, which requires `.`/`--all`/`-A`
    immediately after `git add `.
  - `git -C /other/project add .` / `git -C /tmp add -A`: `-C /path` between
    `git` and `add` breaks bulk_add_re.
  - `git add -u` / `git add --update`: `-u`/`--update` not in the regex
    alternation for bulk_add_re.
"""

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_git_add_env.py"


class TestBlockGitAddEnvExamples:
    """Explicit examples from the test matrix."""

    def test_git_add_env_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add .env"))
        assert code == 2

    def test_git_add_env_local_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.local"))
        assert code == 2

    def test_git_add_env_production_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.production"))
        assert code == 2

    def test_git_add_dot_blocked(self, bash_payload):
        """Bulk `git add .` blocked because it would stage any .env files."""
        code, _, _ = run_hook(HOOK, bash_payload("git add ."))
        assert code == 2

    def test_git_add_dash_A_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add -A"))
        assert code == 2

    def test_git_add_all_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add --all"))
        assert code == 2

    def test_git_add_env_example_allowed(self, bash_payload):
        """Template files (.env.example) are explicitly allowed."""
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.example"))
        assert code == 0

    def test_git_add_env_dist_allowed(self, bash_payload):
        """Template file .env.dist is explicitly allowed."""
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.dist"))
        assert code == 0

    def test_git_add_multiple_templates_allowed(self, bash_payload):
        """All-template commands are allowed."""
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.sample .env.template"))
        assert code == 0

    def test_git_add_specific_file_allowed(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git add src/main.py"))
        assert code == 0

    def test_git_add_mixed_template_and_env_blocked(self, bash_payload):
        """A mix of template and non-template .env files is blocked."""
        code, _, _ = run_hook(HOOK, bash_payload("git add .env.example .env.local"))
        assert code == 2


class TestBlockGitAddEnvScopeGuard:
    """Regression: the hook must be INERT on Bash input outside its scope.

    Claude Code 2.1.201 stopped honoring the settings `if: Bash(git add*)`
    gate, so the hook fires on every Bash command. The in-body scope guard
    must make it a no-op (exit 0) for anything that is not a real `git add`,
    *regardless* of whether the command text mentions a bare `.env` token.
    These tests exercise the hook BODY (feed a payload, assert exit code),
    not the settings gate. Durable version of TESTING.md 2.16/2.21/2.23.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "echo hello",
            "git status",
            # Non-git command that mentions a bare `.env` token: without the
            # scope guard, env_file_re would false-block this.
            "cp x .env.bak && ./y",
            # The word "add" inside a commit MESSAGE must not look like `git add`.
            "git commit -m 'add feature'",
        ],
        ids=["echo", "git-status", "non-git-mentions-env", "commit-msg-says-add"],
    )
    def test_out_of_scope_is_inert(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected inert exit 0 for out-of-scope: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add .env",
            "git add .env.local",
            "git add .",
            "git -C /tmp/r add .",
        ],
        ids=["add-env", "add-env-local", "add-dot", "dash-C-add-dot"],
    )
    def test_in_scope_git_add_blocks(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected blocked exit 2 for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add src/clean_module.py",
            "git add .env.example",
        ],
        ids=["specific-file", "template"],
    )
    def test_in_scope_safe_git_add_allowed(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected allowed exit 0 for: {cmd!r}"


class TestBlockGitAddEnvBulkPatterns:
    """Parametrized tests for bulk add and specific file patterns."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add .",
            "git add -A",
            "git add --all",
            "git add . --verbose",
            "cd /project && git add .",
            "git stash && git add -A",
            "git add . -- ':!.env'",
        ],
        ids=[
            "standard-dot",
            "standard-dash-A",
            "standard-all",
            "dot-before-flags",
            "compound-cd-dot",
            "compound-stash-dash-A",
            "exclusion-syntax-fail-closed",
        ],
    )
    def test_bulk_add_blocked(self, bash_payload, cmd):
        """Bulk git add patterns are blocked.

        The exclusion-syntax case (`git add . -- ':!.env'`) is intentionally
        fail-closed: bulk_add_re catches `git add .` and env_file_re catches
        the `.env` in the pathspec exclusion. The hook errs on the side of
        caution rather than trying to parse git pathspec semantics.
        """
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected exit 2 (blocked) for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add src/main.py",
            "git add src/main.py src/utils.py",
            "git add README.md",
            "git add -p src/main.py",
        ],
        ids=[
            "specific-file",
            "multiple-specific-files",
            "readme",
            "interactive-patch",
        ],
    )
    def test_specific_files_allowed(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected exit 0 (allowed) for: {cmd!r}"


class TestBlockGitAddEnvProperties:
    """Hypothesis property tests for broader coverage."""

    @given(
        suffix=st.from_regex(r"[a-z][a-z0-9_-]{0,20}", fullmatch=True),
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_env_variants_always_blocked(self, bash_payload, suffix):
        """Any .env.{suffix} that is not a template should be blocked."""
        assume(suffix not in ("example", "sample", "template", "dist"))
        cmd = f"git add .env.{suffix}"
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected blocked for: {cmd!r}"

    @given(
        prefix=st.sampled_from(["", "cd /project && "]),
        env_suffix=st.from_regex(r"[a-z][a-z0-9_-]{0,20}", fullmatch=True),
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_env_variants_with_prefixes_blocked(self, bash_payload, prefix, env_suffix):
        """env_file_re searches the full command string, so .env.{suffix}
        is caught regardless of command prefix structure."""
        assume(env_suffix not in ("example", "sample", "template", "dist"))
        cmd = f"{prefix}git add .env.{env_suffix}"
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected blocked for: {cmd!r}"

    @given(
        safe_file=st.from_regex(r"[a-z][a-z0-9_/]{0,30}\.[a-z]{1,4}", fullmatch=True),
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_env_specific_files_allowed(self, bash_payload, safe_file):
        """Specific non-.env files should always be allowed."""
        assume(".env" not in safe_file)
        cmd = f"git add {safe_file}"
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected allowed for: {cmd!r}"


class TestBlockGitAddEnvKnownBugs:
    """Cases where block_git_add_env.py SHOULD block bulk adds but doesn't (Step 0d)."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add -v .",
            "git add --verbose .",
            "git add -n .",
            "git add -f .",
            "git add --force .",
            "git add --intent-to-add .",
        ],
        ids=["dash-v", "verbose", "dash-n", "dash-f", "force", "intent-to-add"],
    )
    def test_git_add_flag_dot_should_block(self, bash_payload, cmd):
        """Step 0d: flags between 'add' and '.' should not prevent bulk-add detection."""
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2

    def test_git_C_path_add_dot_should_block(self, bash_payload):
        """Step 0d: git -C /path add . should still be caught as bulk add."""
        code, _, _ = run_hook(HOOK, bash_payload("git -C /other/project add ."))
        assert code == 2

    def test_git_C_tmp_add_A_should_block(self, bash_payload):
        """Step 0d: git -C /tmp add -A should still be caught as bulk add."""
        code, _, _ = run_hook(HOOK, bash_payload("git -C /tmp add -A"))
        assert code == 2

    def test_git_add_u_should_block(self, bash_payload):
        """Step 0d: git add -u stages all tracked modified files -- equivalent to bulk add."""
        code, _, _ = run_hook(HOOK, bash_payload("git add -u"))
        assert code == 2

    def test_git_add_update_should_block(self, bash_payload):
        """Step 0d: git add --update stages all tracked modified files -- equivalent to bulk add."""
        code, _, _ = run_hook(HOOK, bash_payload("git add --update"))
        assert code == 2
