"""
Coverage check for the MCP tester.

Asserts the three artifacts that describe the tester's tool surface stay in
lockstep with the unified MCP server:

  * TOOL_DEFAULTS — one entry per tool.
  * TOOL_CATEGORIES — every defaulted tool appears in exactly one bucket.
  * input_exam/ — one JSON file per defaulted tool, all parseable.

Exits non-zero on any mismatch. Designed to be runnable without pytest or
any third-party dependency; only the standard library is used.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


TESTTOOL_DIR = Path(__file__).resolve().parent
INPUT_EXAM_DIR = TESTTOOL_DIR / "input_exam"

# Add code-tiny/ to sys.path so ``testtool.tool_defaults`` resolves regardless
# of where the script is invoked from.
sys.path.insert(0, str(TESTTOOL_DIR.parent))

from testtool.tool_defaults import TOOL_CATEGORIES, TOOL_DEFAULTS  # noqa: E402


def _collect_categorised_tools() -> list[str]:
    seen: list[str] = []
    for category, names in TOOL_CATEGORIES.items():
        for name in names:
            seen.append(name)
    return seen


def main() -> int:
    defaults = set(TOOL_DEFAULTS)
    categorised = set(_collect_categorised_tools())

    input_exam_files = {p.stem for p in INPUT_EXAM_DIR.glob("*.json")}
    stems = {p.stem for p in INPUT_EXAM_DIR.glob("*.json")}

    errors: list[str] = []

    if defaults != categorised:
        errors.append(
            "TOOL_CATEGORIES mismatch with TOOL_DEFAULTS:\n"
            f"  in defaults but not in categories: {sorted(defaults - categorised) or '∅'}\n"
            f"  in categories but not in defaults: {sorted(categorised - defaults) or '∅'}"
        )

    if defaults != input_exam_files:
        errors.append(
            "input_exam/ mismatch with TOOL_DEFAULTS:\n"
            f"  tools missing an input_exam file: {sorted(defaults - input_exam_files) or '∅'}\n"
            f"  input_exam files without a default: {sorted(input_exam_files - defaults) or '∅'}"
        )

    parse_errors: list[str] = []
    for path in sorted(INPUT_EXAM_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                json.load(fh)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"  {path.name}: line {exc.lineno}: {exc.msg}")
    if parse_errors:
        errors.append("input_exam/ JSON parse errors:\n" + "\n".join(parse_errors))

    if errors:
        print("FAIL", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print(
        "OK  "
        f"{len(defaults)} tools, "
        f"{len(TOOL_CATEGORIES)} categories, "
        f"{len(stems)} input_exam files"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())