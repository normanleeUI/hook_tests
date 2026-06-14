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
└── fixtures/               # test fixture files
```

## How it works

Tests invoke hook scripts via `subprocess`, piping JSON payloads that mimic what Claude Code sends. They verify exit codes (0 = pass, 2 = block), stderr output, and side effects. Hypothesis generates diverse inputs to find edge cases.
