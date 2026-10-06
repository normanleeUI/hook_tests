"""Tests for scaffold_project.py — the on-demand layout scaffold.

Not an event-wired hook: project_health_check.py offers it, the model runs it
on user acceptance. Contract under test: creates only what is missing, never
overwrites, idempotent, and .gitignore is append-only.

Module-level skipif mirrors test_block_unresolved_findings.py: the suite runs
against the live ~/.claude/hooks (HOOKS_DIR), so these tests sleep until
install.sh ships the script, then wake automatically.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR

SCRIPT = "scaffold_project.py"

pytestmark = pytest.mark.skipif(
    not (HOOKS_DIR / SCRIPT).exists(),
    reason="scaffold_project.py not deployed to live hooks (claude-config install.sh pending)",
)

EXPECTED_DIRS = [
    "docs/plans",
    "docs/reviews",
    "docs/decisions",
    "docs/reports",
    "docs/notes",
    "docs/prompts",
    "tests",
]


def run_scaffold(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestFreshProject:
    def test_creates_full_layout(self, tmp_path):
        proj = tmp_path / "my-proj"
        proj.mkdir()
        result = run_scaffold(proj)

        assert result.returncode == 0
        for d in EXPECTED_DIRS:
            assert (proj / d).is_dir(), f"missing {d}"
            assert (proj / d / ".gitkeep").exists(), f"missing .gitkeep in {d}"
        # package dir derived from the directory name, sanitized
        assert (proj / "src" / "my_proj").is_dir()
        assert (proj / "CLAUDE.md").exists()
        gitignore = (proj / ".gitignore").read_text()
        for entry in (".env", "docs/prompts/", "outputs/"):
            assert entry in gitignore

    def test_leading_digit_pkg_name_prefixed(self, tmp_path):
        proj = tmp_path / "2nd-analysis"
        proj.mkdir()
        run_scaffold(proj)
        assert (proj / "src" / "_2nd_analysis").is_dir()


class TestIdempotence:
    def test_second_run_is_noop(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        run_scaffold(proj)
        snapshot = {
            p.relative_to(proj): p.read_bytes() for p in proj.rglob("*") if p.is_file()
        }

        result = run_scaffold(proj)

        assert "nothing to do" in result.stdout
        after = {
            p.relative_to(proj): p.read_bytes() for p in proj.rglob("*") if p.is_file()
        }
        assert after == snapshot


class TestNeverOverwrites:
    def test_existing_claude_md_untouched(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text("# my real spec\n")

        run_scaffold(proj)

        assert (proj / "CLAUDE.md").read_text() == "# my real spec\n"

    def test_gitignore_append_only(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".gitignore").write_text("*.log\n.env\n")

        run_scaffold(proj)

        content = (proj / ".gitignore").read_text()
        assert content.startswith("*.log\n.env\n")  # existing lines preserved
        assert content.count(".env\n") == 1  # already-present entry not duplicated
        assert "outputs/" in content  # missing entries appended


def _make_uv_project(proj: Path) -> None:
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (proj / "uv.lock").write_text("version = 1\n")


class TestGithubFiles:
    """CI + Dependabot are emitted only for uv projects — the template runs
    `uv sync --locked`, which would fail every push anywhere else."""

    def test_uv_project_gets_ci_and_dependabot(self, tmp_path):
        proj = tmp_path / "proj"
        _make_uv_project(proj)
        run_scaffold(proj)
        ci = (proj / ".github" / "workflows" / "ci.yml").read_text()
        assert "uv sync --locked" in ci
        assert "pyright" in ci and "ruff" in ci and "pytest" in ci
        assert "package-ecosystem: uv" in (proj / ".github" / "dependabot.yml").read_text()

    def test_non_uv_project_gets_no_github_files(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "renv.lock").write_text("{}")
        run_scaffold(proj)
        assert not (proj / ".github").exists()

    def test_existing_ci_untouched(self, tmp_path):
        proj = tmp_path / "proj"
        _make_uv_project(proj)
        wf = proj / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("# mine\n")
        run_scaffold(proj)
        assert (wf / "ci.yml").read_text() == "# mine\n"


class TestGithubOnly:
    """--github-only retrofits an existing repo without imposing the layout."""

    def test_writes_only_github_files(self, tmp_path):
        proj = tmp_path / "proj"
        _make_uv_project(proj)
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / SCRIPT), "--github-only"],
            cwd=proj, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert (proj / ".github" / "workflows" / "ci.yml").exists()
        assert (proj / ".github" / "dependabot.yml").exists()
        for absent in ("docs", "src", "tests", "CLAUDE.md", ".gitignore"):
            assert not (proj / absent).exists(), f"--github-only created {absent}"

    def test_non_uv_project_fails_loudly(self, tmp_path):
        """Explicitly asked for CI on a repo the template can't serve → say so,
        non-zero, rather than a silent no-op."""
        proj = tmp_path / "proj"
        proj.mkdir()
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / SCRIPT), "--github-only"],
            cwd=proj, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0
        assert "uv.lock" in result.stderr
        assert not (proj / ".github").exists()


class TestGithubUnwritable:
    """In Claude's sandbox `.github` at the project root is masked by /dev/null,
    so the model-run scaffold can't write it. A file squatting on `.github`
    reproduces that: the rest of the layout must still land, and the output
    must hand the user the `!` command instead of crashing mid-scaffold."""

    def test_skips_github_and_tells_user(self, tmp_path):
        proj = tmp_path / "proj"
        _make_uv_project(proj)
        (proj / ".github").write_text("")
        result = run_scaffold(proj)
        assert result.returncode == 0, result.stderr
        assert (proj / "CLAUDE.md").exists()
        assert "--github-only" in result.stdout

    def test_github_only_fails_loudly(self, tmp_path):
        proj = tmp_path / "proj"
        _make_uv_project(proj)
        (proj / ".github").write_text("")
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / SCRIPT), "--github-only"],
            cwd=proj, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert ".github" in result.stderr
