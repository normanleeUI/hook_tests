"""Direction-aware drift messaging in config_drift_check.sh.

The hook relays `sync.sh --check`'s per-file direction labels and names a
script only when every file agrees: all live-behind -> install.sh, all
live-ahead -> claude-sync, anything else (mixed, unknown, uncommitted, a
failed check) -> neither. When in sync it heals a stale or dirty
~/.claude/.last_install stamp to the full HEAD sha, but only over a clean tree.

We build a fake HOME containing a fake claude-config repo (git-initialized so
HEAD resolves, sync.sh replaced by a stub that reports drift or not) and run
the REPO copy of the hook inside it — the deployed ~/.claude copy may predate
this feature, and the fake tree keeps the test hermetic either way.
"""

from __future__ import annotations

import datetime
import os
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
    # The heal's dirty check reads the deployed path list from the repo.
    shutil.copy(REPO_HOOK.parent.parent / "deploy.paths", repo / "deploy.paths")
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
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_in_sync_is_silent(fake_home):
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    assert "unsynced change" not in run_hook(home, repo)


LABELS = {
    "behind": "live-behind: run install.sh",
    "ahead": "live-ahead: run claude-sync",
    "uncommitted": "repo-uncommitted: commit or discard the repo edit",
    "unknown": "direction unknown: no usable install stamp",
}


def drift_stub(repo: Path, *files: tuple[str, str]) -> None:
    """A sync.sh stub printing `--check` drift output for (path, label key)."""
    lines = "".join(f"  DRIFT: {path} ({LABELS[key]})\n" for path, key in files)
    (repo / "sync.sh").write_text(
        "#!/usr/bin/env bash\ncat <<'OUT'\n"
        f"=== Config drift check ===\n{lines}DRIFT_TOTAL={len(files)}\nOUT\nexit 1\n"
    )


def advises(out: str) -> tuple[bool, bool]:
    """(names install.sh, names claude-sync)."""
    return "install.sh" in out, "claude-sync" in out


def test_all_live_behind_advises_install(fake_home):
    """AC-HOOK-01."""
    home, repo = fake_home
    drift_stub(repo, ("hooks/a.py", "behind"), ("rules/b.md", "behind"))
    out = run_hook(home, repo)
    assert advises(out) == (True, False), out
    assert "hooks/a.py (live-behind)" in out, out
    assert "rules/b.md (live-behind)" in out, out


def test_all_live_ahead_advises_claude_sync(fake_home):
    """AC-HOOK-02."""
    home, repo = fake_home
    drift_stub(repo, ("settings.json", "ahead"), ("hooks/a.py", "ahead"))
    out = run_hook(home, repo)
    assert advises(out) == (False, True), out
    assert "settings.json (live-ahead)" in out, out


def test_lone_repo_uncommitted_advises_neither(fake_home):
    """AC-HOOK-03."""
    home, repo = fake_home
    drift_stub(repo, ("hooks/a.py", "uncommitted"))
    out = run_hook(home, repo)
    assert advises(out) == (False, False), out
    assert "hooks/a.py (repo-uncommitted)" in out, out


def test_mixed_directions_advise_neither(fake_home):
    """AC-HOOK-04."""
    home, repo = fake_home
    drift_stub(repo, ("hooks/a.py", "behind"), ("settings.json", "ahead"))
    out = run_hook(home, repo)
    assert advises(out) == (False, False), out
    assert "mixed" in out, out


def test_unknown_direction_advises_neither(fake_home):
    """AC-HOOK-05."""
    home, repo = fake_home
    drift_stub(repo, ("settings.json", "unknown"))
    out = run_hook(home, repo)
    assert advises(out) == (False, False), out
    assert "settings.json (direction unknown)" in out, out


@pytest.mark.parametrize(
    ("stub", "rc", "last"),
    [
        ("echo 'fatal: something broke' >&2\nexit 2", 2, "fatal: something broke"),
        ("echo 'sync.sh needs rsync'\nexit 1", 1, "sync.sh needs rsync"),
        (
            "echo '  DRIFT: hooks/a.py (live-behind: run install.sh)'\necho DRIFT_TOTAL=1\nexit 2",
            2,
            "DRIFT_TOTAL=1",
        ),
        ("exit 0", 0, ""),
    ],
)
def test_check_crash_reports_failure(fake_home, stub, rc, last):
    """AC-HOOK-06: anything but rc 0 + IN_SYNC or rc 1 + DRIFT_TOTAL is a
    failed check, not drift: stderr crash, rc 1 without a total (usage or
    missing rsync), a total with the wrong rc, rc 0 with no IN_SYNC."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(f"#!/usr/bin/env bash\n{stub}\n")
    out = run_hook(home, repo)
    assert f"drift check failed (rc {rc})" in out, out
    assert last in out, out
    assert advises(out) == (False, False), out
    assert "unsynced change(s)" not in out, out


def test_drift_total_without_lines_advises_neither(fake_home):
    """No parseable DRIFT lines means no evidence of any direction."""
    home, repo = fake_home
    out = run_hook(home, repo)  # fixture stub: DRIFT_TOTAL=2, no DRIFT lines
    assert "2 unsynced change(s)" in out, out
    assert advises(out) == (False, False), out


@pytest.mark.parametrize("old_sha", ["deadbee", "HEAD7"])
def test_in_sync_stale_stamp_heals_to_full_sha(fake_home, old_sha):
    """AC-HOOK-07: a stale sha, or the current HEAD's 7-char short form
    (old install.sh stamps), heals to the 40-hex HEAD and logs a HEAL line."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    full = head_sha(repo)
    old = full[:7] if old_sha == "HEAD7" else old_sha
    (home / ".claude/.last_install").write_text(f"2026-01-01T00:00:00-07:00 {old}\n")
    run_hook(home, repo)
    when, sha = (home / ".claude/.last_install").read_text().split()
    assert sha == full and len(sha) == 40, sha
    datetime.datetime.fromisoformat(when)  # install.sh writes `date -Iseconds`
    log = (home / "hook_debug.log").read_text()
    assert f"HEAL   stamp {old} -> {full}" in log, log


def test_in_sync_dirty_stamp_heals(fake_home):
    """AC-HOOK-08: clean tree, stamp at HEAD marked dirty -> dirty dropped."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    full = head_sha(repo)
    (home / ".claude/.last_install").write_text(
        f"2026-01-01T00:00:00-07:00 {full} dirty\n"
    )
    run_hook(home, repo)
    fields = (home / ".claude/.last_install").read_text().split()
    assert fields[1:] == [full], fields


def test_in_sync_dirty_tree_does_not_heal(fake_home):
    """AC-HOOK-11: IN_SYNC means live equals the working tree, not HEAD."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    (repo / "hooks/x.py").write_text("uncommitted\n")
    original = f"2026-01-01T00:00:00-07:00 {head_sha(repo)} dirty\n"
    (home / ".claude/.last_install").write_text(original)
    run_hook(home, repo)
    assert (home / ".claude/.last_install").read_text() == original


def test_missing_deploy_paths_skips_heal_loudly(fake_home):
    """Without deploy.paths the dirty check would cover the whole repo (or
    nothing): skip the heal and say why in the debug log."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    (repo / "deploy.paths").unlink()
    original = "2026-01-01T00:00:00-07:00 deadbee\n"
    (home / ".claude/.last_install").write_text(original)
    assert run_hook(home, repo) == ""
    assert (home / ".claude/.last_install").read_text() == original
    assert "deploy.paths missing" in (home / "hook_debug.log").read_text()


def test_drift_list_capped_at_ten(fake_home):
    """AC-HOOK-10."""
    home, repo = fake_home
    drift_stub(repo, *[(f"skills/s{i}/SKILL.md", "behind") for i in range(30)])
    out = run_hook(home, repo)
    assert out.count("(live-behind)") == 10, out
    assert "…and 20 more" in out, out


def test_in_sync_current_stamp_untouched(fake_home):
    """AC-HOOK-09: in sync with a current full stamp: no rewrite."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    original = f"2026-08-13T10:00:00-07:00 {head_sha(repo)}\n"
    (home / ".claude/.last_install").write_text(original)
    run_hook(home, repo)
    assert (home / ".claude/.last_install").read_text() == original


def test_in_sync_stale_stamp_read_only_warns_and_leaves_stamp(fake_home):
    """2026-08-29: ~/.claude/.last_install can be read-only in-sandbox. When the
    self-heal cannot write, the hook must say so (stdout notice + WARN log line)
    and leave the stamp byte-identical, rather than leak a redirect error."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    stamp = home / ".claude/.last_install"
    original = "2026-01-01T00:00:00-07:00 deadbee\n"
    stamp.write_text(original)
    stamp.chmod(0o444)
    out = run_hook(home, repo)
    assert stamp.read_text() == original
    assert "read-only" in out
    log = (home / "hook_debug.log").read_text()
    assert "WARN   stamp stale (deadbee -> " in log


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


# ── Semgrep ruleset staleness → systemMessage (2026-10-14) ─────────────────
# The refresh is a user-run networked step, so the warning must render on the
# user's screen (SessionStart `systemMessage`), not sit in model-only stdout.
# Claude Code parses stdout as JSON only when all of it is JSON, so drift text
# must move into `additionalContext` whenever the warning fires.


def _stale_ruleset(repo: Path) -> None:
    old = datetime.date.today() - datetime.timedelta(days=91)
    (repo / "semgrep_rules/RULESET_STAMP").write_text(f"date: {old.isoformat()}\n")


def test_stale_ruleset_in_sync_is_system_message_only(fake_home):
    import json

    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    _stale_ruleset(repo)
    payload = json.loads(run_hook(home, repo))
    assert "91 days old" in payload["systemMessage"]
    assert "refresh.sh" in payload["systemMessage"]
    assert "additionalContext" not in payload


def test_stale_ruleset_with_drift_keeps_drift_in_additional_context(fake_home):
    import json

    home, repo = fake_home
    _stale_ruleset(repo)
    payload = json.loads(run_hook(home, repo))
    assert "days old" in payload["systemMessage"]
    assert "2 unsynced change(s)" in payload["additionalContext"]


def test_missing_ruleset_stamp_is_system_message(fake_home):
    import json

    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    (repo / "semgrep_rules/RULESET_STAMP").unlink()
    payload = json.loads(run_hook(home, repo))
    assert "missing" in payload["systemMessage"]


def test_ruleset_at_90_days_is_silent(fake_home):
    """Boundary: the warning is strictly > 90 days."""
    home, repo = fake_home
    (repo / "sync.sh").write_text(SYNC_STUB_CLEAN)
    exactly = datetime.date.today() - datetime.timedelta(days=90)
    (repo / "semgrep_rules/RULESET_STAMP").write_text(f"date: {exactly.isoformat()}\n")
    assert run_hook(home, repo) == ""
