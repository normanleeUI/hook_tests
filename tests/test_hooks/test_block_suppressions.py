"""Tests for block_suppressions.py hook.

Verifies that the hook blocks unjustified suppression comments
in Python files (exit 2), allows justified suppressions and
pre-approved patterns (exit 0), and skips non-.py files and exempt
directories.  Uses both explicit examples and hypothesis property tests.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "block_suppressions.py"

# Build suppression comment fragments dynamically so that writing this
# test file does not itself trigger the block_suppressions hook.
_TI = "# type" + ": ignore"
_NQ = "# no" + "qa"


def _ti(code: str = "", justification: str = "") -> str:
    """Build a type-ignore comment string with optional code and justification."""
    base = _TI
    if code:
        base += f"[{code}]"
    if justification:
        base += f"  {justification}"
    return base


def _nq(code: str = "", justification: str = "") -> str:
    """Build a noqa comment string with optional code and justification."""
    base = _NQ
    if code:
        base += f": {code}"
    if justification:
        base += f"  {justification}"
    return base


class TestBlockSuppressionsExamples:
    """Explicit examples covering the full test matrix."""

    def test_blocks_bare_type_ignore(self, edit_payload):
        """Bare type-ignore with no justification marker is blocked."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {_ti()}")
        )
        assert code == 2

    def test_allows_justified_type_ignore_mypy_bug(self, edit_payload):
        """type-ignore with mypy-bug justification is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('override', '# mypy-bug: SQLModel metaclass')}",
            ),
        )
        assert code == 0

    def test_allows_justified_type_ignore_known_issue(self, edit_payload):
        """type-ignore with known-issue justification is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('misc', '# known-issue: upstream bug 1234')}",
            ),
        )
        assert code == 0

    def test_allows_justified_type_ignore_sqlmodel_metaclass(self, edit_payload):
        """type-ignore with sqlmodel-metaclass justification is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('call-overload', '# sqlmodel-metaclass: Field()')}",
            ),
        )
        assert code == 0

    def test_blocks_bare_noqa(self, edit_payload):
        """noqa C901 with no noqa-reason marker is blocked."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {_nq('C901')}")
        )
        assert code == 2

    def test_blocks_bare_noqa_no_code(self, edit_payload):
        """Bare # noqa without an error code is blocked."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {_nq()}")
        )
        assert code == 2

    def test_allows_justified_noqa(self, edit_payload):
        """noqa with noqa-reason justification is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_nq('C901', '# noqa-reason: complex but intentional')}",
            ),
        )
        assert code == 0

    def test_allows_noqa_e402(self, edit_payload):
        """noqa E402 is pre-approved and does not need a marker."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"import os  {_nq('E402')}")
        )
        assert code == 0

    def test_skips_non_python_files(self, edit_payload):
        """Non-.py files are never checked, even with suppression comments."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.txt", f"x = 1  {_ti()}")
        )
        assert code == 0

    def test_skips_venv_files(self, edit_payload):
        """Files in .venv/ are exempt from checking."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/.venv/lib/foo.py", f"x = 1  {_ti()}")
        )
        assert code == 0

    def test_skips_spikes_files(self, edit_payload):
        """Files in spikes/ are exempt from checking."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/spikes/experiment.py", f"x = 1  {_nq('C901')}"),
        )
        assert code == 0

    def test_blocks_multiple_violations(self, edit_payload):
        """Multiple violations in a single payload are all detected."""
        multi = f"x = 1  {_ti()}\ny = 2  {_nq('C901')}\nz = 3  {_ti()}"
        code, stderr, _ = run_hook(HOOK, edit_payload("/project/src/foo.py", multi))
        assert code == 2
        assert "type" in stderr
        assert "noqa" in stderr


class TestBlockSuppressionsProperties:
    """Hypothesis property tests for justification markers."""

    @given(reason=st.from_regex(r"[a-zA-Z0-9 _-]{3,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_mypy_bug_justification_always_passes(self, reason, edit_payload):
        """Any mypy-bug reason text should be accepted."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('misc', f'# mypy-bug: {reason}')}",
            ),
        )
        assert code == 0

    @given(reason=st.from_regex(r"[a-zA-Z0-9 _-]{3,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_known_issue_justification_always_passes(self, reason, edit_payload):
        """Any known-issue reason text should be accepted."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('arg-type', f'# known-issue: {reason}')}",
            ),
        )
        assert code == 0

    @given(reason=st.from_regex(r"[a-zA-Z0-9 _-]{3,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_sqlmodel_metaclass_justification_always_passes(self, reason, edit_payload):
        """Any sqlmodel-metaclass reason text should be accepted."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_ti('call-overload', f'# sqlmodel-metaclass: {reason}')}",
            ),
        )
        assert code == 0

    @given(reason=st.from_regex(r"[a-zA-Z0-9 _-]{3,30}", fullmatch=True))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_noqa_reason_justification_always_passes(self, reason, edit_payload):
        """Any noqa-reason text should be accepted."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = 1  {_nq('C901', f'# noqa-reason: {reason}')}",
            ),
        )
        assert code == 0

    @given(
        ext=st.from_regex(r"\.[a-z]{1,5}", fullmatch=True).filter(lambda e: e != ".py")
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_python_extensions_always_pass(self, ext, edit_payload):
        """Files with non-.py extensions are never blocked."""
        code, _, _ = run_hook(
            HOOK, edit_payload(f"/project/src/foo{ext}", f"x = 1  {_ti()}")
        )
        assert code == 0

    @given(exempt_dir=st.sampled_from([".venv", "spikes", "hooks"]))
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_exempt_directories_always_pass(self, exempt_dir, edit_payload):
        """Files in exempt directories are never blocked."""
        multi = f"x = 1  {_ti()}\ny = 2  {_nq('C901')}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(f"/project/{exempt_dir}/module.py", multi),
        )
        assert code == 0


class TestBlockSuppressionsEdgeCases:
    """Edge cases from Step 0d intent specs."""

    def test_type_ignore_with_code_but_no_justification_blocked(self, edit_payload):
        """Step 0d: specific mypy code without justification still needs a reason."""
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = foo()  {_ti('override')}")
        )
        assert code == 2

    def test_uppercase_mypy_bug_justification_allowed(self, edit_payload):
        """Step 0d: justification markers are case-insensitive (re.IGNORECASE)."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = foo()  {_ti('misc', '# MYPY-BUG: upstream issue')}",
            ),
        )
        assert code == 0

    def test_mixed_case_known_issue_justification_allowed(self, edit_payload):
        """Step 0d: justification markers are case-insensitive."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/src/foo.py",
                f"x = foo()  {_ti('', '# Known-Issue: third-party types')}",
            ),
        )
        assert code == 0

    def test_uppercase_type_ignore_blocked(self, edit_payload):
        """Step 0d: case variants of type: ignore must also block."""
        ti_upper = "# TYPE" + ": IGNORE"
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {ti_upper}")
        )
        assert code == 2

    def test_uppercase_noqa_blocked(self, edit_payload):
        """Step 0d: case variants of noqa must also block."""
        nq_upper = "# NO" + "QA"
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {nq_upper}")
        )
        assert code == 2

    def test_mixed_case_type_ignore_blocked(self, edit_payload):
        """Step 0d: mixed case type: ignore must also block."""
        ti_mixed = "# Type" + ": Ignore"
        code, _, _ = run_hook(
            HOOK, edit_payload("/project/src/foo.py", f"x = 1  {ti_mixed}")
        )
        assert code == 2

    def test_allows_bare_noqa_with_noqa_reason(self, edit_payload):
        """Step 0d: bare noqa with noqa-reason justification is allowed."""
        comment = _nq("", "# noqa-reason: legacy code")
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/src/foo.py", f"x = 1  {comment}"),
        )
        assert code == 0


class TestJustificationBoundary:
    """Boundary tests for justification marker acceptance and rejection."""

    VALID_JUSTIFICATIONS = [
        _ti("override", "# mypy-bug: metaclass conflict"),
        _ti("name-defined", "# known-issue: dynamic import"),
        _ti("misc", "# sqlmodel-metaclass: Table base"),
        _nq("E501", "# noqa-reason: URL too long to break"),
    ]

    INVALID_NEAR_MISSES = [
        _ti("override", "# mypy_bug: underscore not hyphen"),
        _ti("override", "# mypybug: no hyphen at all"),
        "# type" + ": ignore  # this is just a comment",
        _ti("override", "# reason but no marker keyword"),
        _nq("E501", "# reason: wrong keyword format"),
        "# no" + "qa  # noqa_reason: underscore not hyphen",
    ]

    @pytest.mark.parametrize("comment", VALID_JUSTIFICATIONS)
    def test_valid_justification_passes(self, edit_payload, comment: str) -> None:
        """Properly justified suppression comments should be allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/src/mod.py", f"x = 1  {comment}"),
        )
        assert code == 0

    @pytest.mark.parametrize("comment", INVALID_NEAR_MISSES)
    def test_invalid_near_miss_blocks(self, edit_payload, comment: str) -> None:
        """Near-miss justifications that lack a valid marker keyword should be blocked."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/src/mod.py", f"x = 1  {comment}"),
        )
        assert code == 2

    @given(
        marker=st.sampled_from(["mypy-bug", "known-issue", "sqlmodel-metaclass"]),
        noise=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P")),
            min_size=1,
            max_size=30,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_justification_with_arbitrary_reason_text(
        self, marker: str, noise: str
    ) -> None:
        """Any reason text after a valid marker keyword should pass."""
        comment = "# type" + f": ignore[misc]  # {marker}: {noise}"
        code_str = f"x = 1  {comment}\n"
        payload = {
            "tool_input": {"file_path": "/project/src/mod.py", "new_string": code_str}
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0


class TestBlockSuppressionsEarlyExitGuards:
    """The hook should silently pass (exit 0) for non-actionable inputs.

    Malformed JSON, missing file paths, and empty content are not
    actionable — the hook has nothing to check, so it must not block.
    """

    def test_invalid_json_returns_zero(self):
        """JSONDecodeError in main() should return 0, not block."""
        # Passing a payload that will be re-serialized, but the hook reads
        # from stdin -- send malformed JSON directly via subprocess
        import subprocess
        from tests.test_hooks.hook_runner import HOOKS_DIR

        script = HOOKS_DIR / HOOK
        result = subprocess.run(
            ["python3", str(script)],
            input="NOT VALID JSON {{{",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_missing_file_path_returns_zero(self):
        """Payload with no file_path should return 0, not block."""
        payload = {"tool_input": {"new_string": "x = 1"}}
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_empty_new_string_returns_zero(self, edit_payload):
        """Empty new_string on a .py file should return 0, not block."""
        code, _, _ = run_hook(HOOK, edit_payload("/project/src/foo.py", ""))
        assert code == 0


class TestBlockSuppressionsPreToolUse:
    """PreToolUse payloads have no tool_response — verify hook works without it."""

    def test_blocks_bare_type_ignore_without_tool_response(self):
        """PreToolUse payload blocks unjustified type: ignore."""
        # Build suppression dynamically to avoid triggering the hook on this file
        ti = "# type" + ": ignore"
        payload = {
            "tool_input": {
                "file_path": "module.py",
                "new_string": f"x = 1  {ti}\n",
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2

    def test_allows_clean_code_without_tool_response(self):
        """PreToolUse payload allows clean code."""
        payload = {
            "tool_input": {
                "file_path": "module.py",
                "new_string": "x = 1\n",
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0
