"""Inject external tool findings as # HOOK:<TOOL>: comments into source files.

Two modes:
  Single:  python3 inject_tool_findings.py <file_path> <TOOL_NAME>
  Batch:   python3 inject_tool_findings.py --batch <TOOL_NAME> < file_list.txt
           python3 inject_tool_findings.py --batch <TOOL_NAME> f1.py f2.py ...

Batch mode runs the tool ONCE on all files — 30-60x faster for large sets.
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_inject import inject_at_line, remove_hook_comments

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Single-file parsers (legacy, used by main())
# ---------------------------------------------------------------------------


def _parse_pyright(output: str, tmp_path: str) -> list[tuple[int, str]]:
    escaped = re.escape(tmp_path)
    pattern = re.compile(rf"{escaped}:(\d+):\d+ - error: (.+)")
    return [(int(m.group(1)), m.group(2)) for m in pattern.finditer(output)]


def _parse_bandit(output: str, tmp_path: str) -> list[tuple[int, str]]:
    escaped = re.escape(tmp_path)
    location_pattern = re.compile(rf"Location: {escaped}:(\d+):\d+")
    issue_pattern = re.compile(r">> Issue: (.+)")
    findings: list[tuple[int, str]] = []
    current_issue: str | None = None
    for line in output.splitlines():
        m = issue_pattern.search(line)
        if m:
            current_issue = m.group(1)
            continue
        m = location_pattern.search(line)
        if m and current_issue is not None:
            findings.append((int(m.group(1)), current_issue))
            current_issue = None
    return findings


def _parse_semgrep(output: str, tmp_path: str) -> list[tuple[int, str]]:
    import json as _json

    try:
        data = _json.loads(output)
    except (ValueError, _json.JSONDecodeError):
        return []
    findings: list[tuple[int, str]] = []
    for result in data.get("results", []):
        try:
            line_num = result["start"]["line"]
            check_id = result["check_id"]
            message = result["extra"]["message"]
            short_id = check_id.rsplit(".", 1)[-1] if "." in check_id else check_id
            findings.append((line_num, f"[{short_id}] {message}"))
        except (KeyError, TypeError):
            continue
    return findings


# ---------------------------------------------------------------------------
# Batch parsers: return dict[filepath -> list[(line, message)]]
# ---------------------------------------------------------------------------


def _batch_parse_pyright(output: str) -> dict[str, list[tuple[int, str]]]:
    findings: dict[str, list[tuple[int, str]]] = defaultdict(list)
    pattern = re.compile(r"(.+?):(\d+):\d+ - error: (.+)")
    for m in pattern.finditer(output):
        # Pyright indents each diagnostic line by two spaces, so the path
        # group captures leading whitespace. Strip it, or the findings dict
        # is keyed "  /abs/path" and never matches the un-indented lookup
        # in batch_main() → every pyright finding is silently dropped.
        findings[m.group(1).strip()].append((int(m.group(2)), m.group(3)))
    return dict(findings)


def _batch_parse_bandit(output: str) -> dict[str, list[tuple[int, str]]]:
    findings: dict[str, list[tuple[int, str]]] = defaultdict(list)
    issue_pattern = re.compile(r">> Issue: (.+)")
    location_pattern = re.compile(r"Location: (.+?):(\d+):\d+")
    current_issue: str | None = None
    for line in output.splitlines():
        m = issue_pattern.search(line)
        if m:
            current_issue = m.group(1)
            continue
        m = location_pattern.search(line)
        if m and current_issue is not None:
            findings[m.group(1)].append((int(m.group(2)), current_issue))
            current_issue = None
    return dict(findings)


def _batch_parse_semgrep(output: str) -> dict[str, list[tuple[int, str]]]:
    import json as _json

    try:
        data = _json.loads(output)
    except (ValueError, _json.JSONDecodeError):
        return {}
    findings: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for result in data.get("results", []):
        try:
            fp = result["path"]
            line_num = result["start"]["line"]
            check_id = result["check_id"]
            message = result["extra"]["message"]
            short_id = check_id.rsplit(".", 1)[-1] if "." in check_id else check_id
            findings[fp].append((line_num, f"[{short_id}] {message}"))
        except (KeyError, TypeError):
            continue
    return dict(findings)


BATCH_PARSERS = {
    "PYRIGHT": _batch_parse_pyright,
    "BANDIT": _batch_parse_bandit,
    "SEMGREP": _batch_parse_semgrep,
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _find_venv_python(file_path: str) -> str | None:
    d = Path(file_path).resolve().parent
    while d != d.parent:
        candidate = d / ".venv" / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
        d = d.parent
    return None


def _pyright_extra_args(file_path: str) -> list[str]:
    venv_python = _find_venv_python(file_path)
    return ["--pythonpath", venv_python] if venv_python else []


TOOL_CONFIGS: dict[str, dict] = {
    "PYRIGHT": {
        "bin": "pyright",
        "args": [],
        "parser": _parse_pyright,
        "extra_args": _pyright_extra_args,
    },
    "BANDIT": {
        "bin": "bandit",
        "args": ["-ll", "-q"],
        "parser": _parse_bandit,
    },
    "SEMGREP": {
        "bin": "semgrep",
        "args": ["scan", "--disable-version-check", "--config", "auto", "--json"],
        "parser": _parse_semgrep,
        "stdout_only": True,
        "env": {
            "SEMGREP_SETTINGS_FILE": os.path.join(
                os.environ.get("TMPDIR", "/tmp"), "semgrep_settings.yml"
            ),
            "SEMGREP_LOG_FILE": os.path.join(
                os.environ.get("TMPDIR", "/tmp"), "semgrep.log"
            ),
            "SEMGREP_VERSION_CACHE_PATH": os.path.join(
                os.environ.get("TMPDIR", "/tmp"), "semgrep_version"
            ),
        },
    },
}


def _resolve_tool_cmd(file_path: str, bin_name: str) -> list[str]:
    d = Path(file_path).resolve().parent
    while d != d.parent:
        candidate = d / ".venv" / "bin" / bin_name
        if candidate.is_file():
            return [str(candidate)]
        d = d.parent
    return ["uvx", bin_name]


# ---------------------------------------------------------------------------
# Single-file mode (legacy)
# ---------------------------------------------------------------------------


def main(file_path: str, tool_name: str) -> None:
    """Run the tool on one file via a temp copy, inject findings."""
    if tool_name not in TOOL_CONFIGS:
        raise ValueError(f"Unknown tool '{tool_name}'")

    config = TOOL_CONFIGS[tool_name]
    path = Path(file_path)
    if not path.exists():
        return

    lines = path.read_text().splitlines(keepends=True)
    cleaned_for_analysis = remove_hook_comments(lines, tool_name)

    extra = config["extra_args"](file_path) if "extra_args" in config else []
    cmd = _resolve_tool_cmd(file_path, config["bin"]) + config["args"] + extra

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(tmp_fd, "w") as tmp_file:
            tmp_file.write("".join(cleaned_for_analysis))
        try:
            run_env = {**os.environ, **config.get("env", {})}
            result = subprocess.run(
                cmd + [tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
                env=run_env,
            )
            output = (
                result.stdout
                if config.get("stdout_only")
                else result.stdout + result.stderr
            )
            findings = config["parser"](output, tmp_path)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            findings = []
    finally:
        os.unlink(tmp_path)

    _inject_findings(path, tool_name, findings, lines)


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------


def batch_main(file_paths: list[str], tool_name: str) -> None:
    """Run the tool ONCE on all files, parse batch output, inject per-file."""
    if tool_name not in TOOL_CONFIGS:
        raise ValueError(f"Unknown tool '{tool_name}'")

    config = TOOL_CONFIGS[tool_name]
    existing = [fp for fp in file_paths if Path(fp).exists()]
    if not existing:
        return

    # Phase 1: strip stale HOOK comments in-place so tools see clean code
    for fp in existing:
        path = Path(fp)
        lines = path.read_text().splitlines(keepends=True)
        cleaned = remove_hook_comments(lines, tool_name)
        if cleaned != lines:
            path.write_text("".join(cleaned))

    # Phase 2: run tool once on all files
    extra = config["extra_args"](existing[0]) if "extra_args" in config else []
    cmd = (
        _resolve_tool_cmd(existing[0], config["bin"])
        + config["args"]
        + extra
        + existing
    )

    try:
        run_env = {**os.environ, **config.get("env", {})}
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=run_env,
        )
        output = (
            result.stdout
            if config.get("stdout_only")
            else result.stdout + result.stderr
        )
    except subprocess.TimeoutExpired:
        logger.warning("%s batch timed out after 120s", tool_name)
        return
    except FileNotFoundError:
        return

    # Phase 3: parse and inject per-file
    all_findings = BATCH_PARSERS[tool_name](output)
    for fp in existing:
        findings = all_findings.get(fp, [])
        if not findings:
            continue
        path = Path(fp)
        lines = path.read_text().splitlines(keepends=True)
        for ln, msg in sorted(findings, reverse=True):
            inject_at_line(lines, ln, tool_name, msg)
        path.write_text("".join(lines))


# ---------------------------------------------------------------------------
# Shared inject helper
# ---------------------------------------------------------------------------


def _inject_findings(
    path: Path,
    tool_name: str,
    findings: list[tuple[int, str]],
    original_lines: list[str],
) -> None:
    """Strip stale HOOK comments, inject new findings, write back."""
    import fcntl

    lock_path = str(path) + ".hook_lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            current_lines = path.read_text().splitlines(keepends=True)
            cleaned = remove_hook_comments(current_lines, tool_name)
            if not findings and cleaned == current_lines:
                return
            if findings:
                for ln, msg in sorted(findings, reverse=True):
                    inject_at_line(cleaned, ln, tool_name, msg)
            path.write_text("".join(cleaned))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    try:
        os.unlink(lock_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--batch":
        tool_name = sys.argv[2]
        if len(sys.argv) > 3:
            fps = sys.argv[3:]
        else:
            fps = [line.strip() for line in sys.stdin if line.strip()]
        batch_main(fps, tool_name)
    elif len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        print(f"Usage: {sys.argv[0]} <file_path> <TOOL_NAME>", file=sys.stderr)
        print(f"   or: {sys.argv[0]} --batch <TOOL_NAME> [files...]", file=sys.stderr)
        sys.exit(1)
