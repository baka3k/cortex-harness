#!/usr/bin/env python3
"""Record phase-13 mind-tool fixtures from the LIVE Python mind server.

Production-style launch mirrors `doc-tiny/mcp.sh` (venv python,
`doc-tiny/mcp_graph_rag.py`, streamable HTTP) on a harness test port, with
the shared parity environment from `mind_contract.server_env` (remote
qdrant/FalkorDB for project `mindfix`, isolated empty local store). Each
case is recorded through a per-call subprocess client (compare_graph.py
pattern: 1 call = 1 fresh client process → absolute isolation).

Usage:
    python scripts/rust_mcp/record_mind.py [--port 8797]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_TINY = REPO_ROOT / "doc-tiny"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mind_contract  # noqa: E402
from compare_graph import _call_with_timeout  # noqa: E402
from compare_contract import wait_for_server  # noqa: E402
from record_contract import live_initialize, live_tools_list  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "mind_fixtures.json"


def launch_python_mind_server(port: int) -> subprocess.Popen:
    """Start `doc-tiny/mcp_graph_rag.py` exactly like `doc-tiny/mcp.sh`."""
    import os

    env = dict(os.environ)
    env.update(mind_contract.server_env(port))
    process = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "mcp_graph_rag.py",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
        ],
        cwd=str(DOC_TINY),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process


def stop_python_mind_server(process: subprocess.Popen) -> None:
    import os
    import signal

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def record_case(port: int, case: dict, index: int, attempts: int = 2) -> dict:
    """One case through a per-call subprocess client (isolation)."""
    last_error = None
    for _ in range(attempts):
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--call-one",
                str(index),
                "--port",
                str(port),
            ],
            capture_output=True,
            text=True,
            timeout=1900,
        )
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@") :])
        last_error = RuntimeError(
            f"{case['id']}: exit={proc.returncode} stderr={proc.stderr[-300:]}"
        )
        time.sleep(2.0)
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument("--call-one", type=int, default=None)
    args = parser.parse_args()

    if args.call_one is not None:
        # Subprocess entry: fresh client → 1 case → print JSON marker.
        return _record_call_one(args.port, args.call_one)

    process = launch_python_mind_server(args.port)
    if not wait_for_server(args.port, 300.0):
        print("live Python mind server failed to boot")
        stop_python_mind_server(process)
        return 2
    print(f"[record-mind] live Python mind server up on :{args.port}")

    fixtures = []
    failures = []
    server_metadata = {}
    try:
        server_metadata["initialize"] = live_initialize(args.port)
        server_metadata["tools_list"] = live_tools_list(args.port)
        for index, case in enumerate(mind_contract.mind_cases()):
            fixture = dict(case)
            try:
                fixture["expected"] = record_case(args.port, case, index)
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
            print(f"[record-mind] {case['id']} done")
    finally:
        stop_python_mind_server(process)

    payload = {
        "recorded_at_epoch": time.time(),
        "mode": "live-python-mind-server",
        "server_metadata": server_metadata,
        "environment": {
            key: value
            for key, value in mind_contract.server_env(args.port).items()
        },
        "cases": fixtures,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"[record-mind] wrote {FIXTURE_PATH} ({len(fixtures)} cases)")
    if failures:
        print(f"[record-mind] {len(failures)} live failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


def _record_call_one(port: int, index: int) -> int:
    """Subprocess entry: fresh client → 1 case → print JSON marker."""
    case = mind_contract.mind_cases()[index]
    actual = _call_with_timeout(port, case["tool"], case["arguments"])
    print("@@RESULT@@" + json.dumps(actual, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
