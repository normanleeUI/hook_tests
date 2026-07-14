"""PreToolUse hook on Edit|Write: block unpinned dependency versions.

Inspects the NEW/MODIFIED text from Edit or Write operations on dependency
files (pyproject.toml, requirements*.txt). Blocks (exit 2) if any dependency
in the changed portion uses an unpinned version specifier.

For an Edit, diffs new_string against old_string and flags only deps that
are unpinned in new_string but were NOT already unpinned in old_string — an
Edit's new_string includes unchanged context lines, so a naive scan re-flags
pre-existing unpinned deps that merely sit near the change. For a Write
(whole-file content, no old_string baseline), the full content is checked.
Either way, only newly introduced unpinned deps are caught.

What counts as "pinned"?
  - Exact pin:       requests==2.31.0        OK
  - Bounded range:   pandas>=2.0,<3          OK  (upper bound prevents surprises)
  - Open-ended:      requests>=2.0           BLOCKED  (no ceiling)
  - Bare name:       requests                BLOCKED  (any version at all)
  - Compatible:      requests~=2.31          BLOCKED  (prefer explicit bounds)

renv.lock stores exact versions in JSON by design, so it is not checked.

Exit code: 2 if unpinned deps found in changed text, 0 otherwise.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from hook_log import log_hook

log_hook("check_dependency_pins")


def _check_spec(spec: str) -> str | None:
    """Return spec if unpinned, None if pinned or not a real dep."""
    spec = spec.strip()
    if not spec or spec.startswith("#") or spec.startswith("["):
        return None
    # Strip PEP 508 environment markers (e.g. ;python_version<"3.8")
    # before checking version specifiers — markers constrain *when* a
    # dependency applies, not its version bounds.
    version_part = spec.split(";")[0]
    if "==" in version_part:
        return None
    if ">=" in version_part and "<" in version_part:
        return None
    return spec


def _extract_dep_specs(text: str, is_pyproject: bool) -> list[str]:
    """Extract dependency specifiers from text, return any that are unpinned."""
    unpinned: list[str] = []

    if is_pyproject:
        in_deps = False
        bracket_depth = 0

        for line in text.splitlines():
            stripped = line.strip()

            if re.match(r"^(dependencies|requires)\s*=\s*\[", stripped):
                in_deps = True
                bracket_depth = stripped.count("[") - stripped.count("]")
                if bracket_depth <= 0:
                    # Single-line: dependencies = ["foo", "bar"]
                    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', stripped):
                        spec = (m.group(1) or m.group(2)).strip()
                        bad = _check_spec(spec)
                        if bad:
                            unpinned.append(bad)
                    in_deps = False
                continue
            if re.match(r"^\w[\w-]*\s*=\s*\[", stripped) and in_deps:
                bracket_depth += stripped.count("[") - stripped.count("]")
                continue

            if in_deps:
                bracket_depth += stripped.count("[") - stripped.count("]")
                if bracket_depth <= 0:
                    in_deps = False
                    continue

                m = re.search(r'"([^"]+)"', stripped) or re.search(
                    r"'([^']+)'", stripped
                )
                if not m:
                    continue

                bad = _check_spec(m.group(1))
                if bad:
                    unpinned.append(bad)
    else:
        for line in text.splitlines():
            stripped = line.strip()

            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("-")
                or stripped.startswith("http")
                or stripped.startswith("/")
                or stripped.startswith(".")
            ):
                continue

            # Strip PEP 508 environment markers before checking specifiers
            version_part = stripped.split(";")[0]
            if "==" in version_part:
                continue
            if ">=" in version_part and "<" in version_part:
                continue

            unpinned.append(stripped)

    return unpinned


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    raw = (payload.get("tool_response") or {}).get("filePath") or (tool_input).get(
        "file_path"
    )
    if not raw:
        return 0

    p = Path(raw)
    is_pyproject = p.name == "pyproject.toml"
    is_requirements = p.name.startswith("requirements") and p.suffix == ".txt"

    if not (is_pyproject or is_requirements):
        return 0

    inp = payload.get("tool_input") or {}
    new_text = inp.get("new_string") or inp.get("content") or ""
    if not new_text:
        return 0

    # An Edit's new_string includes unchanged *context* lines around the
    # change (Edit replaces a contiguous block), so checking all of it
    # re-flags pre-existing unpinned deps that merely sit near the edit —
    # e.g. you can't add a properly-pinned dep to an array that already
    # contains an open-ended `httpx>=0.28.1`. Diff against the replaced
    # (old) text and only flag deps that are newly unpinned. old_string is
    # present only for Edit; Write (whole-file content) has no baseline, so
    # everything in it is genuinely being authored and is checked in full.
    old_text = inp.get("old_string")
    if old_text:
        new_unpinned = _extract_dep_specs(new_text, is_pyproject)
        old_unpinned = set(_extract_dep_specs(old_text, is_pyproject))
        unpinned = [spec for spec in new_unpinned if spec not in old_unpinned]
    else:
        unpinned = _extract_dep_specs(new_text, is_pyproject)
    if not unpinned:
        return 0

    items = "\n".join(f"  - {spec}" for spec in unpinned)
    print(
        f"BLOCKED: unpinned dependencies in new/modified text for {p.name}:\n"
        f"{items}\n"
        f"\n"
        f"Pin all dependency versions for reproducibility.\n"
        f"Use exact versions (==) or bounded ranges (>=X.Y,<Z).\n"
        f"Avoid bare names or open-ended >= without an upper bound.\n"
        f"\n"
        f"Tip: `uv lock` generates a lockfile with exact pins automatically.\n"
        f"For requirements.txt, `uv pip compile` pins all transitive deps.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
