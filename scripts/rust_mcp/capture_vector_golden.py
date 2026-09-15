#!/usr/bin/env python3
"""Record phase-01 vector-lane golden fixtures from the LIVE Python servers.

Boots the production-shaped Python servers with the embedder ON:

* unified — `code-tiny/mcp/unified_mcp.py` on a snapshot of instance `cortex`
  (see `vector_contract.ensure_snapshot`);
* mind — `doc-tiny/mcp_graph_rag.py` on the phase-13 fixture store.

Each case runs through a per-call subprocess MCP client (compare_graph.py
pattern). Output: `fixtures/vector_fixtures.json` with mode
`live-python-vector-server` — the behavioral ground truth that
`compare_vector.py` replays against (Python replay for the determinism gate,
Rust server for the phase-03/04 acceptance gates).

Usage:
    python scripts/rust_mcp/capture_vector_golden.py \
        [--flavor both|unified|mind] [--refresh-snapshot] \
        [--port-unified 8801] [--port-mind 8803]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vector_contract as vc  # noqa: E402
from compare_contract import wait_for_server  # noqa: E402
from compare_graph import _call_with_timeout  # noqa: E402
from record_contract import live_initialize, live_tools_list  # noqa: E402

SERVER_SPECS = {
    "unified": {
        "cwd": vc.CODE_TINY,
        "script": "mcp/unified_mcp.py",
        "env_builder": vc.unified_server_env,
        "expected_server": "graph_mcp",
    },
    "mind": {
        "cwd": vc.DOC_TINY,
        "script": "mcp_graph_rag.py",
        "env_builder": vc.mind_server_env,
        "expected_server": "mind_mcp",
    },
}


def launch_server(flavor: str, port: int) -> subprocess.Popen:
    spec = SERVER_SPECS[flavor]
    env = dict(os.environ)
    env.update(spec["env_builder"](port))
    process = subprocess.Popen(
        [
            str(vc.VENV_PYTHON),
            spec["script"],
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
        ],
        cwd=str(spec["cwd"]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def capture_flavor(flavor: str, port: int) -> dict:
    """Boot the python server and record every case of this flavor."""
    spec = SERVER_SPECS[flavor]
    cases = [case for case in vc.all_cases() if case["flavor"] == flavor]
    process = launch_server(flavor, port)
    record: dict = {}
    try:
        if not wait_for_server(port, 300 if flavor == "unified" else 120):
            raise RuntimeError(f"python {flavor} server failed to start on :{port}")
        init = live_initialize(port)
        record["initialize"] = init
        record["tools_list"] = live_tools_list(port)
        name = init.get("server_name")
        if name != spec["expected_server"]:
            raise RuntimeError(f"unexpected serverInfo.name {name!r}")
        failures: list[str] = []
        for index, case in enumerate(cases, 1):
            result = _call_with_timeout(port, case["tool"], case["arguments"])
            record[case["id"]] = {
                "tool": case["tool"],
                "arguments": case["arguments"],
                "result": result,
            }
            status = "ERR" if result.get("is_error") else "ok"
            print(f"[capture] ({index}/{len(cases)}) {case['id']} {status}")
            if result.get("is_error"):
                failures.append(case["id"])
        record["capture_failures"] = failures
    finally:
        stop_process(process)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", choices=("both", "unified", "mind"), default="both")
    parser.add_argument("--port-unified", type=int, default=8801)
    parser.add_argument("--port-mind", type=int, default=8803)
    parser.add_argument("--refresh-snapshot", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        help="override output path (determinism replay writes to a scratch file)",
    )
    args = parser.parse_args()

    flavors = ["unified", "mind"] if args.flavor == "both" else [args.flavor]
    if "unified" in flavors:
        snapshot = vc.ensure_snapshot(force=args.refresh_snapshot)
        print(f"[capture] snapshot: {snapshot}")

    fixtures_path = args.out if args.out else vc.FIXTURE_PATH
    fixtures: dict = {"mode": "live-python-vector-server", "cases": {}}
    if fixtures_path.exists() and len(flavors) == 1:
        existing = json.loads(fixtures_path.read_text(encoding="utf-8"))
        fixtures["cases"].update(existing.get("cases", {}))
        fixtures["initialize_per_flavor"] = existing.get("initialize_per_flavor", {})

    per_flavor_meta = fixtures.setdefault("initialize_per_flavor", {})
    for flavor in flavors:
        started = time.time()
        record = capture_flavor(flavor, args.port_unified if flavor == "unified" else args.port_mind)
        failures = record.pop("capture_failures", [])
        init = record.pop("initialize", {})
        tools = record.pop("tools_list", {})
        per_flavor_meta[flavor] = {
            "initialize": init,
            "tool_count": len(tools.get("tools", [])),
        }
        fixtures["cases"].update(record)
        print(
            f"[capture] {flavor}: {len(record)} cases "
            f"({len(failures)} is_error) in {time.time() - started:.1f}s"
        )

    fixtures["recorded_at"] = datetime.now(timezone.utc).isoformat()
    fixtures["embedder"] = {
        "code_model": os.environ.get("CODE_EMBEDDING_MODEL", "jinaai/jina-embeddings-v3"),
        "doc_model": "BAAI/bge-m3",
        "note": "python embedder ON (production shape); scores carry torch fingerprint",
    }
    fixtures_path.parent.mkdir(parents=True, exist_ok=True)
    fixtures_path.write_text(
        json.dumps(fixtures, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[capture] wrote {fixtures_path} ({len(fixtures['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
