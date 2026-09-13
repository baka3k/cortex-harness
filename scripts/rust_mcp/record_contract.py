#!/usr/bin/env python3
"""Record MCP wire-contract fixtures for the phase-11 Rust port.

Two recording modes (mirrors the phase plan):

1. **live mode** — start the production Python MCP server exactly like
   `code-tiny/mcp.sh` / `scripts/mcp-lifecycle.py` do (repo venv python,
   `unified_mcp.py`, streamable HTTP, `stateless_http=True`,
   `json_response=True`) on a harness test port, drive it with the Python
   `mcp` client (streamable HTTP), and capture `tools/call` results for the
   fixed query set (`contract_query_set.py`), plus `initialize` and
   `tools/list`.

2. **contract-layer mode** — call the pure Python contract modules
   in-process (no torch / no graph): `cortex_harness.mcp_contract`,
   `code-tiny/mcp/tool_metadata.py`, `framework_registry.py`,
   `tools.common.project_registry.py`. Used for error paths that only exist
   at the contract layer (`project_not_registered`) and for the phase-11
   graph-stub envelope (`capability_unavailable`).

Fixtures land in `scripts/rust_mcp/fixtures/`. The live attempt happens
first; if the server cannot boot or a live call errors out at the transport
level, that case is retried via the contract layer (the fallback is recorded
per case in the fixture metadata).

Usage:
    python scripts/rust_mcp/record_contract.py [--port 8791] [--skip-live]
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
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_TINY = REPO_ROOT / "code-tiny"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contract_query_set as query_set  # noqa: E402

VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
HEALTH_TIMEOUT_SECONDS = 180.0


# ---------------------------------------------------------------------------
# Live server lifecycle (production-style launch)
# ---------------------------------------------------------------------------


def launch_live_server(port: int) -> Optional[subprocess.Popen]:
    """Start `code-tiny/mcp/unified_mcp.py` like `code-tiny/mcp.sh` does."""
    env = dict(os.environ)
    env.update(
        {
            "FASTMCP_HOST": "127.0.0.1",
            "FASTMCP_PORT": str(port),
            "MCP_PRELOAD_EMBEDDER": "0",
            # Belt and braces: planner/graph fixture inputs are chosen to be
            # hash-order independent, but pin the seed anyway.
            "PYTHONHASHSEED": "0",
            "MCP_SEARCH_TIMING": "0",
        }
    )
    process = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "mcp/unified_mcp.py",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
        ],
        cwd=str(CODE_TINY),
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
                        "clientInfo": {"name": "contract-recorder", "version": "1.0"},
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
        time.sleep(1.0)
    return False


def stop_live_server(process: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


# ---------------------------------------------------------------------------
# MCP client (mcp package, streamable HTTP)
# ---------------------------------------------------------------------------


async def call_tool_live(
    port: int, tool: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """Call one tool on the live server; return the wire-level result."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    base_url = f"http://127.0.0.1:{port}"
    async with streamablehttp_client(f"{base_url}/mcp") as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments or None)
            structured = getattr(result, "structuredContent", None)
            if structured is None:
                raw_structured = getattr(result, "structured_content", None)
                structured = raw_structured
            content_texts: List[str] = []
            for block in getattr(result, "content", []) or []:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    content_texts.append(text)
            meta = getattr(result, "meta", None)
            return {
                "content_text": "\n".join(content_texts),
                "structured_content": structured,
                "is_error": bool(getattr(result, "isError", False)),
                "meta": meta,
            }


def call_tool_live_with_retry(
    port: int, tool: str, arguments: Dict[str, Any], attempts: int = 3
) -> Dict[str, Any]:
    last_error: Optional[BaseException] = None
    for _ in range(attempts):
        try:
            return asyncio.run(call_tool_live(port, tool, arguments))
        except BaseException as error:  # noqa: BLE001 - transport level retry
            last_error = error
            time.sleep(1.0)
    raise RuntimeError(f"live call failed after {attempts} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Contract-layer fixtures (pure Python, no graph)
# ---------------------------------------------------------------------------


def contract_layer_envelope(case: Dict[str, Any]) -> Dict[str, Any]:
    sys.path.insert(0, str(CODE_TINY / "mcp"))
    sys.path.insert(0, str(CODE_TINY))
    sys.path.insert(0, str(REPO_ROOT))
    from cortex_harness.mcp_contract import (
        normalize_error,
        result_meta,
        result_summary,
    )

    tool = case["tool"]
    meta = result_meta(tool)
    if case["id"].endswith(".project_not_registered"):
        from tools.common.project_registry import (
            ProjectNotRegisteredError,
            resolve_project_targets,
        )

        try:
            resolve_project_targets(query_set.UNREGISTERED_PROJECT)
            raise AssertionError("expected ProjectNotRegisteredError")
        except ProjectNotRegisteredError as error:
            envelope = normalize_error(error)
    elif case["id"].endswith(".capability_unavailable_stub"):
        envelope = normalize_error(dict(query_set.STUB_LEGACY_PAYLOAD))
    elif case["id"] == "list_mcp_functions.catalog":
        from tool_metadata import build_catalog

        functions = build_catalog(set(query_set.UNIFIED_TOOL_NAMES))
        data = {
            "total_count": len(functions),
            "parameter_guidelines": {
                "always_call_first": "list_mcp_functions",
                "rules": [
                    "Use exact parameter names from tool metadata; avoid inventing aliases.",
                    "Send required fields explicitly on every call.",
                    "Pass parser_type on every call to select a query profile (see list_parsers for aliases).",
                    "When list-like params are accepted, prefer arrays over comma-separated strings.",
                    "On error.invalid_parameters, follow required_params + example and retry once.",
                ],
            },
            "functions": functions,
        }
        envelope = {"ok": True, "data": data, "error": None}
        return {
            "content_text": result_summary(data, ok=True),
            "structured_content": envelope,
            "is_error": False,
            "meta": meta,
        }
    else:
        raise ValueError(f"unsupported contract-layer case {case['id']}")
    message = envelope["error"]["message"]
    return {
        "content_text": result_summary(None, ok=False, message=message),
        "structured_content": envelope,
        "is_error": True,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record(
    port: int, skip_live: bool, keep_going: bool
) -> Dict[str, Any]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    process = None
    live_available = False
    if not skip_live:
        print(f"[record] launching live Python server on :{port} …")
        process = launch_live_server(port)
        live_available = wait_for_server(port, HEALTH_TIMEOUT_SECONDS)
        if live_available:
            print("[record] live Python server is up.")
        else:
            print(
                "[record] live Python server failed to boot — falling back to "
                "contract-layer mode for all cases."
            )

    fixtures: List[Dict[str, Any]] = []
    live_failures: List[str] = []
    metadata = query_set.server_metadata()
    try:
        if live_available:
            metadata["initialize"] = live_initialize(port)
            metadata["tools_list"] = live_tools_list(port)
        for case in query_set.all_cases():
            fixture: Dict[str, Any] = {
                "id": case["id"],
                "tool": case["tool"],
                "arguments": case["arguments"],
                "recorded_via": case["recorded_via"],
            }
            if case.get("notes"):
                fixture["notes"] = case["notes"]
            expected: Optional[Dict[str, Any]] = None
            if live_available and case["recorded_via"] == "live-python-server":
                try:
                    expected = call_tool_live_with_retry(
                        port, case["tool"], case["arguments"]
                    )
                    fixture["recorded_via"] = "live-python-server"
                except Exception as error:  # noqa: BLE001
                    live_failures.append(f"{case['id']}: {error}")
                    if not keep_going:
                        raise
            if expected is None:
                expected = contract_layer_envelope(case)
            fixture["expected"] = expected
            fixtures.append(fixture)
            print(f"[record] {fixture['id']} ← {fixture['recorded_via']}")
    finally:
        if process is not None:
            stop_live_server(process)

    payload = {
        "recorded_at_epoch": time.time(),
        "mode": "live-python-server" if live_available else "contract-layer",
        "server_metadata": metadata,
        "cases": fixtures,
    }
    path = FIXTURE_DIR / "contract_fixtures.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"[record] wrote {path} ({len(fixtures)} cases)")
    if live_failures:
        print(f"[record] {len(live_failures)} live calls fell back:")
        for failure in live_failures:
            print(f"  - {failure}")
    return payload


def live_initialize(port: int) -> Dict[str, Any]:
    async def _run() -> Dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            f"http://127.0.0.1:{port}/mcp"
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                result = await session.initialize()
                return {
                    "server_name": result.serverInfo.name,
                    "server_version": result.serverInfo.version,
                    "instructions": result.instructions,
                }

    return asyncio.run(_run())


def live_tools_list(port: int) -> Dict[str, Any]:
    async def _run() -> Dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            f"http://127.0.0.1:{port}/mcp"
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "properties": sorted(
                                (tool.inputSchema or {}).get("properties", {}).keys()
                            ),
                        }
                        for tool in result.tools
                    ]
                }

    return asyncio.run(_run())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument(
        "--skip-live", action="store_true", help="record contract-layer only"
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="fall back to contract-layer when a live call fails",
    )
    args = parser.parse_args()
    record(args.port, args.skip_live, args.keep_going)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
