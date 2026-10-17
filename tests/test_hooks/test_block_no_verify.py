"""Tests for block_no_verify.py: block any Bash command containing --no-verify,
`git commit -n`, or PRECOMMIT_NO_BLOCK=1 (the pre-commit hook's human-only bypass).

--no-verify skips git-native hooks (including the pre-commit secret backstop),
so it must be caught BEFORE git runs — in this Claude Code PreToolUse hook.
"""

import pytest

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_no_verify.py"


def _payload(command: str) -> dict:
    return {"tool_input": {"command": command}}


class TestBlockNoVerify:
    def test_git_commit_no_verify_exits_2(self) -> None:
        code, stderr, _ = run_hook(HOOK, _payload("git commit --no-verify -m x"))
        assert code == 2
        assert "BLOCKED" in stderr

    def test_normal_git_commit_exits_0(self) -> None:
        code, stderr, _ = run_hook(HOOK, _payload("git commit -m x"))
        assert code == 0
        assert stderr.strip() == ""  # silent on pass — no alert-fatigue noise

    def test_git_C_commit_no_verify_is_blocked(self) -> None:
        """The bypass class: --no-verify anywhere in the command is caught,
        including the `git -C .` form that dodges prefix matchers."""
        code, stderr, _ = run_hook(HOOK, _payload("git -C . commit --no-verify -m x"))
        assert code == 2
        assert "BLOCKED" in stderr

    def test_no_verify_in_compound_command_is_blocked(self) -> None:
        code, _, _ = run_hook(HOOK, _payload("echo hi && git commit --no-verify -m x"))
        assert code == 2

    def test_non_git_command_exits_0(self) -> None:
        code, _, _ = run_hook(HOOK, _payload("ls -la"))
        assert code == 0

    def test_git_commit_short_n_exits_2(self) -> None:
        """`-n` is the short form of --no-verify and also skips git hooks."""
        code, stderr, _ = run_hook(HOOK, _payload("git commit -n -m x"))
        assert code == 2
        assert "BLOCKED" in stderr

    def test_git_commit_bundled_nm_exits_2(self) -> None:
        """Bundled short flags like -nm hide the -n; still a skip."""
        code, _, _ = run_hook(HOOK, _payload("git commit -nm x"))
        assert code == 2

    def test_git_C_commit_short_n_is_blocked(self) -> None:
        code, _, _ = run_hook(HOOK, _payload("git -C . commit -n -m x"))
        assert code == 2

    def test_unrelated_n_flag_after_commit_is_allowed(self) -> None:
        """False-positive guard: a -n in a chained NON-commit command must not
        trip the block — the -n search is scoped to the commit's own args."""
        code, _, _ = run_hook(HOOK, _payload("git commit -m x && sort -n file"))
        assert code == 0

    def test_bare_sort_n_exits_0(self) -> None:
        code, _, _ = run_hook(HOOK, _payload("sort -n file"))
        assert code == 0


class TestBlockPrecommitNoBlockKnob:
    """PRECOMMIT_NO_BLOCK=1 demotes the global pre-commit hook's blocks to
    warnings. It is the human's terminal escape hatch; from a Claude Code
    session it is --no-verify with extra steps, so it is blocked here
    (2026-10-02)."""

    def test_env_prefix_form_is_blocked(self) -> None:
        code, stderr, _ = run_hook(
            HOOK, _payload("PRECOMMIT_NO_BLOCK=1 git commit -m x")
        )
        assert code == 2
        assert "PRECOMMIT_NO_BLOCK" in stderr

    def test_env_command_form_is_blocked(self) -> None:
        code, _, _ = run_hook(
            HOOK, _payload("env PRECOMMIT_NO_BLOCK=1 git commit -m x")
        )
        assert code == 2

    def test_export_then_commit_is_blocked(self) -> None:
        code, _, _ = run_hook(
            HOOK, _payload("export PRECOMMIT_NO_BLOCK=1 && git commit -m x")
        )
        assert code == 2

    def test_quoted_value_is_blocked(self) -> None:
        code, _, _ = run_hook(HOOK, _payload('PRECOMMIT_NO_BLOCK="1" git commit -m x'))
        assert code == 2

    def test_zero_value_is_allowed(self) -> None:
        """=0 is the hook's default behavior spelled out — not a bypass."""
        code, stderr, _ = run_hook(
            HOOK, _payload("PRECOMMIT_NO_BLOCK=0 git commit -m x")
        )
        assert code == 0
        assert stderr.strip() == ""

    def test_bare_mention_is_allowed(self) -> None:
        """Prose/grep mentions of the knob (no `=1`) are not a bypass."""
        code, _, _ = run_hook(HOOK, _payload("grep -rn PRECOMMIT_NO_BLOCK githooks/"))
        assert code == 0


class TestPhase3EscapeHatches:
    """Phase 3: the pre-commit is the only secret layer, so the remaining
    documented ways a session could switch it off are blocked too."""

    def test_second_chained_commit_with_n_blocks(self) -> None:
        """AC-NV-01: every `git commit` in the command is checked, not the first."""
        code, stderr, _ = run_hook(
            HOOK, _payload("git commit -m a && git commit --amend -n --no-edit")
        )
        assert code == 2
        assert "pip-audit" in stderr, "the layer list names the legs that exist"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c core.hooksPath=/dev/null commit -m x",
            "git -c core.hookspath=/dev/null commit -m x",
            "git config core.hooksPath /dev/null",
            "git config --global core.hooksPath /tmp/x",
        ],
    )
    def test_hookspath_override_blocks(self, cmd: str) -> None:
        """AC-NV-02."""
        code, stderr, _ = run_hook(HOOK, _payload(cmd))
        assert code == 2
        assert "core.hooksPath" in stderr

    @pytest.mark.parametrize(
        "cmd",
        [
            "PRECOMMIT_LEGS= git commit -m x",
            "PRECOMMIT_LEGS=ruff git commit -m x",
            "export PRECOMMIT_LEGS=",
        ],
    )
    def test_precommit_legs_assignment_blocks(self, cmd: str) -> None:
        """AC-NV-03."""
        code, stderr, _ = run_hook(HOOK, _payload(cmd))
        assert code == 2
        assert "PRECOMMIT_LEGS" in stderr

    @pytest.mark.parametrize(
        "cmd",
        [
            "git config --get core.hooksPath",
            "git config --get core.hooksPath && echo ok",
            "git config --get core.hooksPath --default none",  # a flag is not a value
            "git config --unset core.hooksPath",
            'echo "$PRECOMMIT_LEGS"',
            "git commit -m a && sort -n f",  # regression guard: allowed before too
        ],
    )
    def test_lookalikes_allowed(self, cmd: str) -> None:
        """AC-NV-04."""
        code, stderr, _ = run_hook(HOOK, _payload(cmd))
        assert code == 0
        assert stderr.strip() == ""
