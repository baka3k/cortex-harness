#!/usr/bin/env python3
"""Rust mind-MCP server tool-call latency under `CORTEX_EMBED_BACKEND` / ORT knobs.

Phase-02 mục 3b: đo cô lập thì ort NHANH hơn worker (25.1 vs 41.2ms) nhưng trong
MCP server thật lại CHẬM hơn 25-31ms/tool-call. Script này bọc đúng đường mà
`compare_mind.measure_latencies` đo (persistent HTTP client session, sequential
`semantic_search`), chạy ma trận env và tách thời gian embed khỏi phần còn lại
của tool call nhờ stderr trace (`CORTEX_EMBED_TRACE=1` -> dòng
`[cortex-embed.trace]`/`[mind.embed.trace]`).

Mỗi config: boot server (env = `mind_contract.server_env` + config), đợi ready,
đo `--rounds` lượt (bỏ `--warmup` lượt đầu), kill, parse trace từ stderr log.
Python reference server được boot MỘT lần và dùng chung cho mọi config.

Usage:
    .venv/bin/python scripts/rust_parity/bench_mind_server.py
    .venv/bin/python scripts/rust_parity/bench_mind_server.py \
        --configs onnx-default,onnx-nospin --rounds 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "rust_mcp"))

import mind_contract  # noqa: E402
from compare_contract import RUST_BIN, stop_rust_server, wait_for_server  # noqa: E402
from record_mind import (  # noqa: E402
    launch_python_mind_server,
    stop_python_mind_server,
)

RUST_PORT = 8798
LOG_DIR = REPO / ".cache" / "embed" / "bench_server"


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0

CONFIGS: dict[str, dict[str, str]] = {
    "rust-python": {"CORTEX_EMBED_BACKEND": "python"},
    "onnx-default": {"CORTEX_EMBED_BACKEND": "onnx"},
    "onnx-nospin": {"CORTEX_EMBED_BACKEND": "onnx", "CORTEX_EMBED_ORT_SPIN": "0"},
    "onnx-t4": {"CORTEX_EMBED_BACKEND": "onnx", "CORTEX_EMBED_ORT_THREADS": "4"},
    "onnx-t4-nospin": {
        "CORTEX_EMBED_BACKEND": "onnx",
        "CORTEX_EMBED_ORT_THREADS": "4",
        "CORTEX_EMBED_ORT_SPIN": "0",
    },
    "onnx-t2-nospin": {
        "CORTEX_EMBED_BACKEND": "onnx",
        "CORTEX_EMBED_ORT_THREADS": "2",
        "CORTEX_EMBED_ORT_SPIN": "0",
    },
    "onnx-nospin-det0": {
        "CORTEX_EMBED_BACKEND": "onnx",
        "CORTEX_EMBED_ORT_SPIN": "0",
        "CORTEX_EMBED_ORT_DETERMINISTIC": "0",
    },
}

TRACE_EMBED_TOTAL = re.compile(r"\[mind\.embed\.trace\] lock_wait=([\d.]+)ms embed=([\d.]+)ms")
TRACE_RUN = re.compile(r"\[cortex-embed\.trace\].*?run=([\d.]+)ms")


def measure_latencies(port: int, rounds: int, warmup: int = 2) -> list[float]:
    """Cùng phép đo với `compare_mind.measure_latencies` — 1 persistent session."""
    import asyncio

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
                    started = time.perf_counter()
                    await session.call_tool(
                        "semantic_search",
                        {"query": query, "project_id": "mindfix"},
                    )
                    elapsed = (time.perf_counter() - started) * 1000.0
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


def trace_stats(log_path: Path) -> dict:
    """p50/p95 embed tổng (mind) và ORT run (cortex-embed) từ stderr log."""
    embed_ms: list[float] = []
    run_ms: list[float] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := TRACE_EMBED_TOTAL.search(line):
            embed_ms.append(float(match.group(2)))
        if match := TRACE_RUN.search(line):
            run_ms.append(float(match.group(1)))
    stats: dict[str, float | int] = {"trace_samples": len(embed_ms)}
    if embed_ms:
        stats["embed_p50_ms"] = round(statistics.median(embed_ms), 1)
        stats["embed_p95_ms"] = round(percentile(embed_ms, 0.95), 1)
    if run_ms:
        stats["ort_run_p50_ms"] = round(statistics.median(run_ms), 1)
    return stats


def launch_rust_with_env(port: int, extra_env: dict[str, str], log_path: Path):
    env = dict(os.environ)
    env.update(mind_contract.server_env(port))
    env["CORTEX_EMBED_TRACE"] = "1"
    env.update(extra_env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
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
        stderr=log_handle,
        start_new_session=True,
    )
    return process, log_handle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--rounds", type=int, default=60)
    parser.add_argument("--rust-port", type=int, default=RUST_PORT)
    parser.add_argument("--python-port", type=int, default=8797)
    args = parser.parse_args()

    names = [name.strip() for name in args.configs.split(",") if name.strip()]
    unknown = [name for name in names if name != "python-server" and name not in CONFIGS]
    if unknown:
        parser.error(f"unknown configs: {unknown}")

    # Server zombie đang giữ port sẽ khiến measure nhắm nhầm process cũ (trace
    # trống + số của binary cũ) — fail sớm thay vì đo nhầm.
    for label, port in (("rust", args.rust_port), ("python", args.python_port)):
        if port_in_use(port):
            print(f"[bench-server] port {port} ({label}) already in use — kill the stale process first")
            return 2

    python_process = launch_python_mind_server(args.python_port)
    if not wait_for_server(args.python_port, 300.0):
        print(f"python mind server failed to start on :{args.python_port}")
        stop_python_mind_server(python_process)
        return 2
    print(f"[bench-server] python reference ready on :{args.python_port}")

    python_samples = measure_latencies(args.python_port, args.rounds)
    python_stats = {
        "python_p50_ms": round(statistics.median(python_samples), 1),
        "python_p95_ms": round(percentile(python_samples, 0.95), 1),
    }
    print(f"[bench-server] python reference: {json.dumps(python_stats)}")

    results: dict[str, dict] = {"python_reference": python_stats}
    exit_code = 0
    try:
        for name in names:
            if name == "python-server":
                continue
            extra_env = CONFIGS[name]
            log_path = LOG_DIR / f"{name}.log"
            process, log_handle = launch_rust_with_env(args.rust_port, extra_env, log_path)
            try:
                if not wait_for_server(args.rust_port, 60):
                    print(f"[bench-server] {name}: rust server failed to boot")
                    exit_code = 2
                    continue
                samples = measure_latencies(args.rust_port, args.rounds)
                stats = {
                    "config": extra_env,
                    "rust_p50_ms": round(statistics.median(samples), 1),
                    "rust_p95_ms": round(percentile(samples, 0.95), 1),
                    "rounds": args.rounds,
                    **trace_stats(log_path),
                }
                stats["vs_python_p95"] = (
                    "PASS"
                    if stats["rust_p95_ms"] <= python_stats["python_p95_ms"]
                    else "FAIL"
                )
                results[name] = stats
                print(f"[bench-server] {name}: {json.dumps(stats)}")
            finally:
                log_handle.close()
                stop_rust_server(process)
                time.sleep(1.0)
    finally:
        stop_python_mind_server(python_process)

    print(json.dumps(results, indent=1))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
