"""Tests for block_bare_pip.py hook.

Verifies that the hook blocks bare `pip install` commands (exit 2) while
allowing `uv pip install`, path-qualified pip, and non-pip commands (exit 0).
Uses explicit examples, parametrized shell structures, and hypothesis
property tests to cover the regex's lookbehind behaviour against real
shell constructs.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_bare_pip.py"


class TestBlockBarePipExamples:
    """Explicit examples from the test matrix."""

    def test_bare_pip_install_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("pip install requests"))
        assert code == 2

    def test_uv_pip_install_allowed(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("uv pip install requests"))
        assert code == 0

    def test_venv_qualified_pip_allowed(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("./venv/bin/pip install requests"))
        assert code == 0

    def test_python_m_pip_blocked(self, bash_payload):
        """python -m pip is the same global install, matched explicitly."""
        code, _, _ = run_hook(HOOK, bash_payload("python -m pip install requests"))
        assert code == 2

    def test_python3_m_pip_blocked(self, bash_payload):
        """python3 -m pip is the same global install, matched explicitly."""
        code, _, _ = run_hook(HOOK, bash_payload("python3 -m pip install requests"))
        assert code == 2

    def test_compound_command_pip_blocked(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("cd /tmp && pip install requests"))
        assert code == 2

    def test_non_pip_command_allowed(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("git status"))
        assert code == 0


class TestBlockBarePipShellStructures:
    """Parametrized tests for shell command patterns."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "cd /tmp && pip install requests",
            "source .venv/bin/activate; pip install requests",
            "export FOO=bar && pip install requests",
            "true || pip install requests",
            "PYTHONPATH=/x pip install requests",
            "CC=gcc pip install numpy",
            "PIP_INDEX_URL=https://x pip install requests",
            "pip install requests 2>&1 | tee install.log",
            "pip install requests > /dev/null",
            "yes | pip install requests",
            "(pip install requests)",
            "{ pip install requests; }",
            'pip install "requests[security]"',
            "pip install 'flask[async]'",
            "pip install requests==2.31.0",
            "pip install -r requirements.txt",
            "pip install --upgrade pip",
            "pip install -e .",
            "pip install \\\n  requests",
            "pip install requests  # needed for API",
            "sudo pip install requests",
        ],
        ids=[
            "compound-and",
            "source-semicolon",
            "export-and",
            "or-chain",
            "env-var-PYTHONPATH",
            "env-var-CC",
            "env-var-PIP_INDEX_URL",
            "pipe-to-tee",
            "redirect-stdout",
            "pipe-from-yes",
            "subshell-parens",
            "brace-group",
            "extras-double-quote",
            "extras-single-quote",
            "pinned-version",
            "dash-r-requirements",
            "upgrade-pip",
            "editable-install",
            "line-continuation",
            "trailing-comment",
            "sudo-prefix",
        ],
    )
    def test_pip_in_complex_command_blocked(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected exit 2 (blocked) for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "uv pip install requests",
            "cd /project && uv pip install requests",
            "./venv/bin/pip install requests",
            "/usr/local/bin/pip install requests",
            "./.venv/bin/pip install requests",
            "pip3 --version",
            "snipped install something",
            "recipe pip-boy install",
            "git status",
            "uv add requests",
            "python main.py",
        ],
        ids=[
            "uv-pip",
            "compound-uv-pip",
            "dot-venv-bin-pip",
            "absolute-path-pip",
            "dot-slash-venv-pip",
            "pip3-version-no-install",
            "unrelated-install",
            "pip-substring-no-install",
            "git-no-pip",
            "uv-add",
            "python-no-pip",
        ],
    )
    def test_non_bare_pip_allowed(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected exit 0 (allowed) for: {cmd!r}"


class TestBlockBarePipCommandPosition:
    """Command-position anchoring: pip must be a command, not a mention.

    The old regex matched `pip install` anywhere in the string, so any command
    that merely *talked about* pip (echo, grep, a comment, a commit message)
    was blocked. Same bug, same fix, as pip_audit_check/guard.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            'echo "pip install requests"',
            "echo 'run pip install requests first'",
            "grep -n 'pip install' setup.log",
            "# pip install requests",
            'git commit -m "document why pip install is blocked"',
            "printf '%s\\n' 'pip install foo' > notes.txt",
        ],
        ids=["echo-double", "echo-single", "grep", "comment", "commit-msg", "printf"],
    )
    def test_pip_mention_allowed(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected exit 0 (mention, not a command) for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "pip install requests",
            "cd /tmp && pip install requests",
            "cd /tmp; pip install requests",
            "make build; pip3 install requests",
            "false || pip install requests",
        ],
        ids=["bare", "and", "semicolon", "semicolon-pip3", "or"],
    )
    def test_pip_at_command_position_blocked(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected exit 2 (blocked) for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "uv pip install requests",
            "uv pip install -r requirements.txt",
            "cd /p && uv pip install requests",
            "uv add requests",
        ],
        ids=["uv-pip", "uv-pip-r", "compound", "uv-add"],
    )
    def test_uv_pip_allowed(self, bash_payload, cmd):
        """Hook policy (see its block message): `uv pip install` is the
        sanctioned pip-semantics escape hatch, so it must stay unblocked."""
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected exit 0 (uv-managed) for: {cmd!r}"


class TestBlockBarePipProperties:
    """Hypothesis property tests for broader coverage."""

    @given(pkg=st.from_regex(r"[a-z][a-z0-9_-]{0,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_bare_pip_always_blocked(self, bash_payload, pkg):
        code, _, _ = run_hook(HOOK, bash_payload(f"pip install {pkg}"))
        assert code == 2, f"pip install {pkg} should be blocked"

    @given(pkg=st.from_regex(r"[a-z][a-z0-9_-]{0,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_pip_always_allowed(self, bash_payload, pkg):
        code, _, _ = run_hook(HOOK, bash_payload(f"uv pip install {pkg}"))
        assert code == 0, f"uv pip install {pkg} should be allowed"

    @given(
        prefix=st.sampled_from(
            [
                "",
                "cd /tmp && ",
                "export X=1 && ",
                "source env/bin/activate; ",
                "PYTHONPATH=/x ",
                "sudo ",
                "true && ",
            ]
        ),
        suffix=st.sampled_from(
            [
                "",
                " > /dev/null",
                " 2>&1",
                " | tee log.txt",
                " && echo done",
                "  # comment",
            ]
        ),
        pkg=st.from_regex(r"[a-z][a-z0-9_-]{0,30}", fullmatch=True),
    )
    @settings(
        max_examples=300,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_bare_pip_blocked_regardless_of_context(
        self, bash_payload, prefix, suffix, pkg
    ):
        cmd = f"{prefix}pip install {pkg}{suffix}"
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Expected blocked for: {cmd!r}"

    @given(
        prefix=st.sampled_from(["uv ", "./venv/bin/", "/usr/bin/", ".venv/bin/"]),
        pkg=st.from_regex(r"[a-z][a-z0-9_-]{0,30}", fullmatch=True),
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_qualified_pip_always_allowed(self, bash_payload, prefix, pkg):
        cmd = f"{prefix}pip install {pkg}"
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Expected allowed for: {cmd!r}"


class TestBlockBarePipKnownBugs:
    """Cases where block_bare_pip.py has known bugs (Step 0d)."""

    def test_pip3_install_should_block(self, bash_payload):
        """Step 0d: pip3 is equally dangerous -- same global install behavior."""
        code, _, _ = run_hook(HOOK, bash_payload("pip3 install requests"))
        assert code == 2

    def test_containment_bypass_should_block(self, bash_payload):
        """Step 0d: 'uv pip install' in a string literal should not whitelist a real bare pip install."""
        code, _, _ = run_hook(
            HOOK, bash_payload('echo "uv pip install" && pip install foo')
        )
        assert code == 2

    @given(pkg=st.from_regex(r"[a-z][a-z0-9_-]{0,30}", fullmatch=True))
    @settings(
        max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_pip3_always_blocked(self, bash_payload, pkg):
        """pip3 install is equally dangerous regardless of package name."""
        code, _, _ = run_hook(HOOK, bash_payload(f"pip3 install {pkg}"))
        assert code == 2

    @pytest.mark.parametrize(
        "cmd",
        [
            "sudo pip3 install requests",
            "cd /tmp && pip3 install requests",
            "pip3 install -r requirements.txt",
            "PYTHONPATH=/x pip3 install numpy",
        ],
        ids=["sudo", "compound-and", "dash-r", "env-var-prefix"],
    )
    def test_pip3_in_shell_context_should_block(self, bash_payload, cmd):
        """pip3 in various shell structures should also be blocked."""
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2

    @pytest.mark.parametrize(
        "cmd",
        [
            "some-pip install foo",
            "auto-pip install bar",
            "my-pip install baz",
        ],
        ids=["some-pip", "auto-pip", "my-pip"],
    )
    def test_hyphenated_pip_false_positive_should_allow(self, bash_payload, cmd):
        """Step 0d: {word}-pip is a distinct executable, not bare pip."""
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0
