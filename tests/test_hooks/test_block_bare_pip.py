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
        """python -m pip: space before pip is not in [./\\w], so regex matches."""
        code, _, _ = run_hook(HOOK, bash_payload("python -m pip install requests"))
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

    @pytest.mark.xfail(strict=True, reason="hook bug: hook regex doesn't match pip3")
    def test_pip3_install_should_block(self, bash_payload):
        """Step 0d: pip3 is equally dangerous -- same global install behavior."""
        code, _, _ = run_hook(HOOK, bash_payload("pip3 install requests"))
        assert code == 2

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: naive 'uv pip install' not in cmd check fooled by string appearing in echo",
    )
    def test_containment_bypass_should_block(self, bash_payload):
        """Step 0d: 'uv pip install' in a string literal should not whitelist a real bare pip install."""
        code, _, _ = run_hook(
            HOOK, bash_payload('echo "uv pip install" && pip install foo')
        )
        assert code == 2

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: hyphen before pip incorrectly matches [^./\\w]",
    )
    def test_some_pip_false_positive_should_allow(self, bash_payload):
        """Step 0d: some-pip is a distinct executable, not bare pip."""
        code, _, _ = run_hook(HOOK, bash_payload("some-pip install foo"))
        assert code == 0
