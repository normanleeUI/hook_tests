# Hook Test Harness

Automated test harness for verifying Claude Code hook scripts in `~/.claude/hooks/`. Tests both **hook logic** (does the script make the right decision?) and **hook wiring** (does Claude Code actually invoke the script when it should?).

## Goals

1. **Intent** — each hook addresses a real threat or enforces a real best practice
2. **Approach** — the mechanism each hook uses is appropriate for its goal
3. **Wiring** — hooks fire when they should (config validation + manual playbook)
4. **Correctness** — hooks do what they're supposed to (hypothesis + fixture tests)

## Setup

```bash
uv venv
uv sync --extra dev
```

## Running tests

```bash
pytest                    # run all tests
pytest -v                 # verbose output
pytest tests/test_hooks/  # hook tests only
```

## Directory structure

```
hook_tests/
├── pyproject.toml          # project config + dev dependencies
├── plans/                  # implementation plan
├── src/                    # trigger modules (trigger specific hooks)
├── tests/
│   └── test_hooks/         # hook logic + wiring tests
├── fixtures/               # test fixture files
├── .githooks/pre-commit    # git-native secret backstop (tracked source)
└── scripts/install_hooks.sh # installs the pre-commit hook into .git/hooks/
```

## How it works

Tests invoke hook scripts via `subprocess`, piping JSON payloads that mimic what Claude Code sends. They verify exit codes (0 = pass, 2 = block), stderr output, and side effects. Hypothesis generates diverse inputs to find edge cases.

## Git-native secret backstop

The Claude Code `scan_secrets_on_commit` hook is scoped `if: Bash(git commit*)`,
which `git -C . commit` bypasses. `.githooks/pre-commit` is a self-contained,
git-native backstop that fires at real commit time regardless of how the commit
is invoked — it scans the staged diff for secret patterns and rejects staged
non-template `.env` files, failing closed if `git diff` errors. Install it with:

```bash
bash scripts/install_hooks.sh
```

`git commit --no-verify` would skip this git hook, so it is blocked one layer
earlier by the Claude Code hook `~/.claude/hooks/block_no_verify.py`. See
[TESTING.md](TESTING.md#git-native-secret-backstop-githookspre-commit) for the
`core.hooksPath` caveat (the absolute pin breaks if the repo is moved).
