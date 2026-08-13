"""Direct-call tests for claude-config/githooks/docstring_analysis.py (Step 4, [n6]).

Ports TestCheckDocstrings' behavioral cases from stdin-payload subprocess
calls to plain function calls (AC-DOC-02), plus an import-purity check
(AC-DOC-01) and a parity spot-check against the old hook.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.test_hooks.hook_runner import run_hook

CLAUDE_CONFIG = Path(
    os.environ.get(
        "CLAUDE_CONFIG", Path.home() / "projects" / "shared_resources" / "claude-config"
    )
)
GITHOOKS_DIR = CLAUDE_CONFIG / "githooks"

sys.path.insert(0, str(GITHOOKS_DIR))
from docstring_analysis import analyze_docstrings, should_skip  # noqa: E402


class TestImportPurity:
    """AC-DOC-01: importing the module has no side effects."""

    def test_import_is_pure(self) -> None:
        """Import with stdin on /dev/null: exit 0, no output (no log_hook/stdin/sys.exit)."""
        with open(os.devnull) as devnull:
            result = subprocess.run(
                [sys.executable, "-c", "import docstring_analysis"],
                stdin=devnull,
                capture_output=True,
                text=True,
                cwd=str(GITHOOKS_DIR),
                timeout=10,
            )
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""


class TestAnalyzeDocstrings:
    """AC-DOC-02: one direct-call test per analyzer input class."""

    def test_warns_on_missing_docstring(self) -> None:
        src = "def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n"
        assert analyze_docstrings(src) == [
            (1, "missing docstring for function 'compute'")
        ]

    def test_no_warn_with_docstring(self) -> None:
        src = (
            "def compute(x, y):\n"
            '    """Add and double."""\n'
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
        )
        assert analyze_docstrings(src) == []

    def test_warns_on_class_without_docstring(self) -> None:
        src = "class MyClass:\n    def method(self):\n        pass\n"
        assert analyze_docstrings(src) == [(1, "missing docstring for class 'MyClass'")]

    def test_warns_on_init_with_params_no_docstring(self) -> None:
        src = (
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __init__(self, name, value):\n"
            "        self.name = name\n"
            "        self.value = value\n"
            "        self.combined = name + str(value)\n"
        )
        assert analyze_docstrings(src) == [
            (3, "missing docstring for function '__init__'")
        ]

    def test_no_warn_on_init_self_only(self) -> None:
        src = (
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "        self.y = 2\n"
            "        self.z = 3\n"
        )
        assert analyze_docstrings(src) == []

    def test_warns_on_async_def_without_docstring(self) -> None:
        src = (
            "async def fetch_data(url):\n"
            "    response = await get(url)\n"
            "    data = response.json()\n"
            "    return data\n"
        )
        assert analyze_docstrings(src) == [
            (1, "missing docstring for function 'fetch_data'")
        ]

    def test_no_warn_on_trivial_two_statement_func(self) -> None:
        src = "def compute(x, y):\n    a = x + y\n    return a\n"
        assert analyze_docstrings(src) == []

    def test_skips_private_functions(self) -> None:
        src = "def _helper(x, y, z):\n    a = x + y\n    b = a * z\n    return b\n"
        assert analyze_docstrings(src) == []

    def test_skips_dunder_methods(self) -> None:
        src = (
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __repr__(self):\n"
            "        a = self.name\n"
            "        b = a.upper()\n"
            "        return f'MyClass({b})'\n"
        )
        assert analyze_docstrings(src) == []

    def test_syntax_error_returns_empty(self) -> None:
        assert analyze_docstrings("def broken(:\n") == []


class TestShouldSkip:
    """AC-DOC-02: path-based skip logic."""

    def test_skips_test_files(self) -> None:
        assert should_skip(Path("/x/test_module.py")) is True

    def test_skips_init_py(self) -> None:
        assert should_skip(Path("/x/__init__.py")) is True

    def test_skips_conftest_py(self) -> None:
        assert should_skip(Path("/x/conftest.py")) is True

    def test_skips_setup_py(self) -> None:
        assert should_skip(Path("/x/setup.py")) is True

    def test_skips_manage_py(self) -> None:
        assert should_skip(Path("/x/manage.py")) is True

    def test_skips_dunder_main_py(self) -> None:
        assert should_skip(Path("/x/__main__.py")) is True

    def test_skips_claude_directory(self) -> None:
        assert should_skip(Path("/x/.claude/module.py")) is True

    def test_skips_non_py_suffix(self) -> None:
        assert should_skip(Path("/x/module.txt")) is True

    def test_keeps_ordinary_module(self) -> None:
        assert should_skip(Path("/x/module.py")) is False


class TestParityWithOldHook:
    """New analyzer flags the same line numbers the old hook injects on."""

    def test_flagged_lines_match(self, tmp_path: Path) -> None:
        src = (
            "class MyClass:\n"
            "    def method(self, a, b):\n"
            "        x = a + b\n"
            "        y = x * 2\n"
            "        return y\n"
            "\n"
            "def compute(x, y):\n"
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
        )
        f = tmp_path / "module.py"
        f.write_text(src)
        payload = {
            "tool_input": {"file_path": str(f)},
            "tool_response": {"filePath": str(f)},
        }
        rc, _stderr, _stdout = run_hook("check_docstrings.py", payload)
        assert rc == 0
        # inject_at_line inserts each comment ABOVE the flagged line, so map
        # comment positions back to clean-file line numbers by counting only
        # non-comment lines.
        old_flagged = []
        clean_lineno = 0
        for line in f.read_text().splitlines():
            if line.strip().startswith("# HOOK:DOCSTRING:"):
                old_flagged.append(clean_lineno + 1)
            else:
                clean_lineno += 1
        # ast.walk is breadth-first, so sort before comparing line numbers.
        new_flagged = sorted(lineno for lineno, _msg in analyze_docstrings(src))
        assert new_flagged == [1, 2, 7]
        assert old_flagged == new_flagged
