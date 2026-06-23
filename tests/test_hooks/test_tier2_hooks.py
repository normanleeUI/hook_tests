"""Tests for tier-2 informational hooks: check_docstrings, check_random_seeds."""

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
        assert "# HOOK:DOCSTRING:" in f.read_text()

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
        assert "# HOOK:DOCSTRING:" not in f.read_text()

    def test_skips_test_files(self, tmp_path: Path) -> None:
        """Files named test_*.py should be skipped entirely."""
        f = tmp_path / "test_module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    def test_warns_on_class_without_docstring(self, tmp_path: Path) -> None:
        """Class definition without docstring should trigger warning."""
        f = tmp_path / "module.py"
        f.write_text("class MyClass:\n    def method(self):\n        pass\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:DOCSTRING:" in f.read_text()

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
        assert "# HOOK:DOCSTRING:" in f.read_text()

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
        assert "# HOOK:DOCSTRING:" not in f.read_text()

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
        assert "# HOOK:DOCSTRING:" in f.read_text()

    def test_skips_files_in_claude_directory(self, tmp_path: Path) -> None:
        """Files inside a .claude/ subdirectory should be skipped."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        f = claude_dir / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    def test_no_warn_on_trivial_two_statement_func(self, tmp_path: Path) -> None:
        """Function with only 2 statements (trivial) should not trigger warning."""
        f = tmp_path / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    return a\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:DOCSTRING:" not in f.read_text()

    def test_skips_init_py(self, tmp_path: Path) -> None:
        """__init__.py files should be skipped entirely."""
        f = tmp_path / "__init__.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    def test_skips_private_functions(self, tmp_path: Path) -> None:
        """Private functions (_helper) should not trigger warnings."""
        f = tmp_path / "module.py"
        f.write_text(
            "def _helper(x, y, z):\n    a = x + y\n    b = a * z\n    return b\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:DOCSTRING:" not in f.read_text()

    def test_skips_conftest_py(self, tmp_path: Path) -> None:
        """conftest.py should be skipped entirely."""
        f = tmp_path / "conftest.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    def test_skips_setup_py(self, tmp_path: Path) -> None:
        """setup.py should be skipped entirely."""
        f = tmp_path / "setup.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

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
        assert "# HOOK:DOCSTRING:" not in f.read_text()

    @given(
        name=st.from_regex(r"[a-z_][a-z0-9_]{1,20}", fullmatch=True),
        stmt_count=st.integers(min_value=3, max_value=8),
        has_docstring=st.booleans(),
    )
    @settings(max_examples=100)
    def test_docstring_detection_property(
        self, name: str, stmt_count: int, has_docstring: bool
    ) -> None:
        """Property: public funcs with docstring -> no injection; without -> injection."""
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
            content = f.read_text()
            if has_docstring:
                assert "# HOOK:DOCSTRING:" not in content
            else:
                assert "# HOOK:DOCSTRING:" in content

    # -- New injection-specific tests --

    def test_docstring_injects_comment_for_missing_docstring(
        self, tmp_path: Path
    ) -> None:
        """File with a non-trivial function, no docstring -> # HOOK:DOCSTRING: comment injected."""
        f = tmp_path / "module.py"
        f.write_text("def compute(x, y):\n    a = x + y\n    b = a * 2\n    return b\n")
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        assert "# HOOK:DOCSTRING: missing docstring for function 'compute'" in content

    def test_docstring_no_injection_when_all_documented(self, tmp_path: Path) -> None:
        """File where all functions have docstrings -> no # HOOK:DOCSTRING: in file."""
        f = tmp_path / "module.py"
        f.write_text(
            "def compute(x, y):\n"
            '    """Compute result."""\n'
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
            "\n"
            "def transform(data):\n"
            '    """Transform data."""\n'
            "    result = data.copy()\n"
            "    result.append(1)\n"
            "    return result\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:DOCSTRING:" not in f.read_text()

    def test_docstring_self_cleaning(self, tmp_path: Path) -> None:
        """File with stale # HOOK:DOCSTRING: comment, but function now HAS docstring -> stale comment removed."""
        f = tmp_path / "module.py"
        f.write_text(
            "# HOOK:DOCSTRING: missing docstring for function 'compute'\n"
            "def compute(x, y):\n"
            '    """Now documented."""\n'
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        assert "# HOOK:DOCSTRING:" not in content
        # The original code should remain intact (minus the stale comment)
        assert "def compute(x, y):" in content

    def test_docstring_inside_string_literal_not_corrupted(
        self, tmp_path: Path
    ) -> None:
        """File with a string literal containing '# HOOK:DOCSTRING:' text -> string preserved."""
        f = tmp_path / "module.py"
        f.write_text(
            "def compute(x, y):\n"
            '    """Compute values."""\n'
            '    MSG = "# HOOK:DOCSTRING: this is inside a string"\n'
            "    a = x + y\n"
            "    b = a * 2\n"
            "    return b\n"
        )
        rc, stderr, stdout = run_hook("check_docstrings.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        # The string literal line should be preserved -- remove_hook_comments only
        # removes lines whose stripped form starts with '# HOOK:DOCSTRING:'
        assert '    MSG = "# HOOK:DOCSTRING: this is inside a string"' in content
        # No actual HOOK comments should be injected (function has docstring)
        lines = content.splitlines()
        hook_lines = [ln for ln in lines if ln.strip().startswith("# HOOK:DOCSTRING:")]
        assert len(hook_lines) == 0


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
        assert "# HOOK:SEED:" in f.read_text()

    def test_no_warn_with_seed(self, tmp_path: Path) -> None:
        """import random with seed should not warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import random\nrandom.seed(42)\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    def test_warns_on_numpy_without_seed(self, tmp_path: Path) -> None:
        """import numpy without seed should warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import numpy as np\nx = np.random.rand()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_no_warn_on_numpy_with_seed(self, tmp_path: Path) -> None:
        """import numpy with seed should not warn."""
        f = tmp_path / "analysis.py"
        f.write_text("import numpy as np\nnp.random.seed(42)\nx = np.random.rand()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    def test_warns_on_torch_without_seed(self, tmp_path: Path) -> None:
        """import torch without seed should warn."""
        f = tmp_path / "model.py"
        f.write_text("import torch\nx = torch.randn(3, 3)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_warns_on_tensorflow_without_seed(self, tmp_path: Path) -> None:
        """import tensorflow without seed should warn."""
        f = tmp_path / "model.py"
        f.write_text("import tensorflow as tf\nx = tf.random.normal([3, 3])\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_warns_on_scipy_stats_without_seed(self, tmp_path: Path) -> None:
        """from scipy.stats without seed should warn."""
        f = tmp_path / "analysis.py"
        f.write_text("from scipy.stats import norm\nx = norm.rvs(size=10)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_no_warn_on_pythonhashseed_reference(self, tmp_path: Path) -> None:
        """PYTHONHASHSEED reference counts as seed-setting."""
        f = tmp_path / "analysis.py"
        f.write_text("import os\nimport random\nos.environ['PYTHONHASHSEED'] = '42'\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    def test_no_warn_on_sklearn_random_state(self, tmp_path: Path) -> None:
        """sklearn with random_state=42 should not warn."""
        f = tmp_path / "model.py"
        f.write_text(
            "from sklearn.ensemble import RandomForestClassifier\n"
            "clf = RandomForestClassifier(random_state=42)\n"
        )
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    def test_warns_on_r_file_without_seed(self, tmp_path: Path) -> None:
        """R file with sample() but no set.seed() should warn."""
        f = tmp_path / "analysis.R"
        f.write_text("x <- sample(1:100, 10)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_warns_on_from_random_import(self, tmp_path: Path) -> None:
        """'from random import randint' should trigger warning."""
        f = tmp_path / "analysis.py"
        f.write_text("from random import randint\nx = randint(1, 100)\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    def test_skips_test_files(self, tmp_path: Path) -> None:
        """Test files should be skipped entirely (seeds in tests are optional)."""
        f = tmp_path / "test_analysis.py"
        f.write_text("import random\nx = random.random()\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    def test_skips_files_in_claude_directory(self, tmp_path: Path) -> None:
        """Files inside .claude/ should be skipped."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        f = claude_dir / "analysis.py"
        f.write_text("import random\nx = random.random()\n")
        original = f.read_text()
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert stdout.strip() == ""
        assert f.read_text() == original

    @given(
        module=st.sampled_from(["random", "numpy"]),
        seeded=st.booleans(),
    )
    @settings(max_examples=100)
    def test_seed_detection_property(self, module: str, seeded: bool) -> None:
        """Property: seeded -> no injection, unseeded -> injection."""
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
            content = f.read_text()
            if seeded:
                assert "# HOOK:SEED:" not in content
            else:
                assert "# HOOK:SEED:" in content

    # -- New injection-specific tests --

    def test_seeds_injects_comment_for_unseeded_random(self, tmp_path: Path) -> None:
        """File with import random, no seed -> # HOOK:SEED: comment injected."""
        f = tmp_path / "analysis.py"
        f.write_text("import random\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        assert "# HOOK:SEED: random module used without seed" in content

    def test_seeds_no_injection_when_seeded(self, tmp_path: Path) -> None:
        """File with import random + random.seed(42) -> no # HOOK:SEED: in file."""
        f = tmp_path / "analysis.py"
        f.write_text("import random\nrandom.seed(42)\nx = random.random()\n")
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    def test_seeds_self_cleaning(self, tmp_path: Path) -> None:
        """Stale # HOOK:SEED: comment removed when seed is now present."""
        f = tmp_path / "analysis.py"
        f.write_text(
            "# HOOK:SEED: random module used without seed\n"
            "import random\nrandom.seed(42)\nx = random.random()\n"
        )
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        assert "# HOOK:SEED:" not in content
        assert "import random" in content

    def test_seeds_inside_string_literal_not_corrupted(self, tmp_path: Path) -> None:
        """String literal containing '# HOOK:SEED:' text is preserved, not stripped."""
        f = tmp_path / "analysis.py"
        f.write_text(
            "import random\nrandom.seed(42)\n"
            'MSG = "# HOOK:SEED: this is inside a string"\n'
            "x = random.random()\n"
        )
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        content = f.read_text()
        assert 'MSG = "# HOOK:SEED: this is inside a string"' in content
        hook_lines = [
            ln for ln in content.splitlines() if ln.strip().startswith("# HOOK:SEED:")
        ]
        assert len(hook_lines) == 0


# =====================================================================
# TestDocstringTriviality
# =====================================================================


class TestDocstringTriviality:
    """Property test: trivial functions (<=2 statements) should not warn about missing docstrings."""

    @given(n_statements=st.integers(min_value=1, max_value=6))
    @settings(max_examples=50, deadline=None)
    def test_triviality_threshold(self, n_statements: int) -> None:
        """Functions with <=2 total body statements are trivial (no warning).

        We generate n_statements assignment lines plus a return statement,
        so total_statements = n_statements + 1. The hook's threshold is
        <=2 total body statements (excluding docstring).
        """
        body = "\n".join(f"    x{i} = {i}" for i in range(n_statements))
        code = f"def public_func(arg):\n{body}\n    return arg\n"
        total_statements = n_statements + 1

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "module.py"
            f.write_text(code)
            payload = _make_payload(str(f))
            rc, stderr, stdout = run_hook("check_docstrings.py", payload)
            assert rc == 0
            content = f.read_text()
            if total_statements <= 2:
                assert "# HOOK:DOCSTRING:" not in content
            else:
                assert "# HOOK:DOCSTRING:" in content


# =====================================================================
# TestSeedDetectionContext
# =====================================================================


class TestSeedDetectionContext:
    """Test that the seed hook distinguishes real seed calls from comments and strings."""

    SEED_IN_CODE = [
        "np.random.seed(42)",
        "random.seed(12345)",
        "torch.manual_seed(0)",
        "rng = np.random.default_rng(42)",
    ]

    SEED_IN_COMMENT = [
        "# np.random.seed(42)  -- disabled for now",
        "# TODO: add random.seed() call here",
    ]

    SEED_IN_STRING = [
        's = "np.random.seed(42)"',
        "doc = '''Use np.random.seed(42) to set seed'''",
    ]

    @pytest.mark.parametrize("seed_line", SEED_IN_CODE)
    def test_real_seed_suppresses_warning(self, tmp_path: Path, seed_line: str) -> None:
        """Seed call in actual code should suppress the missing-seed warning."""
        code = f"import numpy as np\nimport random\nimport torch\n\n{seed_line}\nx = np.random.randn(10)\n"
        f = tmp_path / "gen.py"
        f.write_text(code)
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" not in f.read_text()

    @pytest.mark.parametrize("seed_line", SEED_IN_COMMENT)
    def test_commented_seed_should_still_warn(
        self, tmp_path: Path, seed_line: str
    ) -> None:
        """Commented-out seed calls should not count as real seeding per spec intent."""
        code = f"import numpy as np\n\n{seed_line}\nx = np.random.randn(10)\n"
        f = tmp_path / "gen.py"
        f.write_text(code)
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()

    @pytest.mark.parametrize("seed_line", SEED_IN_STRING)
    def test_string_seed_should_still_warn(
        self, tmp_path: Path, seed_line: str
    ) -> None:
        """Seed calls inside string literals should not count as real seeding per spec intent."""
        code = f"import numpy as np\n\n{seed_line}\nx = np.random.randn(10)\n"
        f = tmp_path / "gen.py"
        f.write_text(code)
        rc, stderr, stdout = run_hook("check_random_seeds.py", _make_payload(str(f)))
        assert rc == 0
        assert "# HOOK:SEED:" in f.read_text()
