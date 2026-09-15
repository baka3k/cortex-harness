#!/usr/bin/env python3
"""Phase 03 benchmark — write 1k nodes: Python writer vs Rust writer.

Cùng fixture sinh on-the-fly (1k function rows full-metadata), ghi vào 2 graph
FalkorDB riêng, đo wall-clock của write path (không tính dump/cleanup). Ghi
số liệu vào reports/phase03-benchmark.md (không đặt gate tuyệt đối, theo
phase-03.md).

Usage (repo root):
    .venv/bin/python scripts/rust_parity/bench_writer_1k.py \
        [--host 127.0.0.1] [--port 6379] [--rows 1000] [--runs 3]
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402

REPORT = REPO / "plans/260913-2130-rust-full-migration/reports/phase03-benchmark.md"
RUST_BIN = REPO / "rust/target/release/examples/bench_1k"


def make_rows(count: int, project_id: str) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "id": f"bench:fn:{index:05d}",
                "name": f"bench_fn_{index}",
                "node_type": "code",
                "qualified_name": f"bench::mod{index // 50}::bench_fn_{index}",
                "kind": "function",
                "class_name": "",
                "package_name": f"pkg{index // 50}",
                "scope_name": "",
                "file_path": f"bench/src/mod{index // 50}.rs",
                "start_byte": index * 120,
                "end_byte": index * 120 + 110,
                "start_line": (index % 400) + 1,
                "end_line": (index % 400) + 12,
                "arity": index % 6,
                "code": "fn bench() { /* x */ }",
                "comment": "",
                "summary": f"bench fn {index}",
                "note": "",
                "exported": index % 3 == 0,
                "visibility": "public" if index % 2 == 0 else "private",
                "is_public_api": index % 5 == 0,
                "visibility_source": "tree_sitter",
                "export_evidence": "",
                "signature": "",
                "external": False,
                "builtin": False,
                "react_role": "",
                "middleware_kind": "",
                "project_id": project_id,
                "project_id_normalized": project_id,
                "project_name": "bench",
                "language": "rust",
                "repo": "bench-repo",
                "build_system": "cargo",
            }
        )
    return rows


def bench_python(driver: FalkorDBDriver, graph: str, rows: list[dict]) -> list[float]:
    timings = []

    async def once() -> float:
        await driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph)
        writer = LanguageCodeWriter(driver, database=graph, batch_size=1000)
        started = time.monotonic()
        await writer.ensure_schema()
        await writer.write_functions_full(rows)
        return time.monotonic() - started

    async def run() -> None:
        for _ in range(3):
            timings.append(await once())

    asyncio.run(run())
    return timings


def bench_rust(graph: str, rows: int, host: str, port: int) -> list[float]:
    timings = []
    for _ in range(3):
        started = time.monotonic()
        proc = subprocess.run(
            [
                str(RUST_BIN),
                "--graph",
                graph,
                "--host",
                host,
                "--port",
                str(port),
                "--rows",
                str(rows),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = time.monotonic() - started
        if proc.returncode != 0:
            print(proc.stdout[-2000:], proc.stderr[-2000:])
            raise RuntimeError("rust bench failed")
        timings.append(elapsed)
    return timings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    driver = FalkorDBDriver(host=args.host, port=args.port)
    rows = make_rows(args.rows, "bench")

    print(f"bench {args.rows} nodes, {args.runs} runs mỗi bên")
    py_timings = bench_python(driver, "bench_pw", rows[: args.rows])

    if not RUST_BIN.exists():
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "-p",
                "cortex-graph-writer",
                "--example",
                "bench_1k",
            ],
            cwd=REPO / "rust",
            check=True,
        )
    rust_timings = []
    for _ in range(args.runs):
        started = time.monotonic()
        proc = subprocess.run(
            [
                str(RUST_BIN),
                "--graph",
                "bench_rw",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--rows",
                str(args.rows),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            print(proc.stdout[-2000:], proc.stderr[-2000:])
            return 1
        rust_timings.append(time.monotonic() - started)

    def fmt(values: list[float]) -> str:
        return ", ".join(f"{value:.3f}s" for value in values)

    py_median = statistics.median(py_timings)
    rust_median = statistics.median(rust_timings)
    lines = [
        "# Phase 03 benchmark — write 1k nodes (functions_full)",
        "",
        f"- rows: {args.rows}, runs: {args.runs}, batch_size: 1000, backend: FalkorDB {args.host}:{args.port}",
        f"- Python (LanguageCodeWriter + FalkorDBDriver): runs=[{fmt(py_timings)}] median={py_median:.3f}s",
        "- Rust (LanguageCodeWriter + FalkorDbStore): including process spawn + connect:",
        f"  runs=[{fmt(rust_timings)}] median={rust_median:.3f}s",
        f"- ratio (py/rust, incl. spawn): {py_median / rust_median:.2f}x",
        "",
        "Ghi chú: đo wall-clock toàn bộ write path; Rust timing gồm process "
        "spawn + connect (khoảng ~50-100ms) nên bất lợi nhẹ cho Rust. "
        "Không đặt gate tuyệt đối theo phase-03.md.",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
