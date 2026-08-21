"""Tests for pip_audit_check.py hook.

Verifies that the PostToolUse hook runs ``uvx pip-audit`` after
dependency-changing commands (``uv add``, ``uv sync``, ``uv pip install``)
run, and silently exits for non-matching commands or interrupted commands.

Payload schema note (regression): real PostToolUse Bash payloads carry a
``tool_response`` key (NOT ``tool_result``), and that response has NO
``exitCode`` field -- its keys are ``stdout``, ``stderr``, ``interrupted``,
``isImage``, ``noOutputExpected``. The hook once gated on
``tool_result["exitCode"] == 0``, which never matched the real schema, so the
audit never ran. It now audits whenever a matching uv command ran and was not
interrupted. See ``TestPipAuditCheckRealPayloadSchema`` for the load-bearing
regression tests.

Exit codes: always 0 on PostToolUse (exit codes are cosmetic). When vulns
are found, findings are persisted to ``.hook_state/pip_audit/report.json``
so the companion guard hook (``pip_audit_guard.py``) can block future ops.

The hook has two sequential gates:
  1. Command check -- ``uv add``, ``uv sync``, or ``uv pip install`` in
     COMMAND POSITION (start of string or after a shell connector), so prose
     mentions (heredoc commit messages, echo strings) do not trigger.
  2. Interruption check -- ``tool_response.interrupted`` must be falsy.

Only when both gates pass does the hook engage and run ``uvx pip-audit``.
"""

import hashlib
import json as json_mod
import os
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import HOOKS_DIR, run_hook

HOOK = "pip_audit_check.py"


@pytest.fixture
def post_tool_payload():
    """Build a realistic PostToolUse Bash payload.

    Mirrors the real Claude Code schema: a ``tool_response`` key (not
    ``tool_result``) whose Bash payload has NO ``exitCode`` -- keys are
    ``stdout``, ``stderr``, ``interrupted``, ``isImage``, ``noOutputExpected``.
    """

    def _make(command: str, interrupted: bool = False) -> dict:
        return {
            "tool_input": {"command": command},
            "tool_response": {
                "stdout": "",
                "stderr": "",
                "interrupted": interrupted,
                "isImage": False,
                "noOutputExpected": False,
            },
        }

    return _make


def _run_hook_expecting_engagement(payload: dict, timeout: int = 15) -> str:
    """Run the hook and return stderr, tolerating TimeoutExpired.

    The engagement tests only need to verify that the hook printed
    ``[pip-audit]`` to stderr before spawning ``uvx pip-audit``. The
    subprocess may time out if pip-audit is slow, but the engagement
    message is written *before* the subprocess call, so it will appear
    in stderr either way.
    """
    try:
        _, stderr, _ = run_hook(HOOK, payload, timeout=timeout)
        return stderr
    except subprocess.TimeoutExpired as exc:
        stderr_bytes = exc.stderr or b""
        if isinstance(stderr_bytes, bytes):
            return stderr_bytes.decode("utf-8", errors="replace")
        return stderr_bytes


def _run_hook_raw_stdin(raw_stdin: str, timeout: int = 10) -> tuple[int, str, str]:
    """Invoke the hook with a raw string on stdin (bypasses json.dumps).

    Used for testing invalid-JSON handling, since the standard run_hook
    helper serializes the payload via json.dumps.
    """
    script = HOOKS_DIR / HOOK
    if not script.exists():
        pytest.skip(f"Hook script not found: {script}")

    result = subprocess.run(
        ["python3", str(script)],
        input=raw_stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ,
    )
    return result.returncode, result.stderr, result.stdout


def make_fake_uv_bin(
    tmp_path,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    export_out: str = "flask==1.0\n",
    argv_dump: str | None = None,
):
    """Create fake ``uv`` (handles ``export``) and ``uvx`` (pip-audit) on one PATH dir.

    The hook now audits the *exported project lockfile* (``uv export`` ->
    ``uvx pip-audit -r``), because bare ``uvx pip-audit`` audits uvx's isolated
    env, not the project. So the fake ``uv export`` must yield non-empty deps or
    the hook short-circuits. ``argv_dump`` (a path) captures uvx's argv so a test
    can assert pip-audit was pointed at the exported requirements (regression
    guard against the "0 packages audited" bug).
    """
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "uv").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "export" ]; then printf %s ' + repr(export_out) + "; exit 0; fi\n"
        "exit 0\n"
    )
    # Also snapshot the requirements file uvx was pointed at: the hook now
    # creates it with tempfile.mkstemp and unlinks it when the audit returns,
    # so a test can only read it from inside the fake uvx.
    dump = (
        f'printf "%s" "$*" > {argv_dump}\n'
        f'for a in "$@"; do last="$a"; done\n'
        f'cat "$last" > {argv_dump}.reqs 2>/dev/null\n'
        if argv_dump
        else ""
    )
    (fake_bin / "uvx").write_text(
        f"#!/bin/sh\n{dump}echo '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n"
    )
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "uvx").chmod(0o755)
    return str(fake_bin)


def audit_json(vuln_ids=()) -> str:
    """pip-audit ``-f json`` output for the fake uvx to emit.

    The hook now decides vulns-vs-clean by parsing this JSON (exit code alone
    can't distinguish "vulns found" from "audit couldn't run"), so fakes must
    emit the real schema: a ``dependencies`` list whose entries carry ``vulns``.
    """
    if vuln_ids:
        deps = [
            {
                "name": "pkg1",
                "version": "1.0",
                "vulns": [{"id": i, "fix_versions": []} for i in vuln_ids],
            }
        ]
    else:
        deps = [
            {"name": n, "version": "1.0", "vulns": []} for n in ("pkg1", "pkg2", "pkg3")
        ]
    return json_mod.dumps({"dependencies": deps, "fixes": []})


CLEAN_JSON = audit_json()
VULN_JSON = audit_json(["CVE-2024-1234"])


class TestPipAuditCheckExamples:
    """Explicit test cases from the test matrix."""

    def test_non_matching_command_exits_zero(self, post_tool_payload):
        """A command without uv add/sync/pip install exits 0 immediately."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload("git status"))
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_invalid_json_exits_zero(self):
        """Invalid JSON on stdin triggers the JSONDecodeError handler, exit 0."""
        code, stderr, _ = _run_hook_raw_stdin("not json")
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_empty_payload_exits_zero(self):
        """Empty payload {} has no tool_input, so command is '' -- no match."""
        code, stderr, _ = run_hook(HOOK, {})
        assert code == 0
        assert "[pip-audit]" not in stderr

    def test_interrupted_uv_add_skips_audit(self, post_tool_payload):
        """A matching uv command whose tool_response.interrupted is True skips.

        An interrupted command may have left deps half-changed; the hook
        early-returns without engaging pip-audit.
        """
        payload = post_tool_payload("uv add requests", interrupted=True)
        code, stderr, _ = run_hook(HOOK, payload)
        assert code == 0
        assert "[pip-audit]" not in stderr

    @pytest.mark.network
    def test_uv_add_with_exit_zero_engages(self, post_tool_payload):
        """uv add with exitCode=0 passes both gates and runs pip-audit.

        The hook engages and prints to stderr before spawning ``uvx pip-audit``.
        We use a short timeout and accept either normal completion or a
        TimeoutExpired -- in both cases the engagement message must appear
        in stderr.
        """
        payload = post_tool_payload("cd /project && uv add requests")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"

    @pytest.mark.network
    def test_uv_pip_install_with_exit_zero_engages(self, post_tool_payload):
        """uv pip install with exitCode=0 passes both gates and runs pip-audit."""
        payload = post_tool_payload("uv pip install requests")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"

    @pytest.mark.network
    def test_uv_sync_frozen_with_exit_zero_engages(self, post_tool_payload):
        """uv sync --frozen with exitCode=0 passes both gates and runs pip-audit."""
        payload = post_tool_payload("uv sync --frozen")
        stderr = _run_hook_expecting_engagement(payload)
        assert "[pip-audit]" in stderr, "Hook should have engaged for matching command"


class TestPipAuditCheckProperties:
    """Hypothesis property tests for gate logic."""

    non_matching_cmds = st.from_regex(
        r"[a-z][a-z0-9 _/.-]{0,50}", fullmatch=True
    ).filter(
        lambda c: "uv add" not in c and "uv sync" not in c and "uv pip install" not in c
    )
    pkg_names = st.from_regex(r"[a-z][a-z0-9_-]{0,20}", fullmatch=True)

    @given(command=non_matching_cmds)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_non_matching_commands_exit_zero(self, post_tool_payload, command):
        """Any command not containing the trigger substrings exits 0."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload(command))
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_add_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv add whose tool_response.interrupted is True skips the audit."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv add {pkg}", interrupted=True)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_sync_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv sync whose tool_response.interrupted is True skips the audit."""
        code, stderr, _ = run_hook(HOOK, post_tool_payload("uv sync", interrupted=True))
        assert code == 0
        assert "[pip-audit]" not in stderr

    @given(pkg=pkg_names)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_uv_pip_install_interrupted_returns_zero(self, post_tool_payload, pkg):
        """uv pip install whose tool_response.interrupted is True skips."""
        code, stderr, _ = run_hook(
            HOOK, post_tool_payload(f"uv pip install {pkg}", interrupted=True)
        )
        assert code == 0
        assert "[pip-audit]" not in stderr


class TestPipAuditCheckKnownBugs:
    """Known bugs in pip_audit_check.py (Step 0d)."""

    def test_non_dict_json_should_not_crash(self):
        """Step 0d fail-safe: non-dict JSON payload should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, ["not", "a", "dict"])
        assert code == 0

    def test_null_command_should_not_crash(self):
        """Step 0d fail-safe: null command should exit 0, not crash."""
        code, _, _ = run_hook(HOOK, {"tool_input": {"command": None}})
        assert code == 0


class TestPipAuditCheckGateEngagement:
    """Each trigger command should independently engage the hook.

    The hook should run pip-audit after any of: 'uv add', 'uv sync',
    or 'uv pip install'. These tests verify each command engages the
    hook on its own.
    """

    @pytest.mark.network
    def test_uv_add_alone_engages(self, post_tool_payload):
        """'uv add' alone (without 'uv sync' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv add requests")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv add' command"

    @pytest.mark.network
    def test_uv_sync_alone_engages(self, post_tool_payload):
        """'uv sync' alone (without 'uv add' or 'uv pip install') should engage."""
        payload = post_tool_payload("uv sync")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, "Hook should engage for 'uv sync' command"

    @pytest.mark.network
    def test_uv_pip_install_alone_engages(self, post_tool_payload):
        """'uv pip install' alone should engage."""
        payload = post_tool_payload("uv pip install requests")
        stderr = _run_hook_expecting_engagement(payload, timeout=10)
        assert "[pip-audit]" in stderr, (
            "Hook should engage for 'uv pip install' command"
        )


class TestPipAuditCheckCommandPosition:
    """Regression: dep commands must match in COMMAND POSITION only.

    A plain substring match once treated prose as a command -- the companion
    guard blocked a `git commit` because the heredoc commit MESSAGE contained
    "uv add". Command position = start of string or after a shell connector
    (&&, ||, ;, |, newline, $(, backtick), optionally with env-var prefixes.
    """

    def _run(self, post_tool_payload, tmp_path, command):
        """Run the hook with a fake clean-audit uvx; return (code, stderr)."""
        fake_bin = make_fake_uv_bin(tmp_path, 0, stdout=CLEAN_JSON)
        code, stderr, _ = run_hook(
            HOOK,
            post_tool_payload(command),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
            },
            timeout=15,
        )
        return code, stderr

    def test_heredoc_commit_message_mentioning_uv_add_skips(
        self, post_tool_payload, tmp_path
    ):
        command = (
            "git commit -m \"$(cat <<'EOF'\n"
            "Pin semgrep and explain why we ran uv add requests earlier\n"
            "EOF\n"
            ')"'
        )
        code, stderr = self._run(post_tool_payload, tmp_path, command)
        assert code == 0
        assert "[pip-audit]" not in stderr, "Prose in a commit message must not engage"

    def test_echo_string_mentioning_uv_sync_skips(self, post_tool_payload, tmp_path):
        code, stderr = self._run(
            post_tool_payload, tmp_path, 'echo "run uv sync later"'
        )
        assert code == 0
        assert "[pip-audit]" not in stderr, "Quoted prose must not engage the audit"

    def test_plain_uv_add_engages(self, post_tool_payload, tmp_path):
        _, stderr = self._run(post_tool_payload, tmp_path, "uv add requests")
        assert "[pip-audit]" in stderr

    def test_uv_sync_after_connector_engages(self, post_tool_payload, tmp_path):
        _, stderr = self._run(post_tool_payload, tmp_path, "cd proj && uv sync")
        assert "[pip-audit]" in stderr

    def test_uv_pip_install_requirements_engages(self, post_tool_payload, tmp_path):
        _, stderr = self._run(
            post_tool_payload, tmp_path, "uv pip install -r requirements.txt"
        )
        assert "[pip-audit]" in stderr


class TestPipAuditCheckSubprocessResults:
    """The hook decides clean-vs-vulnerable from pip-audit's JSON output.

    Clean audit (JSON with no vulns) — hook exits 0, no report.
    Vulnerabilities in the JSON — hook exits 0 (PostToolUse is cosmetic)
    and persists a report for the guard.
    Uses a fake ``uvx`` script to test without network access.
    """

    def test_audit_clean_returns_zero(self, post_tool_payload, tmp_path):
        """When pip-audit reports no vulns, hook should return 0."""
        fake_bin = make_fake_uv_bin(tmp_path, 0, stdout=CLEAN_JSON)
        # PATH: fake bin first (for uv/uvx), then real bins (for python).
        # HOOK_STATE_DIR pins state to tmp_path so the hook never writes into the
        # real repo .hook_state (that leak left a stale fake-vuln report before).
        real_path = os.environ.get("PATH", "")
        payload = post_tool_payload("uv add requests")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={
                "PATH": f"{fake_bin}:{real_path}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
            },
            timeout=15,
        )
        assert code == 0
        assert "[pip-audit]" in stderr

    def test_audit_vuln_found_exits_zero_and_reports(self, post_tool_payload, tmp_path):
        """When pip-audit finds vulns, hook exits 0 (PostToolUse is cosmetic) but reports to stderr.

        Blocking is handled by the companion guard hook (pip_audit_guard.py)
        via the state file, not by exit code on PostToolUse.
        """
        fake_bin = make_fake_uv_bin(
            tmp_path,
            1,
            stdout=VULN_JSON,
            stderr="VULNERABILITIES FOUND",
        )
        real_path = os.environ.get("PATH", "")
        payload = post_tool_payload("uv add requests")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={
                "PATH": f"{fake_bin}:{real_path}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
            },
            timeout=15,
        )
        assert code == 0, (
            "PostToolUse hook should always exit 0 (guard handles blocking)"
        )
        assert "[pip-audit]" in stderr


class TestPipAuditCheckStateFile:
    """State-file gating: pip_audit_check.py writes/clears .hook_state/pip_audit/report.json.

    When pip-audit finds vulnerabilities, the hook persists findings to a state
    file so the companion guard hook (pip_audit_guard.py) can block future
    dependency operations. When the audit is clean, any existing state file is
    deleted so the guard stops blocking.
    """

    _make_fake_uvx = staticmethod(
        make_fake_uv_bin
    )  # audits exported deps, not bare env

    def _run_with_state_dir(
        self,
        post_tool_payload,
        tmp_path,
        exit_code,
        stdout="",
        stderr_text="",
        command="uv add requests",
    ):
        """Run the hook with a fake uvx and HOOK_STATE_DIR set."""
        fake_bin = self._make_fake_uvx(tmp_path, exit_code, stdout, stderr_text)
        real_path = os.environ.get("PATH", "")
        state_dir = str(tmp_path / ".hook_state")
        payload = post_tool_payload(command)
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={
                "PATH": f"{fake_bin}:{real_path}",
                "HOOK_STATE_DIR": state_dir,
            },
            timeout=15,
        )
        return code, stderr, Path(state_dir)

    def test_vuln_found_creates_state_file(self, post_tool_payload, tmp_path):
        """When pip-audit finds vulns (fake uvx exits 1), state file is created."""
        _, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=1,
            stdout=VULN_JSON,
            stderr_text="VULNERABILITIES FOUND",
        )
        report = state_dir / "pip_audit" / "report.json"
        assert report.exists(), "State file should be created when vulns found"

    def test_clean_audit_clears_state_file(self, post_tool_payload, tmp_path):
        """When pip-audit is clean (fake uvx exits 0), existing state file is deleted."""
        state_dir = tmp_path / ".hook_state"
        report = state_dir / "pip_audit" / "report.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"vulns": "old data", "summary": "stale"}')

        _, _, state_dir_result = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=0,
            stdout=CLEAN_JSON,
        )
        report = state_dir_result / "pip_audit" / "report.json"
        assert not report.exists(), "State file should be deleted when audit is clean"

    def test_clean_audit_no_state_file_is_noop(self, post_tool_payload, tmp_path):
        """When pip-audit is clean and no state file exists, nothing crashes."""
        code, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=0,
            stdout=CLEAN_JSON,
        )
        report = state_dir / "pip_audit" / "report.json"
        assert not report.exists()
        assert code == 0

    def test_state_file_contains_summary(self, post_tool_payload, tmp_path):
        """The state file content includes vulnerability details from pip-audit."""
        _, _, state_dir = self._run_with_state_dir(
            post_tool_payload,
            tmp_path,
            exit_code=1,
            stdout=VULN_JSON,
            stderr_text="Found 1 vulnerability",
        )
        report = state_dir / "pip_audit" / "report.json"
        data = json_mod.loads(report.read_text())
        assert "vulns" in data, "State file must have 'vulns' key"
        assert "summary" in data, "State file must have 'summary' key"
        assert "CVE-2024-1234" in data["vulns"], "Vuln details should be in state file"


class TestPipAuditCheckRealPayloadSchema:
    """Regression tests locking in the tool_response schema fix.

    Real PostToolUse Bash payloads carry ``tool_response`` (not ``tool_result``)
    and that response has NO ``exitCode`` -- keys are stdout/stderr/interrupted/
    isImage/noOutputExpected. The hook previously gated on
    ``tool_result["exitCode"] == 0`` (defaulting a missing exitCode to 1), so
    against the real schema it always early-returned and never audited.

    These tests exercise the REAL schema via the ``post_tool_payload`` fixture.
    The engage-and-report test is load-bearing: it fails against the old
    exitCode gate (no engagement, no report file) and passes with the fix. A
    fake ``uvx`` on PATH stands in for the real network call.
    """

    _make_fake_uvx = staticmethod(
        make_fake_uv_bin
    )  # audits exported deps, not bare env

    def _run(self, payload, tmp_path, exit_code, stdout="", stderr_text=""):
        fake_bin = self._make_fake_uvx(tmp_path, exit_code, stdout, stderr_text)
        real_path = os.environ.get("PATH", "")
        state_dir = str(tmp_path / ".hook_state")
        code, stderr, _ = run_hook(
            HOOK,
            payload,
            env={"PATH": f"{fake_bin}:{real_path}", "HOOK_STATE_DIR": state_dir},
            timeout=15,
        )
        return code, stderr, Path(state_dir)

    def test_real_response_has_no_exitcode_key(self, post_tool_payload):
        """Sanity guard: the realistic payload must NOT carry an exitCode.

        If a future edit reintroduces exitCode, the load-bearing test below
        would stop distinguishing old from new behavior -- this pins the schema.
        """
        payload = post_tool_payload("uv add requests")
        assert "tool_result" not in payload
        assert "exitCode" not in payload["tool_response"]

    def test_real_response_vuln_found_engages_and_writes_report(
        self, post_tool_payload, tmp_path
    ):
        """LOAD-BEARING: real tool_response (no exitCode) + uv add engages the
        audit and, on a vulnerable result, writes report.json.

        Old code gated on tool_result["exitCode"] == 0; a real payload has no
        tool_result/exitCode, so it early-returned -- no [pip-audit] message and
        no report file. This asserts the fixed behavior.
        """
        payload = post_tool_payload("uv add requests")
        code, stderr, state_dir = self._run(
            payload,
            tmp_path,
            exit_code=1,
            stdout=VULN_JSON,
            stderr_text="VULNERABILITIES FOUND",
        )
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" in stderr, "Hook must engage on the real payload schema"
        assert report.exists(), "Vulnerable audit must write report.json"
        assert "CVE-2024-1234" in json_mod.loads(report.read_text())["vulns"]

    def test_real_response_clean_audit_writes_no_report(
        self, post_tool_payload, tmp_path
    ):
        """Real tool_response + uv add engages, and a clean audit leaves no
        report.json (companion guard then stops blocking)."""
        payload = post_tool_payload("uv sync")
        code, stderr, state_dir = self._run(
            payload, tmp_path, exit_code=0, stdout=CLEAN_JSON
        )
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" in stderr, "Hook must engage on the real payload schema"
        assert not report.exists(), "Clean audit must not leave a report file"

    def test_real_response_interrupted_skips_audit(self, post_tool_payload, tmp_path):
        """interrupted=True on the real schema skips the audit entirely."""
        payload = post_tool_payload("uv add requests", interrupted=True)
        code, stderr, state_dir = self._run(payload, tmp_path, exit_code=1)
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" not in stderr
        assert not report.exists()

    def test_non_uv_command_skips_audit(self, post_tool_payload, tmp_path):
        """A non-uv command (git status) with a normal response is filtered out."""
        payload = post_tool_payload("git status")
        code, stderr, state_dir = self._run(payload, tmp_path, exit_code=1)
        report = state_dir / "pip_audit" / "report.json"
        assert code == 0
        assert "[pip-audit]" not in stderr
        assert not report.exists()


class TestUvRunCoverage:
    """`uv run` implicitly re-syncs the env from uv.lock but fires on nearly every
    command -- audit it only when the lockfile's content actually changed. Closes
    the gap where deps arrive via `uv run` and no watched install command ever runs.

    Setup keys on HOOK_STATE_DIR: the hook resolves uv.lock as the state dir's
    parent / "uv.lock", so a lock dropped in tmp_path is what the hook hashes.
    """

    _fake_uvx = staticmethod(make_fake_uv_bin)  # audits exported deps, not bare env

    def _run(
        self,
        post_tool_payload,
        tmp_path,
        command,
        exit_code,
        lock_content=None,
        stored_hash=None,
        stdout="",
        stderr_text="",
    ):
        state_dir = tmp_path / ".hook_state"
        if lock_content is not None:
            (tmp_path / "uv.lock").write_text(lock_content)
        if stored_hash is not None:
            (state_dir / "pip_audit").mkdir(parents=True, exist_ok=True)
            (state_dir / "pip_audit" / "last_lock_hash").write_text(stored_hash)
        fake_bin = self._fake_uvx(tmp_path, exit_code, stdout, stderr_text)
        code, stderr, _ = run_hook(
            HOOK,
            post_tool_payload(command),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(state_dir),
            },
            timeout=15,
        )
        return code, stderr, state_dir

    def test_uv_run_audits_when_no_prior_audit(self, post_tool_payload, tmp_path):
        """First `uv run` in a project (no stored hash) audits the current lock."""
        _, stderr, state_dir = self._run(
            post_tool_payload,
            tmp_path,
            "uv run pytest",
            exit_code=1,
            lock_content="lockA\n",
            stdout=VULN_JSON,
            stderr_text="VULNS",
        )
        assert "[pip-audit]" in stderr, "uv run should engage the audit on first sight"
        assert (state_dir / "pip_audit" / "report.json").exists()

    def test_uv_run_skips_when_lock_unchanged(self, post_tool_payload, tmp_path):
        """Lock matches the last-audited hash -> no audit (the cheap common case)."""
        content = "lockA\n"
        matching = hashlib.sha256(content.encode()).hexdigest()
        _, stderr, state_dir = self._run(
            post_tool_payload,
            tmp_path,
            "uv run pytest",
            exit_code=1,  # would create a report IF it ran
            lock_content=content,
            stored_hash=matching,
            stderr_text="VULNS",
        )
        assert "[pip-audit]" not in stderr, "unchanged lock must not trigger an audit"
        assert not (state_dir / "pip_audit" / "report.json").exists()

    def test_uv_run_audits_when_lock_changed(self, post_tool_payload, tmp_path):
        """Lock differs from the stored hash -> deps moved -> audit runs."""
        stale = hashlib.sha256(b"OLD\n").hexdigest()
        _, stderr, state_dir = self._run(
            post_tool_payload,
            tmp_path,
            "cd sub && PYTHONPATH=. uv run python app.py",  # realistic compound cmd
            exit_code=1,
            lock_content="NEW-RESOLVED-DEPS\n",
            stored_hash=stale,
            stdout=VULN_JSON,
            stderr_text="VULNS",
        )
        assert "[pip-audit]" in stderr, "changed lock must trigger an audit"
        assert (state_dir / "pip_audit" / "report.json").exists()

    def test_uv_run_no_lockfile_is_noop(self, post_tool_payload, tmp_path):
        """No uv.lock at the project root -> nothing to audit, clean exit."""
        code, stderr, state_dir = self._run(
            post_tool_payload,
            tmp_path,
            "uv run pytest",
            exit_code=1,
            lock_content=None,
            stderr_text="VULNS",
        )
        assert code == 0
        assert "[pip-audit]" not in stderr
        assert not (state_dir / "pip_audit" / "report.json").exists()

    def test_uv_add_still_audits_even_when_lock_hash_matches(
        self, post_tool_payload, tmp_path
    ):
        """Regression: explicit install commands ignore the hash gate and always
        audit -- an unconditional, robust signal that deps changed."""
        content = "lockA\n"
        matching = hashlib.sha256(content.encode()).hexdigest()
        _, stderr, state_dir = self._run(
            post_tool_payload,
            tmp_path,
            "uv add requests",
            exit_code=1,
            lock_content=content,
            stored_hash=matching,
            stdout=VULN_JSON,
            stderr_text="VULNS",
        )
        assert "[pip-audit]" in stderr
        assert (state_dir / "pip_audit" / "report.json").exists()

    def test_clean_audit_records_lock_hash_for_next_skip(
        self, post_tool_payload, tmp_path
    ):
        """After auditing, the current lock hash is recorded so the next identical
        `uv run` skips cheaply."""
        content = "lockA\n"
        self._run(
            post_tool_payload,
            tmp_path,
            "uv run pytest",
            exit_code=0,  # clean audit
            lock_content=content,
            stdout=CLEAN_JSON,
        )
        recorded = (
            tmp_path / ".hook_state" / "pip_audit" / "last_lock_hash"
        ).read_text()
        assert recorded.strip() == hashlib.sha256(content.encode()).hexdigest()


class TestPipAuditAuditsProjectDeps:
    """Regression guard for the "0 packages audited" bug.

    Bare ``uvx pip-audit`` audits uvx's isolated tool env, not the project, so a
    known-vulnerable pin came back clean. The hook must instead export the
    project's locked deps and audit THAT (``uvx pip-audit -r <exported>``).
    These tests would fail against the old bare-uvx invocation.
    """

    def test_pip_audit_receives_exported_project_requirements(
        self, post_tool_payload, tmp_path
    ):
        argv_dump = tmp_path / "uvx_argv.txt"
        fake_bin = make_fake_uv_bin(
            tmp_path,
            0,
            stdout=CLEAN_JSON,
            export_out="SENTINEL_DEP==9.9\n",
            argv_dump=str(argv_dump),
        )
        code, _, _ = run_hook(
            HOOK,
            post_tool_payload("uv sync"),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
                "TMPDIR": str(tmp_path),
            },
            timeout=15,
        )
        assert code == 0
        argv = argv_dump.read_text().split()
        assert "-r" in argv, f"pip-audit must audit a requirements file, got: {argv!r}"
        # The reqs file is a per-run mkstemp path, unlinked once the audit
        # returns, so we assert on the snapshot the fake uvx took while it lived.
        assert "SENTINEL_DEP" in Path(f"{argv_dump}.reqs").read_text(), (
            "pip-audit must be pointed at the EXPORTED project deps, not the bare env"
        )

    def test_exported_reqs_file_is_unique_and_cleaned_up(
        self, post_tool_payload, tmp_path
    ):
        """Regression guard: a fixed $TMPDIR/pip_audit_reqs.txt raced between
        concurrent audits. The path must be unique per run and removed after."""
        argv_dump = tmp_path / "uvx_argv.txt"
        fake_bin = make_fake_uv_bin(
            tmp_path, 0, stdout=CLEAN_JSON, argv_dump=str(argv_dump)
        )
        code, _, _ = run_hook(
            HOOK,
            post_tool_payload("uv sync"),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
                "TMPDIR": str(tmp_path),
            },
            timeout=15,
        )
        assert code == 0
        argv = argv_dump.read_text().split()
        reqs_path = Path(argv[argv.index("-r") + 1])
        assert reqs_path.name != "pip_audit_reqs.txt", (
            "fixed temp name races concurrent audits; use a unique path"
        )
        assert not reqs_path.exists(), "temp requirements file must be cleaned up"

    def test_empty_export_reports_nothing_to_audit(self, post_tool_payload, tmp_path):
        """No exportable deps -> hook reports 'nothing to audit' and writes no
        report (it must NOT fall through to a bare-env audit)."""
        fake_bin = make_fake_uv_bin(tmp_path, 1, export_out="", stderr="VULNS")
        code, stderr, _ = run_hook(
            HOOK,
            post_tool_payload("uv sync"),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(tmp_path / ".hook_state"),
            },
            timeout=15,
        )
        assert code == 0
        assert "nothing to audit" in stderr
        assert not (tmp_path / ".hook_state" / "pip_audit" / "report.json").exists()


class TestPipAuditNetworkFailure:
    """Regression guards for the network-outage lockout bug.

    pip-audit needs the network every run (it queries PyPI/OSV live). In the
    sandbox (``allowedHosts: []``) it exits nonzero WITHOUT producing JSON.
    The hook once treated any nonzero exit as "vulns found", wrote report.json
    from the error output, and the guard then blocked all dependency
    operations on a phantom report. These tests would fail against that code:
    an unparseable audit must leave every piece of state exactly as it was.
    """

    TRACEBACK = "Traceback (most recent call last): ConnectionError"

    def _run_failure(self, post_tool_payload, tmp_path, command="uv add requests"):
        """Run the hook with a fake uvx that fails without emitting JSON."""
        fake_bin = make_fake_uv_bin(tmp_path, 1, stdout="", stderr=self.TRACEBACK)
        state_dir = tmp_path / ".hook_state"
        (tmp_path / "uv.lock").write_text("lockA\n")
        code, stderr, _ = run_hook(
            HOOK,
            post_tool_payload(command),
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "HOOK_STATE_DIR": str(state_dir),
            },
            timeout=15,
        )
        return code, stderr, state_dir

    def test_failed_audit_writes_no_report(self, post_tool_payload, tmp_path):
        """LOAD-BEARING: nonzero exit + non-JSON output must NOT arm the guard."""
        code, stderr, state_dir = self._run_failure(post_tool_payload, tmp_path)
        assert code == 0
        assert "could not run" in stderr
        assert "VULNERABILITIES" not in stderr
        assert not (state_dir / "pip_audit" / "report.json").exists(), (
            "A failed audit must not write report.json — that armed the guard "
            "and locked out all dependency ops on a phantom report"
        )

    def test_failed_audit_does_not_record_lock_hash(self, post_tool_payload, tmp_path):
        """No hash record on failure, so the next `uv run` retries the audit
        instead of skipping on a lock state that was never actually audited."""
        _, _, state_dir = self._run_failure(post_tool_payload, tmp_path)
        assert not (state_dir / "pip_audit" / "last_lock_hash").exists()

    def test_failed_audit_preserves_existing_report(self, post_tool_payload, tmp_path):
        """A failed audit must not clear a report from a previous REAL audit —
        known vulns keep blocking until a successful clean audit clears them."""
        state_dir = tmp_path / ".hook_state"
        report = state_dir / "pip_audit" / "report.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"vulns": "pkg1 1.0: CVE-2024-1234", "summary": "real"}')

        self._run_failure(post_tool_payload, tmp_path)
        assert report.exists(), "Failure must not clear a genuine prior report"
        assert "CVE-2024-1234" in report.read_text()
