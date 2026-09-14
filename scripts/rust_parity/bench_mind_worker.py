#!/usr/bin/env python3
"""Measure the CURRENT mind-MCP embedding path: the persistent NDJSON worker.

This is the baseline the phase-02 P95 gate compares against. It drives
`scripts/rust_mcp/embed_worker.py` exactly the way `cortex-mcp/src/mind/embed.rs`
does — spawn once, one JSON request per line on stdin, one JSON response per line
on stdout — so the numbers include the IPC round-trip the Rust client pays, not
just torch compute.

Reports: cold start (spawn + first request), single-request p50/p95, and batched
ingest throughput for the same corpus the Rust bench uses.

Usage:
    .venv/bin/python scripts/rust_parity/bench_mind_worker.py
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "scripts" / "rust_mcp" / "embed_worker.py"
FIXTURE = REPO / "rust" / "crates" / "cortex-embed" / "tests" / "fixtures" / "bge_golden.json"
THREADS = int(os.environ.get("BENCH_THREADS", "4"))
SAMPLES = 40


def request(process, texts: list[str]):
    payload = json.dumps({"texts": texts, "device": "cpu"}) + "\n"
    started = time.perf_counter()
    process.stdin.write(payload)
    process.stdin.flush()
    line = process.stdout.readline()
    elapsed = time.perf_counter() - started
    if not line or not line.startswith("{"):
        raise SystemExit(f"[worker] bad response line: {line!r}")
    body = json.loads(line)
    if "error" in body:
        raise SystemExit(f"[worker] error: {body['error']}")
    return body, elapsed


def main() -> int:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ["OMP_NUM_THREADS"] = str(THREADS)
    os.environ["MKL_NUM_THREADS"] = str(THREADS)

    if not WORKER.is_file():
        raise SystemExit(f"worker script missing: {WORKER}")
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Lane *query*, không phải paragraph ingest: gate so P95 của đường query mind
    # MCP, nơi text thật ngắn (~20-40 ký tự). Paragraph 500 ký tự phóng đại số đo.
    texts = [case["text"] for case in corpus["cases"] if case["lane"] == "query"]
    if not texts:
        texts = [case["text"] for case in corpus["cases"] if case["lane"] == "ingest"]
    samples = [text for text in (texts * 4)][:48]
    print(f"[worker] model=BAAI/bge-m3 query_samples={len(samples)} threads={THREADS}")

    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, "-u", str(WORKER)],
        cwd=str(REPO),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        first, cold = request(process, [texts[0]])
        print(
            f"[worker] cold start (spawn+load+first request): {cold:.2f}s "
            f"dimension={first['dimension']}"
        )

        latencies: list[float] = []
        for text in samples:
            _, elapsed = request(process, [text])
            latencies.append(elapsed * 1000.0)
        latencies.sort()
        print(
            f"[worker] single-request p50={statistics.median(latencies):.1f}ms "
            f"p95={latencies[int(len(latencies) * 0.95) - 1]:.1f}ms n={len(latencies)}"
        )

        for batch in (8, 32, 128):
            total, sent = 0.0, 0
            for start in range(0, len(texts), batch):
                chunk = texts[start : start + batch]
                _, elapsed = request(process, chunk)
                total += elapsed
                sent += len(chunk)
            print(
                f"[worker] batch={batch:3} texts={sent} time={total:.2f}s "
                f"throughput={sent / total:.1f} texts/s"
            )
    finally:
        process.stdin.close()
        process.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
