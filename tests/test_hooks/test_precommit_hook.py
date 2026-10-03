"""CHARACTERIZATION tests for the global bandit pre-commit hook.

These pin the CURRENT behavior of claude-config/githooks/pre-commit (the
warn-only bandit-on-added-lines skeleton) as a safety net for the #11
migration steps. They must pass against the hook as-is — they are not
TDD-red. Step 2 additions (TestStep2) DO assert the reworded trailer and
the error-path ledger line.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CLAUDE_CONFIG = Path(
    os.environ.get(
        "CLAUDE_CONFIG", Path.home() / "projects" / "shared_resources" / "claude-config"
    )
)
HOOK_SRC = CLAUDE_CONFIG / "githooks" / "pre-commit"

# B602 payload assembled from parts so THIS file never contains the literal
# flaggable string (the live hook scans lines we add when committing here).
# A static-string shell=True is only LOW severity (filtered by the hook's
# -ll); a dynamic command makes it HIGH. Finding lands on line 3.
B602_CODE = (
    "import subprocess\n"
    + 'x = "ls"\n'
    + 'subprocess.call("ls " + x, shell=Tr'
    + "ue)\n"
)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _commit(
    repo: Path,
    ledger: Path,
    msg: str,
    legs: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PRECOMMIT_LOG": str(ledger), **(extra_env or {})}
    if legs is None:
        # conftest exports PRECOMMIT_LEGS="" for suite isolation, which would
        # disable every leg; drop it so "unset = all legs" applies here.
        env.pop("PRECOMMIT_LEGS", None)
    else:
        env["PRECOMMIT_LEGS"] = legs
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", msg],
        env=env,
        capture_output=True,
        text=True,
    )


def _stage(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _run(repo, "add", name)


def _head_count(repo: Path) -> int:
    return int(_run(repo, "rev-list", "--count", "HEAD").stdout.strip())


@pytest.fixture
def hook_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Temp git repo with the hook installed, plus a tmp ledger path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _run(repo, "config", "user.email", "test@test.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("init\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "init")
    hooks_dir = repo / ".git" / "hooks"
    dest = hooks_dir / "pre-commit"
    dest.write_text(HOOK_SRC.read_text())
    dest.chmod(0o755)
    # Copy any .py siblings beside the hook and link semgrep_rules — harmless
    # now, load-bearing in later steps.
    for sib in HOOK_SRC.parent.glob("*.py"):
        shutil.copy(sib, hooks_dir / sib.name)
    (repo / ".git" / "semgrep_rules").symlink_to(CLAUDE_CONFIG / "semgrep_rules")
    # Local hooksPath shadows the live global one — mandatory.
    _run(repo, "config", "core.hooksPath", ".git/hooks")
    return repo, tmp_path / "ledger.log"


WARN_HEADER = "⚠  pre-commit (warn-only): findings on lines you added:"
BLOCK_HEADER = "✖  pre-commit BLOCKED this commit"

# Undocumented non-trivial public function: >=3 body statements so
# _is_trivial (<=2 statements) does not skip it. Finding lands on line 1.
UNDOC_CODE = "def public_fn(x):\n    a = x * 2\n    b = a + x\n    return b\n"


class TestPrecommitCharacterization:
    def test_b602_blocks_commit(self, hook_repo) -> None:
        """Blocking policy 2026-08-19: bandit finding -> stderr block, rc != 0,
        commit does NOT land, bypass hint present."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad")
        assert result.returncode != 0
        assert _head_count(repo) == before
        assert BLOCK_HEADER in result.stderr
        assert "bad.py:3" in result.stderr
        assert "B602" in result.stderr
        assert "PRECOMMIT_NO_BLOCK=1" in result.stderr

    def test_no_block_env_demotes_to_warning(self, hook_repo) -> None:
        """PRECOMMIT_NO_BLOCK=1: same finding warns, commit lands, ledger
        line is tagged BYPASSED."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad", extra_env={"PRECOMMIT_NO_BLOCK": "1"})
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert BLOCK_HEADER not in combined
        assert "B602" in combined
        assert "BYPASSED" in ledger.read_text()

    def test_preexisting_finding_not_reported(self, hook_repo) -> None:
        """AC-LEG-05: a finding already in history, untouched, stays silent."""
        repo, ledger = hook_repo
        _stage(repo, "old.py", B602_CODE)
        # bypass the block so the B602 actually lands in history
        _commit(repo, ledger, "seed finding", extra_env={"PRECOMMIT_NO_BLOCK": "1"})
        _stage(repo, "other.py", "x = 1\n")
        result = _commit(repo, ledger, "clean change")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "B602" not in combined
        assert "old.py" not in combined

    def test_readme_only_commit_silent_no_ledger(self, hook_repo) -> None:
        """AC-LEG-06: no .py staged -> no warn output, no ledger line."""
        repo, ledger = hook_repo
        _stage(repo, "NOTES.md", "notes\n")
        result = _commit(repo, ledger, "docs")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER not in combined
        assert not ledger.exists() or ledger.read_text() == ""

    def test_finding_written_to_ledger(self, hook_repo) -> None:
        """AC-LOG-01: findings commit writes finding(s) line with file:line."""
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        _commit(repo, ledger, "add bad")
        text = ledger.read_text()
        # B602 code also trips a semgrep subprocess rule, so no exact count.
        assert "BLOCKED" in text
        assert "finding(s):" in text
        assert "bad.py:3 [B602 HIGH]" in text

    def test_clean_py_commit_logged_clean(self, hook_repo) -> None:
        """AC-LOG-02: clean .py commit writes a 'clean (' ledger line."""
        repo, ledger = hook_repo
        _stage(repo, "ok.py", "x = 1\n")
        _commit(repo, ledger, "clean py")
        assert "clean (" in ledger.read_text()

    def test_live_ledger_untouched(self, hook_repo) -> None:
        """AC-LOG-03: with PRECOMMIT_LOG redirected, the live ledger is inert."""
        live = Path.home() / ".claude" / "logs" / "precommit.log"
        before = len(live.read_text().splitlines()) if live.exists() else 0
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        _commit(repo, ledger, "add bad")
        after = len(live.read_text().splitlines()) if live.exists() else 0
        assert after == before


class TestStep2:
    def test_warn_trailer_instructs_fix(self, hook_repo) -> None:
        """AC-TRL-01: warn trailer actively instructs a fix, not 'informational'.
        Uses a docstring finding — bandit findings now block instead of warn."""
        repo, ledger = hook_repo
        _stage(repo, "mod.py", UNDOC_CODE)
        result = _commit(repo, ledger, "add mod", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "--amend" in combined
        assert "follow-up commit" in combined
        assert "informational" not in combined

    def test_block_trailer_instructs_fix(self, hook_repo) -> None:
        """Block message must be actionable (fix + restage) and name the bypass."""
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad")
        assert result.returncode != 0
        assert "git add" in result.stderr
        assert "commit again" in result.stderr
        assert "PRECOMMIT_NO_BLOCK=1" in result.stderr

    def test_internal_error_writes_ledger_line(self, tmp_path) -> None:
        """AC-LOG-04: outside a git repo the except path logs error:, exit 0."""
        ledger = tmp_path / "ledger.log"
        nonrepo = tmp_path / "nonrepo"
        nonrepo.mkdir()
        result = subprocess.run(
            [sys.executable, str(HOOK_SRC)],
            cwd=nonrepo,
            env={**os.environ, "PRECOMMIT_LOG": str(ledger)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Traceback" not in result.stderr
        assert "error:" in ledger.read_text()


class TestStep3:
    def test_test_file_excluded(self, hook_repo) -> None:
        """AC-EXC-01: staged test_foo.py with a B602 probe -> no warning."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "test_foo.py", B602_CODE)
        # legs="bandit": semgrep (Step 7) has NO test-file gate by design and
        # would legitimately warn on this probe — isolate the bandit exclusion.
        result = _commit(repo, ledger, "add test file", legs="bandit")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER not in combined
        assert "B602" not in combined

    def test_claude_dir_excluded(self, hook_repo) -> None:
        """AC-EXC-02: staged .claude/x.py with a B602 probe -> no warning."""
        repo, ledger = hook_repo
        (repo / ".claude").mkdir()
        _stage(repo, ".claude/x.py", B602_CODE)
        result = _commit(repo, ledger, "add claude file")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER not in combined
        assert "B602" not in combined

    def test_claudette_dir_still_scanned(self, hook_repo) -> None:
        """.claude excludes by path PART — a claudette/ dir is still scanned."""
        repo, ledger = hook_repo
        (repo / "claudette").mkdir()
        _stage(repo, "claudette/x.py", B602_CODE)
        result = _commit(repo, ledger, "add claudette file")
        combined = result.stdout + result.stderr
        assert result.returncode != 0  # scanned -> bandit finding -> blocked
        assert "claudette/x.py:3" in combined
        assert "B602" in combined

    def test_uvx_stub_failure_swallowed(self, hook_repo, tmp_path) -> None:
        """AC-INV-03: broken uvx resolver -> commit lands, no traceback."""
        repo, ledger = hook_repo
        stub_dir = tmp_path / "stubbin"
        stub_dir.mkdir()
        stub = stub_dir / "uvx"
        stub.write_text("#!/bin/sh\nexit 97\n")
        stub.chmod(0o755)
        before = _head_count(repo)
        _stage(repo, "bad.py", B602_CODE)
        env = {**os.environ, "PRECOMMIT_LOG": str(ledger)}
        env.pop("PRECOMMIT_LEGS", None)
        env["PATH"] = f"{stub_dir}:{env['PATH']}"
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "add bad"],
            env=env,
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in combined
        # Proves the broken stub was actually on the resolution path: a working
        # bandit would have warned on the B602 probe.
        assert WARN_HEADER not in combined

    def test_legs_knob_empty_disables_bandit(self, hook_repo) -> None:
        """PRECOMMIT_LEGS='' -> no legs run, B602 probe commits silently."""
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad", legs="")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER not in combined
        assert "B602" not in combined

    def test_legs_knob_unknown_value_disables_bandit(self, hook_repo) -> None:
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad", legs="nosuchleg")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "B602" not in combined

    def test_legs_knob_bandit_enables_bandit(self, hook_repo) -> None:
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad", legs="bandit")
        assert result.returncode != 0
        assert BLOCK_HEADER in result.stderr
        assert "bad.py:3" in result.stderr


class TestStep5:
    def test_undocumented_fn_warns_commit_lands(self, hook_repo) -> None:
        """AC-LEG-04: new undocumented public fn -> warn names file:line, rc 0."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "mod.py", UNDOC_CODE)
        result = _commit(repo, ledger, "add mod", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "mod.py:1" in combined
        assert "[docstring]" in combined
        assert "missing docstring for function 'public_fn'" in combined

    def test_docstring_finding_in_ledger(self, hook_repo) -> None:
        repo, ledger = hook_repo
        _stage(repo, "mod.py", UNDOC_CODE)
        result = _commit(repo, ledger, "add mod", legs="docstring")
        assert result.returncode == 0
        text = ledger.read_text()
        # exact count pins the finding(s) line (not an "error: docstring..." line)
        assert "1 finding(s):" in text
        assert "mod.py:1" in text
        assert "[docstring]" in text

    def test_preexisting_undocumented_fn_silent(self, hook_repo) -> None:
        """AC-LEG-05 scope negative: finding predating the commit stays silent."""
        repo, ledger = hook_repo
        _stage(repo, "mod.py", UNDOC_CODE)
        _commit(repo, ledger, "seed undocumented fn", legs="docstring")
        _stage(repo, "other.py", "x = 1\n")
        result = _commit(repo, ledger, "clean change", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "public_fn" not in combined
        assert "mod.py" not in combined

    def test_missing_sibling_module_commit_lands(self, tmp_path) -> None:
        """AC-INV-05: no docstring_analysis.py sibling -> rc 0, ledger error:."""
        repo = tmp_path / "bare_repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        _run(repo, "config", "user.email", "test@test.com")
        _run(repo, "config", "user.name", "Test")
        (repo / "README.md").write_text("init\n")
        _run(repo, "add", ".")
        _run(repo, "commit", "-m", "init")
        # Deliberately BARE install: only the hook file, no .py siblings.
        dest = repo / ".git" / "hooks" / "pre-commit"
        dest.write_text(HOOK_SRC.read_text())
        dest.chmod(0o755)
        _run(repo, "config", "core.hooksPath", ".git/hooks")
        ledger = tmp_path / "ledger.log"
        before = _head_count(repo)
        _stage(repo, "mod.py", UNDOC_CODE)
        result = _commit(repo, ledger, "add mod", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in combined
        text = ledger.read_text()
        assert "error:" in text
        assert "docstring" in text

    def test_findings_sorted_by_line(self, hook_repo) -> None:
        """ast.walk is breadth-first; the leg must print in line order."""
        repo, ledger = hook_repo
        # Class on line 1 (undocumented), nested method line 2, second
        # top-level fn line 7 — walk yields [1, 7, 2]; sorted -> 1, 2, 7.
        code = (
            "class Thing:\n"
            "    def method(self, x):\n"
            "        a = x * 2\n"
            "        b = a + x\n"
            "        return b\n"
            "\n"
            "def later_fn(x):\n"
            "    a = x * 2\n"
            "    b = a + x\n"
            "    return b\n"
        )
        _stage(repo, "multi.py", code)
        result = _commit(repo, ledger, "add multi", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        p1 = combined.index("multi.py:1")
        p2 = combined.index("multi.py:2")
        p7 = combined.index("multi.py:7")
        assert p1 < p2 < p7

    def test_test_file_docstring_silent(self, hook_repo) -> None:
        """should_skip keeps test_*.py docstring-silent."""
        repo, ledger = hook_repo
        _stage(repo, "test_probe.py", UNDOC_CODE)
        result = _commit(repo, ledger, "add test file", legs="docstring")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER not in combined
        assert "public_fn" not in combined


class TestStep6:
    def test_added_type_error_line_blocks(self, hook_repo) -> None:
        """AC-LEG-02 (hardened 2026-10-02): ONE bad line added to an existing
        committed file -> BLOCKED at exactly that line (catches 0-based
        off-by-one); a month of warn-only pyright findings went unactioned."""
        repo, ledger = hook_repo
        _stage(repo, "typed.py", "x: int = 1\n")
        _commit(repo, ledger, "seed clean file", legs="pyright")
        before = _head_count(repo)
        _stage(repo, "typed.py", 'x: int = 1\ny: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="pyright")
        assert result.returncode != 0
        assert _head_count(repo) == before
        assert BLOCK_HEADER in result.stderr
        assert WARN_HEADER not in result.stdout + result.stderr
        assert "typed.py:2" in result.stderr
        assert "[pyright error]" in result.stderr
        assert "typed.py:1" not in result.stdout + result.stderr
        assert "BLOCKED" in ledger.read_text()

    def test_mypy_repo_under_subdir_opts_out_pyright(self, hook_repo) -> None:
        """The [tool.mypy] opt-out is decided by the NEAREST pyproject.toml
        walking up from the staged file, not the repo root — universo keeps
        its Python project under backend/ and was silently pyright-checked."""
        repo, ledger = hook_repo
        (repo / "backend").mkdir()
        (repo / "backend" / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        _run(repo, "add", "backend/pyproject.toml")
        _stage(repo, "backend/typed.py", 'y: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="pyright")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "pyright" not in combined
        text = ledger.read_text() if ledger.exists() else ""
        assert "error:" not in text

    def test_mypy_repo_opts_out(self, hook_repo) -> None:
        """AC-EXC-03: [tool.mypy] in repo pyproject -> pyright leg silent."""
        repo, ledger = hook_repo
        (repo / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        _run(repo, "add", "pyproject.toml")
        _stage(repo, "typed.py", 'y: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="pyright")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER not in combined
        assert "pyright" not in combined
        # Guard against a vacuous pass (pyright missing would also be silent):
        # the ledger must show a clean scan, not a pyright error line.
        text = ledger.read_text() if ledger.exists() else ""
        assert "error:" not in text

    def test_broken_venv_pyright_swallowed(self, hook_repo) -> None:
        """AC-INV-04: repo .venv pyright stub emits non-JSON -> commit lands,
        no traceback, ledger error line (loud-failure rule)."""
        repo, ledger = hook_repo
        stub = repo / ".venv" / "bin" / "pyright"
        stub.parent.mkdir(parents=True)
        stub.write_text("#!/bin/sh\necho 'not json{'\n")
        stub.chmod(0o755)
        before = _head_count(repo)
        _stage(repo, "typed.py", 'y: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="pyright")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in combined
        assert WARN_HEADER not in combined
        assert "error:" in ledger.read_text()


class TestStep7:
    def test_added_warning_line_warns(self, hook_repo) -> None:
        """Integration with REAL semgrep + the vendored ruleset: a WARNING-
        severity finding on an added line warns and the commit lands; the
        pre-existing line is not reported. Uses string-concat-in-list — the
        2026-10-02 tier audit promoted pdb-remove to blocking (see
        TestTierPolicy), so this test moved to a rule that stays advisory."""
        repo, ledger = hook_repo
        _stage(repo, "probe.py", "y = 1\n")
        _commit(repo, ledger, "seed clean file", legs="semgrep")
        before = _head_count(repo)
        _stage(repo, "probe.py", 'y = 1\nx = ["a" "b"]\n')
        result = _commit(repo, ledger, "add implicit concat", legs="semgrep")
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "probe.py:2" in combined
        assert "[semgrep string-concat-in-list]" in combined
        assert "probe.py:1" not in combined

    def test_missing_ruleset_loud_but_commit_lands(self, tmp_path) -> None:
        """AC-INV-02: hook installed WITHOUT semgrep_rules sibling -> rc 0,
        commit lands, stderr names the ruleset, ledger error: line."""
        repo = tmp_path / "norules_repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        _run(repo, "config", "user.email", "test@test.com")
        _run(repo, "config", "user.name", "Test")
        (repo / "README.md").write_text("init\n")
        _run(repo, "add", ".")
        _run(repo, "commit", "-m", "init")
        hooks_dir = repo / ".git" / "hooks"
        dest = hooks_dir / "pre-commit"
        dest.write_text(HOOK_SRC.read_text())
        dest.chmod(0o755)
        for sib in HOOK_SRC.parent.glob("*.py"):
            shutil.copy(sib, hooks_dir / sib.name)
        # Deliberately NO .git/semgrep_rules symlink — the guard under test.
        _run(repo, "config", "core.hooksPath", ".git/hooks")
        ledger = tmp_path / "ledger.log"
        before = _head_count(repo)
        _stage(repo, "probe.py", "import pdb\npdb.set_trace()\n")
        result = _commit(
            repo,
            ledger,
            "add pdb",
            legs="semgrep",
            extra_env={"TMPDIR": str(tmp_path)},
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in combined
        assert "ruleset" in result.stderr
        text = ledger.read_text()
        assert "error:" in text
        assert "semgrep ruleset missing" in text
        debug_log = tmp_path / "hook_debug.log"
        assert "semgrep ruleset missing" in debug_log.read_text()


class TestSemgrepLegUnit:
    def test_command_shape(self, monkeypatch, tmp_path) -> None:
        """AC-SEM-01: local --config (never 'auto'), metrics off, no version
        check — the offline/no-telemetry contract."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        rules = tmp_path / "semgrep_rules" / "rules"
        rules.mkdir(parents=True)
        (rules / "python.yaml").write_text("rules: []\n")
        monkeypatch.setattr(mod, "_SEMGREP_RULES", rules)
        calls = _canned_pyright(monkeypatch, mod, '{"results": []}')
        assert mod.semgrep_leg({"f.py": {1}}) == []
        cmd = calls[0]
        assert cmd[cmd.index("--config") + 1].endswith("semgrep_rules/rules")
        assert "--metrics=off" in cmd
        assert "--disable-version-check" in cmd
        assert "auto" not in cmd
        assert "f.py" in cmd

    def test_missing_rules_returns_empty(self, monkeypatch, tmp_path, capsys) -> None:
        """Guard: missing rules dir -> [], no subprocess call, stderr warning."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_SEMGREP_RULES", tmp_path / "nope" / "rules")
        calls = _canned_pyright(monkeypatch, mod, '{"results": []}')
        assert mod.semgrep_leg({"f.py": {1}}) == []
        assert calls == []
        assert "ruleset" in capsys.readouterr().err

    def test_error_severity_blocks_warning_does_not(
        self, monkeypatch, tmp_path
    ) -> None:
        """Blocking policy 2026-08-19: ERROR-severity findings carry block=True,
        WARNING-severity block=False."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        rules = tmp_path / "semgrep_rules" / "rules"
        rules.mkdir(parents=True)
        (rules / "python.yaml").write_text("rules: []\n")
        monkeypatch.setattr(mod, "_SEMGREP_RULES", rules)
        payload = (
            '{"results": ['
            '{"path": "f.py", "start": {"line": 1}, "check_id": "x.err",'
            ' "extra": {"message": "bad", "severity": "ERROR"}},'
            '{"path": "f.py", "start": {"line": 2}, "check_id": "x.warn",'
            ' "extra": {"message": "meh", "severity": "WARNING"}}]}'
        )
        _canned_pyright(monkeypatch, mod, payload)
        assert mod.semgrep_leg({"f.py": {1, 2}}) == [
            ("f.py", 1, "[semgrep err] bad", True),
            ("f.py", 2, "[semgrep warn] meh", False),
        ]


# ── pyright_leg unit tests (monkeypatched subprocess.run, canned JSON) ─────


def _canned_pyright(monkeypatch, mod, payload: str) -> list:
    """Monkeypatch subprocess.run to return canned pyright stdout; capture cmd."""
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class P:
            stdout = payload

        return P()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


class TestLogOneRecordPerLine:
    def test_embedded_newlines_collapsed(self, monkeypatch, tmp_path) -> None:
        """2026-10-02: pyright messages carry embedded newlines, which spread one
        finding over several ledger lines and broke the grep-based read-outs.
        _log must keep the ledger one record per line."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        ledger = tmp_path / "ledger.log"
        monkeypatch.setattr(mod, "_LOG", ledger)
        mod._log("pyright: line one\n  continuation\nline three")
        lines = ledger.read_text().splitlines()
        assert len(lines) == 1
        assert "line one" in lines[0] and "line three" in lines[0]


class TestSuppressionLeg:
    def test_markers_on_added_lines_block_justified_ones_pass(
        self, monkeypatch, tmp_path
    ) -> None:
        """2026-10-02: the Edit|Write hook never fires for Bash-made edits, so
        suppression markers must be caught at commit. Unjustified markers on
        added lines block; the same justification escapes as
        block_suppressions.py pass; markers inside strings or docstrings (test
        payloads, docs describing the markers) are not comments."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        lines = [
            '"""Docs describing `# noqa` and `# type: ignore` markers.',  # 1 docstring
            'More docstring: use `# nosec` sparingly."""',  # 2 docstring
            "x = 1  # noqa",  # 3 BLOCK
            "y = 2  # noqa: E501  # noqa-reason: generated table",  # 4 ok
            "import os  # noqa: E402",  # 5 ok (pre-approved)
            "z: int = 's'  # type: ignore",  # 6 BLOCK
            "w: int = 's'  # type: ignore[assignment]  # known-issue: upstream stub",  # 7 ok
            "subprocess.call(cmd, shell=True)  # nosec",  # 8 BLOCK
            "v = 1  # pyright: ignore",  # 9 BLOCK
            "def dead():  # pragma: no cover",  # 10 BLOCK
            "@pytest.mark.skip",  # 11 BLOCK
            "@pytest.mark.skipif(sys.platform == 'win32', reason='posix only')",  # 12 ok
            "@pytest.mark.xfail(strict=True)",  # 13 BLOCK
            'PAYLOAD = "x = 1  # noqa\\n"',  # 14 ok: inside a string
            "a = 1  # unrelated comment",  # 15 ok
            "b = 2  # NOQA",  # 16 not added -> ignored
        ]
        added = set(range(1, 16))
        hits = mod._suppression_hits(lines, added)
        assert [n for n, _ in hits] == [3, 6, 8, 9, 10, 11, 13]
        msgs = dict(hits)
        assert "noqa-reason" in msgs[3]
        assert "known-issue" in msgs[6]
        assert "skipif" in msgs[11]

    def test_noqa_on_added_line_blocks_commit(self, hook_repo) -> None:
        """Integration: an added `# noqa` blocks with the [suppression] tag and
        the fix-or-justify instruction; a pre-approved `# noqa: E402` lands."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "mod.py", "import os  # noqa: E402\nprint(os.sep)\n")
        result = _commit(repo, ledger, "pre-approved e402", legs="suppression")
        assert result.returncode == 0, result.stderr
        assert _head_count(repo) == before + 1
        _stage(repo, "mod.py", "import os  # noqa: E402\nprint(os.sep)  # noqa\n")
        result = _commit(repo, ledger, "dodge the linter", legs="suppression")
        assert result.returncode == 1
        assert _head_count(repo) == before + 1
        assert "[suppression]" in result.stderr
        assert "mod.py:2" in result.stderr
        assert "noqa-reason" in result.stderr
        assert "BLOCKED" in ledger.read_text()

    def test_preexisting_marker_not_reported(self, hook_repo) -> None:
        """Diff-scoped: a `# noqa` already in HEAD does not block a commit that
        adds an unrelated clean line (legacy stays legacy)."""
        repo, ledger = hook_repo
        _stage(repo, "legacy.py", "x = 1  # noqa\n")
        _commit(repo, ledger, "seed legacy", legs="")  # no legs: lands regardless
        before = _head_count(repo)
        _stage(repo, "legacy.py", "x = 1  # noqa\ny = 2\n")
        result = _commit(repo, ledger, "clean addition", legs="suppression")
        assert result.returncode == 0, result.stderr
        assert _head_count(repo) == before + 1


class TestTierPolicy:
    """2026-10-02 tier audit: per-rule promotions out of the discarded tiers."""

    def test_bandit_runs_at_l_and_promotes_hardcoded_password(
        self, monkeypatch, tmp_path
    ) -> None:
        """Command uses -l (all severities); the leg keeps MEDIUM/HIGH plus
        B105-B107, and drops the rest of LOW (B101 assert) silently."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        payload = (
            '{"results": ['
            '{"filename": "f.py", "line_number": 1, "test_id": "B101",'
            ' "issue_severity": "LOW", "issue_text": "assert used"},'
            '{"filename": "f.py", "line_number": 2, "test_id": "B105",'
            ' "issue_severity": "LOW", "issue_text": "hardcoded password"},'
            '{"filename": "f.py", "line_number": 3, "test_id": "B602",'
            ' "issue_severity": "HIGH", "issue_text": "shell=True"}]}'
        )
        calls = _canned_proc(monkeypatch, mod, payload, returncode=1)
        hits = mod.bandit_leg("f.py", {1, 2, 3})
        assert [(h[1], h[3]) for h in hits] == [(2, True), (3, True)]
        assert "B105" in hits[0][2]
        cmd = calls[0][0]
        assert "-l" in cmd and "-ll" not in cmd

    def test_hardcoded_password_blocks_commit(self, hook_repo) -> None:
        """Integration with REAL bandit: B105 is LOW severity, so -ll used to
        drop it; now an added `password = "..."` blocks."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "cfg.py", 'password = "correct-horse-battery"\n')
        result = _commit(repo, ledger, "hardcode it", legs="bandit")
        assert result.returncode == 1, result.stdout + result.stderr
        assert _head_count(repo) == before
        assert "B105" in result.stderr

    def test_low_assert_still_dropped_in_commit(self, hook_repo) -> None:
        """Real bandit: a bare assert (B101 LOW) neither blocks nor warns —
        the rest of LOW is discarded, not demoted to the dead warn channel."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "chk.py", "x = 1\nassert x == 1\n")
        result = _commit(repo, ledger, "assert", legs="bandit")
        assert result.returncode == 0, result.stderr
        assert _head_count(repo) == before + 1
        assert "B101" not in result.stdout + result.stderr

    def test_semgrep_promoted_warning_blocks(self, monkeypatch, tmp_path) -> None:
        """A WARNING rule in the promoted set blocks; another WARNING rule
        stays advisory; the set holds exactly the five audited IDs."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        rules = tmp_path / "semgrep_rules" / "rules"
        rules.mkdir(parents=True)
        (rules / "python.yaml").write_text("rules: []\n")
        monkeypatch.setattr(mod, "_SEMGREP_RULES", rules)
        payload = (
            '{"results": ['
            '{"path": "f.py", "start": {"line": 1},'
            ' "check_id": "python.lang.correctness.pdb.pdb-remove",'
            ' "extra": {"message": "pdb left in", "severity": "WARNING"}},'
            '{"path": "f.py", "start": {"line": 2},'
            ' "check_id": "python.lang.correctness.string-concat-in-list",'
            ' "extra": {"message": "missing comma?", "severity": "WARNING"}}]}'
        )
        _canned_pyright(monkeypatch, mod, payload)
        assert mod.semgrep_leg({"f.py": {1, 2}}) == [
            ("f.py", 1, "[semgrep pdb-remove] pdb left in", True),
            ("f.py", 2, "[semgrep string-concat-in-list] missing comma?", False),
        ]
        assert mod._SEMGREP_PROMOTED_WARNINGS == {
            "pdb-remove",
            "dict-del-while-iterate",
            "sync-sleep-in-async-code",
            "test-is-missing-assert",
            "file-object-redefined-before-close",
        }

    def test_semgrep_pdb_blocks_commit(self, hook_repo) -> None:
        """Integration with REAL semgrep + the vendored ruleset: an added
        `pdb.set_trace()` (WARNING in the ruleset) now BLOCKS instead of
        warning — the Step 7 characterization test's inverse."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "dbg.py", "import pdb\npdb.set_trace()\n")
        result = _commit(repo, ledger, "debugger left in", legs="semgrep")
        assert result.returncode == 1, result.stdout + result.stderr
        assert _head_count(repo) == before
        assert "pdb-remove" in result.stderr


class TestPyrightLegUnit:
    def test_warning_severity_excluded(self, monkeypatch, tmp_path) -> None:
        """Errors only: a warning-severity diagnostic produces no finding."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        payload = (
            '{"generalDiagnostics": [{"file": "%s/f.py", "severity": "warning",'
            ' "range": {"start": {"line": 0}}, "message": "unused import"}]}' % tmp_path
        )
        _canned_pyright(monkeypatch, mod, payload)
        assert mod.pyright_leg({"f.py": {1}}) == []

    def test_shapeless_json_no_diagnostics_key(self, monkeypatch, tmp_path) -> None:
        """Valid JSON without generalDiagnostics -> [], not KeyError."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        _canned_pyright(monkeypatch, mod, '{"summary": {}}')
        assert mod.pyright_leg({"f.py": {1}}) == []

    def test_shapeless_diagnostic_missing_range(self, monkeypatch, tmp_path) -> None:
        """A diagnostic missing range/message is skipped; the rest survive."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        payload = (
            '{"generalDiagnostics": ['
            '{"file": "%s/f.py", "severity": "error"},'
            '{"file": "%s/f.py", "severity": "error",'
            ' "range": {"start": {"line": 0}}, "message": "boom"}]}'
            % (tmp_path, tmp_path)
        )
        _canned_pyright(monkeypatch, mod, payload)
        assert mod.pyright_leg({"f.py": {1}}) == [
            ("f.py", 1, "[pyright error] boom", True)
        ]

    def test_pythonpath_only_when_venv_exists(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "f.py").write_text("x = 1\n")
        calls = _canned_pyright(monkeypatch, mod, "{}")
        mod.pyright_leg({"f.py": {1}})
        assert "--pythonpath" not in calls[0]
        venv_py = tmp_path / ".venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("")
        mod.pyright_leg({"f.py": {1}})
        assert calls[1][calls[1].index("--pythonpath") + 1] == str(venv_py)

    def test_uses_mypy_detects_tool_mypy_at_nearest_root(
        self, monkeypatch, tmp_path
    ) -> None:
        """De-vacuouses AC-EXC-03: the opt-out predicate itself is True/False,
        and it reads the NEAREST pyproject (backend/) over the repo root."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "f.py").write_text("x = 1\n")
        assert mod._project_root("f.py") is None
        assert mod._uses_mypy(None) is False
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        assert mod._project_root("f.py") == tmp_path
        assert mod._uses_mypy(tmp_path) is False
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        (backend / "g.py").write_text("x = 1\n")
        assert mod._project_root("backend/g.py") == backend
        assert mod._uses_mypy(backend) is True
        # The root project is still not a mypy project — nearest wins per file.
        assert mod._uses_mypy(mod._project_root("f.py")) is False


# ── mypy / ruff / pytest legs (2026-10-02 hardening) ──────────────────────


def _canned_proc(monkeypatch, mod, stdout: str, returncode: int = 0) -> list:
    """Like _canned_pyright, but the fake CompletedProcess also carries
    returncode/stderr and the call records kwargs (cwd matters for mypy)."""
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


def _stub_tool(repo: Path, name: str, body: str) -> Path:
    """Write an executable .venv/bin/<name> stub the hook's _resolve_cmd /
    _find_venv_python parent-walk will pick over uvx."""
    stub = repo / ".venv" / "bin" / name
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


class TestMypyLeg:
    def test_error_lines_on_added_lines_block(self, monkeypatch, tmp_path) -> None:
        """Only `error:` lines on added lines become findings; they block; the
        process runs with cwd=root so mypy reads that root's [tool.mypy]."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "backend"
        root.mkdir()
        (root / "pyproject.toml").write_text("[tool.mypy]\n")
        (root / "f.py").write_text("x = 1\n")
        payload = (
            "f.py:2:1: error: boom  [assignment]\n"
            "f.py:3: note: just a hint\n"
            "f.py:5: error: not on an added line  [misc]\n"
        )
        calls = _canned_proc(monkeypatch, mod, payload, returncode=1)
        assert mod.mypy_leg(root, {"backend/f.py": {1, 2}}) == [
            ("backend/f.py", 2, "[mypy error] boom  [assignment]", True)
        ]
        cmd, kwargs = calls[0]
        assert kwargs["cwd"] == root
        assert "--no-error-summary" in cmd
        assert cmd[-1] == "f.py", "paths are handed to mypy relative to its root"

    def test_mypy_crash_exit_is_logged_and_skipped(self, monkeypatch, tmp_path) -> None:
        """Exit 2+ means mypy itself failed (bad config, crash): no findings,
        an error: ledger line, never a block (global-hook invariant)."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "f.py").write_text("x = 1\n")
        _canned_proc(monkeypatch, mod, "f.py:1: error: looks real", returncode=2)
        assert mod.mypy_leg(tmp_path, {"f.py": {1}}) == []
        assert "error: mypy exited 2" in (tmp_path / "unit-ledger.log").read_text()

    def test_mypy_repo_type_error_blocks_commit(self, hook_repo) -> None:
        """Integration: [tool.mypy] repo + a mypy stub reporting an error on
        the added line -> commit BLOCKED with [mypy error]."""
        repo, ledger = hook_repo
        (repo / "pyproject.toml").write_text("[tool.mypy]\n")
        _run(repo, "add", "pyproject.toml")
        _stub_tool(
            repo, "mypy", 'echo "typed.py:1:1: error: boom  [assignment]"\nexit 1\n'
        )
        before = _head_count(repo)
        _stage(repo, "typed.py", 'y: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="mypy")
        assert result.returncode != 0
        assert _head_count(repo) == before
        assert BLOCK_HEADER in result.stderr
        assert "typed.py:1" in result.stderr
        assert "[mypy error] boom" in result.stderr

    def test_mypy_stub_garbage_commit_lands(self, hook_repo) -> None:
        """AC-INV-04 for the mypy leg: a broken tool exits 2 with noise ->
        commit lands, no traceback, error: ledger line."""
        repo, ledger = hook_repo
        (repo / "pyproject.toml").write_text("[tool.mypy]\n")
        _run(repo, "add", "pyproject.toml")
        _stub_tool(repo, "mypy", "echo 'segfault-ish nonsense'\nexit 2\n")
        before = _head_count(repo)
        _stage(repo, "typed.py", 'y: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="mypy")
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in result.stdout + result.stderr
        assert "error: mypy exited 2" in ledger.read_text()


class TestRuffLeg:
    def test_only_added_line_violations_block(self, monkeypatch, tmp_path) -> None:
        """Canned ruff JSON: a hit on an added line blocks, a hit on a
        pre-existing line is dropped (diff-scoping), code lands in the tag."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "f.py").write_text("x = 1\n")
        payload = (
            '[{"filename": "%s/f.py", "code": "F821", "message": "Undefined name `y`",'
            ' "location": {"row": 1, "column": 1}},'
            ' {"filename": "%s/f.py", "code": "E722", "message": "bare except",'
            ' "location": {"row": 7, "column": 1}}]' % (tmp_path, tmp_path)
        )
        _canned_proc(monkeypatch, mod, payload, returncode=1)
        assert mod.ruff_leg({"f.py": {1, 2}}) == [
            ("f.py", 1, "[ruff F821] Undefined name `y`", True)
        ]

    def test_c901_pushed_globally_on_cli(self, monkeypatch, tmp_path) -> None:
        """2026-10-02 (radon (7) closure): the complexity trip-wire rides the
        CLI so it applies on top of every project's own ruff config."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        calls = _canned_pyright(monkeypatch, mod, "[]")
        assert mod.ruff_leg({"f.py": {1}}) == []
        cmd = calls[0]
        assert cmd[cmd.index("--extend-select") + 1] == "C901"
        assert cmd[cmd.index("--config") + 1] == "lint.mccabe.max-complexity=15"

    def test_new_over_complex_function_blocks_commit(self, hook_repo) -> None:
        """Integration with REAL ruff: a newly added function with CC 17
        blocks (C901 > 15) even though the repo configures no ruff rules;
        one with CC 11 lands — the threshold is 15, not ruff's default 10."""
        repo, ledger = hook_repo
        real_ruff = shutil.which("ruff")
        if real_ruff is None:
            pytest.skip("real ruff not found on PATH")
        stub = repo / ".venv" / "bin" / "ruff"
        stub.parent.mkdir(parents=True)
        stub.symlink_to(real_ruff)

        def branchy(n: int) -> str:
            body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n))
            return f"def f(x):\n{body}    return -1\n"

        before = _head_count(repo)
        _stage(repo, "mod.py", branchy(10))  # CC 11: over ruff's default, under ours
        result = _commit(repo, ledger, "eleven branches", legs="ruff")
        assert result.returncode == 0, result.stderr
        assert _head_count(repo) == before + 1
        _stage(repo, "big.py", branchy(16))  # CC 17
        result = _commit(repo, ledger, "seventeen branches", legs="ruff")
        assert result.returncode == 1
        assert _head_count(repo) == before + 1
        assert "[ruff C901]" in result.stderr
        assert "big.py:1" in result.stderr

    def test_non_json_output_logged_and_skipped(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        _canned_proc(monkeypatch, mod, "ruff: not json", returncode=2)
        assert mod.ruff_leg({"f.py": {1}}) == []
        assert (
            "error: ruff emitted non-JSON" in (tmp_path / "unit-ledger.log").read_text()
        )

    def test_bare_except_on_added_line_blocks_commit(self, hook_repo) -> None:
        """Integration with REAL ruff (E722 is in ruff's default rule set):
        the bare-except guarantee that used to live in the Stop hook
        (whole-file, nagging) now lives here, diff-scoped and blocking."""
        repo, ledger = hook_repo
        real_ruff = shutil.which("ruff")
        if real_ruff is None:
            pytest.skip("real ruff not found on PATH")
        stub = repo / ".venv" / "bin" / "ruff"
        stub.parent.mkdir(parents=True)
        stub.symlink_to(real_ruff)
        _stage(repo, "app.py", "x = 1\n")
        _commit(repo, ledger, "seed clean file", legs="ruff")
        before = _head_count(repo)
        _stage(repo, "app.py", "x = 1\ntry:\n    y = 2\nexcept:\n    pass\n")
        result = _commit(repo, ledger, "add bare except", legs="ruff")
        assert result.returncode != 0
        assert _head_count(repo) == before
        assert BLOCK_HEADER in result.stderr
        assert "[ruff E722]" in result.stderr
        assert "app.py:4" in result.stderr
        assert "app.py:1" not in result.stderr


OPT_IN = "[tool.claude-precommit]\npytest = true\n"


class TestPytestLeg:
    def test_opt_in_predicate(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        pp = tmp_path / "pyproject.toml"
        assert mod._pytest_opt_in(tmp_path) is False  # missing file
        pp.write_text("[project]\nname = 'x'\n")
        assert mod._pytest_opt_in(tmp_path) is False
        pp.write_text("[tool.claude-precommit]\npytest = false\n")
        assert mod._pytest_opt_in(tmp_path) is False
        pp.write_text(OPT_IN)
        assert mod._pytest_opt_in(tmp_path) is True
        pp.write_text("[tool.claude-precommit\npytest = true\n")  # malformed
        assert mod._pytest_opt_in(tmp_path) is False
        assert "unreadable" in (tmp_path / "unit-ledger.log").read_text()

    def _opted_in_repo(self, hook_repo, stub_body: str, log: Path):
        repo, ledger = hook_repo
        (repo / "pyproject.toml").write_text(OPT_IN)
        _run(repo, "add", "pyproject.toml")
        _stub_tool(repo, "python", f'echo "python $@" >> {log}\n' + stub_body)
        _stage(repo, "mod.py", "x = 1\n")
        return repo, ledger

    def test_failing_suite_blocks_commit(self, hook_repo, tmp_path) -> None:
        log = tmp_path / "python_calls.log"
        repo, ledger = self._opted_in_repo(
            hook_repo,
            'echo "FAILED tests/test_x.py::test_y"\necho "1 failed in 0.10s"\nexit 1\n',
            log,
        )
        before = _head_count(repo)
        result = _commit(repo, ledger, "add mod", legs="pytest")
        assert result.returncode != 0
        assert _head_count(repo) == before
        assert BLOCK_HEADER in result.stderr
        assert "[pytest] 1 failed in 0.10s" in result.stderr
        assert "FAILED tests/test_x.py::test_y" in result.stderr, (
            "output tail reaches the committer"
        )
        assert "-m pytest -x -q" in log.read_text()
        # One ledger record per line: the multi-line pytest tail went to stderr,
        # the finding itself is the one-line summary. root is relative to the repo.
        assert (
            "BLOCKED 1 finding(s): .:0 [pytest] 1 failed in 0.10s" in ledger.read_text()
        )

    def test_passing_suite_commit_lands(self, hook_repo, tmp_path) -> None:
        log = tmp_path / "python_calls.log"
        repo, ledger = self._opted_in_repo(
            hook_repo, 'echo "3 passed in 0.10s"\nexit 0\n', log
        )
        before = _head_count(repo)
        result = _commit(repo, ledger, "add mod", legs="pytest")
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert BLOCK_HEADER not in result.stderr
        assert "-m pytest -x -q" in log.read_text()

    def test_no_tests_collected_warns_only(self, hook_repo, tmp_path) -> None:
        log = tmp_path / "python_calls.log"
        repo, ledger = self._opted_in_repo(
            hook_repo, 'echo "no tests ran"\nexit 5\n', log
        )
        before = _head_count(repo)
        result = _commit(repo, ledger, "add mod", legs="pytest")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "[pytest] no tests collected" in combined

    def test_without_opt_in_pytest_never_runs(self, hook_repo, tmp_path) -> None:
        repo, ledger = hook_repo
        log = tmp_path / "python_calls.log"
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        _run(repo, "add", "pyproject.toml")
        _stub_tool(repo, "python", f'echo "python $@" >> {log}\nexit 1\n')
        _stage(repo, "mod.py", "x = 1\n")
        result = _commit(repo, ledger, "add mod", legs="pytest")
        assert result.returncode == 0
        assert not log.exists(), "python stub must not be invoked without the opt-in"

    def test_legs_knob_disables_pytest(self, hook_repo, tmp_path) -> None:
        log = tmp_path / "python_calls.log"
        repo, ledger = self._opted_in_repo(hook_repo, "exit 1\n", log)
        result = _commit(repo, ledger, "add mod", legs="bandit")
        assert result.returncode == 0
        assert not log.exists()

    def test_malformed_pyproject_commit_lands_with_error_line(
        self, hook_repo, tmp_path
    ) -> None:
        repo, ledger = hook_repo
        (repo / "pyproject.toml").write_text("[tool.claude-precommit\npytest = true\n")
        _run(repo, "add", "pyproject.toml")
        _stage(repo, "mod.py", "x = 1\n")
        before = _head_count(repo)
        result = _commit(repo, ledger, "add mod", legs="pytest")
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert "Traceback" not in result.stdout + result.stderr
        assert "error:" in ledger.read_text() and "unreadable" in ledger.read_text()


# ── added_lines() unit tests (monkeypatched _git, canned diff text) ────────


def _load_hook_module(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PRECOMMIT_LOG", str(tmp_path / "unit-ledger.log"))
    loader = importlib.machinery.SourceFileLoader(
        "precommit_hook_under_test", str(HOOK_SRC)
    )
    spec = importlib.util.spec_from_file_location(
        "precommit_hook_under_test", HOOK_SRC, loader=loader
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAddedLines:
    def test_single_hunk(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "_git", lambda *a: "@@ -1,2 +5,3 @@\n+a\n+b\n+c\n")
        assert mod.added_lines("f.py") == {5, 6, 7}

    def test_multi_hunk(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        diff = "@@ -1,1 +2,2 @@\n+a\n+b\n@@ -10,0 +20,2 @@\n+c\n+d\n"
        monkeypatch.setattr(mod, "_git", lambda *a: diff)
        assert mod.added_lines("f.py") == {2, 3, 20, 21}

    def test_no_count_defaults_to_one(self, monkeypatch, tmp_path) -> None:
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "_git", lambda *a: "@@ -3 +7 @@\n+x\n")
        assert mod.added_lines("f.py") == {7}
