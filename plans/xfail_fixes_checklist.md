# Xfail Fixes Checklist

All xfails document real bugs in hook scripts under `~/.claude/hooks/`.
Tests assert the *correct* behavior (from Step 0d intent specs) and are
marked `@pytest.mark.xfail(strict=True)` so pytest flags when a fix
lands (strict xpass = test now passes, remove the xfail marker).

Hook scripts live at: `~/.claude/hooks/<name>`
Tests live at: `tests/test_hooks/test_<name>.py` (or grouped files)
Adversarial tests: `tests/test_hooks/test_adversarial_payloads.py`

## Workflow per fix

1. Read the xfail test to understand the expected behavior
2. Read the hook script to understand the current bug
3. Fix the hook script
4. Run the specific test WITHOUT the xfail — confirm it passes
5. Remove the xfail marker from the test
6. Run the full suite (`uv run pytest tests/test_hooks/ -x`) to check for regressions
7. Commit the hook fix + test cleanup together

---

## Category 1: Input validation (adversarial crashes) — DONE

All 10 Python hooks hardened with guarded input preambles. Crash sets and
dynamic xfail logic removed from `test_adversarial_payloads.py`. 480
adversarial tests now pass without any xfails.

- [x] **block_read_env.py**
- [x] **block_bare_pip.py**
- [x] **block_git_add_env.py**
- [x] **block_glob_deny_rules.py**
- [x] **block_suppressions.py**
- [x] **check_dependency_pins.py**
- [x] **check_docstrings.py**
- [x] **check_random_seeds.py**
- [x] **pip_audit_check.py** (+ 2 static xfails removed from test_pip_audit_check.py)
- [x] **scan_prompt_injection.py**

---

## Category 2: Regex bugs (hook-specific logic fixes) — DONE

- [x] **block_bare_pip.py: pip3 not matched** — regex updated to `pip3?\s+install`
- [x] **block_bare_pip.py: containment bypass** — per-match prefix check replaces naive substring check
- [x] **block_bare_pip.py: hyphenated false positive** — added `-` to negative char class
- [x] **block_git_add_env.py: flags between add and .** — regex allows optional flags
- [x] **block_git_add_env.py: -C /path** — regex allows optional `-C <path>` segment
- [x] **block_git_add_env.py: -u/--update** — added to bulk-add alternation
- [x] **block_git_add_env.py: quoted .env filenames** — boundary allows `"` and `'` terminators
- [x] **check_dependency_pins.py: env marker fools detection** — strips markers before checking specifiers
- [x] **block_glob_deny_rules.py: only checks read keys** — scans all 7 filesystem sandbox keys
- [x] **scan_secrets_on_commit.py: git returncode not checked** — fails closed on git errors
- [x] **scan_secrets_on_commit.py: diff header false positives** — only scans `+` lines

---

## Category 3: Logic limitations — DONE

- [x] **check_random_seeds.py: code vs comments/strings** — replaced regex with `ast.parse` for seed detection; falls back to regex on syntax errors
- [x] **scan_prompt_injection.py: missed injection phrases** — added optional `(?:your|the)` between verb and adjective
- [x] **scan_prompt_injection.py: false positives** — added negative lookaheads for common benign verb continuations
- [x] **scan_prompt_injection.py: synonym verbs** — added stop following, throw away, abandon, delete, reset, drop
- [x] **scan_prompt_injection.py: synonym targets** — added original, old, initial, first, default

---

## Summary

| Category | Status | Xfails resolved |
|----------|--------|-----------------|
| 1. Input validation | **DONE** | ~92 dynamic + 2 static |
| 2. Regex bugs | **DONE** | 16 static |
| 3. Logic limitations | **DONE** | 9 static |

**Final suite**: 922 passed, 0 xfailed, 0 failures

### Sessions log

- 2026-06-22: All three categories completed via parallel agents. All 10 hooks hardened, all regex bugs fixed, adversarial test infrastructure cleaned up, ast-based seed detection added, prompt injection regex expanded and tightened. Zero xfails remain.
