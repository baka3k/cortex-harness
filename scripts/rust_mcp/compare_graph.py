#!/usr/bin/env python3
"""Replay phase-12 graph fixtures against the Rust MCP server.

So sánh per-key với scalar byte-level, mask declared volatile fields; kế thừa
comparator machinery của phase-11 (`compare_contract.py`).

Usage:
    python scripts/rust_mcp/compare_graph.py [--port 8798] [--skip-build]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_contract import (  # noqa: E402
    GATES,
    build_rust_server,
    deep_diff,
    launch_rust_server,
    stop_rust_server,
    wait_for_server,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "graph_fixtures.json"


def _call_with_timeout(port, tool, arguments, timeout_s=1800.0):
    """1 call = 1 process/event-loop mới — isolation tuyệt đối giữa các case
    (session churn của streamable-http client gây đứt transport khi chạy
    dồn trong 1 process; gọi lẻ từng case luôn OK)."""
    from datetime import timedelta

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (
            read_stream, write_stream, _session_id,
        ):
            async with ClientSession(
                read_stream, write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_s),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or None)
                content_texts = []
                for block in getattr(result, "content", []) or []:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        content_texts.append(text)
                structured = getattr(result, "structuredContent", None)
                if structured is None:
                    structured = getattr(result, "structured_content", None)
                return {
                    "content_text": "\n".join(content_texts),
                    "structured_content": structured,
                    "is_error": bool(getattr(result, "isError", False)),
                    "meta": getattr(result, "meta", None),
                }

    import asyncio

    return asyncio.run(run())


def _call_one_main(port: int, index: int) -> int:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    case = fixtures["cases"][index]
    actual = _call_with_timeout(port, case["tool"], case["arguments"])
    print("@@RESULT@@" + json.dumps(actual, ensure_ascii=True, default=str))
    return 0


def call_tool_rust(port, tool, arguments, attempts=3, restart=None, case_index=None):
    import subprocess
    import sys
    import time as _time

    if case_index is None:
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        case_index = next(
            i for i, case in enumerate(fixtures["cases"]) if case["tool"] == tool
        )
    last_error = None
    for attempt in range(attempts):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--call-one", str(case_index),
             "--port", str(port)],
            capture_output=True, text=True, timeout=1900,
        )
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@"):])
        last_error = RuntimeError(
            f"case {tool}: exit={proc.returncode} stderr={proc.stderr[-300:]}"
        )
        _time.sleep(2.0)
        if restart is not None and attempt >= 1:
            # Server state kẹt bền sau case nặng — restart sạch rồi thử lại.
            print(f"[compare-graph] restarting rust server before retry {attempt + 1}")
            restart()
    raise last_error


def compare_calls(fixtures: dict, port: int, restart=None) -> None:
    entry = GATES.setdefault("graph tools/call", {"pass": 0, "fail": 0, "failures": []})
    for index, case in enumerate(fixtures["cases"]):
        actual = call_tool_rust(
            port,
            case["tool"],
            case["arguments"],
            restart=restart,
            case_index=index,
        )
        expected = case["expected"]
        diffs: list[str] = []
        deep_diff(actual, expected, case["id"], diffs)
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs)
        else:
            entry["pass"] += 1
            print(f"[compare-graph] {case['id']} OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--attach",
        action="store_true",
        help="reuse a running server on --port thay vì launch process mới",
    )
    parser.add_argument("--show", type=int, default=12, help="failures shown per gate")
    parser.add_argument("--call-one", type=int, default=None,
                        help="internal: chạy 1 case theo index rồi in JSON")
    args = parser.parse_args()
    if args.call_one is not None:
        return _call_one_main(args.port, args.call_one)

    if not FIXTURE_PATH.exists():
        print("fixtures missing — run scripts/rust_mcp/record_graph.py first")
        return 2
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    print(
        f"[compare-graph] fixtures: {len(fixtures['cases'])} cases "
        f"(mode: {fixtures['mode']})"
    )

    if not args.skip_build:
        build_rust_server()
    process = None if args.attach else launch_rust_server(args.port)

    def restart_server():
        nonlocal process
        if process is not None:
            stop_rust_server(process)
        process = launch_rust_server(args.port)
        wait_for_server(args.port, 60)

    try:
        if not wait_for_server(args.port, 60):
            print(f"Rust server failed to start on :{args.port}")
            return 2
        compare_calls(fixtures, args.port, restart=restart_server)
    finally:
        if process is not None:
            stop_rust_server(process)

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
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
