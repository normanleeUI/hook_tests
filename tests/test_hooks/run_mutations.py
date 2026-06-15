"""Custom mutation testing runner for hook scripts.

mutmut 3.x uses a trampoline/import-based approach that doesn't work for
hook scripts invoked via subprocess. This script applies mutations in-place,
runs the relevant tests, and restores the original.

Usage:
    python tests/test_hooks/run_mutations.py [hook_name]

If hook_name is omitted, runs against all 8 blocking hooks.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HOOKS_DIR = Path.home() / ".claude" / "hooks"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_LOG = PROJECT_ROOT / "tests" / "test_hooks" / "mutation_run_log.jsonl"

HOOK_TEST_MAP: dict[str, list[str]] = {
    "block_bare_pip.py": ["tests/test_hooks/test_block_bare_pip.py"],
    "block_git_add_env.py": ["tests/test_hooks/test_block_git_add_env.py"],
    "block_read_env.py": ["tests/test_hooks/test_block_read_env.py"],
    "block_suppressions.py": ["tests/test_hooks/test_block_suppressions.py"],
    "check_dependency_pins.py": ["tests/test_hooks/test_check_dependency_pins.py"],
    "block_glob_deny_rules.py": ["tests/test_hooks/test_block_glob_deny_rules.py"],
    "pip_audit_check.py": ["tests/test_hooks/test_pip_audit_check.py"],
    "scan_secrets_on_commit.py": [
        "tests/test_hooks/test_scan_secrets_gate.py",
        "tests/test_hooks/test_secret_patterns.py",
    ],
}


@dataclass
class Mutation:
    """A single mutation to apply to a hook source file."""

    hook: str
    line_num: int
    description: str
    original_line: str
    mutated_line: str
    category: str


@dataclass
class MutationResult:
    mutation: Mutation
    status: str  # "killed", "survived", "timeout", "error"
    detail: str = ""


@dataclass
class HookResults:
    hook: str
    total: int = 0
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    error: int = 0
    equivalent: int = 0
    results: list[MutationResult] = field(default_factory=list)

    @property
    def kill_rate(self) -> float:
        testable = self.total - self.equivalent
        if testable == 0:
            return 100.0
        return (self.killed / testable) * 100


def _is_code_line(lines: list[str]) -> list[bool]:
    """Determine which lines are actual code vs strings/comments/imports.

    Tracks multi-line string boundaries (triple-quoted) to avoid mutating
    error message text, which is equivalent to no behavioral change.
    """
    result = [False] * len(lines)
    in_multiline_string = False
    multiline_delim = ""

    for i, line in enumerate(lines):
        stripped = line.strip()

        if in_multiline_string:
            if multiline_delim in stripped:
                in_multiline_string = False
            continue

        for delim in ('"""', "'''"):
            if delim in stripped:
                count = stripped.count(delim)
                if count == 1:
                    in_multiline_string = True
                    multiline_delim = delim
                    break
                if count == 2 and stripped.startswith(delim):
                    break
        else:
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("import ") or stripped.startswith("from "):
                continue
            if stripped.startswith('"') or stripped.startswith("'"):
                continue
            if stripped.startswith('f"') or stripped.startswith("f'"):
                continue
            result[i] = True
            continue

        continue

    return result


# --- Mutation strategies ---
# Each returns a list of Mutations for a single source line.


def _mutate_conditions(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Negate if/elif conditions."""
    if_match = re.match(r"^(\s*)(if|elif)\s+(.+):\s*$", line)
    if not if_match:
        return []
    cond = if_match.group(3)
    if cond.startswith("not "):
        negated = cond[4:]
    elif " and " in cond or " or " in cond:
        negated = f"not ({cond})"
    else:
        negated = f"not {cond}"
    mutated = f"{if_match.group(1)}{if_match.group(2)} {negated}:"
    return [
        Mutation(
            hook,
            i,
            f"Negate condition: {cond!r} -> {negated!r}",
            line,
            mutated,
            "condition",
        )
    ]


def _mutate_exit_codes(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Swap exit codes: sys.exit(N) and return N."""
    results: list[Mutation] = []
    exit_match = re.match(r"^(\s*)sys\.exit\((\d+)\)\s*$", line)
    if exit_match:
        code = int(exit_match.group(2))
        new_code = 0 if code != 0 else 2
        mutated = f"{exit_match.group(1)}sys.exit({new_code})"
        results.append(
            Mutation(
                hook,
                i,
                f"Change exit code: {code} -> {new_code}",
                line,
                mutated,
                "exit_code",
            )
        )
    return_match = re.match(r"^(\s*)return\s+(\d+)\s*$", line)
    if return_match:
        code = int(return_match.group(2))
        new_code = 0 if code != 0 else 2
        mutated = f"{return_match.group(1)}return {new_code}"
        results.append(
            Mutation(
                hook,
                i,
                f"Change return code: {code} -> {new_code}",
                line,
                mutated,
                "exit_code",
            )
        )
    return results


def _mutate_bool_operators(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Swap and/or operators."""
    results: list[Mutation] = []
    if " and " in stripped and not stripped.startswith("#"):
        mutated = line.replace(" and ", " or ", 1)
        if mutated != line:
            results.append(
                Mutation(hook, i, "Replace 'and' with 'or'", line, mutated, "operator")
            )
    if " or " in stripped and not stripped.startswith("#"):
        mutated = line.replace(" or ", " and ", 1)
        if mutated != line:
            results.append(
                Mutation(hook, i, "Replace 'or' with 'and'", line, mutated, "operator")
            )
    return results


def _mutate_comparisons(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Swap == and != operators."""
    results: list[Mutation] = []
    if "==" in stripped and "re.compile" not in stripped:
        mutated = line.replace("==", "!=", 1)
        if mutated != line:
            results.append(
                Mutation(hook, i, "Replace '==' with '!='", line, mutated, "operator")
            )
    if "!=" in stripped and "re.compile" not in stripped:
        mutated = line.replace("!=", "==", 1)
        if mutated != line:
            results.append(
                Mutation(hook, i, "Replace '!=' with '=='", line, mutated, "operator")
            )
    return results


def _mutate_regex_methods(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Swap .search() and .match() calls."""
    results: list[Mutation] = []
    if ".search(" in stripped:
        mutated = line.replace(".search(", ".match(", 1)
        results.append(
            Mutation(hook, i, "Replace .search() with .match()", line, mutated, "regex")
        )
    if (
        ".match(" in stripped
        and "re.match(" not in stripped
        and "env_match" not in stripped
    ):
        mutated = line.replace(".match(", ".search(", 1)
        results.append(
            Mutation(hook, i, "Replace .match() with .search()", line, mutated, "regex")
        )
    return results


def _mutate_not_removal(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Remove 'not' from conditions."""
    not_match = re.search(r"\bnot\s+", stripped)
    if not_match and ("if " in stripped or "and " in stripped or "or " in stripped):
        mutated = line.replace("not ", "", 1)
        if mutated != line:
            return [
                Mutation(
                    hook, i, "Remove 'not' from condition", line, mutated, "condition"
                )
            ]
    return []


def _mutate_startswith(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Mutate startswith() string arguments."""
    sw_match = re.search(r'\.startswith\(["\'](.+?)["\']\)', stripped)
    if not sw_match:
        return []
    original_arg = sw_match.group(1)
    mutated_arg = "XX" + original_arg
    mutated = line.replace(f'"{original_arg}"', f'"{mutated_arg}"', 1)
    if mutated == line:
        mutated = line.replace(f"'{original_arg}'", f"'{mutated_arg}'", 1)
    if mutated != line:
        return [
            Mutation(
                hook,
                i,
                f"Mutate startswith arg: {original_arg!r} -> {mutated_arg!r}",
                line,
                mutated,
                "string",
            )
        ]
    return []


def _mutate_in_operators(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Swap 'in' and 'not in' operators."""
    in_match = re.search(r'("[^"]+"|\'[^\']+\')\s+not\s+in\s+', stripped)
    if in_match:
        mutated = line.replace(" not in ", " in ", 1)
        if mutated != line:
            return [
                Mutation(
                    hook, i, "Replace 'not in' with 'in'", line, mutated, "operator"
                )
            ]
    elif re.search(r'("[^"]+"|\'[^\']+\')\s+in\s+', stripped):
        mutated = line.replace(" in ", " not in ", 1)
        if mutated != line:
            return [
                Mutation(
                    hook, i, "Replace 'in' with 'not in'", line, mutated, "operator"
                )
            ]
    return []


def _mutate_line_deletion(
    hook: str, i: int, line: str, stripped: str, indent: str
) -> list[Mutation]:
    """Replace key assignment lines with pass."""
    if any(
        kw in stripped for kw in ["env_match = None", "env_match =", "bulk_match ="]
    ):
        mutated = f"{indent}pass"
        return [
            Mutation(
                hook,
                i,
                f"Replace line with pass: {stripped!r}",
                line,
                mutated,
                "condition",
            )
        ]
    return []


_STRATEGIES = [
    _mutate_conditions,
    _mutate_exit_codes,
    _mutate_bool_operators,
    _mutate_comparisons,
    _mutate_regex_methods,
    _mutate_not_removal,
    _mutate_startswith,
    _mutate_in_operators,
    _mutate_line_deletion,
]


def generate_mutations(hook_name: str, source: str) -> list[Mutation]:
    """Generate targeted mutations for a hook's decision logic."""
    mutations: list[Mutation] = []
    lines = source.splitlines()
    code_flags = _is_code_line(lines)

    for i, line in enumerate(lines, start=1):
        if not code_flags[i - 1]:
            continue
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        for strategy in _STRATEGIES:
            mutations.extend(strategy(hook_name, i, line, stripped, indent))

    return mutations


def apply_mutation(hook_path: Path, mutation: Mutation, source_lines: list[str]) -> str:
    """Apply a single mutation to the source and return the mutated source."""
    mutated_lines = source_lines.copy()
    mutated_lines[mutation.line_num - 1] = mutation.mutated_line
    return "\n".join(mutated_lines) + "\n"


def run_tests_for_hook(
    hook_name: str, hooks_dir: Path, timeout: int = 60
) -> tuple[bool, str]:
    """Run the test suite for a given hook. Returns (all_passed, output)."""
    test_files = HOOK_TEST_MAP[hook_name]
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "pytest"),
        *test_files,
        "--no-header",
        "-q",
        "-o",
        "addopts=",
        "-p",
        "no:xdist",
        "-x",
        "-m",
        "not network",
        "--tb=no",
    ]
    env = {**os.environ, "HOOKS_DIR": str(hooks_dir)}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"


def _test_single_mutant(
    mutation: Mutation,
    sandbox_hook: Path,
    source_lines: list[str],
    original_source: str,
    hook_name: str,
    sandbox_dir: Path,
) -> MutationResult:
    """Apply one mutation, run tests, restore original, return result."""
    mutated_source = apply_mutation(sandbox_hook, mutation, source_lines)
    sandbox_hook.write_text(mutated_source)
    try:
        passed, output = run_tests_for_hook(hook_name, sandbox_dir, timeout=90)
        if passed:
            return MutationResult(
                mutation=mutation,
                status="survived",
                detail="Tests passed with mutation applied",
            )
        if "TIMEOUT" in output:
            return MutationResult(mutation=mutation, status="timeout")
        return MutationResult(mutation=mutation, status="killed")
    finally:
        sandbox_hook.write_text(original_source)


def run_mutation_testing(hook_name: str) -> HookResults:
    """Run mutation testing for a single hook.

    Copies all hooks to a sandbox temp directory, mutates the copy, and
    runs tests with HOOKS_DIR pointed at the sandbox.
    """
    hook_path = HOOKS_DIR / hook_name
    results = HookResults(hook=hook_name)

    if not hook_path.exists():
        print(f"  SKIP: {hook_path} not found")
        return results

    sandbox_dir = Path(
        tempfile.mkdtemp(prefix=f"mutmut_sandbox_{hook_name.replace('.py', '')}_")
    )
    shutil.rmtree(sandbox_dir)
    shutil.copytree(HOOKS_DIR, sandbox_dir)
    sandbox_hook = sandbox_dir / hook_name

    original_source = hook_path.read_text()
    source_lines = original_source.splitlines()

    print("  Verifying baseline tests pass...")
    passed, output = run_tests_for_hook(hook_name, sandbox_dir)
    if not passed:
        print(f"  ERROR: Baseline tests fail for {hook_name}!")
        print(f"  Output: {output[:500]}")
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return results

    mutations = generate_mutations(hook_name, original_source)
    results.total = len(mutations)
    print(f"  Generated {len(mutations)} mutations")

    for idx, mutation in enumerate(mutations, 1):
        print(
            f"  [{idx}/{len(mutations)}] {mutation.description} (line {mutation.line_num})...",
            end=" ",
            flush=True,
        )
        result = _test_single_mutant(
            mutation,
            sandbox_hook,
            source_lines,
            original_source,
            hook_name,
            sandbox_dir,
        )
        print(result.status.upper())
        if result.status == "killed":
            results.killed += 1
        elif result.status == "survived":
            results.survived += 1
        elif result.status == "timeout":
            results.timeout += 1
        results.results.append(result)

    shutil.rmtree(sandbox_dir, ignore_errors=True)
    return results


def main() -> int:
    hooks = sys.argv[1:] if len(sys.argv) > 1 else list(HOOK_TEST_MAP.keys())

    all_results: dict[str, HookResults] = {}

    for hook_name in hooks:
        if hook_name not in HOOK_TEST_MAP:
            print(f"Unknown hook: {hook_name}")
            continue
        print(f"\n{'=' * 60}")
        print(f"Mutation testing: {hook_name}")
        print(f"{'=' * 60}")
        results = run_mutation_testing(hook_name)
        all_results[hook_name] = results

        print(
            f"\n  Summary: {results.killed} killed, {results.survived} survived, "
            f"{results.timeout} timeout, {results.error} error / {results.total} total"
        )
        print(f"  Kill rate: {results.kill_rate:.1f}%")

        if results.survived > 0:
            print("\n  Surviving mutants:")
            for r in results.results:
                if r.status == "survived":
                    print(f"    - Line {r.mutation.line_num}: {r.mutation.description}")
                    print(f"      Original: {r.mutation.original_line.strip()}")
                    print(f"      Mutated:  {r.mutation.mutated_line.strip()}")

    # Load previous run for delta comparison
    prev_run = _load_previous_run()

    print(f"\n\n{'=' * 60}")
    print("OVERALL SUMMARY")
    print(f"{'=' * 60}")
    header = f"{'Hook':<35} {'Total':>5} {'Killed':>6} {'Survived':>8} {'Rate':>6}"
    if prev_run:
        header += f"  {'Prev':>6}  {'Delta':>6}"
    print(header)
    print("-" * len(header))
    for hook_name, results in all_results.items():
        line = (
            f"{hook_name:<35} {results.total:>5} {results.killed:>6} "
            f"{results.survived:>8} {results.kill_rate:>5.1f}%"
        )
        if prev_run and hook_name in prev_run:
            prev_rate = prev_run[hook_name]["kill_rate"]
            delta = results.kill_rate - prev_rate
            sign = "+" if delta > 0 else ""
            line += f"  {prev_rate:>5.1f}%  {sign}{delta:>5.1f}%"
        print(line)

    _append_run_log(all_results)

    return 0


def _load_previous_run() -> dict | None:
    """Load the most recent entry from the run log, if it exists."""
    if not RUN_LOG.exists():
        return None
    lines = RUN_LOG.read_text().strip().splitlines()
    if not lines:
        return None
    last = json.loads(lines[-1])
    return last.get("hooks", {})


def _append_run_log(all_results: dict[str, HookResults]) -> None:
    """Append this run's results to the persistent JSONL log."""
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hooks": {},
    }
    for hook_name, results in all_results.items():
        entry["hooks"][hook_name] = {
            "total": results.total,
            "killed": results.killed,
            "survived": results.survived,
            "timeout": results.timeout,
            "error": results.error,
            "equivalent": results.equivalent,
            "kill_rate": results.kill_rate,
            "survivors": [
                {
                    "line": r.mutation.line_num,
                    "description": r.mutation.description,
                    "original": r.mutation.original_line.strip(),
                    "mutated": r.mutation.mutated_line.strip(),
                    "category": r.mutation.category,
                }
                for r in results.results
                if r.status == "survived"
            ],
        }

    run_number = 1
    if RUN_LOG.exists():
        run_number = len(RUN_LOG.read_text().strip().splitlines()) + 1
    entry["run"] = run_number

    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\nRun #{run_number} appended to {RUN_LOG}")


if __name__ == "__main__":
    sys.exit(main())
