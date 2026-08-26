"""Direction-aware drift messaging in config_drift_check.sh.

The hook compares the ~/.claude/.last_install stamp (written by install.sh:
"<timestamp> <repo sha>") against the config repo's HEAD to decide which way
the drift points: same sha -> live edits need claude-sync; different sha ->
an install.sh run is pending.

We build a fake HOME containing a fake claude-config repo (git-initialized so
HEAD resolves, sync.sh replaced by a stub that reports drift or not) and run
the REPO copy of the hook inside it — the deployed ~/.claude copy may predate
this feature, and the fake tree keeps the test hermetic either way.
"""

from __future__ import annotations

import datetime
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_HOOK = Path(
    "~/projects/shared_resources/claude-config/hooks/config_drift_check.sh"
).expanduser()

SYNC_STUB_DRIFT = "#!/usr/bin/env bash\necho DRIFT_TOTAL=2\nexit 1\n"
SYNC_STUB_CLEAN = "#!/usr/bin/env bash\necho IN_SYNC\nexit 0\n"


@pytest.fixture
def fake_home(tmp_path):
    if not REPO_HOOK.exists():
        pytest.skip(f"Hook script not found: {REPO_HOOK}")
    home = tmp_path / "home"
    repo = home / "projects/shared_resources/claude-config"
    (repo / "hooks").mkdir(parents=True)
    (repo / "semgrep_rules").mkdir()
    (home / ".claude").mkdir()

    shutil.copy(REPO_HOOK, repo / "hooks/config_drift_check.sh")
    # Fresh ruleset stamp so the semgrep-staleness nudge stays quiet.
    (repo / "semgrep_rules/RULESET_STAMP").write_text(
        f"{datetime.date.today().isoformat()}\n"
    )
    sync = repo / "sync.sh"
    sync.write_text(SYNC_STUB_DRIFT)
    sync.chmod(sync.stat().st_mode | stat.S_IEXEC)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return home, repo


def run_hook(home: Path, repo: Path) -> str:
    result = subprocess.run(
        ["bash", str(repo / "hooks/config_drift_check.sh")],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(home)},
        timeout=10,
    )
    assert result.returncode == 0  # informational hook, never blocks
    return result.stdout


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_in_sync_is_silent(fake_home):
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    assert "unsynced change" not in run_hook(home, repo)


def test_drift_without_stamp_suggests_sync(fake_home):
    home, repo = fake_home
    out = run_hook(home, repo)
    assert "2 unsynced change(s)" in out
    assert "claude-sync" in out
    assert "Last install.sh" not in out


def test_drift_with_current_stamp_suggests_sync(fake_home):
    home, repo = fake_home
    (home / ".claude/.last_install").write_text(
        f"2026-08-13T10:00:00-07:00 {head_sha(repo)}\n"
    )
    out = run_hook(home, repo)
    assert "Last install.sh: 2026-08-13T10:00:00-07:00" in out
    assert "claude-sync" in out
    assert "install.sh' and restart" not in out


def test_drift_with_stale_stamp_suggests_install(fake_home):
    home, repo = fake_home
    (home / ".claude/.last_install").write_text("2026-01-01T00:00:00-07:00 deadbee\n")
    out = run_hook(home, repo)
    assert "Last install.sh" in out
    assert "install.sh" in out
    assert "restart" in out
    assert "claude-sync" not in out


def test_drift_with_stale_stamp_mentions_manual_copy_possibility(fake_home):
    """2026-08-26: the repo-moved message must also name the alternative —
    a manual-copy deploy that left the stamp stale — and point at the
    per-file sync.sh --check output, instead of asserting install.sh is
    the only fix."""
    home, repo = fake_home
    (home / ".claude/.last_install").write_text("2026-01-01T00:00:00-07:00 deadbee\n")
    out = run_hook(home, repo)
    assert "manual" in out
    assert "--check" in out


def test_in_sync_stale_stamp_self_heals_to_head_and_logs(fake_home):
    """2026-08-26 self-heal: when sync.sh --check says IN SYNC but the stamp
    sha != repo HEAD, the live content provably equals the repo — a manual
    copy moved content without moving the stamp. The hook rewrites the stamp
    to '<timestamp> <HEAD short sha>' (install.sh's format) and logs a HEAL
    decision line, so the next drift nudge names the right direction."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    (home / ".claude/.last_install").write_text("2026-01-01T00:00:00-07:00 deadbee\n")
    run_hook(home, repo)
    stamp = (home / ".claude/.last_install").read_text().strip()
    when, sha = stamp.split()
    assert sha == head_sha(repo)
    # install.sh writes `date -Iseconds` — assert the same shape.
    datetime.datetime.fromisoformat(when)
    log = (home / "hook_debug.log").read_text()
    assert f"HEAL   stamp deadbee -> {sha}" in log


def test_in_sync_current_stamp_untouched(fake_home):
    """In sync with a current stamp: no rewrite (byte-identical stamp)."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    original = f"2026-08-13T10:00:00-07:00 {head_sha(repo)}\n"
    (home / ".claude/.last_install").write_text(original)
    run_hook(home, repo)
    assert (home / ".claude/.last_install").read_text() == original


def test_debug_log_decision_line_and_dated_timestamp(fake_home):
    """Both branches log a decision line, timestamped with a date (run_hook
    sets TMPDIR=$HOME so the debug log lands in the fake home)."""
    import re

    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    run_hook(home, repo)
    log = (home / "hook_debug.log").read_text()
    assert "ALLOW  in sync" in log
    for line in log.splitlines():
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}  ", line), (
            f"undated timestamp: {line!r}"
        )
