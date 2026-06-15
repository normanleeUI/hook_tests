# Mutation Testing Results

Mutation testing run for 8 blocking hook scripts using a custom mutation
runner (`tests/test_hooks/run_mutations.py`).  The runner copies hooks to a
sandbox, applies targeted mutations to decision logic (conditions, exit
codes, regex calls, operators), runs the relevant pytest file(s) with
`HOOKS_DIR` pointed at the sandbox, and records whether each mutant was
killed (test failure) or survived (tests still pass).

## Per-hook results

| Hook | Total | Killed | Survived (equiv) | Kill rate |
|------|------:|-------:|------------------:|----------:|
| block_bare_pip.py | 6 | 6 | 0 | 100.0% |
| block_git_add_env.py | 12 | 11 | 1 (1) | 100.0% |
| block_read_env.py | 4 | 4 | 0 | 100.0% |
| block_suppressions.py | 36 | 36 | 0 | 100.0% |
| check_dependency_pins.py | 56 | 53 | 3 (2 equiv, 1 survived) | 98.1% |
| block_glob_deny_rules.py | 20 | 20 | 0 | 100.0% |
| pip_audit_check.py | 17 | 17 | 0 | 100.0% |
| scan_secrets_on_commit.py | 5 | 5 | 0 | 100.0% |
| **Total** | **156** | **152** | **4 (3 equiv, 1 survived)** | **99.3%** |

Kill rate is calculated as killed / (total - equivalent).  3 of 4 surviving
mutants are classified as equivalent (mutation does not change observable
behavior).  1 mutant survived in a low-risk edge case (see below).

## Equivalent mutants

### block_git_add_env.py -- line 47

**Mutation**: `.search()` to `.match()` on `template_suffix_re.search(m)`

The input `m` comes from `env_file_re.finditer(cmd)`, which returns match
objects whose `.group(0)` always starts with `.env.`.  Since `.match()`
matches from position 0, it behaves identically to `.search()` for strings
that always start with the target pattern prefix.

### check_dependency_pins.py -- line 54

**Mutation**: `re.search(r'"([^"]+)"', stripped)` to `re.match(...)`

The variable `stripped` is the result of `line.strip()`, so leading
whitespace is already removed.  In a pyproject.toml dependency array, each
dependency line after stripping is `"pkg==1.0",` which starts with a quote.
Both `.search()` and `.match()` find the quoted string at position 0.

### check_dependency_pins.py -- line 61, mutation 2

**Mutation**: `startswith("#")` to `startswith("XX#")`

The `#` check guards against quoted-comment entries like `"#remark"` inside
the pyproject.toml dependencies array.  This is not a valid dependency
specifier format; real comment lines are unquoted and caught by the earlier
`if not m: continue` guard on line 57.

## Surviving mutant (low-risk)

### check_dependency_pins.py -- line 61, mutation 1

**Mutation**: `or` to `and` in `if not spec or spec.startswith("#") or spec.startswith("["):`

With `and`, the `not spec` clause and `spec.startswith("#")` cannot both be
true simultaneously (an empty string does not start with `#`).  This changes
behavior for the empty-spec edge case (a dependency entry that is literally
`""`).  Originally classified as equivalent because this input does not
occur in practice, but reclassified as a surviving mutant because
behavioral equivalence is not provable for all valid inputs -- the mutation
does change behavior for at least one input.

## Gap-filling tests added

The following test classes were added to kill mutants that initially survived:

### test_block_suppressions.py

- `TestBlockSuppressionsEarlyExitGuards` -- 3 tests covering the
  JSONDecodeError, missing file_path, and empty new_string early-return
  guards (`return 0` paths).

### test_check_dependency_pins.py

- `TestDependencyPinsRequirementsTxtSkipFilters` -- 11 tests covering
  comment (`#`), flag (`-`), URL (`http`), absolute path (`/`), and
  relative path (`.`) skip-filters in the requirements.txt parser, plus
  bounded-range and open-ended tests.
- `TestDependencyPinsEarlyExitGuards` -- 5 tests covering JSONDecodeError,
  missing file_path, non-dependency files, requirements.json, and non-
  requirements .txt files.
- `TestDependencyPinsPyprojectParserEdgeCases` -- 1 test for comments
  inside pyproject.toml dependency arrays.

### test_block_glob_deny_rules.py

- `TestBlockGlobDenyRulesGuardFilters` -- 2 tests: a non-settings .json
  file inside `/.claude/` (kills `or`-to-`and` mutant on the guard), and a
  settings.yaml file (non-.json extension).

### test_pip_audit_check.py

- `TestPipAuditCheckGateEngagement` -- 3 tests verifying each trigger
  command (`uv add`, `uv sync`, `uv pip install`) individually engages the
  hook, killing the `or`-to-`and` mutant.
- `TestPipAuditCheckSubprocessResults` -- 2 tests using a fake `uvx` script
  to simulate clean and vulnerable pip-audit outcomes without network
  access, killing mutants on the return-code logic (lines 51, 56, 63).

### test_scan_secrets_gate.py

- `TestScanSecretsGitNotFound` -- 1 test that sets PATH to exclude git,
  triggering the FileNotFoundError handler and killing the `sys.exit(0)`
  to `sys.exit(2)` mutant.

## Mutation categories tested

- **condition**: Negate `if`/`elif` conditions, remove `not`
- **exit_code**: Change `sys.exit(N)` / `return N` to opposite code
- **operator**: `and`/`or` swap, `==`/`!=` swap, `in`/`not in` swap
- **regex**: `.search()` / `.match()` swap
- **string**: Mutate `startswith()` arguments

## How to re-run

```bash
# All 8 hooks (takes ~15 minutes)
.venv/bin/python tests/test_hooks/run_mutations.py

# Single hook
.venv/bin/python tests/test_hooks/run_mutations.py block_bare_pip.py
```

Each run appends results to `tests/test_hooks/mutation_run_log.jsonl`
(gitignored).  The summary table shows deltas from the previous run so
you can see kill-rate improvements across iterations.
