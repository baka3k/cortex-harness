#!/usr/bin/env python3
"""Record phase-12 graph-tool fixtures from the LIVE Python unified server.

Production-style launch giống `record_contract.py` (venv python,
`unified_mcp.py`, streamable HTTP) trên port test; gọi từng case trong
`graph_contract.graph_cases()` và ghi wire-level result.

Usage:
    python scripts/rust_mcp/record_graph.py [--port 8796]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import graph_contract  # noqa: E402
from record_contract import (  # noqa: E402
    call_tool_live_with_retry,
    launch_live_server,
    stop_live_server,
    wait_for_server,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "graph_fixtures.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8796)
    args = parser.parse_args()

    process = launch_live_server(args.port)
    if not wait_for_server(args.port, 180.0):
        print("live Python server failed to boot")
        stop_live_server(process)
        return 2
    print(f"[record-graph] live Python server up on :{args.port}")

    fixtures = []
    failures = []
    try:
        for case in graph_contract.graph_cases():
            fixture = dict(case)
            try:
                fixture["expected"] = call_tool_live_with_retry(
                    args.port, case["tool"], case["arguments"]
                )
            except Exception as error:  # noqa: BLE001
                failures.append(f"{case['id']}: {error}")
                fixture["expected"] = {
                    "content_text": "",
                    "structured_content": None,
                    "is_error": True,
                    "meta": None,
                    "record_error": str(error),
                }
            fixtures.append(fixture)
            print(f"[record-graph] {case['id']} done")
    finally:
        stop_live_server(process)

    payload = {
        "recorded_at_epoch": time.time(),
        "mode": "live-python-server",
        "cases": fixtures,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"[record-graph] wrote {FIXTURE_PATH} ({len(fixtures)} cases)")
    if failures:
        print(f"[record-graph] {len(failures)} live failures:")
        for failure in failures:
            print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
