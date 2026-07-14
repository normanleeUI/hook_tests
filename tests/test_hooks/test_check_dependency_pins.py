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


class TestDependencyPinsRequirementsTxtSkipFilters:
    """The hook should only check actual dependency specifiers in requirements.txt.

    Standard requirements.txt features — comment lines (#), pip flags (-r,
    --index-url), URL deps (http), and local path deps (/, .) — are not
    dependency specifiers and must not be treated as unpinned deps.
    """

    def test_comment_lines_skipped(self):
        """Lines starting with # in requirements.txt should be ignored."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "# This is a comment\nrequests==2.32.3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_comment_line_not_treated_as_dep(self):
        """A comment that looks like a bare dep name should not block."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "# requests\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_flag_lines_skipped(self):
        """Lines starting with - (pip flags like -r, -e, --index-url) are skipped."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "-r base.txt\n--index-url https://pypi.org/simple\nrequests==2.32.3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_url_lines_skipped(self):
        """Lines starting with http (direct URL deps) are skipped."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "https://example.com/my-package-1.0.tar.gz\nrequests==2.32.3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_absolute_path_lines_skipped(self):
        """Lines starting with / (absolute local paths) are skipped."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "/opt/local/my-package\nrequests==2.32.3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_relative_path_lines_skipped(self):
        """Lines starting with . (relative local paths like -e .) are skipped."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "./local-package\nrequests==2.32.3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_mixed_skip_and_real_deps(self):
        """Mix of skippable lines and real deps -- only real deps checked."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": (
                    "# pinned deps\n"
                    "-r base.txt\n"
                    "https://example.com/pkg.tar.gz\n"
                    "/local/pkg\n"
                    "./editable-pkg\n"
                    "requests==2.32.3\n"
                ),
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_requirements_txt_bounded_range_passes(self):
        """Bounded range >=X,<Y in requirements.txt should pass."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "pandas>=2.0,<3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_requirements_txt_open_gte_only_blocks(self):
        """Open-ended >= without < in requirements.txt should block."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "pandas>=2.0\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2

    def test_requirements_txt_lt_only_blocks(self):
        """A line with only < (no >=) in requirements.txt should block as unpinned."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.txt",
                "new_string": "pandas<3\n",
            },
            "tool_response": {"filePath": "/project/requirements.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2


class TestDependencyPinsEarlyExitGuards:
    """The hook should silently pass (exit 0) for non-actionable inputs.

    Non-dependency files, missing paths, and malformed payloads are not
    actionable — the hook has nothing to check, so it must not block.
    """

    def test_invalid_json_returns_zero(self):
        """JSONDecodeError should return 0, not block."""
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
        """Payload with no file_path or filePath should return 0."""
        payload = {"tool_input": {"new_string": "requests"}}
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_non_dep_file_returns_zero(self, edit_payload):
        """Non-dependency file should return 0 even with unpinned content."""
        code, _, _ = run_hook(HOOK, edit_payload("/project/config.yaml", "requests"))
        assert code == 0

    def test_non_txt_requirements_like_file_passes(self):
        """A file named requirements.json should not be treated as requirements.txt."""
        payload = {
            "tool_input": {
                "file_path": "/project/requirements.json",
                "new_string": "requests",
            },
            "tool_response": {"filePath": "/project/requirements.json"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_random_txt_file_not_treated_as_requirements(self):
        """A .txt file not starting with 'requirements' should pass."""
        payload = {
            "tool_input": {
                "file_path": "/project/notes.txt",
                "new_string": "requests",
            },
            "tool_response": {"filePath": "/project/notes.txt"},
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0


class TestDependencyPinsPyprojectParserEdgeCases:
    """Gap-filling tests for pyproject.toml parser edge cases."""

    def test_pyproject_comment_in_deps_skipped(self, edit_payload):
        """Comment lines inside dependencies array should not be flagged."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = [\n    # this is a comment\n    "requests==2.32.3",\n]',
            ),
        )
        assert code == 0

    def test_single_line_bare_name_blocked(self, edit_payload):
        """Single-line dependencies array with bare name is blocked."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = ["requests>=2.31.0,<3", "flask"]',
            ),
        )
        assert code == 2

    def test_single_line_all_pinned_allowed(self, edit_payload):
        """Single-line dependencies array with all pinned deps is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = ["requests==2.31.0", "flask>=3.0,<4"]',
            ),
        )
        assert code == 0

    def test_single_line_mixed_pins_blocks_unpinned(self, edit_payload):
        """Single-line array with one pinned and one bare name blocks."""
        code, stderr, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                'dependencies = ["requests==2.31.0", "httpx"]',
            ),
        )
        assert code == 2
        assert "httpx" in stderr

    def test_single_line_empty_array_allowed(self, edit_payload):
        """Single-line empty dependencies array is allowed."""
        code, _, _ = run_hook(
            HOOK,
            edit_payload(
                "/project/pyproject.toml",
                "dependencies = []",
            ),
        )
        assert code == 0


class TestDependencyPinsPreToolUse:
    """PreToolUse payloads have no tool_response key — verify hook works without it."""

    def test_blocks_unpinned_without_tool_response(self):
        """PreToolUse payload with no tool_response still blocks unpinned deps."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "old_string": "",
                "new_string": 'dependencies = [\n    "requests",\n]',
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2

    def test_allows_pinned_via_write_content(self):
        """PreToolUse Write payload uses content field (no tool_response)."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "content": 'dependencies = [\n    "requests==2.32.3",\n]',
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0


class TestDependencyPinsDiffAware:
    """An Edit's new_string includes the unchanged *context* lines around the
    change, so scanning all of it re-flags pre-existing unpinned deps that
    merely sit near the edit (Batch 5 usability bug). The hook must diff
    new_string against old_string and flag only *newly* unpinned deps.
    """

    def test_preexisting_open_ended_dep_in_context_not_reflagged(self):
        """Adding a properly pinned dep next to a pre-existing open-ended dep
        (present in both old and new) must be allowed."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "old_string": 'dependencies = [\n    "httpx>=0.28.1",\n]',
                "new_string": 'dependencies = [\n    "httpx>=0.28.1",\n    "requests==2.32.3",\n]',
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 0

    def test_new_unpinned_still_blocks_and_names_only_the_new_dep(self):
        """A genuinely new unpinned dep is still blocked, but the pre-existing
        open-ended dep must NOT appear in the message."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "old_string": 'dependencies = [\n    "httpx>=0.28.1",\n]',
                "new_string": 'dependencies = [\n    "httpx>=0.28.1",\n    "requests",\n]',
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2
        assert "requests" in stderr
        assert "httpx" not in stderr

    def test_changing_pinned_dep_to_open_ended_blocks(self):
        """If the edit itself loosens a dep from == to open-ended >=, that dep
        is newly unpinned and must be blocked."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "old_string": 'dependencies = [\n    "requests==2.32.3",\n]',
                "new_string": 'dependencies = [\n    "requests>=2.32.3",\n]',
            }
        }
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 2
        assert "requests" in stderr

    def test_empty_old_string_checks_full_new_text(self):
        """With an empty old_string baseline (nothing pre-existing), a new
        unpinned dep is blocked — preserves original behavior."""
        payload = {
            "tool_input": {
                "file_path": "pyproject.toml",
                "old_string": "",
                "new_string": 'dependencies = [\n    "requests",\n]',
            }
        }
        code, _, _ = run_hook(HOOK, payload)
        assert code == 2
