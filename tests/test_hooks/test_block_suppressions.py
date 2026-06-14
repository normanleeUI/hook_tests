"""Tests for block_suppressions.py hook.

Verifies that the hook blocks unjustified suppression comments
in Python files (exit 2), allows justified suppressions and
pre-approved patterns (exit 0), and skips non-.py files and exempt
directories.  Uses both explicit examples and hypothesis property tests.
"""

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
