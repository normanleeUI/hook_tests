"""Tests for check_dependency_pins.py hook.

Verifies that the hook blocks unpinned dependency versions in pyproject.toml
and requirements*.txt files (exit 2), allows exact pins and bounded ranges
(exit 0), and skips non-dependency files.  Uses both explicit examples and
hypothesis property tests.
"""

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "check_dependency_pins.py"


class TestDependencyPinsExamples:
    """Explicit examples covering the full test matrix."""

    def test_blocks_bare_name_pyproject(self, edit_payload):
        """Bare package name without any version specifier is blocked."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    "requests",\n]',
            ),
        )
        assert code == 2

    def test_allows_exact_pin_pyproject(self, edit_payload):
        """Exact version pin (==) is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    "requests==2.32.3",\n]',
            ),
        )
        assert code == 0

    def test_allows_bounded_range(self, edit_payload):
        """Bounded range (>=X,<Y) is allowed because it has an upper bound."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    "pandas>=2.0,<3",\n]',
            ),
        )
        assert code == 0

    def test_blocks_open_ended_gte(self, edit_payload):
        """Open-ended >= without upper bound is blocked."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    "pandas>=2.0",\n]',
            ),
        )
        assert code == 2

    def test_blocks_compatible_release(self, edit_payload):
        """Compatible release (~=) is blocked; prefer explicit bounds."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    "numpy~=1.26",\n]',
            ),
        )
        assert code == 2

    def test_blocks_bare_name_requirements_txt(self):
        """Bare names in requirements.txt are blocked."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "requests\nflask\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2

    def test_allows_pinned_requirements_txt(self):
        """Exact pins in requirements.txt are allowed."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "requests==2.32.3\nflask==3.0.0\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_skips_non_dependency_file(self, edit_payload):
        """Non-dependency files (e.g. .json) are skipped entirely."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/config.json", "requests"),
        )
        assert code == 0

    def test_allows_empty_new_string(self, edit_payload):
        """Empty new_string means no dependencies to check."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload("/project/pyproject.toml", ""),
        )
        assert code == 0


class TestDependencyPinsProperties:
    """Hypothesis property tests for dependency pin detection."""

    pkg_names = st.from_regex(r"[a-z][a-z0-9](-?[a-z0-9]){0,20}", fullmatch=True)
    versions = st.from_regex(r"[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]{1,2})?", fullmatch=True)

    @given(pkg=pkg_names, version=versions)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_exact_pin_always_passes(self, pkg, version, edit_payload):
        """Any package with == pin should be allowed."""
        dep_line = f"{pkg}=={version}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 0

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_bare_name_always_blocks(self, pkg, edit_payload):
        """Any bare package name should be blocked."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{pkg}",\n]',
            ),
        )
        assert code == 2

    @given(pkg=pkg_names, lower=versions, upper=versions)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_bounded_range_always_passes(self, pkg, lower, upper, edit_payload):
        """Bounded range (>=lower,<upper) should always be allowed."""
        assume(lower != upper)
        dep_line = f"{pkg}>={lower},<{upper}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 0

    @given(pkg=pkg_names, lower=versions)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_open_ended_gte_always_blocks(self, pkg, lower, edit_payload):
        """Open-ended >= without upper bound should always be blocked."""
        dep_line = f"{pkg}>={lower}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 2

    @given(pkg=pkg_names, version=versions)
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_tilde_equals_always_blocks(self, pkg, version, edit_payload):
        """Compatible release (~=) should always be blocked."""
        dep_line = f"{pkg}~={version}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 2

    @given(
        pkg=pkg_names,
        extra=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
        version=versions,
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_extras_with_pin_passes(self, pkg, extra, version, edit_payload):
        """Package with extras and == pin should be allowed."""
        dep_line = f"{pkg}[{extra}]=={version}"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 0

    @pytest.mark.xfail(
        strict=True,
        reason="hook bug: environment marker < fools upper-bound detection",
    )
    @pytest.mark.parametrize(
        "dep_line",
        [
            'requests>=2.0;python_version<"3.8"',
            'numpy>=1.21;sys_platform<"win32"',
            'pandas>=2.0;platform_machine<"x86_64"',
        ],
        ids=["python-version", "sys-platform", "platform-machine"],
    )
    def test_env_marker_open_ended_should_block(self, edit_payload, dep_line):
        """Step 0d: >=X is open-ended regardless of which environment marker follows."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 2

    @given(pkg=pkg_names, version=versions)
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_requirements_txt_exact_pin_passes(self, pkg, version):
        """Exact pins in requirements.txt should always be allowed."""
        dep_line = f"{pkg}=={version}"
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": dep_line,
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    @given(pkg=pkg_names)
    @settings(max_examples=100)
    def test_requirements_txt_bare_name_blocks(self, pkg):
        """Bare names in requirements.txt should always be blocked."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": pkg,
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2


class TestDependencyPinsEdgeCases:
    """Edge cases from Step 0d intent specs."""

    def test_bounded_with_exclusion_allowed(self, edit_payload):
        """Step 0d: >=2.0,<3,!=2.5.0 has both >= and < -- exclusion is additional constraint."""
        dep_line = "requests>=2.0,<3,!=2.5.0"
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                f'dependencies = [\n    "{dep_line}",\n]',
            ),
        )
        assert code == 0

    def test_requirements_dev_txt_pinned_passes(self):
        """Step 0d: requirements-dev.txt with pinned deps should pass."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements-dev.txt",
                "new_string": "requests==2.32.3",
            },
            "tool_response": {"filePath": "/project/requirements-dev.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_requirements_dev_txt_enforces_pins(self):
        """Step 0d: requirements-dev.txt matches requirements*.txt -- dev deps should be pinned."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements-dev.txt",
                "new_string": "requests",
            },
            "tool_response": {"filePath": "/project/requirements-dev.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2
