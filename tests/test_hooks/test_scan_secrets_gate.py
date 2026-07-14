"""End-to-end tests for scan_secrets_on_commit.py hook.

Tests the full hook execution path: create a temporary git repo, stage
files with various content (including hypothesis-generated secrets), run
the hook via subprocess with cwd pointing at the repo, and verify exit
codes and stderr output.

This complements test_secret_patterns.py (Step 11) which tested regex
patterns in isolation. Here we verify the hook's actual subprocess
behavior against real git state.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import run_hook

HOOK = "scan_secrets_on_commit.py"

PAYLOAD = {"tool_input": {"command": "git commit -m 'test'"}}


def _init_git_repo(repo_path: Path) -> None:
    """Initialize a git repo with an initial commit at repo_path."""
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    readme = repo_path / "README.md"
    readme.write_text("init")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _stage_file(repo_path: Path, filename: str, content: str) -> None:
    """Write content to a file in the repo and stage it."""
    filepath = repo_path / filename
    filepath.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo_path), "add", filename],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    _init_git_repo(tmp_path)
    return tmp_path


# -- Hypothesis strategies --

anthropic_suffixes = st.from_regex(r"[A-Za-z0-9_-]{20,40}", fullmatch=True)
openai_suffixes = st.from_regex(r"[A-Za-z0-9]{20,40}", fullmatch=True)
aws_suffixes = st.from_regex(r"[0-9A-Z]{16}", fullmatch=True)


class TestScanSecretsGate:
    """Main test matrix for hook end-to-end behavior."""

    def test_clean_staged_file_exits_0(self, git_repo: Path) -> None:
        """Clean staged file with no secret patterns exits 0 with PASSED."""
        _stage_file(git_repo, "app.py", "x = 42\n")
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0
        assert "PASSED" in stderr

    @given(suffix=anthropic_suffixes)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_anthropic_key_exits_2(self, suffix: str) -> None:
        """Staged file containing sk-ant-<suffix> triggers exit 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)
            _stage_file(repo, "config.py", f"KEY = 'sk-ant-{suffix}'\n")
            code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(repo))
            assert code == 2
            assert "BLOCKED" in stderr

    @given(suffix=openai_suffixes)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_openai_key_exits_2(self, suffix: str) -> None:
        """Staged file containing sk-<suffix> (not ant-) triggers exit 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)
            _stage_file(repo, "config.py", f"KEY = 'sk-{suffix}'\n")
            code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(repo))
            assert code == 2
            assert "BLOCKED" in stderr

    @given(suffix=aws_suffixes)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_aws_key_exits_2(self, suffix: str) -> None:
        """Staged file containing AKIA<16 uppercase chars> triggers exit 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)
            _stage_file(repo, "config.py", f"KEY = 'AKIA{suffix}'\n")
            code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(repo))
            assert code == 2
            assert "BLOCKED" in stderr

    def test_pem_private_key_header_exits_2(self, git_repo: Path) -> None:
        """Staged file containing a PEM private key header triggers exit 2."""
        _stage_file(
            git_repo,
            "key.pem",
            "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
        )
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 2
        assert "BLOCKED" in stderr

    def test_certificate_header_exits_0(self, git_repo: Path) -> None:
        """Staged file containing BEGIN CERTIFICATE (not private key) exits 0."""
        _stage_file(
            git_repo,
            "cert.pem",
            "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n",
        )
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0
        assert "PASSED" in stderr

    def test_non_repo_directory_exits_2(self) -> None:
        """git diff fails in a non-repo directory; hook fails closed (exit 2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            code, _, _ = run_hook(HOOK, PAYLOAD, cwd=tmpdir)
            assert code == 2

    def test_openai_pattern_matches_sk_ant_without_hyphen(self, git_repo: Path) -> None:
        """sk-antAAAAA... (no hyphen after ant) should match OpenAI pattern, not Anthropic."""
        # sk-ant followed by alphanums (no hyphen) -- the negative lookahead (?!ant-) doesn't fire
        _stage_file(git_repo, "config.py", f"KEY = 'sk-ant{'A' * 20}'\n")
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 2
        assert "BLOCKED" in stderr

    @pytest.mark.parametrize(
        "secret,pattern_name",
        [
            (f"ghp_{'A' * 36}", "GitHub personal access token"),
            (f"github_pat_{'A' * 82}", "GitHub fine-grained token"),
            (f"xoxb-{'A' * 20}", "Slack token"),
            (f"AIza{'A' * 35}", "Google API key"),
        ],
    )
    def test_remaining_patterns_exit_2(
        self, git_repo: Path, secret: str, pattern_name: str
    ) -> None:
        """End-to-end test for patterns not covered by hypothesis tests."""
        _stage_file(git_repo, "config.py", f"KEY = '{secret}'\n")
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 2
        assert "BLOCKED" in stderr

    @given(content=st.from_regex(r"[a-z_][a-z0-9_ =\n]{10,100}", fullmatch=True))
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_safe_content_exits_0(self, content: str) -> None:
        """Random safe content should never trigger the hook (false positive check)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)
            _stage_file(repo, "app.py", content)
            code, _, _ = run_hook(HOOK, PAYLOAD, cwd=str(repo))
            assert code == 0

    @pytest.mark.parametrize(
        "content_template,description",
        [
            (f"sk-ant-{'A' * 19}", "Anthropic key with 19-char suffix (needs 20+)"),
            (f"sk-{'A' * 19}", "OpenAI key with 19-char suffix (needs 20+)"),
            (f"AKIA{'A' * 15}", "AWS key with 15-char suffix (needs 16)"),
        ],
    )
    def test_below_threshold_length_exits_0(
        self, git_repo: Path, content_template: str, description: str
    ) -> None:
        """Secret-like strings just below the minimum length should not trigger the hook."""
        _stage_file(git_repo, "config.py", f"KEY = '{content_template}'\n")
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0
        assert "PASSED" in stderr


class TestScanSecretsGateStep0d:
    """Step 0d edge cases: fail-closed design and empty diff."""

    def test_secret_on_removed_line_passes(self, git_repo: Path) -> None:
        """Removing a secret (diff '-' prefix) should pass.

        The hook only scans added content lines ('+' prefix), so a secret
        appearing only on a removed line is not flagged -- the developer is
        doing the right thing by deleting it.
        """
        _stage_file(git_repo, "secret.py", f"API_KEY = 'sk-ant-{'A' * 20}'\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "add secret"],
            check=True,
            capture_output=True,
        )
        # Now remove the secret line
        _stage_file(git_repo, "secret.py", "# secret removed\n")
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0
        assert "PASSED" in stderr

    def test_empty_staged_diff_exits_0(self, git_repo: Path) -> None:
        """Empty staged diff (nothing staged) exits 0."""
        code, stderr, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0
        assert "PASSED" in stderr


class TestScanSecretsGateKnownBugs:
    """Known hook bugs documented as xfail tests.

    Each test here exercises a real bug in scan_secrets_on_commit.py.
    strict=True ensures we notice when the bug gets fixed.
    """

    def test_git_error_should_fail_closed(self, git_repo: Path) -> None:
        """Hook should fail-closed (exit 2) when git diff returns an error,
        not silently pass. Currently the hook ignores result.returncode."""
        # Corrupt the repo so git diff --cached fails
        head_file = git_repo / ".git" / "HEAD"
        head_file.write_text("garbage")
        code, _, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 2

    def test_secret_like_filename_clean_content_should_pass(
        self, git_repo: Path
    ) -> None:
        """A file with a secret-like name but clean content should not trigger the hook.
        Currently the hook scans raw diff including +++ b/filename headers."""
        _stage_file(git_repo, f"sk-ant-{'A' * 24}.py", "x = 42\n")
        code, _, _ = run_hook(HOOK, PAYLOAD, cwd=str(git_repo))
        assert code == 0


class TestScanSecretsScopeGuard:
    """Regression: the hook must be INERT on Bash input outside its scope.

    Claude Code 2.1.201 stopped honoring the settings `if: Bash(git commit*)`
    gate, so the hook fires on every Bash command. The in-body scope guard
    reads the command from stdin and must exit 0 (without scanning) for
    anything that is not a real `git commit` -- even when a secret is already
    staged. These tests exercise the hook BODY, not the settings gate. Durable
    version of TESTING.md 2.16/2.21/2.23.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "echo hello",
            "git status",
            # `git add` is a different scope -- this hook must NOT scan on it.
            "git add .env",
        ],
        ids=["echo", "git-status", "git-add"],
    )
    def test_out_of_scope_command_is_inert_even_with_staged_secret(
        self, git_repo: Path, command: str
    ) -> None:
        """A staged secret must NOT block a non-commit command.

        This is the load-bearing regression: if the guard were removed, the
        hook would run `git diff --cached`, find the staged secret, and exit 2
        on an unrelated Bash command. With the guard it exits 0 without ever
        scanning.
        """
        _stage_file(git_repo, "config.py", f"KEY = 'sk-ant-{'A' * 24}'\n")
        payload = {"tool_input": {"command": command}}
        code, _, _ = run_hook(HOOK, payload, cwd=str(git_repo))
        assert code == 0, f"Expected inert exit 0 for out-of-scope: {command!r}"

    def test_git_commit_in_scope_still_blocks_staged_secret(
        self, git_repo: Path
    ) -> None:
        """Sanity counterpart: the guard must let a real `git commit` through
        so the scan still fires (exit 2) on a staged secret."""
        _stage_file(git_repo, "config.py", f"KEY = 'sk-ant-{'A' * 24}'\n")
        payload = {"tool_input": {"command": "git commit -m 'ship it'"}}
        code, stderr, _ = run_hook(HOOK, payload, cwd=str(git_repo))
        assert code == 2
        assert "BLOCKED" in stderr


class TestScanSecretsGitNotFound:
    """When git is unavailable, the hook should fail open (exit 0).

    If git is not installed, the hook cannot scan for secrets — but
    blocking all commits is worse than skipping the scan. The hook
    should let the commit proceed.
    """

    def test_git_not_found_exits_zero(self, git_repo: Path) -> None:
        """When git is not on PATH, the hook should exit 0 (not block).

        We use the project venv's python (which lives in a different
        directory from /usr/bin/git) and set PATH to only include
        that directory, so the hook's subprocess.run(["git", ...])
        raises FileNotFoundError.
        """

        venv_python = str(
            Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "python3"
        )
        venv_bin = str(Path(venv_python).parent)
        # Verify git is NOT in the venv bin directory
        assert not Path(venv_bin, "git").exists(), (
            "git found in venv bin -- test setup invalid"
        )
        code, _, _ = run_hook(
            HOOK,
            PAYLOAD,
            cwd=str(git_repo),
            interpreter=venv_python,
            env={"PATH": venv_bin},
        )
        assert code == 0
