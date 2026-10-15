"""Tests for gate_new_packages.py (claude-config #14 slopsquatting guard).

The hook asks (permissionDecision "ask", exit 0) before `uv add`,
`uv pip install`, and any uvx-style command that would fetch a package
outside KNOWN_TOOLS; everything else is silent.
"""

import json

import pytest

from tests.test_hooks.hook_runner import run_hook

HOOK = "gate_new_packages.py"


def _run(bash_payload, command: str) -> str:
    code, _, stdout = run_hook(HOOK, bash_payload(command))
    assert code == 0  # the hook never blocks; asking is JSON on stdout
    return stdout


def _reason(stdout: str) -> str:
    out = json.loads(stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask"
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command, named",
    [
        ("uv add reqeusts==1.0", "uv add reqeusts"),
        ("uv add --dev pytset", "pytset"),
        ("uv pip install foo", "uv pip install foo"),
        ("uvx cowsay --help", "cowsay"),
        ("uvx --from evil ruff check", "evil"),
        ("uvx --with=evilplugin ruff check", "evilplugin"),
        ("uv tool install sometool", "sometool"),
        ("uv tool run sometool", "sometool"),
        ("uvx --index https://evil.example ruff", "custom index"),
        ("cd /tmp && uvx cowsay", "cowsay"),
        ("FOO=1 uvx cowsay", "cowsay"),
        ("uvx --python", "could not tell"),
    ],
)
def test_asks(bash_payload, command, named):
    assert named in _reason(_run(bash_payload, command))


@pytest.mark.parametrize(
    "command",
    [
        "uvx ruff check .",
        "uvx ruff@0.15.12 format x.py",
        "uvx --python 3.12 pyright src",
        "uvx semgrep@1.172.0 scan --config rules",
        "uvx pip-audit -r requirements.txt",
        "uvx --from ruff ruff check",
        "uv tool run bandit -r src",
        "uv sync",
        "uv lock --upgrade",
        'echo "uv add x"',
        'git commit -m "uvx foo"',
        "grep -r 'uv pip install' docs",
        "ls",
    ],
)
def test_silent(bash_payload, command):
    assert _run(bash_payload, command).strip() == ""


def test_ask_is_logged_as_ask(bash_payload, tmp_path):
    code, _, _ = run_hook(
        HOOK, bash_payload("uvx cowsay"), env={"TMPDIR": str(tmp_path)}
    )
    assert code == 0
    assert "ASK" in (tmp_path / "hook_debug.log").read_text()


def test_malformed_payload_is_silent():
    code, _, stdout = run_hook(HOOK, {"tool_input": "nope"})
    assert code == 0
    assert stdout == ""
