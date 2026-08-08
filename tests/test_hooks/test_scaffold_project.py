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
