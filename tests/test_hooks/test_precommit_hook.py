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

# Undocumented non-trivial public function: >=3 body statements so
# _is_trivial (<=2 statements) does not skip it. Finding lands on line 1.
UNDOC_CODE = "def public_fn(x):\n    a = x * 2\n    b = a + x\n    return b\n"


class TestPrecommitCharacterization:
    def test_b602_warns_but_commit_lands(self, hook_repo) -> None:
        """AC-INV-01 + AC-LEG-01: finding warns on stderr/stdout, exit 0."""
        repo, ledger = hook_repo
        before = _head_count(repo)
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "bad.py:3" in combined
        assert "B602" in combined

    def test_preexisting_finding_not_reported(self, hook_repo) -> None:
        """AC-LEG-05: a finding already in history, untouched, stays silent."""
        repo, ledger = hook_repo
        _stage(repo, "old.py", B602_CODE)
        _commit(repo, ledger, "seed finding")  # seeds the B602 into history
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
        """AC-TRL-01: trailer actively instructs a fix, not 'informational'."""
        repo, ledger = hook_repo
        _stage(repo, "bad.py", B602_CODE)
        result = _commit(repo, ledger, "add bad")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "--amend" in combined
        assert "follow-up commit" in combined
        assert "informational" not in combined

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
        assert result.returncode == 0
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
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert WARN_HEADER in combined
        assert "bad.py:3" in combined


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
    def test_added_type_error_line_warns(self, hook_repo) -> None:
        """AC-LEG-02: ONE bad line added to an existing committed file ->
        warned at exactly that line (catches 0-based off-by-one), rc 0."""
        repo, ledger = hook_repo
        _stage(repo, "typed.py", "x: int = 1\n")
        _commit(repo, ledger, "seed clean file", legs="pyright")
        before = _head_count(repo)
        _stage(repo, "typed.py", 'x: int = 1\ny: int = "s"\n')
        result = _commit(repo, ledger, "add bad line", legs="pyright")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "typed.py:2" in combined
        assert "[pyright error]" in combined
        assert "typed.py:1" not in combined

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
    def test_added_pdb_line_warns(self, hook_repo) -> None:
        """AC-LEG-03: ONE pdb.set_trace() line added to an existing committed
        file -> warned at exactly that line via the vendored ruleset, rc 0."""
        repo, ledger = hook_repo
        _stage(repo, "probe.py", "import pdb\n")
        _commit(repo, ledger, "seed clean file", legs="semgrep")
        before = _head_count(repo)
        _stage(repo, "probe.py", "import pdb\npdb.set_trace()\n")
        result = _commit(repo, ledger, "add pdb line", legs="semgrep")
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert _head_count(repo) == before + 1
        assert WARN_HEADER in combined
        assert "probe.py:2" in combined
        assert "[semgrep pdb-remove]" in combined
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
        assert mod.pyright_leg({"f.py": {1}}) == [("f.py", 1, "[pyright error] boom")]

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

    def test_mypy_repo_detects_tool_mypy(self, monkeypatch, tmp_path) -> None:
        """De-vacuouses AC-EXC-03: the opt-out predicate itself is True/False."""
        mod = _load_hook_module(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        assert mod._mypy_repo() is False
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        assert mod._mypy_repo() is True


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
