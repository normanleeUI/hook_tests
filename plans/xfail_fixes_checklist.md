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

## Category 3: Logic limitations (design decisions needed)

These require judgment calls about how far to push the hook's capabilities.
Discuss approach before implementing.

### check_random_seeds.py (`~/.claude/hooks/check_random_seeds.py`)

- [ ] **Cannot distinguish code from comments** (1 xfail: `test_tier2_hooks.py:570`)
  - Bug: `# np.random.seed(42)  -- disabled` is treated as a real seed call
  - Fix options: (a) skip lines starting with `#` before regex matching, (b) use a simple comment-stripping pass, (c) use `ast.parse` for Python files
  - Tests: `test_commented_seed_should_still_warn` (2 parametrized comment patterns). Test expects the hook to STILL inject `# HOOK:SEED:` warning (commented seed doesn't count as real seeding)

- [ ] **Cannot distinguish code from strings** (1 xfail: `test_tier2_hooks.py:586`)
  - Bug: `s = "np.random.seed(42)"` in a string literal is treated as a real seed call
  - Fix options: same as above — `ast.parse` would handle both comments and strings
  - Tests: `test_string_seed_should_still_warn` (2 parametrized string patterns). Same expectation: string seed doesn't count.

### scan_prompt_injection.py (`~/.claude/hooks/scan_prompt_injection.py`)

- [ ] **Missed injection phrases** (1 xfail: `test_prompt_injection.py:417`)
  - Bug: regex requires verb adjacent to adjective — misses "IGNORE YOUR PRIOR INSTRUCTIONS", "disregard the above guidelines", "forget your existing directives"
  - Fix: allow intervening words between verb and target in the override pattern
  - Tests: `test_injection_phrase_missed_by_hook` (3 parametrized phrases)

- [ ] **False positives on benign phrases** (1 xfail: `test_prompt_injection.py:440`)
  - Bug: "you are now" / "from now on you are" regex is too broad — matches "You are now looking at the test results" and "from now on you are going to see better performance"
  - Fix: tighten the identity manipulation regex to require role-assignment language after "you are now" (e.g., "you are now a", "you are now my", "you are now an")
  - Tests: `test_benign_phrase_false_positive` (2 parametrized benign phrases). Expects exit 0 with empty stdout.

- [ ] **Override synonym verbs not covered** (1 xfail: `test_intent_gaps.py:734`)
  - Bug: verb regex only matches `ignore|disregard|forget|override|bypass|skip|do not follow`
  - Missing verbs: "stop following", "throw away", "abandon", "delete", "reset", "drop"
  - Fix: add these to the verb alternation
  - Tests: `test_override_synonym_verbs_not_in_regex` (hypothesis, 50 examples: 6 verbs x 5 targets x 4 nouns)

- [ ] **Override synonym targets not covered** (1 xfail: `test_intent_gaps.py:757`)
  - Bug: target regex only matches `previous|prior|above|earlier|existing|your|the|system`
  - Missing targets: "original", "old", "initial", "first", "default"
  - Fix: add these to the target alternation
  - Tests: `test_override_synonym_targets_not_in_regex` (hypothesis, 50 examples: 4 verbs x 5 targets x 4 nouns)

---

## Summary

| Category | Status | Xfails resolved |
|----------|--------|-----------------|
| 1. Input validation | **DONE** | ~92 dynamic + 2 static |
| 2. Regex bugs | **DONE** | 16 static |
| 3. Logic limitations | TODO | 9 remaining (4 seed + 5 injection) |

**Current suite**: 913 passed, 9 xfailed, 0 failures

### Sessions log

- 2026-06-22: Categories 1+2 completed via parallel agents. All 10 hooks hardened, all regex bugs fixed, adversarial test infrastructure cleaned up. 9 Category 3 xfails remain (design decisions needed).
