"""Robustness baseline: no hook crashes on malformed input.

Verifies that every hook exits 0 (pass) or 2 (block) on adversarial
payloads -- never exit 1 (unhandled exception). Also checks that no
hook emits a Python traceback on stderr for any input.

Three test classes:
- TestNoCrashOnAdversarialInput: 18 hooks x 18 payloads via run_hook
- TestNoCrashOnInvalidJson: 18 hooks x 6 raw inputs via subprocess
- TestNoCrashOnHypothesisPayloads: 5 high-value hooks x hypothesis JSON
"""

import json
import subprocess

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_bash_hook, run_hook
from tests.test_hooks.test_hook_wiring import CANONICAL_HOOKS

# Stop hooks that read git state, not stdin JSON
STDIN_INDEPENDENT = {"ruff_lint.sh"}

ALL_HOOKS: dict[str, str] = {
    name: cfg["interpreter"]
    for name, cfg in CANONICAL_HOOKS.items()
    if name not in STDIN_INDEPENDENT
}

ADVERSARIAL_PAYLOADS: list = [
    {},
    {"tool_input": None},
    {"tool_input": {}},
    {"tool_input": {"file_path": ""}},
    {"tool_input": {"file_path": None}},
    {"tool_input": {"command": ""}},
    {"tool_input": {"command": None}},
    {"tool_response": {}},
    {"tool_input": {"file_path": "/dev/null"}},
    {"tool_input": {"file_path": "\x00"}},
    {"tool_input": {"file_path": "a" * 10000}},
    {"tool_input": {"command": "a" * 10000}},
    {"tool_input": {"new_string": "\n" * 5000}},
    {"unexpected_key": "unexpected_value"},
    [],
    "just a string",
    42,
    True,
]


def _payload_id(payload: object) -> str:
    return str(payload)[:40]


class TestNoCrashOnAdversarialInput:
    """Every hook must exit 0 or 2 (never 1) on any input. No tracebacks on stderr."""

    @pytest.mark.parametrize("hook_name,interpreter", list(ALL_HOOKS.items()))
    @pytest.mark.parametrize(
        "payload",
        ADVERSARIAL_PAYLOADS,
        ids=[_payload_id(p) for p in ADVERSARIAL_PAYLOADS],
    )
    def test_no_crash(
        self,
        hook_name: str,
        interpreter: str,
        payload: object,
    ) -> None:
        if interpreter == "bash":
            code, stderr, _ = run_bash_hook(hook_name, payload, timeout=5)
        else:
            code, stderr, _ = run_hook(hook_name, payload, timeout=5)
        assert code in (0, 2), (
            f"{hook_name} crashed (exit {code}) on payload {str(payload)[:80]}.\n"
            f"stderr: {stderr[:500]}"
        )
        assert "Traceback (most recent call last)" not in stderr, (
            f"{hook_name} raised unhandled exception on payload {str(payload)[:80]}.\n"
            f"stderr: {stderr[:500]}"
        )


class TestNoCrashOnInvalidJson:
    """Hooks receive stdin. Verify they handle non-JSON gracefully."""

    INVALID_INPUTS = [
        "",
        "{",
        "}{",
        "\x00\x01\x02",
        "null",
        '{"tool_input": {"file_path": "test.py"}',
    ]

    @pytest.mark.parametrize("hook_name,interpreter", list(ALL_HOOKS.items()))
    @pytest.mark.parametrize("raw_input", INVALID_INPUTS)
    def test_invalid_json_no_crash(
        self,
        hook_name: str,
        interpreter: str,
        raw_input: str,
    ) -> None:
        """Direct subprocess call with raw string input (bypasses json.dumps in run_hook)."""
        script = HOOKS_DIR / hook_name
        if not script.exists():
            pytest.skip(f"Hook not found: {hook_name}")

        result = subprocess.run(
            [interpreter, str(script)],
            input=raw_input,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode in (0, 2), (
            f"{hook_name} crashed (exit {result.returncode}) on raw input {raw_input!r}.\n"
            f"stderr: {result.stderr[:500]}"
        )
        assert "Traceback" not in result.stderr, (
            f"{hook_name} traceback on raw input {raw_input!r}.\n"
            f"stderr: {result.stderr[:500]}"
        )


class TestNoCrashOnHypothesisPayloads:
    """Fuzz the JSON structure with hypothesis-generated payloads."""

    json_values = st.recursive(
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(),
            st.floats(allow_nan=False),
            st.text(max_size=50),
        ),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(max_size=20), children, max_size=5),
        ),
        max_leaves=10,
    )

    @given(payload=json_values)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    @pytest.mark.parametrize(
        "hook_name,interpreter",
        [
            ("block_bare_pip.py", "python3"),
            ("block_read_env.py", "python3"),
            ("block_git_add_env.py", "python3"),
            ("block_suppressions.py", "python3"),
            ("scan_prompt_injection.py", "python3"),
        ],
    )
    def test_fuzzed_payload_no_crash(
        self,
        hook_name: str,
        interpreter: str,
        payload: object,
    ) -> None:
        """High-value hooks must not crash on arbitrary JSON structures."""
        script = HOOKS_DIR / hook_name
        if not script.exists():
            pytest.skip(f"Hook not found: {hook_name}")

        try:
            input_str = json.dumps(payload)
        except (TypeError, ValueError):
            pytest.skip("Payload not JSON-serializable")

        result = subprocess.run(
            [interpreter, str(script)],
            input=input_str,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode in (0, 2), (
            f"{hook_name} crashed (exit {result.returncode}) on fuzzed payload.\n"
            f"stderr: {result.stderr[:500]}"
        )
        assert "Traceback" not in result.stderr, (
            f"{hook_name} traceback on fuzzed payload.\nstderr: {result.stderr[:500]}"
        )
