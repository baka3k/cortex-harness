#!/usr/bin/env python3
"""Dev helper: diff selected graph fixture cases against a RUNNING Rust server.

Usage:
    python scripts/rust_mcp/graph_diff_cases.py [--port 8798] \
        [--cases id1,id2|all] [--max-diffs 40]

Prints EVERY diff (no --show truncation) so divergences can be categorized
without waiting for a full comparator pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_contract import deep_diff  # noqa: E402
from compare_graph import FIXTURE_PATH, _call_with_timeout  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("--cases", default="all", help="comma-separated case ids, or 'all'")
    parser.add_argument("--max-diffs", type=int, default=40)
    args = parser.parse_args()

    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    wanted = None if args.cases == "all" else set(args.cases.split(","))

    total_pass = total_fail = 0
    for case in fixtures["cases"]:
        if wanted is not None and case["id"] not in wanted:
            continue
        actual = _call_with_timeout(args.port, case["tool"], case["arguments"])
        diffs: list[str] = []
        deep_diff(actual, case["expected"], case["id"], diffs)
        if diffs:
            total_fail += 1
            print(f"\n=== FAIL {case['id']} ({len(diffs)} diffs) ===")
            for line in diffs[: args.max_diffs]:
                print(f"  - {line}")
            if len(diffs) > args.max_diffs:
                print(f"  … and {len(diffs) - args.max_diffs} more")
        else:
            total_pass += 1
            print(f"[diff] {case['id']} OK")

    print(f"\nSELECTED: {total_pass} pass / {total_fail} fail")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
