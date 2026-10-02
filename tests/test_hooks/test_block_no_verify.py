"""Tests for block_no_verify.py: block any Bash command containing --no-verify,
`git commit -n`, or PRECOMMIT_NO_BLOCK=1 (the pre-commit hook's human-only bypass).

--no-verify skips git-native hooks (including the pre-commit secret backstop),
so it must be caught BEFORE git runs — in this Claude Code PreToolUse hook.
"""

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
        code, stderr, _ = run_hook(HOOK, _payload("PRECOMMIT_NO_BLOCK=1 git commit -m x"))
        assert code == 2
        assert "PRECOMMIT_NO_BLOCK" in stderr

    def test_env_command_form_is_blocked(self) -> None:
        code, _, _ = run_hook(HOOK, _payload("env PRECOMMIT_NO_BLOCK=1 git commit -m x"))
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
        code, stderr, _ = run_hook(HOOK, _payload("PRECOMMIT_NO_BLOCK=0 git commit -m x"))
        assert code == 0
        assert stderr.strip() == ""

    def test_bare_mention_is_allowed(self) -> None:
        """Prose/grep mentions of the knob (no `=1`) are not a bypass."""
        code, _, _ = run_hook(HOOK, _payload("grep -rn PRECOMMIT_NO_BLOCK githooks/"))
        assert code == 0
