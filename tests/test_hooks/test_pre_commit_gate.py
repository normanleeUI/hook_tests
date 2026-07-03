"""End-to-end tests for the git-native pre-commit backstop (.githooks/pre-commit).

Unlike the Claude Code hook tests (which pipe JSON to a hook script), this
hook is a real git pre-commit hook. So we test it the way git runs it:
install the tracked source into a temp repo's .git/hooks/, stage content,
run a REAL `git commit`, and assert the commit is aborted (non-zero exit,
nothing added to history).

The `git -C <repo> commit` case is the whole reason this hook exists: that
invocation bypasses the Claude Code hook's `if: Bash(git commit*)` matcher,
but a git-native hook fires regardless of how the commit is invoked.
"""

import subprocess
from pathlib import Path

import pytest

# Tracked source of the hook (repo_root/.githooks/pre-commit).
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_SRC = REPO_ROOT / ".githooks" / "pre-commit"

# A real-length fake Anthropic key (matches sk-ant-[A-Za-z0-9_-]{20,}). Built by
# concatenation so this test file is not itself a matchable secret — otherwise
# committing it would trip scan_secrets / the pre-commit hook under test.
FAKE_SECRET = "sk-ant-" + "api03TESTKEY1234567890abcdefghijklmnop"


def _init_git_repo(repo_path: Path) -> None:
    """Init a git repo with one initial commit and the pre-commit hook installed."""
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
    (repo_path / "README.md").write_text("init")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    # Install the hook exactly like scripts/install_hooks.sh does.
    dest = repo_path / ".git" / "hooks" / "pre-commit"
    dest.write_text(HOOK_SRC.read_text())
    dest.chmod(0o755)


def _stage_file(repo_path: Path, filename: str, content: str) -> None:
    filepath = repo_path / filename
    filepath.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo_path), "add", filename],
        check=True,
        capture_output=True,
    )


def _head_count(repo_path: Path) -> int:
    """Number of commits reachable from HEAD (to prove a commit did/didn't land)."""
    out = subprocess.run(
        ["git", "-C", str(repo_path), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _init_git_repo(tmp_path)
    return tmp_path


class TestPreCommitGate:
    def test_staged_secret_aborts_plain_commit(self, git_repo: Path) -> None:
        """A staged secret aborts `git commit` (run from inside the repo)."""
        before = _head_count(git_repo)
        _stage_file(git_repo, "config.py", f"KEY = '{FAKE_SECRET}'\n")
        result = subprocess.run(
            ["git", "commit", "-m", "add secret"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "BLOCKED" in result.stderr
        assert _head_count(git_repo) == before  # nothing committed

    def test_staged_secret_aborts_git_C_commit(self, git_repo: Path) -> None:
        """The bypass that motivated this hook: `git -C <repo> commit` is a
        different invocation than `git commit*`, so the Claude Code matcher
        misses it — but the git-native hook still fires and aborts."""
        before = _head_count(git_repo)
        _stage_file(git_repo, "config.py", f"KEY = '{FAKE_SECRET}'\n")
        result = subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "add secret"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "BLOCKED" in result.stderr
        assert _head_count(git_repo) == before

    def test_staged_env_file_is_rejected(self, git_repo: Path) -> None:
        """A staged non-template .env file aborts the commit by basename."""
        before = _head_count(git_repo)
        _stage_file(git_repo, ".env", "MY_TOKEN=whatever\n")
        result = subprocess.run(
            ["git", "commit", "-m", "add env"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "BLOCKED" in result.stderr
        assert ".env" in result.stderr
        assert _head_count(git_repo) == before

    def test_template_env_file_is_allowed(self, git_repo: Path) -> None:
        """.env.example is documentation, not a secret — commit succeeds."""
        before = _head_count(git_repo)
        _stage_file(git_repo, ".env.example", "MY_TOKEN=your-token-here\n")
        result = subprocess.run(
            ["git", "commit", "-m", "add env template"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert _head_count(git_repo) == before + 1

    def test_clean_commit_succeeds(self, git_repo: Path) -> None:
        """A clean staged file commits normally (exit 0, one new commit)."""
        before = _head_count(git_repo)
        _stage_file(git_repo, "app.py", "x = 42\n")
        result = subprocess.run(
            ["git", "commit", "-m", "clean"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert _head_count(git_repo) == before + 1
