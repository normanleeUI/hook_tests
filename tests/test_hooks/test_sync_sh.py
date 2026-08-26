"""Loud-failure behavior in sync.sh (the 2026-08-18 silent-gap fix).

A copy that fails mid-sync must not abort the run (set -e) or vanish into
scrolled-past stderr: sync.sh now records each failed write, keeps syncing
the rest, and ends with a "SYNC INCOMPLETE" summary + exit 1.

sync.sh syncs into its own dirname (REPO_DIR derives from $0), so each test
runs a COPY of the script from a tmp_path "repo" dir and feeds it a fake
live config via --from. An unwritable destination is simulated by chmod 555
on the repo dir, with the should-succeed destination files pre-created
writable (cp truncates in place, so those still work).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_SYNC = Path("~/projects/shared_resources/claude-config/sync.sh").expanduser()


@pytest.fixture
def setup(tmp_path):
    if not REPO_SYNC.exists():
        pytest.skip(f"sync.sh not found: {REPO_SYNC}")
    repo = tmp_path / "repo"
    fake = tmp_path / "fake"
    repo.mkdir()
    fake.mkdir()
    shutil.copy(REPO_SYNC, repo / "sync.sh")
    (fake / "CLAUDE.md").write_text("global\n")
    (fake / "settings.json").write_text("{}\n")
    (fake / ".mcp.json").write_text('{"mcp": 1}\n')
    yield repo, fake
    repo.chmod(0o755)  # undo any read-only test setup so tmp_path cleans up


def run_sync(repo: Path, fake: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "sync.sh"), "--from", str(fake), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_clean_sync_exits_zero_without_summary(setup):
    repo, fake = setup
    result = run_sync(repo, fake)
    assert result.returncode == 0, result.stderr
    assert "SYNC INCOMPLETE" not in result.stdout
    assert (repo / ".mcp.json").read_text() == '{"mcp": 1}\n'


def test_unwritable_destination_fails_loudly_but_syncs_rest(setup):
    repo, fake = setup
    # Destinations that should succeed exist and are writable; .mcp.json is
    # absent, so its cp must create a file in the read-only repo dir → fails.
    (repo / "CLAUDE.global.md").write_text("old\n")
    (repo / "settings.json").write_text("old\n")
    repo.chmod(0o555)

    result = run_sync(repo, fake)
    assert result.returncode == 1
    assert "SYNC INCOMPLETE: 1 file(s)/dir(s)" in result.stdout
    assert str(repo / ".mcp.json") in result.stdout
    # The failure did not abort the run: later sections still ran, and the
    # other config files were still synced.
    assert "[5/5]" in result.stdout
    assert (repo / "CLAUDE.global.md").read_text() == "global\n"
    assert (repo / "settings.json").read_text() == "{}\n"


def test_unwritable_dir_mkdir_fails_loudly_not_abort(setup):
    """sync_dir's mkdir leg must record a failure, not abort under set -e.

    A directory source (fake/hooks) whose repo destination can't be created
    used to kill the whole run at [2/5] with no summary.
    """
    repo, fake = setup
    hooks = fake / "hooks"
    hooks.mkdir()
    (hooks / "example.sh").write_text("#!/bin/sh\n")
    (repo / "CLAUDE.global.md").write_text("old\n")
    (repo / "settings.json").write_text("old\n")
    repo.chmod(0o555)

    result = run_sync(repo, fake)
    assert result.returncode == 1
    # Both the .mcp.json cp and the hooks/ mkdir fail; the run still finishes.
    assert "SYNC INCOMPLETE: 2 file(s)/dir(s)" in result.stdout
    assert str(repo / "hooks") in result.stdout
    assert "[5/5]" in result.stdout


def test_check_mode_unaffected(setup):
    repo, fake = setup
    # Drifted: repo lacks the files → --check reports drift, exit 1.
    result = run_sync(repo, fake, "--check")
    assert result.returncode == 1
    assert "DRIFT_TOTAL=" in result.stdout
    assert "SYNC INCOMPLETE" not in result.stdout

    # In sync: exit 0 and IN_SYNC, even with a read-only repo (no writes).
    clean = run_sync(repo, fake)
    assert clean.returncode == 0
    repo.chmod(0o555)
    result = run_sync(repo, fake, "--check")
    assert result.returncode == 0
    assert "IN_SYNC" in result.stdout
