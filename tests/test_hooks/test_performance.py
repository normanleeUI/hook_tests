"""Performance baseline assertions for Python hooks' internal logic.

Hooks run on every tool call in Claude Code. If a hook's internal logic
(regex matching, JSON parsing, file filtering) is slow, it degrades UX.
These tests establish performance baselines that catch accidental O(n²)
loops, unbounded file reads, or runaway regex backtracking.

Only tests hooks' *own* logic speed — shell wrappers that invoke external
tools are excluded since their speed depends on external tool performance.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

PERFORMANCE_BUDGET_SECONDS = 2.0

# Build suppression comment dynamically to avoid triggering the
# block_suppressions hook on this test file itself.
_SUPPRESSION_COMMENT = "# type" + ": ignore"


class TestHookPerformance:
    """Verify each Python hook completes within a 2-second budget."""

    @pytest.mark.parametrize(
        "hook_name,payload,expected_code",
        [
            pytest.param(
                "block_read_env.py",
                {"tool_input": {"file_path": "/project/.env"}},
                2,
                id="block_read_env",
            ),
            pytest.param(
                "block_bare_pip.py",
                {"tool_input": {"command": "pip install requests"}},
                2,
                id="block_bare_pip",
            ),
            pytest.param(
                "block_git_add_env.py",
                {"tool_input": {"command": "git add .env"}},
                2,
                id="block_git_add_env",
            ),
            pytest.param(
                "block_suppressions.py",
                {
                    "tool_input": {
                        "file_path": "/project/src/foo.py",
                        "new_string": ("x = 1  " + _SUPPRESSION_COMMENT + "\n") * 100,
                    }
                },
                2,
                id="block_suppressions",
            ),
            pytest.param(
                "check_dependency_pins.py",
                {
                    "tool_input": {
                        "file_path": "/project/pyproject.toml",
                        "new_string": "\n".join(
                            f'    "pkg{i}=={i}.0.0",' for i in range(50)
                        ),
                    },
                    "tool_response": {"filePath": "/project/pyproject.toml"},
                },
                0,
                id="check_dependency_pins",
            ),
            pytest.param(
                "scan_prompt_injection.py",
                {
                    "tool_response": {"content": "Normal text. " * 500},
                    "tool_name": "WebFetch",
                },
                0,
                id="scan_prompt_injection",
            ),
        ],
    )
    def test_hook_completes_within_budget(
        self, hook_name: str, payload: dict, expected_code: int
    ) -> None:
        """Each Python hook must complete within the performance budget."""
        start = time.perf_counter()
        returncode, _stderr, _stdout = run_hook(
            hook_name,
            payload,
            timeout=int(PERFORMANCE_BUDGET_SECONDS + 1),
        )
        elapsed = time.perf_counter() - start

        assert returncode == expected_code, (
            f"{hook_name} returned {returncode}, expected {expected_code}"
        )
        assert elapsed < PERFORMANCE_BUDGET_SECONDS, (
            f"{hook_name} took {elapsed:.3f}s, exceeds budget of "
            f"{PERFORMANCE_BUDGET_SECONDS}s"
        )

    def test_block_bare_pip_no_regex_backtracking(self) -> None:
        """A 5000-char prefix before 'pip install' must not cause backtracking."""
        long_prefix = "a" * 5000 + " && pip install requests"
        payload = {"tool_input": {"command": long_prefix}}

        start = time.perf_counter()
        returncode, _stderr, _stdout = run_hook("block_bare_pip.py", payload, timeout=5)
        elapsed = time.perf_counter() - start

        assert returncode == 2, (
            f"Expected block (code 2) for pip install in long command, got {returncode}"
        )
        assert elapsed < PERFORMANCE_BUDGET_SECONDS, (
            f"Regex backtracking suspected: took {elapsed:.3f}s"
        )

    def test_block_suppressions_large_file(self) -> None:
        """100 lines with periodic suppressions must complete within budget."""
        lines = []
        for i in range(100):
            if i % 10 == 0:
                lines.append(f"x{i} = {i}  {_SUPPRESSION_COMMENT}")
            else:
                lines.append(f"x{i} = {i}")
        content = "\n".join(lines)

        payload = {
            "tool_input": {
                "file_path": "/project/src/large.py",
                "new_string": content,
            }
        }

        start = time.perf_counter()
        returncode, _stderr, _stdout = run_hook(
            "block_suppressions.py", payload, timeout=5
        )
        elapsed = time.perf_counter() - start

        assert returncode == 2, (
            f"Expected block (code 2) for file with suppressions, got {returncode}"
        )
        assert elapsed < PERFORMANCE_BUDGET_SECONDS, (
            f"Large file processing took {elapsed:.3f}s, exceeds budget"
        )

    def test_scan_prompt_injection_large_content(self) -> None:
        """~50KB of normal text must be scanned within budget."""
        # ~50KB of benign content (each repetition is ~13 bytes)
        large_content = "Normal text. " * 4000
        payload = {
            "tool_response": {"content": large_content},
            "tool_name": "WebFetch",
        }

        start = time.perf_counter()
        returncode, _stderr, _stdout = run_hook(
            "scan_prompt_injection.py", payload, timeout=5
        )
        elapsed = time.perf_counter() - start

        assert returncode == 0, (
            f"Expected pass (code 0) for benign content, got {returncode}"
        )
        assert elapsed < PERFORMANCE_BUDGET_SECONDS, (
            f"Prompt injection scan took {elapsed:.3f}s on ~50KB content"
        )

    @given(
        path_length=st.integers(min_value=1000, max_value=10000),
    )
    @settings(max_examples=10, deadline=None)
    def test_block_read_env_long_path_no_slowdown(self, path_length: int) -> None:
        """Long file paths ending in .env must not cause slowdown."""
        # Build a path like /aaa...aaa/.env
        long_path = "/" + "a" * (path_length - 5) + "/.env"
        payload = {"tool_input": {"file_path": long_path}}

        start = time.perf_counter()
        returncode, _stderr, _stdout = run_hook("block_read_env.py", payload, timeout=5)
        elapsed = time.perf_counter() - start

        assert returncode == 2, (
            f"Expected block (code 2) for .env path of length {path_length}, "
            f"got {returncode}"
        )
        assert elapsed < PERFORMANCE_BUDGET_SECONDS, (
            f"Path length {path_length} took {elapsed:.3f}s, exceeds budget"
        )
