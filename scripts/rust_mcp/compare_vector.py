#!/usr/bin/env python3
"""Compare a server under test against the vector-lane golden fixtures.

Replay targets:

* Python server  — determinism gate for phase-01 (`--flavor …`, captures are
  re-recorded into a temp fixtures file by capture_vector_golden.py and diffed
  here with --fixtures).
* Rust server    — acceptance gate for phase-03 (remote lane) / phase-04
  (local sidecar). The Rust unified server today returns the phase-12 stub
  (`results: []`), so vector cases are expected RED until those phases land.

Score-ish floats compare under the phase-02 absolute tolerance (1e-6); all
structure compares byte-exact after masking volatile fields.

Usage:
    python scripts/rust_mcp/compare_vector.py [--flavor both|unified|mind]
        [--rust | --replay <fixtures.json>] [--port …]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vector_contract as vc  # noqa: E402
from compare_contract import RUST_BIN, wait_for_server  # noqa: E402
from compare_graph import _call_with_timeout  # noqa: E402
from compare_mind import tolerant_deep_diff  # noqa: E402

GATES: dict[str, dict] = {}

# Order-insensitive list fields: set-iteration order in the Python reference
# can swap siblings between runs (same class as the phase-13
# `fetch_relations_with_depth` finding — mask the order, not the content).
MULTISET_PREFIXES = (
    "structured_content.data.capability_diagnostics.available_relationships",
)


def _lookup(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def reconcile_multisets(diffs: list[str], actual, expected, case_id: str) -> None:
    """Drop order-only diffs under multiset prefixes after verifying content."""
    kept = []
    for diff in diffs:
        head, sep, _ = diff.partition(": ")
        matched = False
        if sep:
            for prefix in MULTISET_PREFIXES:
                full = f"{case_id}.{prefix}"
                if head.startswith(full + "["):
                    actual_list = _lookup(actual, prefix) or []
                    expected_list = _lookup(expected, prefix) or []
                    if sorted(map(repr, actual_list)) == sorted(map(repr, expected_list)):
                        matched = True
                    break
        if not matched:
            kept.append(diff)
    diffs[:] = kept


def gate(name: str) -> dict:
    return GATES.setdefault(name, {"pass": 0, "fail": 0, "failures": []})


def launch_rust_server(flavor: str, port: int) -> subprocess.Popen:
    spec_env = (
        vc.unified_server_env(port) if flavor == "unified" else vc.mind_server_env(port)
    )
    env = dict(os.environ)
    env.update(spec_env)
    command = [
        str(RUST_BIN),
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--path",
        "/mcp",
    ]
    if flavor == "mind":
        command += ["--server", "mind"]
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def replay_cases(port: int, cases: list[dict], expected: dict, label: str) -> None:
    entry = gate(label)
    for case in cases:
        golden = expected["cases"].get(case["id"])
        if golden is None:
            entry["fail"] += 1
            entry["failures"].append(f"{case['id']}: missing golden")
            continue
        try:
            actual = _call_with_timeout(port, case["tool"], case["arguments"])
        except Exception as error:  # noqa: BLE001 — comparator must keep going
            entry["fail"] += 1
            entry["failures"].append(f"{case['id']}: client error {error!r}")
            continue
        diffs: list[str] = []
        tolerant_deep_diff(
            actual,
            golden["result"],
            case["id"],
            diffs,
            tolerance_keys=vc.TOLERANCE_KEYS,
            tolerance=vc.TOLERANCE,
        )
        reconcile_multisets(diffs, actual, golden["result"], case["id"])
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs[:6])
            print(f"[compare] {case['id']} FAIL ({len(diffs)} diffs)")
        else:
            entry["pass"] += 1
            print(f"[compare] {case['id']} OK")


def compare_rust(flavor: str, port: int, expected: dict) -> None:
    cases = [case for case in vc.all_cases() if case["flavor"] == flavor]
    process = launch_rust_server(flavor, port)
    try:
        if not wait_for_server(port, 60):
            entry = gate(f"rust {flavor}")
            entry["fail"] += 1
            entry["failures"].append(f"rust {flavor} server failed to start on :{port}")
            return
        replay_cases(port, cases, expected, f"rust {flavor}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def compare_replay(flavor: str, port: int, expected: dict, fresh: dict) -> None:
    """Determinism gate: golden fixtures vs a fresh python capture."""
    entry = gate(f"replay {flavor}")
    cases = [case for case in vc.all_cases() if case["flavor"] == flavor]
    for case in cases:
        golden = expected["cases"].get(case["id"])
        fresh_case = fresh.get("cases", {}).get(case["id"])
        if golden is None or fresh_case is None:
            entry["fail"] += 1
            entry["failures"].append(f"{case['id']}: missing side (golden/fresh)")
            continue
        diffs: list[str] = []
        tolerant_deep_diff(
            fresh_case["result"],
            golden["result"],
            case["id"],
            diffs,
            tolerance_keys=vc.TOLERANCE_KEYS,
            tolerance=vc.TOLERANCE,
        )
        reconcile_multisets(diffs, fresh_case["result"], golden["result"], case["id"])
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs[:6])
            print(f"[replay] {case['id']} FAIL ({len(diffs)} diffs)")
        else:
            entry["pass"] += 1
            print(f"[replay] {case['id']} OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", choices=("both", "unified", "mind"), default="both")
    parser.add_argument("--rust", action="store_true", help="compare the Rust server")
    parser.add_argument(
        "--replay",
        type=Path,
        help="determinism gate: diff golden vs a fresh capture JSON",
    )
    parser.add_argument("--port-unified", type=int, default=8802)
    parser.add_argument("--port-mind", type=int, default=8804)
    args = parser.parse_args()

    if not vc.FIXTURE_PATH.exists():
        print("golden fixtures missing — run capture_vector_golden.py first")
        return 2
    expected = json.loads(vc.FIXTURE_PATH.read_text(encoding="utf-8"))

    flavors = ["unified", "mind"] if args.flavor == "both" else [args.flavor]
    if args.replay is not None:
        fresh = json.loads(args.replay.read_text(encoding="utf-8"))
        for flavor in flavors:
            port = args.port_unified if flavor == "unified" else args.port_mind
            compare_replay(flavor, port, expected, fresh)
    elif args.rust:
        for flavor in flavors:
            compare_rust(flavor, args.port_unified if flavor == "unified" else args.port_mind, expected)
    else:
        parser.error("choose --rust or --replay <file>")

    total_pass = sum(entry["pass"] for entry in GATES.values())
    total_fail = sum(entry["fail"] for entry in GATES.values())
    print()
    for name, entry in GATES.items():
        status = "PASS" if entry["fail"] == 0 else "FAIL"
        print(f"GATE {name}: {status} — {entry['pass']} pass / {entry['fail']} fail")
        for failure in entry["failures"][:12]:
            print(f"  - {failure}")
        if len(entry["failures"]) > 12:
            print(f"  … and {len(entry['failures']) - 12} more")
    print(f"TOTAL: {total_pass} pass / {total_fail} fail")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
