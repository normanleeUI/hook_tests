"""Logic + output-channel tests for project_health_check.py.

The hook inspects the *current working directory* (not a stdin payload) and,
when it finds gaps, emits a JSON object on stdout with a ``systemMessage`` and a
``hookSpecificOutput.additionalContext`` block. The undocumented
``hookEventName: "SessionStart"`` field inside hookSpecificOutput is what makes
additionalContext actually reach the model — so the channel assertions here are
as important as the logic ones (cf. the check_dep_freshness stderr bug).

Intent (from the hook's own docstring + CLAUDE.md health rules): stay SILENT
when the project is healthy; surface gaps, a CONTRIBUTING.md, or git warnings.
These tests assert that intent, not the current implementation's internals.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR, run_hook

sys.path.insert(0, str(HOOKS_DIR))
phc = pytest.importorskip("project_health_check")


# ── helpers ──────────────────────────────────────────────────────────────────

ALL_ITEMS = {"git", "venv", "deps", "gitignore", "readme", "claude-md", "hooks", "ci"}


def _init_git_repo(path: Path) -> None:
    """Initialize a minimal git repo at *path* with one commit (no remote)."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@t.com"), ("user.name", "T")):
        subprocess.run(
            ["git", "-C", str(path), "config", k, v], check=True, capture_output=True
        )
    (path / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "-C", str(path), "add", ".gitkeep"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _make_project(path: Path, items: set[str]) -> None:
    """Create a project directory containing exactly the named health items."""
    if "git" in items:
        _init_git_repo(path)
    if "venv" in items:
        (path / ".venv").mkdir(exist_ok=True)
    if "deps" in items:
        (path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    if "gitignore" in items:
        (path / ".gitignore").write_text(".venv/\n")
    if "readme" in items:
        (path / "README.md").write_text("# project\n")
    if "claude-md" in items:
        (path / "CLAUDE.md").write_text("# spec\n")
    if "hooks" in items:
        (path / ".claude").mkdir(exist_ok=True)
        (path / ".claude" / "settings.json").write_text("{}")
    if "ci" in items:
        (path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)


def _run_health(cwd: Path, env: dict[str, str] | None = None):
    """Invoke the hook with cwd set; payload is ignored (hook reads the FS)."""
    return run_hook(
        "project_health_check.py",
        {"hook_event_name": "SessionStart"},
        cwd=str(cwd),
        env=env,
    )


# ── detect_category (pure) ───────────────────────────────────────────────────


class TestDetectCategory:
    def test_solo_when_no_special_ancestor(self):
        assert phc.detect_category(Path("/home/u/projects/foo")) == "solo"

    def test_collab_ancestor(self):
        assert phc.detect_category(Path("/home/u/collab/foo")) == "collab"

    def test_oneoff_ancestor(self):
        assert phc.detect_category(Path("/home/u/oneOff/foo")) == "oneOff"


# ── is_project_dir (pure) ────────────────────────────────────────────────────


class TestIsProjectDir:
    def test_git_dir_is_project(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert phc.is_project_dir(tmp_path, tmp_path / "home") is True

    def test_pyproject_is_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        assert phc.is_project_dir(tmp_path, tmp_path / "home") is True

    def test_home_dir_is_skipped(self, tmp_path):
        assert phc.is_project_dir(tmp_path, tmp_path) is False

    def test_claude_config_dir_is_skipped(self, tmp_path):
        cwd = tmp_path / ".claude" / "hooks"
        assert phc.is_project_dir(cwd, tmp_path) is False

    def test_empty_dir_still_counts_as_project(self, tmp_path):
        # Intent: a fresh/empty dir should still get guided setup, so the
        # health check runs rather than staying silent.
        assert phc.is_project_dir(tmp_path, tmp_path / "home") is True


# ── check_git_state (pure-ish; drives real git) ──────────────────────────────


class TestCheckGitState:
    def test_non_git_dir_returns_empty(self, tmp_path):
        assert phc.check_git_state(tmp_path) == []

    def test_clean_repo_no_upstream_returns_empty(self, tmp_path):
        _init_git_repo(tmp_path)
        assert phc.check_git_state(tmp_path) == []

    def test_reports_stash_entries(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / ".gitkeep").write_text("dirty")  # modify a tracked file
        subprocess.run(
            ["git", "-C", str(tmp_path), "stash"], check=True, capture_output=True
        )
        warnings = phc.check_git_state(tmp_path)
        assert any("stash" in w.lower() for w in warnings)

    def test_reports_unpushed_commits(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        _init_git_repo(work)
        bare = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(work), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        branch = subprocess.run(
            ["git", "-C", str(work), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(work), "push", "-u", "origin", branch],
            check=True,
            capture_output=True,
        )
        # Commit locally without pushing → now ahead of upstream.
        (work / "new.txt").write_text("x")
        subprocess.run(
            ["git", "-C", str(work), "add", "new.txt"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "ahead"],
            check=True,
            capture_output=True,
        )
        warnings = phc.check_git_state(work)
        assert any("unpushed" in w for w in warnings)


# ── main() output + channel (subprocess) ─────────────────────────────────────


class TestProjectHealthOutput:
    def test_healthy_project_stays_silent(self, tmp_path):
        """The 1.4 case: a fully-healthy repo emits nothing on stdout."""
        _make_project(tmp_path, ALL_ITEMS)
        rc, _stderr, stdout = _run_health(tmp_path)
        assert rc == 0
        assert stdout.strip() == "", f"healthy project should be silent, got {stdout!r}"

    def test_not_a_project_dir_stays_silent(self, tmp_path):
        """When cwd is the home dir itself, the hook must no-op."""
        rc, _stderr, stdout = _run_health(tmp_path, env={"HOME": str(tmp_path)})
        assert rc == 0
        assert stdout.strip() == ""

    def test_missing_items_surface_via_sessionstart_context(self, tmp_path):
        """Missing setup items → JSON with the SessionStart-tagged context.

        Guards both the logic (missing items detected) and the channel (the
        hookEventName field that makes additionalContext reach the model).
        """
        _make_project(tmp_path, ALL_ITEMS - {"readme", "venv"})
        rc, _stderr, stdout = _run_health(tmp_path)
        assert rc == 0

        payload = json.loads(stdout)
        assert "missing" in payload["systemMessage"].lower()

        hook_specific = payload["hookSpecificOutput"]
        assert hook_specific["hookEventName"] == "SessionStart", (
            "additionalContext only reaches the model when tagged with hookEventName"
        )
        context = hook_specific["additionalContext"]
        assert "README.md" in context
        assert "virtual environment" in context

    def test_contributing_md_surfaces_even_when_healthy(self, tmp_path):
        """A CONTRIBUTING.md breaks silence (informational: read it first)."""
        _make_project(tmp_path, ALL_ITEMS)
        (tmp_path / "CONTRIBUTING.md").write_text("# contributing\n")
        rc, _stderr, stdout = _run_health(tmp_path)
        assert rc == 0

        payload = json.loads(stdout)
        assert "CONTRIBUTING" in payload["systemMessage"]
        assert "CONTRIBUTING.md" in payload["hookSpecificOutput"]["additionalContext"]

    def test_collab_mode_ignores_solo_only_items(self, tmp_path):
        """Under a collab/ ancestor, only git + .gitignore are checked — a repo
        missing README/venv/deps/CI must NOT be flagged (light mode)."""
        proj = tmp_path / "collab" / "proj"
        proj.mkdir(parents=True)
        _init_git_repo(proj)
        (proj / ".gitignore").write_text(".venv/\n")
        rc, _stderr, stdout = _run_health(proj)
        assert rc == 0
        assert stdout.strip() == "", (
            "collab light mode must not flag solo-only items (README, venv, ...)"
        )

    def test_collab_mode_flags_missing_gitignore(self, tmp_path):
        """Light mode still flags its own items and labels the mode."""
        proj = tmp_path / "collab" / "proj"
        proj.mkdir(parents=True)
        _init_git_repo(proj)  # no .gitignore created
        rc, _stderr, stdout = _run_health(proj)
        assert rc == 0

        context = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        assert ".gitignore" in context
        assert "LIGHT health-check mode" in context
