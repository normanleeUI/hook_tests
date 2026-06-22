"""Tests for block_read_env.py hook.

Verifies that the hook blocks reading .env files (exit 2), allows template
files like .env.example (exit 0), and allows non-env files (exit 0).
Covers both Read-path (file_path payload) and Bash-path (command payload)
routing. Uses both explicit examples and hypothesis property tests to
cover the decision space.
"""

import pytest

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_read_env.py"
TEMPLATE_SUFFIXES = {"example", "sample", "template", "dist"}


class TestBlockReadEnvExamples:
    """Explicit examples from the test matrix."""

    def test_blocks_dot_env(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env"))
        assert code == 2

    def test_blocks_dot_env_local(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.local"))
        assert code == 2

    def test_blocks_dot_env_production_backup(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.production.backup"))
        assert code == 2

    def test_allows_env_example(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.example"))
        assert code == 0

    def test_allows_env_sample(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.sample"))
        assert code == 0

    def test_allows_env_template(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.template"))
        assert code == 0

    def test_allows_env_dist(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.dist"))
        assert code == 0

    def test_allows_non_env_file(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload("/project/main.py"))
        assert code == 0

    def test_allows_dot_environment(self, read_payload):
        """'.environment' does not match the .env pattern (no dot after .env)."""
        code, _, _ = run_hook(HOOK, read_payload("/project/.environment"))
        assert code == 0

    def test_allows_empty_file_path(self, read_payload):
        code, _, _ = run_hook(HOOK, read_payload(""))
        assert code == 0


class TestBlockReadEnvProperties:
    """Hypothesis property tests for broader coverage."""

    @given(suffix=st.from_regex(r"[a-zA-Z0-9._-]{1,20}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_env_variants_blocked_unless_template(self, read_payload, suffix):
        assume(suffix not in TEMPLATE_SUFFIXES)
        path = f"/project/.env.{suffix}"
        code, _, _ = run_hook(HOOK, read_payload(path))
        assert code == 2, f".env.{suffix} should be blocked"

    @given(suffix=st.sampled_from(list(TEMPLATE_SUFFIXES)))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_template_suffixes_allowed(self, read_payload, suffix):
        code, _, _ = run_hook(HOOK, read_payload(f"/project/.env.{suffix}"))
        assert code == 0

    @given(
        name=st.from_regex(
            r"[a-z][a-z0-9_]{0,20}\.(py|txt|json|toml|yaml|md)", fullmatch=True
        )
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_env_files_always_pass(self, read_payload, name):
        code, _, _ = run_hook(HOOK, read_payload(f"/project/{name}"))
        assert code == 0


class TestBlockReadEnvEdgeCases:
    """Edge cases from Step 0d intent specs."""

    def test_env_example_bak_blocked(self, read_payload):
        """Backup of template (.env.example.bak) is not a recognized suffix."""
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.example.bak"))
        assert code == 2

    def test_env_dist_local_blocked(self, read_payload):
        """Compound suffix (.env.dist.local) is not a recognized template."""
        code, _, _ = run_hook(HOOK, read_payload("/project/.env.dist.local"))
        assert code == 2

    def test_uppercase_ENV_allowed(self, read_payload):
        """Case-sensitive filesystem: .ENV does not match .env pattern."""
        code, _, _ = run_hook(HOOK, read_payload("/project/.ENV"))
        assert code == 0

    def test_mixed_case_Env_allowed(self, read_payload):
        """Case-sensitive filesystem: .Env does not match .env pattern."""
        code, _, _ = run_hook(HOOK, read_payload("/project/.Env"))
        assert code == 0

    def test_missing_file_path_allowed(self):
        """Fail-safe: missing file_path key should exit 0."""
        code, _, _ = run_hook(HOOK, {"tool_input": {}})
        assert code == 0


class TestBlockReadEnvBashMatcher:
    """Tests for the Bash-path: commands referencing .env files are blocked."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "cat .env",
            "cat .env.local",
            "cat .env.production",
            "cat path/to/.env",
            "head .env",
            "head -5 .env",
            "tail .env",
            "less .env",
            "grep API_KEY .env",
            "grep -r SECRET .env.local",
            "base64 .env",
            "source .env",
            ". .env",
            "python3 -c 'open(\".env\").read()'",
            "xargs cat < .env",
            "$(cat .env)",
        ],
    )
    def test_bash_blocks_env_file_read_commands(self, bash_payload, cmd):
        code, stderr, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 2, f"Should block: {cmd}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "cat README.md",
            "cat .env.example",
            "cat .env.template",
            "cat .env.sample",
            "cat .env.dist",
            "head pyproject.toml",
            "grep pattern src/main.py",
            "echo hello world",
            "ls -la",
            "pip install python-dotenv",
        ],
    )
    def test_bash_allows_non_env_commands(self, bash_payload, cmd):
        code, _, _ = run_hook(HOOK, bash_payload(cmd))
        assert code == 0, f"Should allow: {cmd}"

    def test_bash_blocks_compound_command_with_env_read(self, bash_payload):
        code, _, _ = run_hook(HOOK, bash_payload("echo hello && cat .env"))
        assert code == 2

    def test_read_tool_still_blocks(self, read_payload):
        """Regression: existing Read matcher behavior preserved."""
        code, _, _ = run_hook(HOOK, read_payload(".env"))
        assert code == 2
