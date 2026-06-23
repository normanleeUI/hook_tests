# Interactive Hook Wiring Playbook

Manual QA checklist for verifying that Claude Code hooks **actually fire** in a live
session. Automated tests cover hook logic (correct decisions for given inputs). This
playbook tests the layer automated tests cannot reach: does Claude Code's runtime
invoke the hook, and does the output reach the user or model through a working channel?

Tests are batched by interaction pattern to minimize context-switching. Work through
the batches in order — some have dependencies on earlier results.

**Tools you'll use:**
- `./scripts/verify_prerequisites.sh` — pre-flight environment check
- `./scripts/observe.sh <hook> [file]` — post-test observation (debug log + inline comments + state files)
- `./scripts/observe.sh --all [file]` — show all hooks that fired (for cross-cutting tests)
- `./scripts/observe.sh --reset` — clear observation state between batches

---

## Reference: Observation Guide

Empirically validated output channels (see `probes/PROBE_RESULTS_PHASE2.md`):

| Code | Meaning | How to confirm | Status |
|------|---------|----------------|--------|
| **BLOCKED** | PreToolUse exit 2; action prevented | Claude reports block; stderr visible for Bash/Edit/Write | Working |
| **INLINE** | `# HOOK:<NAME>: <msg>` injected into edited file | Read file after edit — look for `# HOOK:` lines | Working (Strategy B) |
| **STATE-FILE** | Findings written to `.hook_state/<hook>/` | Check file existence/content | Working (Strategy C) |
| **SIDE-EFFECT** | Hook modifies files on disk | `git diff` after edit | Working |
| **MODEL-CONTEXT** | hookSpecificOutput.additionalContext → `<system-reminder>` | Claude's next response references it | Working (needs `hookEventName`) |
| **SESSION-STDOUT** | SessionStart stdout reaches model | Claude's first response reflects it | Working |
| ~~STATUS~~ | ~~statusMessage flash~~ | | **Never works** — ignore |
| ~~STDERR (exit 0)~~ | ~~stderr on allow~~ | | **Never visible** |

**Key limitations:**
- Read matcher exit-2 blocks show only a red dot (stderr not rendered), but the block works
- PostToolUse exit 2 is **cosmetic** — the edit already happened; only informs Claude
- PostToolUse hooks in the same group run **in parallel** — file locking serializes writes
- `if:` conditions work for single patterns; `|` OR syntax does NOT work

---

## Reference: Current Hook Wiring

20 active hooks across 8 groups:

| Event | Matcher | Hooks | Strategy |
|-------|---------|-------|----------|
| SessionStart | (none) | project_health_check, git_pull_on_start, check_dep_freshness | stdout to model |
| PreToolUse | Read | block_read_env | D (dual-wiring) |
| PreToolUse | Bash | block_read_env, block_bare_pip, scan_secrets_on_commit (`if: git commit*`), block_git_add_env (`if: git add*`), pip_audit_guard | exit 2 blocks |
| PreToolUse | Edit\|Write | check_dependency_pins, block_suppressions, block_glob_deny_rules | A (promotion) |
| PostToolUse | Edit\|Write | ruff_format, pyright_check, check_docstrings, check_random_seeds, bandit_check, semgrep_check | B (inline injection) |
| PostToolUse | Bash | pip_audit_check | C (state-file) |
| PostToolUse | WebFetch\|mcp__.* | scan_prompt_injection | MODEL-CONTEXT |
| Stop | (none) | ruff_lint | SIDE-EFFECT |

---

## Known Issues

- [ ] **`scan_secrets_on_commit` logic bug**: Hook fires but `git diff --cached` returns empty in PreToolUse context (commit hasn't happened yet). Fundamental design issue, not a channel problem. Channel redesign did not address it.

---

## Batch 0: Pre-session prep

Run in your normal terminal before starting any Claude session.

```bash
cd ~/projects/hook_tests

# 1. Verify environment
./scripts/verify_prerequisites.sh

# 2. Stage a file with a fake secret for scan_secrets_on_commit test (Batch 2)
echo 'API_KEY = "sk-ant-FAKE-key-here"' > src/staged_secret_test.py
git add src/staged_secret_test.py

# 3. Set stale dep marker for check_dep_freshness test (Batch 1)
touch -d "60 days ago" .last_dep_check

# 4. Ensure .env files exist for blocker tests
test -f .env.local      || echo 'DB_PASSWORD=fake-secret-for-testing' > .env.local
test -f .env.production  || echo 'SECRET_KEY=fake-prod-secret-for-testing' > .env.production

# 5. Clear stale state
rm -f .hook_state/pip_audit/report.json 2>/dev/null

# 6. Reset observation state
./scripts/observe.sh --reset
```

- [ ] verify_prerequisites.sh passes
- [ ] staged_secret_test.py is staged in git
- [ ] .last_dep_check set to 60 days ago
- [ ] .env, .env.local, .env.production exist
- [ ] .hook_state/pip_audit/report.json cleared
- [ ] observation state reset

---

## Batch 1: Session restarts (~15 min)

Each test requires a fresh Claude Code session. Do these first — they're the slowest.

### check_dep_freshness.sh

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SESSION-STDOUT (if stale) + debug log.

**Test 1.1 — stale deps**
> Pre-condition: `.last_dep_check` set to 60 days ago (Batch 0).
> Start a new Claude Code session in `hook_tests/`.

- [ ] Claude mentions stale dependencies in first response
- [ ] `./scripts/observe.sh check_dep_freshness` — hook FIRED

**Test 1.2 — fresh deps**
> Run in normal terminal: `touch .last_dep_check`
> Start a new Claude Code session.

- [ ] No staleness warning in Claude's first response
- [ ] `./scripts/observe.sh check_dep_freshness` — hook FIRED (exited 0)

**Test 1.3 — missing marker file**
> Run in normal terminal: `rm -f .last_dep_check`
> Start a new Claude Code session.

- [ ] No crash — hook handles gracefully
- [ ] `./scripts/observe.sh check_dep_freshness` — hook FIRED

### project_health_check.py

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SESSION-STDOUT.

**Test 1.4 — normal session**
> Start a new Claude Code session in `hook_tests/`.

- [ ] Claude's first response reflects project health awareness
- [ ] `./scripts/observe.sh project_health_check` — hook FIRED

**Test 1.5 — missing README**
> Run in normal terminal: `mv README.md README.md.bak`
> Start a new Claude Code session.

- [ ] Health check flags missing README
- [ ] `./scripts/observe.sh project_health_check` — hook FIRED
- [ ] Run in normal terminal: `mv README.md.bak README.md` (restore)

### git_pull_on_start.sh

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SIDE-EFFECT (pulls changes) + debug log.

**Test 1.6 — clean repo with remote**
> Start a new Claude Code session.

- [ ] `./scripts/observe.sh git_pull_on_start` — hook FIRED

**Test 1.7 — uncommitted changes**
> Run in normal terminal: `echo "# temp" >> README.md`
> Start a new Claude Code session.

- [ ] Hook skips pull (doesn't clobber uncommitted work)
- [ ] `./scripts/observe.sh git_pull_on_start` — hook FIRED
- [ ] Run in normal terminal: `git checkout -- README.md` (restore)

### Batch 1 reset

```bash
touch .last_dep_check  # restore normal state
./scripts/observe.sh --reset
```

---

## Batch 2: Bash command barrage (~10 min)

All "run this command, check if blocked" — rapid fire. Stay in one Claude session.
Phrase each as: "run `<command>`"

### block_read_env.py — Bash matcher (Strategy D dual-wiring)

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Closes circumvention path where `cat .env` bypasses the Read tool block.

**Should BLOCK:**

- [ ] 2.1: run `cat .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 2.2: run `head -5 .env.production` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 2.3: run `base64 .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 2.4: run `source .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 2.5: run `python3 -c 'open(".env").read()'` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 2.6: run `echo hello && cat .env` — BLOCKED → `./scripts/observe.sh block_read_env`

**Should ALLOW:**

- [ ] 2.7: run `cat .env.example` — allowed → `./scripts/observe.sh block_read_env`
- [ ] 2.8: run `cat .env.template` — allowed → `./scripts/observe.sh block_read_env`
- [ ] 2.9: run `cat README.md` — allowed → `./scripts/observe.sh block_read_env`

### block_bare_pip.py

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Logic thoroughly tested by automated tests — manual test confirms wiring only.

- [ ] 2.10: run `pip install requests` — BLOCKED → `./scripts/observe.sh block_bare_pip`
- [ ] 2.11: run `uv pip install requests` — allowed → `./scripts/observe.sh block_bare_pip`
- [ ] 2.12: run `git status` — allowed, hook fired → `./scripts/observe.sh block_bare_pip`

### block_git_add_env.py

> **Wiring**: PreToolUse, matcher=Bash, `if: Bash(git add*)`.
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Logic thoroughly tested by automated tests — manual test confirms wiring only.

- [ ] 2.13: run `git add .env` — BLOCKED → `./scripts/observe.sh block_git_add_env`
- [ ] 2.14: run `git add .` — BLOCKED → `./scripts/observe.sh block_git_add_env`
- [ ] 2.15: run `git add src/clean_module.py` — allowed → `./scripts/observe.sh block_git_add_env`
- [ ] 2.16: run `git status` — NOT FIRED (if: filters) → `./scripts/observe.sh block_git_add_env`

### Cross-cutting: multiple Bash hooks share a matcher

> Multiple PreToolUse Bash hooks fire on every Bash command (unless `if:` filters).
> Focus on which OTHER hooks fired alongside the primary one.

- [ ] 2.17: run `pip install requests` → `./scripts/observe.sh --all`
  - block_bare_pip BLOCKS
  - block_read_env (Bash) also fired? (yes — no `if:`)
  - scan_secrets_on_commit fired? (no — `if: Bash(git commit*)` doesn't match)
- [ ] 2.18: run `git add .env` → `./scripts/observe.sh --all`
  - block_git_add_env BLOCKS
  - block_bare_pip also fired? (yes — no `if:`)
- [ ] 2.19: run `git commit -m 'test'` → `./scripts/observe.sh --all`
  - scan_secrets_on_commit FIRES
  - pip_audit_guard also fired? (yes — no `if:`)

### `if:` condition filtering

> P13 confirmed single-pattern `if:` conditions work. `|` OR syntax does NOT.

- [ ] 2.20: run `git commit -m 'test wiring'` — scan_secrets FIRES → `./scripts/observe.sh scan_secrets_on_commit`
- [ ] 2.21: run `git status` — scan_secrets NOT FIRED → `./scripts/observe.sh scan_secrets_on_commit`
- [ ] 2.22: run `git add .` — block_git_add_env FIRES → `./scripts/observe.sh block_git_add_env`
- [ ] 2.23: run `echo hello` — block_git_add_env NOT FIRED → `./scripts/observe.sh block_git_add_env`

### scan_secrets_on_commit — logic bug investigation

> **Wiring**: PreToolUse, matcher=Bash, `if: Bash(git commit*)`.
> **Known bug**: `git diff --cached` sees nothing in PreToolUse context.

- [ ] 2.24: run `git commit -m 'secret test'` → `./scripts/observe.sh scan_secrets_on_commit`
  - Hook fires but may NOT block (known bug)
  - Check debug log for what `git diff --cached` returned

```bash
# Clean up staged secret test file:
git reset HEAD src/staged_secret_test.py 2>/dev/null
rm -f src/staged_secret_test.py
./scripts/observe.sh --reset
```

---

## Batch 3: Read tool tests (~3 min)

Phrase as: "read the file `<path>`"

### block_read_env.py — Read matcher

> **Wiring**: PreToolUse, matcher=Read, no `if:`.
> **Observe via**: BLOCKED (exit 2). Note: Read matcher blocks show only a red dot
> in the UI (stderr not rendered). The block itself works — Claude reports it as a tool error.

**Should BLOCK:**

- [ ] 3.1: read the file `.env` — BLOCKED (red dot, tool error) → `./scripts/observe.sh block_read_env`
- [ ] 3.2: read the file `.env.local` — BLOCKED → `./scripts/observe.sh block_read_env`
- [ ] 3.3: read the file `.env.production` — BLOCKED → `./scripts/observe.sh block_read_env`

**Should ALLOW:**

- [ ] 3.4: read the file `.env.example` — allowed (contents shown) → `./scripts/observe.sh block_read_env`
- [ ] 3.5: read the file `src/clean_module.py` — allowed → `./scripts/observe.sh block_read_env`

```bash
./scripts/observe.sh --reset
```

---

## Batch 4: pip_audit two-phase flow (~10 min)

**Order matters**: pip_audit_check (PostToolUse) must create the state file before
pip_audit_guard (PreToolUse) can read it. Network-dependent — may be slow.

### Step 1 — pip_audit_check creates state

> **Wiring**: PostToolUse, matcher=Bash, no `if:`.
> **Observe via**: STATE-FILE at `.hook_state/pip_audit/report.json`.
> Fires on every Bash command; internally filters for `uv add`/`uv sync`/`uv pip install`.

- [ ] 4.1: run `uv add httpx` — state file created → `./scripts/observe.sh pip_audit_check` then `cat .hook_state/pip_audit/report.json`
- [ ] 4.2: run `uv sync` — state file updated → `./scripts/observe.sh pip_audit_check`
- [ ] 4.3: run `git status` — hook fires but exits immediately (not a uv command) → `./scripts/observe.sh pip_audit_check`

### Step 2 — pip_audit_guard reads state and blocks

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2) when state file has vulns; silent exit 0 otherwise.

- [ ] 4.4: run `uv add requests` — BLOCKED if vulns in state file, else allowed → `./scripts/observe.sh pip_audit_guard`
- [ ] 4.5: run `git status` — guard fires but exits 0 (not a uv command) → `./scripts/observe.sh pip_audit_guard`

### Step 3 — clean audit clears state

- [ ] 4.6: run `uv sync` — if no vulns, state file cleared → `ls -la .hook_state/pip_audit/report.json`
- [ ] 4.7: run `uv add requests` — allowed (no state file = no block) → `./scripts/observe.sh pip_audit_guard`

### Step 4 — negative: no state file at all

```bash
# Run in normal terminal:
rm -f .hook_state/pip_audit/report.json
```

- [ ] 4.8: run `uv add httpx` — pip_audit_guard exits 0 (no state file) → `./scripts/observe.sh pip_audit_guard`

```bash
./scripts/observe.sh --reset
```

---

## Batch 5: PreToolUse Edit|Write blockers (~5 min)

These hooks were promoted from PostToolUse to PreToolUse in the channel redesign.
exit 2 **genuinely prevents** the edit (unlike PostToolUse exit 2, which is cosmetic).

Phrase as: "edit `<file>` to `<change>`" or "add `<content>` to `<file>`"

### check_dependency_pins.py (Strategy A)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented, file unchanged.

- [ ] 5.1: edit `pyproject.toml` to add `"requests"` as a dependency (no version pin) — BLOCKED, file unchanged → `./scripts/observe.sh check_dependency_pins` then `git diff pyproject.toml`
- [ ] 5.2: edit `pyproject.toml` to add `"requests==2.32.3"` as a dependency — allowed → `./scripts/observe.sh check_dependency_pins`
- [ ] 5.3: add a comment to `src/clean_module.py` — hook fires, exits 0 (not a dep file) → `./scripts/observe.sh check_dependency_pins`

### block_suppressions.py (Strategy A)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented.

- [ ] 5.4: add `x = 1  # type: ignore` to `src/clean_module.py` — BLOCKED, line never written → `./scripts/observe.sh block_suppressions` then `git diff`
- [ ] 5.5: add `x = 1  # type: ignore[override]  # mypy-bug: reason` to `src/clean_module.py` — allowed → `./scripts/observe.sh block_suppressions`

### block_glob_deny_rules.py (Strategy A — file reconstruction)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented.
> Uses file reconstruction: reads current file, applies proposed edit in memory, checks result.

- [ ] 5.6: edit `.claude/settings.json` to add `Read(**/.env)` in a deny rule — BLOCKED → `./scripts/observe.sh block_glob_deny_rules`
- [ ] 5.7: edit `.claude/settings.json` to add `Read(.env)` (specific, not glob) — allowed → `./scripts/observe.sh block_glob_deny_rules`

> **Important**: Restore settings.json after test 5.7 if Claude modified it.

```bash
./scripts/observe.sh --reset
```

---

## Batch 6: PostToolUse inline injection (~15 min)

Strategy B hooks fire after an Edit/Write completes and inject `# HOOK:<NAME>: <msg>`
comments at relevant lines. Comments are self-cleaning (stale ones removed each run).

**Parallel execution**: All PostToolUse Edit|Write hooks run in parallel with
`fcntl.flock` serializing the read-modify-write phase.

**After each edit, check the file for `# HOOK:` comments.**

### ruff_format.sh

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: SIDE-EFFECT (file reformatted on disk).

- [ ] 6.1: edit `src/clean_module.py` and add a function with bad formatting: `def    messy(  x,y  ) :  return   x+y` — file reformatted by ruff → `git diff src/clean_module.py`
- [ ] 6.2: edit `fixtures/sample_r_file.R` and add a comment — no formatting applied → `git diff fixtures/sample_r_file.R`

### pyright_check.sh

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: INLINE — `# HOOK:PYRIGHT: <error>` comments.

- [ ] 6.3: edit `src/type_errors.py` — add a blank line at the end → `./scripts/observe.sh pyright_check src/type_errors.py` — `# HOOK:PYRIGHT:` comments at type error lines
- [ ] 6.4: edit `src/clean_module.py` — add a blank line → `./scripts/observe.sh pyright_check src/clean_module.py` — no `# HOOK:PYRIGHT:` comments
- [ ] 6.5: edit `fixtures/sample_r_file.R` — add a line → `./scripts/observe.sh pyright_check fixtures/sample_r_file.R` — hook skips
- [ ] 6.6: **Visibility test** (after 6.3): Does Claude mention pyright findings in its response? (It should — it reads back the file and sees the comments)

### check_docstrings.py

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: INLINE — `# HOOK:DOCSTRING: <msg>` comments.

- [ ] 6.7: edit `src/missing_docstrings.py` — add a blank line → `./scripts/observe.sh check_docstrings src/missing_docstrings.py` — `# HOOK:DOCSTRING:` at undocumented functions
- [ ] 6.8: edit any `test_*.py` file — add a blank line → `./scripts/observe.sh check_docstrings` — hook skips (test files excluded)

### check_random_seeds.py

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: INLINE — `# HOOK:SEED: <msg>` comments.

- [ ] 6.9: edit `src/unseeded_random.py` — add a blank line → `./scripts/observe.sh check_random_seeds src/unseeded_random.py` — `# HOOK:SEED:` at unseeded usage
- [ ] 6.10: edit `src/clean_module.py` — add a blank line → `./scripts/observe.sh check_random_seeds src/clean_module.py` — no seed comments

### bandit_check.sh

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: INLINE — `# HOOK:BANDIT: <finding>` comments.

- [ ] 6.11: edit `src/security_issues.py` — add a blank line → `./scripts/observe.sh bandit_check src/security_issues.py` — `# HOOK:BANDIT:` at security issues
- [ ] 6.12: edit `src/clean_module.py` — add a blank line → `./scripts/observe.sh bandit_check src/clean_module.py` — no bandit comments

### semgrep_check.sh

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: INLINE — `# HOOK:SEMGREP: <finding>` comments.

- [ ] 6.13: edit `src/security_issues.py` — add a blank line → `./scripts/observe.sh semgrep_check src/security_issues.py` — `# HOOK:SEMGREP:` at security issues
- [ ] 6.14: edit a `.txt` file → `./scripts/observe.sh semgrep_check` — hook skips

### Integration: multiple injection hooks + parallel execution

> Verify file locking prevents lost writes when multiple hooks inject into the same file.

- [ ] 6.15: edit `src/multi_trigger.py` — add a blank line → `./scripts/observe.sh --all src/multi_trigger.py`
  - MULTIPLE `# HOOK:` comments appear: PYRIGHT, BANDIT, DOCSTRING, SEED, possibly SEMGREP
  - No lost writes (all expected hooks produced output)
- [ ] 6.16: check debug log for overlapping timestamps (parallel execution) → `grep -E 'pyright|bandit|docstring|random_seeds|semgrep|ruff_format' /tmp/hook_debug.log | tail -20`
- [ ] 6.17: edit `src/multi_trigger.py` again — add another blank line → `./scripts/observe.sh --all src/multi_trigger.py`
  - Stale `# HOOK:` comments cleaned up, fresh ones injected

```bash
./scripts/observe.sh --reset
```

---

## Batch 7: Cross-cutting — PreToolUse blocks PostToolUse (~2 min)

When a PreToolUse Edit|Write hook blocks (exit 2), the edit never happens, so
PostToolUse Edit|Write hooks have nothing to process.

- [ ] 7.1: edit `src/clean_module.py` and add a line `x = 1  # type: ignore` → `./scripts/observe.sh --all src/clean_module.py`
  - block_suppressions (PreToolUse) BLOCKS
  - ruff_format did NOT run
  - pyright_check did NOT run
  - No `# HOOK:` comments appear (file was never modified)
  - `git diff` shows file unchanged

```bash
./scripts/observe.sh --reset
```

---

## Batch 8: Miscellaneous (~10 min)

### scan_prompt_injection.py

> **Wiring**: PostToolUse, matcher=WebFetch|mcp__.*, no `if:`.
> **Observe via**: MODEL-CONTEXT — output reaches Claude as `<system-reminder>`.
> Hook must include `hookEventName` field (undocumented requirement discovered during probes).

> **Setup** (if test 8.1 needs a local server for injection content):
> ```bash
> # In another terminal:
> cd ~/projects/hook_tests/fixtures && python3 -m http.server 8888 &
> ```
> Then ask Claude to fetch `http://localhost:8888/injection_payload.txt`

- [ ] 8.1: fetch a URL with injection content (e.g., `http://localhost:8888/injection_payload.txt`) — Claude acknowledges injection risk → `./scripts/observe.sh scan_prompt_injection`
- [ ] 8.2: fetch a clean URL (e.g., `https://httpbin.org/get`) — no injection warning → `./scripts/observe.sh scan_prompt_injection`
- [ ] 8.3: edit a `.py` file — hook does NOT fire (wrong matcher) → `./scripts/observe.sh scan_prompt_injection`

### ruff_lint.sh — Stop hook

> **Wiring**: Stop, no matcher.
> **Observe via**: SIDE-EFFECT (files auto-fixed after turn ends).

- [ ] 8.4: edit `src/clean_module.py` and add `import os` at the top (unused import), let the turn complete — after Claude finishes, unused import auto-removed → `git diff src/clean_module.py`
- [ ] 8.5: edit `src/clean_module.py` (already clean) — add a docstring tweak — no side effects → `git diff src/clean_module.py`
- [ ] 8.6: ask Claude a question (no file edits) — nothing happens → `./scripts/observe.sh ruff_lint`
- [ ] 8.7: edit `fixtures/sample_r_file.R` — add a comment — ruff_lint does not touch non-.py files → `git diff fixtures/sample_r_file.R`

---

## Cleanup

Run in your normal terminal after all testing:

```bash
# Restore any modified files
git checkout -- src/ fixtures/ pyproject.toml 2>/dev/null

# Remove test artifacts
git reset HEAD src/staged_secret_test.py 2>/dev/null
rm -f src/staged_secret_test.py
rm -f src/*.hook_lock
rm -f .env.local .env.production  # if created during prep

# Restore normal state
touch .last_dep_check

echo "Testing complete — review checkboxes above for results."
```

---

## Fixture Files Reference

**Source files** (`src/`):
- `type_errors.py` — deliberate type errors for pyright
- `missing_docstrings.py` — functions without docstrings
- `security_issues.py` — eval(), subprocess.call(shell=True)
- `unseeded_random.py` — random/numpy usage without seeds
- `has_suppressions.py` — type: ignore, noqa comments
- `clean_module.py` — clean file that should pass all hooks
- `multi_trigger.py` — triggers pyright, bandit, semgrep, docstrings, random seeds simultaneously

**Fixture files** (`fixtures/`):
- `staged_secret.py` — contains fake API key for secret scanning
- `glob_deny_settings.json` — settings with dangerous glob patterns
- `injection_payload.txt` — prompt injection text samples
- `unpinned_requirements.txt` — dependencies without version pins

**State directories** (gitignored):
- `.hook_state/pip_audit/` — pip_audit_check findings

---

## Follow-up: Secret Detection False Positives

The `scan_secrets_on_commit` hook fires on patterns like `sk-ant-FAKE-...` in
documentation and test fixtures, even though these are clearly not real secrets.
This came up while committing this very file — the Batch 0 prep instructions
contained a dummy key string that triggered the hook.

**After completing the TESTING.md playbook**, revisit the secret detection regex
to determine whether it can be tightened without introducing false negatives.
Possible approaches:
- Minimum entropy or length threshold (real keys have high randomness)
- Allowlist for paths like `TESTING.md`, `fixtures/`, `prompts/`
- Require a minimum number of non-repeating characters after the prefix
- Context-aware scanning (skip strings inside markdown code fences or comments
  that contain "FAKE", "TEST", "EXAMPLE", "PLACEHOLDER")
