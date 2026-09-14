#!/usr/bin/env python3
"""Replay phase-13 mind fixtures against the Rust mind-flavor server.

Comparator kế thừa `compare_contract.py`/`compare_graph.py` với 2 mở rộng
của phase 13:

* float tolerance 1e-9 cho các score keys (`score`, `rerank_score`,
  `confidence`, `graph_proximity`) — declared volatile mask vẫn áp dụng;
* per-case `relations_multiset` — so khớp `relations` như multiset (hop ≥ 2
  của `fetch_relations_with_depth` duyệt frontier bằng Python set, thứ tự
  hàng là hash-iteration order — implementation-defined).

Gates: tools/call (per-case), tools/list metadata, initialize metadata,
GLiNER sidecar contract (verify once), latency P95 (semantic_search, Rust
vs Python). Optionally đo lại latency bằng --latency-only.

Usage:
    python scripts/rust_mcp/compare_mind.py [--port 8798] [--skip-build]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mind_contract  # noqa: E402
from compare_contract import (  # noqa: E402
    GATES,
    RUST_BIN,
    build_rust_server,
    deep_diff as base_deep_diff,
    stop_rust_server,
    wait_for_server,
)
from compare_graph import _call_with_timeout  # noqa: E402
from record_mind import (  # noqa: E402
    launch_python_mind_server,
    stop_python_mind_server,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "mind_fixtures.json"

GATE_CALLS = "mind tools/call"
GATE_TOOLS_LIST = "mind tools/list"
GATE_INITIALIZE = "mind initialize"
GATE_GLINER = "gliner sidecar contract"
GATE_LATENCY = "latency P95 semantic_search"


def launch_rust_mind_server(port: int) -> subprocess.Popen:
    """Start the Rust binary in mind flavor with the shared parity env."""
    env = dict(os.environ)
    env.update(mind_contract.server_env(port))
    process = subprocess.Popen(
        [
            str(RUST_BIN),
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
            "--server",
            "mind",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process


# ---------------------------------------------------------------------------
# Comparator: masked keys + float tolerance + multiset relations
# ---------------------------------------------------------------------------


def tolerant_deep_diff(
    actual, expected, path, diffs, tolerance_keys=None, tolerance=1e-9
):
    """compare_contract.deep_diff + float tolerance on score-ish keys."""
    tolerance_keys = tolerance_keys or set()
    key_hint = path.rsplit(".", 1)[-1] if "." in path else path
    if (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and key_hint in tolerance_keys
    ):
        if abs(float(actual) - float(expected)) > tolerance:
            diffs.append(
                f"{path}: {actual!r} != {expected!r} (beyond tolerance {tolerance})"
            )
        return
    before = len(diffs)
    base_deep_diff(actual, expected, path, diffs)
    if len(diffs) > before and key_hint in tolerance_keys:
        # Re-check the failing scalar pair under the tolerance rule.
        if (
            isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and abs(float(actual) - float(expected)) <= tolerance
        ):
            del diffs[before:]


def _multiset_key(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def compare_case(case: dict, actual: dict, diffs: list[str]) -> None:
    expected = case["expected"]
    tolerant_deep_diff(
        actual,
        expected,
        case["id"],
        diffs,
        tolerance_keys=mind_contract.TOLERANCE_KEYS,
        tolerance=mind_contract.TOLERANCE,
    )
    if case.get("relations_multiset"):
        # Reconcile relation ORDER differences: strip order-sensitive diffs
        # under `…relations[i]` paths and compare as multisets instead.
        prefix = f"{case['id']}.structured_content.data.relations"
        order_diffs = [diff for diff in diffs if diff.startswith(prefix + "[")]
        if order_diffs and len(order_diffs) == sum(
            1
            for diff in diffs
            if diff.startswith(prefix + "[")
        ):
            actual_relations = (
                actual.get("structured_content", {}) or {}
            ).get("data", {}) or {}
            actual_relations = (actual_relations or {}).get("relations") or []
            expected_relations = (
                (expected.get("structured_content", {}) or {})
                .get("data", {})
                .get("relations")
            ) or []
            if sorted(map(_multiset_key, actual_relations)) == sorted(
                map(_multiset_key, expected_relations)
            ):
                for diff in order_diffs:
                    diffs.remove(diff)
            else:
                for diff in order_diffs:
                    diffs.remove(diff)
                diffs.append(
                    f"{prefix}: multiset mismatch "
                    f"(actual {len(actual_relations)} vs expected "
                    f"{len(expected_relations)})"
                )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def compare_calls(fixtures: dict, port: int) -> None:
    entry = GATES.setdefault(GATE_CALLS, {"pass": 0, "fail": 0, "failures": []})
    for case in fixtures["cases"]:
        actual = _call_with_timeout(port, case["tool"], case["arguments"])
        diffs: list[str] = []
        compare_case(case, actual, diffs)
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs)
        else:
            entry["pass"] += 1
            print(f"[compare-mind] {case['id']} OK")


async def _session_info_mind(port: int):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (
        read_stream,
        write_stream,
        _session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            initialize = await session.initialize()
            tools = await session.list_tools()
            initialize_payload = {
                "server_name": initialize.serverInfo.name,
                "server_version": initialize.serverInfo.version,
                "instructions": initialize.instructions,
            }
            tools_payload = {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "properties": sorted(
                            (tool.inputSchema or {}).get("properties", {}).keys()
                        ),
                    }
                    for tool in tools.tools
                ]
            }
            return initialize_payload, tools_payload


def compare_metadata(fixtures: dict, port: int) -> None:
    import asyncio

    initialize_payload, tools_payload = asyncio.run(_session_info_mind(port))
    metadata = fixtures.get("server_metadata", {})

    entry = GATES.setdefault(GATE_INITIALIZE, {"pass": 0, "fail": 0, "failures": []})
    expected_initialize = metadata.get("initialize")
    if expected_initialize:
        diffs: list[str] = []
        tolerant_deep_diff(initialize_payload, expected_initialize, "initialize", diffs)
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs)
        else:
            entry["pass"] += 1

    entry = GATES.setdefault(GATE_TOOLS_LIST, {"pass": 0, "fail": 0, "failures": []})
    expected_tools = (metadata.get("tools_list") or {}).get("tools")
    if not expected_tools:
        expected_tools = [{"name": name, "description": None, "properties": None} for name in mind_contract.MIND_TOOLS]
    actual_by_name = {tool["name"]: tool for tool in tools_payload["tools"]}
    for expected_tool in expected_tools:
        name = expected_tool["name"]
        actual_tool = actual_by_name.get(name)
        if actual_tool is None:
            entry["fail"] += 1
            entry["failures"].append(f"tools/list: missing tool {name}")
            continue
        if (
            expected_tool.get("description")
            and actual_tool["description"] != expected_tool["description"]
        ):
            entry["fail"] += 1
            entry["failures"].append(
                f"tools/list[{name}]: description mismatch\n"
                f"  rust   = {actual_tool['description']!r}\n"
                f"  python = {expected_tool['description']!r}"
            )
            continue
        if expected_tool.get("properties") and sorted(
            actual_tool["properties"]
        ) != sorted(expected_tool["properties"]):
            entry["fail"] += 1
            entry["failures"].append(
                f"tools/list[{name}]: properties mismatch\n"
                f"  rust   = {sorted(actual_tool['properties'])}\n"
                f"  python = {sorted(expected_tool['properties'])}"
            )
            continue
        entry["pass"] += 1
    extra = sorted(set(actual_by_name) - {tool["name"] for tool in expected_tools})
    if extra:
        entry["fail"] += 1
        entry["failures"].append(f"tools/list: unexpected tools {extra}")


def gate_gliner() -> None:
    entry = GATES.setdefault(GATE_GLINER, {"pass": 0, "fail": 0, "failures": []})
    # The gliner/torch teardown can abort AFTER the verdict is printed
    # (libc++ `recursive_mutex lock failed` at interpreter shutdown —
    # environmental, not a contract mismatch), so the verdict line in stdout
    # is authoritative; retry while it is missing.
    verdict = "GLINER SIDECAR VERIFY: PASS"
    detail = ""
    for _ in range(3):
        result = subprocess.run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "python"),
                str(Path(__file__).resolve().parent / "gliner_sidecar.py"),
                "--verify",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        detail = f"exit={result.returncode}: {(result.stdout + result.stderr)[-300:]}"
        if verdict in result.stdout:
            entry["pass"] += 1
            print("[compare-mind] gliner sidecar contract OK")
            return
    entry["fail"] += 1
    entry["failures"].append(f"gliner verify failed — {detail}")


def measure_latencies(port: int, rounds: int, warmup: int = 2) -> list[float]:
    """semantic_search latency inside ONE persistent client session (ms).

    The client session is opened once and the first `warmup` calls are
    discarded (model-load / connection warmup), so the sample measures pure
    server-side tool latency — apples-to-apples between both servers.
    """
    import asyncio
    from datetime import timedelta

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run() -> list[float]:
        samples: list[float] = []
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (
            read_stream,
            write_stream,
            _session_id,
        ):
            async with ClientSession(
                read_stream, write_stream, read_timeout_seconds=timedelta(seconds=120)
            ) as session:
                await session.initialize()
                for index in range(rounds + warmup):
                    query = (
                        f"Digital Key 3.0 và Plug&Charge trên BMW và Audi — lần {index}"
                    )
                    start = time.perf_counter()
                    await session.call_tool(
                        "semantic_search",
                        {"query": query, "project_id": "mindfix"},
                    )
                    elapsed = (time.perf_counter() - start) * 1000.0
                    if index >= warmup:
                        samples.append(elapsed)
        return samples

    return asyncio.run(run())


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def compare_latency(rust_port: int, python_port: int, rounds: int) -> dict:
    entry = GATES.setdefault(
        GATE_LATENCY, {"pass": 0, "fail": 0, "failures": []}
    )
    print(f"[compare-mind] latency: {rounds} semantic_search rounds per server")
    python_samples = measure_latencies(python_port, rounds)
    rust_samples = measure_latencies(rust_port, rounds)
    stats = {
        "python_p50_ms": round(statistics.median(python_samples), 1),
        "python_p95_ms": round(percentile(python_samples, 0.95), 1),
        "rust_p50_ms": round(statistics.median(rust_samples), 1),
        "rust_p95_ms": round(percentile(rust_samples, 0.95), 1),
        "rounds": rounds,
    }
    ok = stats["rust_p95_ms"] <= stats["python_p95_ms"]
    if ok:
        entry["pass"] += 1
    else:
        entry["fail"] += 1
        entry["failures"].append(
            f"rust P95 {stats['rust_p95_ms']}ms > python P95 {stats['python_p95_ms']}ms"
        )
    print(f"[compare-mind] latency stats: {json.dumps(stats)}")
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("--python-port", type=int, default=8797)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-gliner", action="store_true")
    parser.add_argument("--skip-latency", action="store_true")
    parser.add_argument("--latency-rounds", type=int, default=12)
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()

    if not FIXTURE_PATH.exists():
        print("fixtures missing — run scripts/rust_mcp/record_mind.py first")
        return 2
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    print(
        f"[compare-mind] fixtures: {len(fixtures['cases'])} cases "
        f"(mode: {fixtures['mode']})"
    )

    if not args.skip_build:
        build_rust_server()

    # Python reference server (latency gate) + Rust mind server.
    python_process = None
    if not args.skip_latency:
        python_process = launch_python_mind_server(args.python_port)
        if not wait_for_server(args.python_port, 300.0):
            print(f"python mind server failed to start on :{args.python_port}")
            stop_python_mind_server(python_process)
            return 2

    process = launch_rust_mind_server(args.port)
    latency_stats = {}
    try:
        if not wait_for_server(args.port, 60):
            print(f"Rust mind server failed to start on :{args.port}")
            return 2
        compare_metadata(fixtures, args.port)
        compare_calls(fixtures, args.port)
        if not args.skip_gliner:
            gate_gliner()
        if python_process is not None:
            latency_stats = compare_latency(
                args.port, args.python_port, args.latency_rounds
            )
    finally:
        stop_rust_server(process)
        if python_process is not None:
            stop_python_mind_server(python_process)

    total_pass = sum(entry["pass"] for entry in GATES.values())
    total_fail = sum(entry["fail"] for entry in GATES.values())
    print()
    for name, entry in GATES.items():
        status = "PASS" if entry["fail"] == 0 else "FAIL"
        print(f"GATE {name}: {status} — {entry['pass']} pass / {entry['fail']} fail")
        for failure in entry["failures"][: args.show]:
            print(f"  - {failure}")
        if len(entry["failures"]) > args.show:
            print(f"  … and {len(entry['failures']) - args.show} more")
    print(f"TOTAL: {total_pass} pass / {total_fail} fail")
    if latency_stats:
        Path(__file__).resolve().parent.joinpath("fixtures", "mind_latency.json").write_text(
            json.dumps(latency_stats, indent=1) + "\n", encoding="utf-8"
        )
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
