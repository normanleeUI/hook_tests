"""Tests for tier-2 informational hooks: check_docstrings, check_random_seeds, check_test_pair."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook


def _make_payload(path: str) -> dict:
    """Build the standard hook payload for a file path."""
    return {
        "tool_input": {"file_path": path},
        "tool_response": {"filePath": path},
    }


# =====================================================================
# TestCheckDocstrings
# =====================================================================


class TestCheckDocstrings:
    """Tests for check_docstrings.py: warns on missing docstrings in non-trivial public defs."""

    def test_warns_on_missing_docstring(self, tmp_path: Path) -> None:
        """Public func with 3+ statements and no docstring should trigger warning."""
        f = tmp_path / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" in stdout.lower()

    def test_no_warn_with_docstring(self, tmp_path: Path) -> None:
        """Func with a docstring should not trigger any warning."""
        f = tmp_path / "module.py"
        f.write_text(
            "def compute(x, y):\n"
            '    """Add and double."""\n'
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" not in stdout.lower()

    def test_skips_test_files(self, tmp_path: Path) -> None:
        """Files named test_*.py should be skipped entirely."""
        f = tmp_path / "test_module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_warns_on_class_without_docstring(self, tmp_path: Path) -> None:
        """Class definition without docstring should trigger warning."""
        f = tmp_path / "module.py"
        f.write_text("class MyClass:\n    def method(self):\n        pass\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" in stdout.lower()

    def test_warns_on_init_with_params_no_docstring(self, tmp_path: Path) -> None:
        """__init__ with params beyond self, no docstring (class HAS docstring) should warn."""
        f = tmp_path / "module.py"
        f.write_text(
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __init__(self, name, value):\n"
            "        self.name = name\n"
            "        self.value = value\n"
            "        self.combined = name + str(value)\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" in stdout.lower()

    def test_no_warn_on_init_self_only(self, tmp_path: Path) -> None:
        """__init__(self) with no extra params, class has docstring -> no warning."""
        f = tmp_path / "module.py"
        f.write_text(
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "        self.y = 2\n"
            "        self.z = 3\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" not in stdout.lower()

    def test_warns_on_async_def_without_docstring(self, tmp_path: Path) -> None:
        """async def with 3+ statements and no docstring should warn."""
        f = tmp_path / "module.py"
        f.write_text(
            "async def fetch_data(url):\n"
            "    response = await get(url)\n"
            "    data = response.json()\n"
            "    return data\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" in stdout.lower()

    def test_skips_files_in_claude_directory(self, tmp_path: Path) -> None:
        """Files inside a .claude/ subdirectory should be skipped."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        f = claude_dir / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_no_warn_on_trivial_two_statement_func(self, tmp_path: Path) -> None:
        """Function with only 2 statements (trivial) should not trigger warning."""
        f = tmp_path / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    return a\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" not in stdout.lower()

    def test_skips_init_py(self, tmp_path: Path) -> None:
        """__init__.py files should be skipped entirely."""
        f = tmp_path / "__init__.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_private_functions(self, tmp_path: Path) -> None:
        """Private functions (_helper) should not trigger warnings."""
        f = tmp_path / "module.py"
        f.write_text(
            "def _helper(x, y, z):\n    a = x + y\n    b = a * z\n    return b\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_conftest_py(self, tmp_path: Path) -> None:
        """conftest.py should be skipped entirely."""
        f = tmp_path / "conftest.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_setup_py(self, tmp_path: Path) -> None:
        """setup.py should be skipped entirely."""
        f = tmp_path / "setup.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_dunder_methods(self, tmp_path: Path) -> None:
        """Dunder methods other than __init__ should not trigger warnings."""
        f = tmp_path / "module.py"
        f.write_text(
            "class MyClass:\n"
            '    """A class."""\n'
            "    def __repr__(self):\n"
            "        a = self.name\n"
            "        b = a.upper()\n"
            "        return f'MyClass({b})'\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "missing docstrings" not in stdout.lower()

    @given(
        name=st.from_regex(r"[a-z_][a-z0-9_]{1,20}", fullmatch=True),
        stmt_count=st.integers(min_value=3, max_value=8),
        has_docstring=st.booleans(),
    )
    @settings(max_examples=100)
    def test_docstring_detection_property(
        self, name: str, stmt_count: int, has_docstring: bool
    ) -> None:
        """Property: public funcs with docstring -> no warning; without -> warning."""
        assume(not name.startswith("_"))

        statements = "\n".join(f"    x{i} = {i}" for i in range(stmt_count))
        if has_docstring:
            body = f'    """A docstring."""\n{statements}'
        else:
            body = statements

        source = f"def {name}():\n{body}\n"

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "module.py"
            f.write_text(source)
            rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
            assert rc == 0
            if has_docstring:
                assert "missing docstrings" not in stdout.lower()
            else:
                assert "missing docstrings" in stdout.lower()


# =====================================================================
# TestCheckRandomSeeds
# =====================================================================


class TestCheckRandomSeeds:
    """Tests for check_random_seeds.py: warns on randomness without explicit seeds."""

    def test_warns_on_unseeded_random(self, tmp_path: Path) -> None:
        """import random without seed should warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import random\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_no_warn_with_seed(self, tmp_path: Path) -> None:
        """import random with seed should not warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import random\nrandom.seed(42)\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" not in stdout.lower()

    def test_warns_on_numpy_without_seed(self, tmp_path: Path) -> None:
        """import numpy without seed should warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import numpy as np\nx = np.random.rand()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_no_warn_on_numpy_with_seed(self, tmp_path: Path) -> None:
        """import numpy with seed should not warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import numpy as np\nnp.random.seed(42)\nx = np.random.rand()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" not in stdout.lower()

    def test_warns_on_torch_without_seed(self, tmp_path: Path) -> None:
        """import torch without seed should warn."""
        f = tmp_path / "model.py"
        f.write_text("import torch\nx = torch.randn(3, 3)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_warns_on_tensorflow_without_seed(self, tmp_path: Path) -> None:
        """import tensorflow without seed should warn."""
        f = tmp_path / "model.py"
        f.write_text("import tensorflow as tf\nx = tf.random.normal([3, 3])\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_warns_on_scipy_stats_without_seed(self, tmp_path: Path) -> None:
        """from scipy.stats without seed should warn."""
        f = tmp_path / "analysis.py"
        f.write_text("from scipy.stats import norm\nx = norm.rvs(size=10)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_no_warn_on_pythonhashseed_reference(self, tmp_path: Path) -> None:
        """PYTHONHASHSEED reference counts as seed-setting."""
        f = tmp_path / "analysis.py"
        f.write_text("import os\nimport random\nos.environ['PYTHONHASHSEED'] = '42'\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" not in stdout.lower()

    def test_no_warn_on_sklearn_random_state(self, tmp_path: Path) -> None:
        """sklearn with random_state=42 should not warn."""
        f = tmp_path / "model.py"
        f.write_text(
            "from sklearn.ensemble import RandomForestClassifier\n"
            "clf = RandomForestClassifier(random_state=42)\n"
        )
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" not in stdout.lower()

    def test_warns_on_r_file_without_seed(self, tmp_path: Path) -> None:
        """R file with sample() but no set.seed() should warn."""
        f = tmp_path / "analysis.R"
        f.write_text("x <- sample(1:100, 10)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no set.seed()" in stdout.lower() or "no seed is set" in stdout.lower()

    def test_warns_on_from_random_import(self, tmp_path: Path) -> None:
        """'from random import randint' should trigger warning."""
        f = tmp_path / "analysis.py"
        f.write_text("from random import randint\nx = randint(1, 100)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "no seed is set" in stdout.lower()

    def test_skips_test_files(self, tmp_path: Path) -> None:
        """Test files should be skipped entirely (seeds in tests are optional)."""
        f = tmp_path / "test_analysis.py"
        f.write_text("import random\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_files_in_claude_directory(self, tmp_path: Path) -> None:
        """Files inside .claude/ should be skipped."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        f = claude_dir / "analysis.py"
        f.write_text("import random\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    @given(
        module=st.sampled_from(["random", "numpy"]),
        seeded=st.booleans(),
    )
    @settings(max_examples=100)
    def test_seed_detection_property(self, module: str, seeded: bool) -> None:
        """Property: seeded -> no warning, unseeded -> warning."""
        if module == "random":
            import_line = "import random"
            seed_line = "random.seed(42)"
            use_line = "x = random.random()"
        else:
            import_line = "import numpy as np"
            seed_line = "np.random.seed(42)"
            use_line = "x = np.random.random()"

        lines = [import_line]
        if seeded:
            lines.append(seed_line)
        lines.append(use_line)
        source = "\n".join(lines) + "\n"

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "analysis.py"
            f.write_text(source)
            rc, stderr, stdout = run_hook(
                "check_random_seeds.py", _make_payload(str(f))
            )
            assert rc == 0
            if seeded:
                assert "no seed is set" not in stdout.lower()
            else:
                assert "no seed is set" in stdout.lower()


# =====================================================================
# TestCheckTestPair
# =====================================================================


class TestCheckTestPair:
    """Tests for check_test_pair.py: reminds about missing test files."""

    def test_reminds_when_no_test_file(self, tmp_path: Path) -> None:
        """Implementation file with no corresponding test should warn."""
        f = tmp_path / "utils.py"
        f.write_text("def helper(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert (
            "no matching test file" in stdout.lower()
            or "tdd reminder" in stdout.lower()
        )

    def test_no_remind_when_test_exists(self, tmp_path: Path) -> None:
        """Implementation file with corresponding test_*.py should not warn."""
        f = tmp_path / "utils.py"
        f.write_text("def helper(): pass\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_f = tests_dir / "test_utils.py"
        test_f.write_text("def test_helper(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert "tdd reminder" not in stdout.lower()

    def test_skips_manage_py(self, tmp_path: Path) -> None:
        """manage.py should be skipped."""
        f = tmp_path / "manage.py"
        f.write_text("print('manage')\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_dunder_main(self, tmp_path: Path) -> None:
        """__main__.py should be skipped."""
        f = tmp_path / "__main__.py"
        f.write_text("print('main')\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_warns_on_r_file_without_test(self, tmp_path: Path) -> None:
        """.R file without test should warn."""
        f = tmp_path / "analysis.R"
        f.write_text("x <- 1\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert (
            "no matching test file" in stdout.lower()
            or "tdd reminder" in stdout.lower()
        )

    def test_no_remind_when_r_test_exists(self, tmp_path: Path) -> None:
        """.R file with matching testthat test should not warn."""
        f = tmp_path / "analysis.R"
        f.write_text("x <- 1\n")
        testthat_dir = tmp_path / "tests" / "testthat"
        testthat_dir.mkdir(parents=True)
        test_f = testthat_dir / "test_analysis.R"
        test_f.write_text("test_that('works', { expect_equal(1, 1) })\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""

    def test_skips_files_in_claude_directory(self, tmp_path: Path) -> None:
        """Files inside .claude/ should be skipped."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        f = claude_dir / "hook.py"
        f.write_text("print('hook')\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""


# =====================================================================
# TestCheckTestPairDepth
# =====================================================================


class TestCheckTestPairDepth:
    """Tests for check_test_pair.py: directory depth boundary (2 parent levels)."""

    def test_finds_test_in_same_dir(self, tmp_path: Path) -> None:
        """Test file in same directory should be found -> no warning."""
        f = tmp_path / "utils.py"
        f.write_text("x = 1\n")
        test_f = tmp_path / "test_utils.py"
        test_f.write_text("def test_x(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert "tdd reminder" not in stdout.lower()

    def test_finds_test_one_level_up(self, tmp_path: Path) -> None:
        """Test in ../tests/test_X.py should be found -> no warning."""
        sub = tmp_path / "src"
        sub.mkdir()
        f = sub / "utils.py"
        f.write_text("x = 1\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_f = tests_dir / "test_utils.py"
        test_f.write_text("def test_x(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert "tdd reminder" not in stdout.lower()

    def test_finds_test_two_levels_up(self, tmp_path: Path) -> None:
        """Test in ../../tests/test_X.py (boundary) should be found -> no warning."""
        sub = tmp_path / "src" / "pkg"
        sub.mkdir(parents=True)
        f = sub / "utils.py"
        f.write_text("x = 1\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_f = tests_dir / "test_utils.py"
        test_f.write_text("def test_x(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert "tdd reminder" not in stdout.lower()

    def test_does_NOT_find_test_three_levels_up(self, tmp_path: Path) -> None:
        """Test in ../../../tests/test_X.py should NOT be found -> warns."""
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        f = sub / "utils.py"
        f.write_text("x = 1\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_f = tests_dir / "test_utils.py"
        test_f.write_text("def test_x(): pass\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert (
            "tdd reminder" in stdout.lower()
            or "no matching test file" in stdout.lower()
        )

    @given(
        module_name=st.from_regex(r"[a-z][a-z0-9_]{1,15}", fullmatch=True),
        depth=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=100)
    def test_within_2_levels_always_found(self, module_name: str, depth: int) -> None:
        """Property: test file within 2 parent levels is always found."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            # depth N means N directory levels between base and the source file.
            # The spec used ["src"] + [f"pkg{i}" for ...] which adds an extra level,
            # making depth=2 actually 3 levels deep and unreachable. We use plain
            # [f"d{i}" for ...] so depth N truly means N levels from base.
            parts = [f"d{i}" for i in range(depth)]
            src_dir = base
            for part in parts:
                src_dir = src_dir / part
            src_dir.mkdir(parents=True, exist_ok=True)
            f = src_dir / f"{module_name}.py"
            f.write_text("x = 1\n")
            # Create tests/ at base level
            tests_dir = base / "tests"
            tests_dir.mkdir(exist_ok=True)
            test_f = tests_dir / f"test_{module_name}.py"
            test_f.write_text("def test_x(): pass\n")

            rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
            assert rc == 0
            assert "tdd reminder" not in stdout.lower()

    @given(
        module_name=st.from_regex(r"[a-z][a-z0-9_]{1,15}", fullmatch=True),
        depth=st.integers(min_value=3, max_value=5),
    )
    @settings(max_examples=50)
    def test_beyond_2_levels_not_found(self, module_name: str, depth: int) -> None:
        """Property: test file beyond 2 parent levels is NOT found -> warns."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            parts = [f"d{i}" for i in range(depth)]
            src_dir = base
            for part in parts:
                src_dir = src_dir / part
            src_dir.mkdir(parents=True, exist_ok=True)
            f = src_dir / f"{module_name}.py"
            f.write_text("x = 1\n")
            # Create tests/ at base level — too far away
            tests_dir = base / "tests"
            tests_dir.mkdir(exist_ok=True)
            test_f = tests_dir / f"test_{module_name}.py"
            test_f.write_text("def test_x(): pass\n")

            rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
            assert rc == 0
            assert (
                "tdd reminder" in stdout.lower()
                or "no matching test file" in stdout.lower()
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "__init__.py",
            "conftest.py",
            "test_something.py",
            "something_test.py",
            "setup.py",
            "manage.py",
            "__main__.py",
        ],
    )
    def test_skip_files_never_warned(self, tmp_path: Path, filename: str) -> None:
        """Files that should always be skipped must never produce a warning."""
        f = tmp_path / filename
        f.write_text("x = 1\n")
        rc, stderr, stdout = run_hook("check_test_pair.py", _make_payload(str(f)))
        assert rc == 0
        assert "tdd reminder" not in stdout.lower()
        assert "no matching test file" not in stdout.lower()
