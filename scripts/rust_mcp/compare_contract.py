#!/usr/bin/env python3
"""Replay the recorded contract fixtures against the Rust MCP server.

The comparator starts `rust/crates/cortex-mcp` (built via cargo), drives it
with the Python `mcp` client over streamable HTTP using the same fixed query
set, and compares per key:

* `initialize`   → server name / version / instructions (byte-level)
* `tools/list`   → tool name set + description byte-match (schemas are
  derived from Python signatures on the Python side and from the metadata
  catalog on the Rust side; names+descriptions are the phase-11 gate)
* `tools/call`   → structuredContent compared recursively per key with
  byte-level scalar equality, masking declared volatile fields
  (`updated_at`, `_graph_id`, `elapsed`, timestamps, `resultType`), plus
  content text, `isError` and `_meta`.

Usage:
    python scripts/rust_mcp/compare_contract.py [--port 8793] [--skip-build]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "contract_fixtures.json"
CARGO_ROOT = REPO_ROOT / "rust"
RUST_BIN = CARGO_ROOT / "target" / "debug" / "cortex-mcp"

# Declared volatile fields: masked before comparing scalar values.
MASKED_KEYS = {
    "updated_at",
    "_graph_id",
    "elapsed",
    "elapsed_ms",
    "execution_time_ms",
    "internal_execution_time_ms",
    "timestamp",
    "timestamps",
    "recorded_at_epoch",
    # Protocol-dialect field: rmcp emits `resultType: complete` for peers that
    # negotiate 2026-07-28; the Python stack never emits it. The Rust server
    # pins the negotiated version to 2025-06-18 so both omit it, but mask it
    # defensively.
    "resultType",
    "result_type",
}


def mask(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<masked>"
                if key in MASKED_KEYS
                else mask(item, key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask(item, key_hint) for item in value]
    if key_hint in MASKED_KEYS:
        return "<masked>"
    return value


def deep_diff(actual: Any, expected: Any, path: str, diffs: List[str]) -> None:
    actual = mask(actual, path.rsplit(".", 1)[-1] if "." in path else path)
    expected = mask(expected, path.rsplit(".", 1)[-1] if "." in path else path)
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, expected_value in expected.items():
            if key not in actual:
                diffs.append(f"{path}.{key}: missing (expected {expected_value!r})")
            else:
                deep_diff(actual[key], expected_value, f"{path}.{key}", diffs)
        for key, actual_value in actual.items():
            if key not in expected:
                diffs.append(f"{path}.{key}: unexpected (actual {actual_value!r})")
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append(
                f"{path}: length {len(actual)} != {len(expected)}"
            )
            return
        for index, (a, e) in enumerate(zip(actual, expected)):
            deep_diff(a, e, f"{path}[{index}]", diffs)
        return
    if actual != expected:
        diffs.append(f"{path}: {actual!r} != {expected!r}")


# ---------------------------------------------------------------------------
# Rust server lifecycle
# ---------------------------------------------------------------------------


def build_rust_server(attempts: int = 10, wait_seconds: float = 30.0) -> None:
    """Build with retries — sibling agents add crates to the shared workspace
    concurrently, and a half-written sibling manifest briefly breaks `cargo`."""
    for attempt in range(attempts):
        result = subprocess.run(
            ["cargo", "build", "-p", "cortex-mcp"],
            cwd=str(CARGO_ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        transient = "failed to load manifest" in result.stderr or (
            "no targets specified" in result.stderr
        )
        if transient and attempt + 1 < attempts:
            print(f"[compare] workspace busy (sibling crate mid-write); retrying…")
            time.sleep(wait_seconds)
            continue
        sys.stderr.write(result.stderr)
        result.check_returncode()


def launch_rust_server(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["FASTMCP_PORT"] = str(port)
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
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process


def wait_for_server(port: int, timeout: float) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.post(
                f"http://127.0.0.1:{port}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "contract-comparator", "version": "1.0"},
                    },
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                timeout=5.0,
            )
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def stop_rust_server(process: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


# ---------------------------------------------------------------------------
# MCP client replay
# ---------------------------------------------------------------------------


async def _call(
    port: int, tool: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (
        read_stream,
        write_stream,
        _session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments or None)
            content_texts: List[str] = []
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


def call_tool_rust(
    port: int, tool: str, arguments: Dict[str, Any], attempts: int = 3
) -> Dict[str, Any]:
    last_error: Optional[BaseException] = None
    for _ in range(attempts):
        try:
            return asyncio.run(_call(port, tool, arguments))
        except BaseException as error:  # noqa: BLE001
            last_error = error
            time.sleep(1.0)
    raise RuntimeError(f"rust call failed after {attempts} attempts: {last_error}")


async def _session_info(port: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

GATES: Dict[str, Dict[str, Any]] = {}


def gate(name: str) -> Dict[str, Any]:
    return GATES.setdefault(name, {"pass": 0, "fail": 0, "failures": []})


def compare_initialize(fixtures: Dict[str, Any], port: int) -> None:
    expected = fixtures["server_metadata"].get("initialize")
    if not expected:
        print("[compare] no live initialize fixture — skipping")
        return
    initialize, _ = asyncio.run(_session_info(port))
    diffs: List[str] = []
    deep_diff(initialize, expected, "initialize", diffs)
    entry = gate("initialize")
    if diffs:
        entry["fail"] += 1
        entry["failures"].extend(diffs)
    else:
        entry["pass"] += 1


def compare_tools_list(fixtures: Dict[str, Any], port: int) -> None:
    expected_tools = fixtures["server_metadata"].get("tools_list", {}).get("tools")
    if not expected_tools:
        print("[compare] no tools/list fixture — skipping")
        return
    _, actual = asyncio.run(_session_info(port))
    actual_by_name = {tool["name"]: tool for tool in actual["tools"]}
    entry = gate("tools/list")
    for expected_tool in expected_tools:
        name = expected_tool["name"]
        actual_tool = actual_by_name.get(name)
        if actual_tool is None:
            entry["fail"] += 1
            entry["failures"].append(f"tools/list: missing tool {name}")
            continue
        if actual_tool["description"] != expected_tool["description"]:
            entry["fail"] += 1
            entry["failures"].append(
                f"tools/list[{name}]: description mismatch\n"
                f"  rust   = {actual_tool['description']!r}\n"
                f"  python = {expected_tool['description']!r}"
            )
            continue
        entry["pass"] += 1
    extra = sorted(set(actual_by_name) - {tool["name"] for tool in expected_tools})
    if extra:
        entry["fail"] += 1
        entry["failures"].append(f"tools/list: unexpected tools {extra}")


def compare_calls(fixtures: Dict[str, Any], port: int) -> None:
    entry = gate("tools/call")
    for case in fixtures["cases"]:
        actual = call_tool_rust(port, case["tool"], case["arguments"])
        expected = case["expected"]
        diffs: List[str] = []
        deep_diff(actual, expected, case["id"], diffs)
        if diffs:
            entry["fail"] += 1
            entry["failures"].extend(diffs)
        else:
            entry["pass"] += 1
            print(f"[compare] {case['id']} OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    if not FIXTURE_PATH.exists():
        print("fixtures missing — run scripts/rust_mcp/record_contract.py first")
        return 2
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    print(
        f"[compare] fixtures: {len(fixtures['cases'])} cases "
        f"(mode: {fixtures['mode']})"
    )

    if not args.skip_build:
        build_rust_server()
    process = launch_rust_server(args.port)
    try:
        if not wait_for_server(args.port, 30):
            print("Rust server failed to start on :" f"{args.port}")
            return 2
        compare_initialize(fixtures, args.port)
        compare_tools_list(fixtures, args.port)
        compare_calls(fixtures, args.port)
    finally:
        stop_rust_server(process)

    total_pass = sum(entry["pass"] for entry in GATES.values())
    total_fail = sum(entry["fail"] for entry in GATES.values())
    print()
    for name, entry in GATES.items():
        status = "PASS" if entry["fail"] == 0 else "FAIL"
        print(
            f"GATE {name}: {status} — {entry['pass']} pass / {entry['fail']} fail"
        )
        for failure in entry["failures"][:20]:
            print(f"  - {failure}")
        if len(entry["failures"]) > 20:
            print(f"  … and {len(entry['failures']) - 20} more")
    print(f"TOTAL: {total_pass} pass / {total_fail} fail")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
