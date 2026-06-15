# Interactive Hook Wiring Playbook

Manual QA checklist for verifying that Claude Code hooks **actually fire** when expected during a live session. This does NOT test hook logic (automated tests cover that). This tests whether Claude Code's runtime dispatching correctly invokes hooks based on matchers, `if:` conditions, and event types.

## Prerequisites

- [x] Claude Code CLI installed and working
- [x] Working directory is `hook_tests/` (this project)
- [x] `~/.claude/settings.json` contains all 19 hook definitions
- [x] Python virtual environment activated (`uv sync` or equivalent)
- [x] All hook scripts are executable (`chmod +x hooks/*.py hooks/*.sh`)
- [x] Git repo is initialized with a remote configured
- [x] Fixture files exist in `src/` and `fixtures/` (see list at bottom)

## Observation Guide

| Code | Meaning | How to confirm |
|------|---------|----------------|
| **BLOCKED** | Hook exits 2; Claude shows block message and refuses the action | Claude prints a refusal/block message in the conversation |
| **STDERR** | Text appears in CLI output below the conversation | Look for hook-specific prefixes in terminal output |
| **STATUS** | `statusMessage` flashes briefly during execution | Watch for the flash text in the status bar area |
| **MODEL-ONLY** | Hook stdout goes into Claude's context (system-reminder) | Check Claude's next response for evidence it received the info |
| **SIDE-EFFECT** | Hook modifies files on disk | Check file timestamps or content with `git diff` after |

**Tip**: Run with `--verbose` or check `~/.claude/debug/` logs if you cannot tell whether a hook fired.

## Debug Log

All hooks now log to `/tmp/hook_debug.log` on every invocation (added 2026-06-15). Check this file after any test to confirm whether a hook fired. This resolves the observability gap where exit-0 hooks were indistinguishable from hooks that never fired.

Helper: `~/.claude/hooks/hook_log.py` — Python hooks import it; Bash hooks inline a `printf` equivalent.

## TODO (2026-06-15)

Discoveries from the first manual testing pass that need follow-up:

- [ ] **`scan_secrets_on_commit` logic failure**: Hook fires (confirmed by debug log) but fails to block commits containing `sk-ant-` patterns. Suspect `git diff --cached` returns empty in the hook subprocess context (PreToolUse fires *before* the tool runs, so the commit hasn't happened yet — but the file IS staged). Investigate what the hook actually sees.
- [ ] **`pip_audit_check` logic failure**: Hook fires on all Bash commands (debug log confirms) but never produces visible output. Original diagnosis was wiring failure; now believed to be `uvx pip-audit` timing out under sandbox network restrictions. Investigate whether the hook silently catches the timeout.
- [ ] **`if:` conditions don't filter**: Debug log shows all Bash-matched hooks fire on EVERY Bash command regardless of `if:` condition. Either `if:` doesn't work as documented, or the hook receives the input and must filter internally. Clarify expected behavior.
- [ ] **Re-run section 1 tests with debug log**: Original results were diagnosed as wiring failures. Now that we know hooks fire, re-test and check debug log to get accurate diagnoses.
- [ ] **statusMessage never visible on PreToolUse hooks**: Tested on `block_read_env` — never flashes for blocks, allows, or negative tests. May be a Claude Code limitation for PreToolUse event type.
- [ ] **stderr visibility differs by tool type**: Read tool blocks don't show stderr in console; Bash tool blocks do. Both use the same mechanism (stderr + exit 2). Investigate whether this is a Claude Code UI bug or intentional.

---

## 1. pip_audit_check.py (PRIORITY)

> **Config**: PostToolUse, matcher=`Bash`, if=`Bash(*uv add*)|Bash(*uv sync*)|Bash(*uv pip install*)`, statusMessage: "Auditing dependencies for vulnerabilities..."
>
> **Observe via**: STDERR ("[pip-audit]" prefix) + STATUS

### Positive tests (hook SHOULD fire)

- [x] Ask Claude: "run uv add httpx" -- after install, STDERR shows "[pip-audit] Scanning..." then results
  - **REVISED**: Originally diagnosed as wiring failure. Debug log (added 2026-06-15) shows `pip_audit_check` DOES fire on Bash commands — likely a **logic failure** (uvx pip-audit times out under sandbox network restrictions), not a wiring failure. Needs re-investigation with debug log.
- [x] Ask Claude: "run uv sync" -- same "[pip-audit]" output appears
  - **REVISED**: Same as above — hook likely fires but fails silently.
- [x] Ask Claude: "run uv pip install requests" -- same "[pip-audit]" output appears
  - **REVISED**: Same as above.
- [x] Compound command: "cd /tmp && uv add httpx" -- does the glob still match?
  - **REVISED**: Same as above. Also, `uv add` itself fails (no pyproject.toml in /tmp).

### Negative tests (hook should NOT fire)

- [x] "run uv lock" -- no [pip-audit] output (if: pattern doesn't match)
  - **REVISED**: Debug log shows `pip_audit_check` fires on ALL Bash commands regardless of `if:` condition. So `if:` is not filtering — hook fires but produces no visible output. Needs re-investigation.
- [x] "run git status" -- no [pip-audit] output
  - **REVISED**: Same — hook fires (confirmed by debug log pattern), output not visible.
- [x] "run uv add badpkg" where install fails -- hook fires but exits early
  - **REVISED**: Hook fires (confirmed by debug log pattern). Sandbox network timeout caused the uv failure, not a missing package.

### Investigation notes

~~If positive tests fail, the issue is in Claude Code's runtime matcher.~~ **REVISED 2026-06-15**: Debug logging reveals all `if:`-conditioned hooks fire on every matching Bash command — the `if:` condition does NOT filter invocations. Failures are **logic bugs** in the hooks themselves, not wiring issues. The original "file-write debug probe" may have been testing the wrong thing.

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

- [x] "Read .env" -- BLOCKED
  - **PARTIAL**: Hook blocks correctly (exit 2), but stderr message is not visible in console — only reaches Claude as a tool error. statusMessage flash not observed either. Block message is effectively MODEL-ONLY. Note: Bash tool blocks DO show stderr in console (see 3b); this appears to be a Claude Code UI difference in how Read vs Bash tool errors are rendered.
- [x] "Read .env.local" -- BLOCKED (same stderr visibility issue)
- [x] "Read .env.production" -- BLOCKED (same stderr visibility issue)

#### Positive tests (should ALLOW)

- [x] "Read .env.example" -- allowed (not blocked; file contents shown)
  - **PARTIAL**: statusMessage "Checking for .env file read..." did NOT flash.
  - **INCONCLUSIVE on hook firing**: can't distinguish "hook fired and allowed" from "hook didn't fire." Correct outcome either way, but hook execution unconfirmed.
- [x] "Read .env.sample" -- allowed (not blocked; file doesn't exist but not blocked)
  - Same statusMessage and inconclusiveness issues.

#### Negative tests

- [x] "Read src/main.py" -- allowed (not blocked)
  - **PARTIAL**: statusMessage still not visible. Appears to be a systemic issue — statusMessage never shows for this PreToolUse hook.
  - **INCONCLUSIVE on hook firing**: same issue — correct outcome, but can't confirm hook ran.

---

### 3b. block_bare_pip.py

> **Config**: PreToolUse, matcher=`Bash`, no if:, NO statusMessage
>
> **Observe via**: BLOCKED only -- passing is invisible

#### Positive tests (should BLOCK)

- [x] "Run pip install requests" -- BLOCKED. Full stderr block message visible in console (unlike Read tool blocks).
- [x] "Run cd /tmp && pip install requests" -- BLOCKED (same console visibility)

#### Known bugs (these BYPASS the block)

- [x] "Run pip3 install requests" -- NOT bypassed (pip3 not installed, but hook didn't block either — bypass confirmed, just no pip3 binary to exploit)
- [x] "Run python -m pip install requests" -- **NOT A BUG**: regex `(^|[^./\w])pip\s+install\b` already catches this (space before `pip` doesn't match exclusion set). Plan incorrectly listed as bypass; confirmed working in commit 9b2241b.
- [x] "Run python3 -m pip install requests" -- **NOT A BUG**: same reason as above.

#### Positive tests (should ALLOW)

- [x] "Run uv pip install requests" -- allowed (not blocked; network timeout from sandbox is unrelated)
  - **INCONCLUSIVE on hook firing**: can't confirm hook ran and decided to allow vs. didn't fire.
- [x] "Run ./.venv/bin/pip install requests" -- allowed (not blocked; no venv exists but irrelevant)
  - **INCONCLUSIVE on hook firing**: same issue.

#### Negative tests

- [x] "Run git status" -- allowed (not blocked)
  - **INCONCLUSIVE on hook firing**: wrote "unobservable" originally, which is the problem — can't confirm hook ran. Correct outcome either way.

---

### 3c. scan_secrets_on_commit.py

> **Config**: PreToolUse, matcher=`Bash`, if=`Bash(git commit*)`, NO statusMessage
>
> **Observe via**: BLOCKED (exit 2) or STDERR ("PASSED")

#### Positive tests (should BLOCK)

- [x] Stage a file containing `sk-ant-` followed by random characters, then "git commit -m 'test'" -- BLOCKED
  - **LOGIC FAILURE** (not wiring): Debug log confirms `scan_secrets_on_commit` FIRED, but commit went through unblocked. Hook runs `git diff --cached` but fails to detect the secret — likely a subprocess/context issue where staged state isn't visible to the hook.
- [ ] Stage `fixtures/staged_secret.py`, then commit -- BLOCKED
  - SKIPPED: file already tracked with no changes; can't isolate as a staging test. Test 1 already demonstrates the wiring failure.

#### Positive tests (should PASS)

- [x] Stage a clean file (no secrets), then commit -- STDERR shows "PASSED"
  - **REVISED**: Debug log confirms hook fires on all Bash commands. Hook likely fired and passed, but stderr "PASSED" message not visible in console. Use debug log to confirm in future runs.

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
- [ ] Edit a `.py` file that already passes lint (no issues) -- no side effects, file is unchanged; confirm with `git diff` showing no changes

#### Negative tests

- [ ] Complete a turn without editing any `.py` files -- nothing to fix, no side effects
- [ ] Edit a non-`.py` file with lint-like issues (e.g., `example.txt` containing unused variables) -- ruff_lint should not modify it; confirm file is unchanged

#### Known limitation

File paths with spaces break `xargs` in this hook.

---

## 8. Cross-Cutting Wiring Tests

These test interactions between multiple hooks and matcher behavior.

### Edit|Write matcher coverage

> Verify that Edit fires all 8 Edit|Write hooks, and Write fires all 8 + check_test_pair.

- [ ] Ask Claude to EDIT a `.py` file -- observe STATUS messages for all 8 Edit|Write hooks. Confirm "Checking for test file..." does NOT appear.
- [ ] Ask Claude to WRITE (create) a new `.py` file -- observe STATUS messages for all 8 Edit|Write hooks PLUS "Checking for test file..." from check_test_pair.

### Step 0c Experiments (fold in here)

> These resolve three open questions from the plan (lines 170-176 of `plans/hook_test_harness_plan.md`).
> Record answers back into the Step 0c section of the plan after running.

**Experiment A** (answers Q1: execution order + Q2: blocking cascade):

- [ ] Edit a Python file containing both `# type: ignore` (triggers `block_suppressions`, exit 2) AND bad formatting (triggers `ruff_format`). In settings.json, `ruff_format.sh` comes before `block_suppressions.py`. Record: Does the file get reformatted? Does block_suppressions still block?
- [ ] Swap the order in settings.json (block_suppressions first). Repeat. Does ruff_format still run after the block?

**Experiment B** (answers Q3: side-effect visibility):

- [ ] Edit a Python file with a type error on a line that ruff would reformat. After the edit, check: did pyright report the error on the original line number or the reformatted line number?

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
