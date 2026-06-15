# Interactive Hook Wiring Playbook

Manual QA checklist for verifying that Claude Code hooks **actually fire** when expected during a live session. This does NOT test hook logic (automated tests cover that). This tests whether Claude Code's runtime dispatching correctly invokes hooks based on matchers, `if:` conditions, and event types.

## Prerequisites

- [ ] Claude Code CLI installed and working
- [ ] Working directory is `hook_tests/` (this project)
- [ ] `~/.claude/settings.json` contains all 19 hook definitions
- [ ] Python virtual environment activated (`uv sync` or equivalent)
- [ ] All hook scripts are executable (`chmod +x hooks/*.py hooks/*.sh`)
- [ ] Git repo is initialized with a remote configured
- [ ] Fixture files exist in `src/` and `fixtures/` (see list at bottom)

## Observation Guide

| Code | Meaning | How to confirm |
|------|---------|----------------|
| **BLOCKED** | Hook exits 2; Claude shows block message and refuses the action | Claude prints a refusal/block message in the conversation |
| **STDERR** | Text appears in CLI output below the conversation | Look for hook-specific prefixes in terminal output |
| **STATUS** | `statusMessage` flashes briefly during execution | Watch for the flash text in the status bar area |
| **MODEL-ONLY** | Hook stdout goes into Claude's context (system-reminder) | Check Claude's next response for evidence it received the info |
| **SIDE-EFFECT** | Hook modifies files on disk | Check file timestamps or content with `git diff` after |

**Tip**: Run with `--verbose` or check `~/.claude/debug/` logs if you cannot tell whether a hook fired.

---

## 1. pip_audit_check.py (PRIORITY)

> **Config**: PostToolUse, matcher=`Bash`, if=`Bash(*uv add*)|Bash(*uv sync*)|Bash(*uv pip install*)`, statusMessage: "Auditing dependencies for vulnerabilities..."
>
> **Observe via**: STDERR ("[pip-audit]" prefix) + STATUS

### Positive tests (hook SHOULD fire)

- [ ] Ask Claude: "run uv add httpx" -- after install, STDERR shows "[pip-audit] Scanning..." then results
- [ ] Ask Claude: "run uv sync" -- same "[pip-audit]" output appears
- [ ] Ask Claude: "run uv pip install requests" -- same "[pip-audit]" output appears
- [ ] Compound command: "cd /tmp && uv add httpx" -- does the glob still match?

### Negative tests (hook should NOT fire)

- [ ] "run uv lock" -- no [pip-audit] output (if: pattern doesn't match)
- [ ] "run git status" -- no [pip-audit] output
- [ ] "run uv add badpkg" where install fails -- hook fires but exits early

### Investigation notes

If positive tests fail, the issue is in Claude Code's runtime matcher. Check if the `if:` pattern syntax is correct by comparing with working patterns like `scan_secrets_on_commit`'s `Bash(git commit*)`.

---

## 2. SessionStart Hooks

These fire once at session start, unconditionally (no matcher, no condition).

### 2a. project_health_check.py

> **Config**: SessionStart, command=`python3`, statusMessage: "Checking project health..."
>
> **Observe via**: MODEL-ONLY (stdout goes to system-reminder)

#### Positive tests

- [ ] Start a new Claude Code session in `hook_tests/` -- health check output appears in system-reminder; Claude's first response reflects awareness of project health
- [ ] Delete `README.md`, restart session -- health check flags the missing README

#### Negative tests

- [ ] N/A -- fires unconditionally on every session start

---

### 2b. git_pull_on_start.sh

> **Config**: SessionStart, command=`bash`, statusMessage: "Checking git remote for updates..."
>
> **Observe via**: STATUS + STDERR

#### Positive tests

- [ ] Start session in a clean git repo with a configured remote -- STATUS flashes "Checking git remote for updates...", attempts pull
- [ ] Push a commit from another machine/branch, then restart session -- pulls the new commit

#### Negative tests

- [ ] Uncommitted local changes present -- hook skips the pull
- [ ] Start session in a non-git directory -- hook is silent
- [ ] Repo has no remote configured -- hook is silent

---

### 2c. check_dep_freshness.sh

> **Config**: SessionStart, command=`bash`, statusMessage: "Checking dependency freshness..."
>
> **Observe via**: STATUS + STDERR (WARNING prefix)

#### Positive tests

- [ ] Run `touch -d "60 days ago" .last_dep_check`, then restart session -- STDERR shows WARNING about stale dependencies
- [ ] Run `touch .last_dep_check` (set to now), restart -- no warning, silent

#### Negative tests

- [ ] Delete `.last_dep_check` entirely -- hook handles gracefully (no crash)

---

## 3. PreToolUse Hooks

### 3a. block_read_env.py

> **Config**: PreToolUse, matcher=`Read`, no if:, statusMessage: "Checking for .env file read..."
>
> **Observe via**: BLOCKED (exit 2) or STATUS (exit 0)

#### Positive tests (should BLOCK)

- [ ] "Read .env" -- BLOCKED
- [ ] "Read .env.local" -- BLOCKED
- [ ] "Read .env.production" -- BLOCKED

#### Positive tests (should ALLOW)

- [ ] "Read .env.example" -- allowed, STATUS may flash
- [ ] "Read .env.sample" -- allowed, STATUS may flash

#### Negative tests

- [ ] "Read src/main.py" -- allowed, STATUS may flash "Checking for .env file read..."

---

### 3b. block_bare_pip.py

> **Config**: PreToolUse, matcher=`Bash`, no if:, NO statusMessage
>
> **Observe via**: BLOCKED only -- passing is invisible

#### Positive tests (should BLOCK)

- [ ] "Run pip install requests" -- BLOCKED
- [ ] "Run cd /tmp && pip install requests" -- BLOCKED

#### Known bugs (these BYPASS the block)

- [ ] "Run pip3 install requests" -- bypasses (regex bug)
- [ ] "Run python -m pip install requests" -- bypasses (regex bug)
- [ ] "Run python3 -m pip install requests" -- bypasses (regex bug)

#### Positive tests (should ALLOW)

- [ ] "Run uv pip install requests" -- allowed (legitimate uv usage)
- [ ] "Run ./.venv/bin/pip install requests" -- allowed (venv-qualified)

#### Negative tests

- [ ] "Run git status" -- hook fires (matcher=Bash), exits 0, unobservable

---

### 3c. scan_secrets_on_commit.py

> **Config**: PreToolUse, matcher=`Bash`, if=`Bash(git commit*)`, NO statusMessage
>
> **Observe via**: BLOCKED (exit 2) or STDERR ("PASSED")

#### Positive tests (should BLOCK)

- [ ] Stage a file containing `sk-ant-` followed by random characters, then "git commit -m 'test'" -- BLOCKED
- [ ] Stage `fixtures/staged_secret.py`, then commit -- BLOCKED

#### Positive tests (should PASS)

- [ ] Stage a clean file (no secrets), then commit -- STDERR shows "PASSED"

#### Negative tests (hook should NOT fire)

- [ ] "git status" -- hook doesn't fire (if: requires `git commit*`)
- [ ] "git add ." -- hook doesn't fire
- [ ] "git log" -- hook doesn't fire

---

### 3d. block_git_add_env.py

> **Config**: PreToolUse, matcher=`Bash`, if=`Bash(git add*)`, NO statusMessage
>
> **Observe via**: BLOCKED (exit 2)

#### Positive tests (should BLOCK)

- [ ] "git add .env" -- BLOCKED
- [ ] "git add ." -- BLOCKED (catches broad adds)
- [ ] "git add -A" -- BLOCKED
- [ ] "git add --all" -- BLOCKED

#### Known bugs (these BYPASS the block)

- [ ] "git add -v ." -- bypasses
- [ ] "git add -u" -- bypasses
- [ ] "git -C /tmp add ." -- bypasses

#### Positive tests (should ALLOW)

- [ ] "git add src/main.py" -- allowed (specific safe file)
- [ ] "git add .env.example" -- allowed

#### Negative tests (hook should NOT fire)

- [ ] "git status" -- hook skipped (if: requires `git add*`)
- [ ] "git commit -m 'test'" -- hook skipped

---

## 4. PostToolUse Edit|Write Hooks

All 8 hooks below share matcher=`Edit|Write`. They fire on every Edit or Write of any file. The hook logic internally decides whether to act based on file type.

### 4a. block_glob_deny_rules.py

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Checking for dangerous glob patterns..."
>
> **Observe via**: BLOCKED (exit 2) or STATUS

#### Positive tests (should BLOCK)

- [ ] Edit `.claude/settings.json` to add `Read(**/.env)` in a deny rule -- BLOCKED

#### Positive tests (should PASS)

- [ ] Edit `.claude/settings.json` with specific file paths (not globs) -- allowed, STATUS flashes

#### Negative tests

- [ ] Edit a `.py` file -- hook fires, exits 0, STATUS may flash

---

### 4b. ruff_format.sh

> **Config**: PostToolUse, matcher=`Edit|Write`, NO statusMessage
>
> **Observe via**: SIDE-EFFECT (file reformatted)

#### Positive tests

- [ ] Ask Claude to edit a `.py` file with intentionally bad formatting (wrong indentation, missing spaces around operators) -- file is reformatted after edit; confirm with `git diff`

#### Negative tests

- [ ] Edit a `.txt` file -- no formatting applied
- [ ] Edit a `.json` file -- no formatting applied

---

### 4c. pyright_check.sh

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Type-checking..."
>
> **Observe via**: MODEL-ONLY (stdout) + STATUS

#### Positive tests

- [ ] Edit `src/type_errors.py` (contains type errors) -- Claude mentions type issues in its response
- [ ] Edit a clean `.py` file -- STATUS flashes "Type-checking...", no type errors reported

#### Negative tests

- [ ] Edit a `.txt` file -- hook skipped
- [ ] Edit a file inside `~/.claude/` -- hook skipped

---

### 4d. check_docstrings.py

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Checking docstrings..."
>
> **Observe via**: MODEL-ONLY (stdout) + STATUS

#### Positive tests

- [ ] Edit `src/missing_docstrings.py` -- Claude mentions missing docstrings in response

#### Negative tests

- [ ] Edit a `test_*.py` file -- hook skipped (test files excluded)
- [ ] Edit `__init__.py` -- hook skipped
- [ ] Edit a `.txt` file -- hook skipped

---

### 4e. check_dependency_pins.py

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Checking dependency pins..."
>
> **Observe via**: BLOCKED (exit 2) or STATUS

#### Positive tests (should BLOCK)

- [ ] Edit `pyproject.toml` to add bare `"requests"` (no version pin) -- BLOCKED

#### Positive tests (should PASS)

- [ ] Edit `pyproject.toml` with `"requests==2.32.3"` (pinned) -- allowed, STATUS flashes

#### Negative tests

- [ ] Edit a `.py` file -- hook fires, exits 0 immediately

---

### 4f. check_random_seeds.py

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Checking random seeds..."
>
> **Observe via**: MODEL-ONLY (stdout) + STATUS

#### Positive tests

- [ ] Edit `src/unseeded_random.py` -- Claude mentions seed warning in response

#### Negative tests

- [ ] Edit a `.py` file that does not use `random` or `numpy` -- no warning
- [ ] Edit a `test_*.py` file -- hook skipped

---

### 4g. block_suppressions.py

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Checking for suppression comments..."
>
> **Observe via**: BLOCKED (exit 2) or STATUS

#### Positive tests (should BLOCK)

- [ ] Add `# type: ignore` (no justification) to a `.py` file -- BLOCKED
- [ ] Add `# noqa: C901` to a `.py` file -- BLOCKED
- [ ] Add `# TYPE: IGNORE` (uppercase) -- BLOCKED (case-insensitive check)

#### Positive tests (should PASS)

- [ ] Add `# type: ignore[override]  # mypy-bug: reason` (justified) -- allowed
- [ ] Add `# noqa: E402` (pre-approved code) -- allowed

#### Negative tests

- [ ] Edit a `.txt` file with `# type: ignore` -- allowed (not a Python file)
- [ ] Edit a file inside `.venv/` with `# type: ignore` -- allowed (excluded path)

---

### 4h. bandit_check.sh

> **Config**: PostToolUse, matcher=`Edit|Write`, statusMessage: "Security scanning..."
>
> **Observe via**: MODEL-ONLY (stdout) + STATUS

#### Positive tests

- [ ] Edit `src/security_issues.py` (contains `eval()`, `subprocess.call(shell=True)`) -- Claude mentions security findings in response

#### Negative tests

- [ ] Edit a `.txt` file -- hook skipped
- [ ] Edit a clean `.py` file with no security issues -- no findings reported

---

## 5. PostToolUse Write-Only Hook

### 5a. check_test_pair.py

> **Config**: PostToolUse, matcher=`Write`, statusMessage: "Checking for test file..."
>
> **Observe via**: MODEL-ONLY (stdout) + STATUS

#### Positive tests

- [ ] Ask Claude to CREATE (Write) a new file `src/no_test_pair.py` -- "TDD reminder" appears in Claude's response

#### Negative tests (KEY TESTS for matcher distinction)

- [ ] Ask Claude to EDIT an existing `.py` file -- "Checking for test file..." STATUS does NOT appear (Edit does not match Write-only matcher)
- [ ] Write a `test_*.py` file -- hook skipped (test files excluded)
- [ ] Write an `__init__.py` file -- hook skipped
- [ ] Write a `.txt` file -- hook skipped

#### Effectiveness test

- [ ] After receiving a TDD reminder, does Claude actually mention testing or offer to write tests?

---

## 6. PostToolUse WebFetch|mcp__.* Hook

### 6a. scan_prompt_injection.py

> **Config**: PostToolUse, matcher=`WebFetch|mcp__.*`, statusMessage: "Scanning for prompt injection..."
>
> **Observe via**: MODEL-ONLY (stdout JSON) + STATUS

#### Positive tests

- [ ] Fetch a URL whose content contains "ignore all previous instructions" -- Claude acknowledges injection risk in response
- [ ] MCP tool returns text containing prompt injection patterns -- warning appears

#### Negative tests

- [ ] Edit a `.py` file -- wrong matcher, hook skipped entirely
- [ ] Fetch a clean webpage with no injection patterns -- no warnings

#### Notes

Always exits 0 -- informational only, never blocks.

---

## 7. Stop Hook

### 7a. ruff_lint.sh

> **Config**: Stop, no matcher, statusMessage: "Lint-fixing changed Python files..."
>
> **Observe via**: SIDE-EFFECT (files modified) + STATUS

#### Positive tests

- [ ] Edit a `.py` file with lint issues (e.g., unused import), let the turn complete -- file is auto-fixed after turn ends; confirm with `git diff`

#### Negative tests

- [ ] Complete a turn without editing any `.py` files -- nothing to fix, no side effects

#### Known limitation

File paths with spaces break `xargs` in this hook.

---

## 8. Cross-Cutting Wiring Tests

These test interactions between multiple hooks and matcher behavior.

### Edit|Write matcher coverage

> Verify that Edit fires all 8 Edit|Write hooks, and Write fires all 8 + check_test_pair.

- [ ] Ask Claude to EDIT a `.py` file -- observe STATUS messages for all 8 Edit|Write hooks. Confirm "Checking for test file..." does NOT appear.
- [ ] Ask Claude to WRITE (create) a new `.py` file -- observe STATUS messages for all 8 Edit|Write hooks PLUS "Checking for test file..." from check_test_pair.

### Blocking cascade

> When one hook blocks, do subsequent hooks in the same event still fire?

- [ ] Edit a `.py` file that triggers `block_suppressions` (exit 2) -- Record: Did `ruff_format` still run? Did `pyright` still run?

### PreToolUse Bash: multiple hooks, one matcher

> Multiple PreToolUse hooks share matcher=Bash. Test which ones fire.

- [ ] "pip install requests" -- `block_bare_pip` blocks. Do `scan_secrets_on_commit` and `block_git_add_env` also fire? (They have if: conditions that don't match, so they should fire but exit 0.)
- [ ] "git add .env" -- `block_git_add_env` blocks. Does `block_bare_pip` also fire? (It has no if:, so it fires on all Bash commands.)

---

## Available Fixture Files

These files exist in the project for use during testing:

**Source files** (`src/`):
- `type_errors.py` -- contains deliberate type errors for pyright
- `missing_docstrings.py` -- functions without docstrings
- `security_issues.py` -- eval(), subprocess.call(shell=True)
- `unseeded_random.py` -- random/numpy usage without seeds
- `has_suppressions.py` -- type: ignore, noqa comments
- `no_test_pair.py` -- source file with no corresponding test
- `clean_module.py` -- clean file that should pass all hooks

**Fixture files** (`fixtures/`):
- `staged_secret.py` -- contains fake API key for secret scanning
- `glob_deny_settings.json` -- settings with dangerous glob patterns
- `injection_payload.txt` -- prompt injection text samples
- `unpinned_requirements.txt` -- dependencies without version pins
- `sample_r_file.R` -- non-Python file for negative tests
