"""Tests for block_read_env.py hook.

Verifies that the hook blocks reading .env files (exit 2), allows template
files like .env.example (exit 0), and allows non-env files (exit 0).
Uses both explicit examples and hypothesis property tests to cover the
decision space.
"""

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
