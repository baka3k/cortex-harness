"""LadybugDB benchmark smoke (gate measurement, not optimization).

Compares the two ingest write paths on an embedded LadybugDB store —
(a) MERGE row-by-row through the driver's BoundedLane and (b) the
``COPY FROM <DataFrame>`` bulk fast path — plus read latency (p50/p95)
for representative queries.  Results feed the full-cutover decision
recorded in ``plans/260913-1538-ladybug-graph-provider/phase-06-results.md``.

Usage::

    .venv/bin/python scripts/benchmark_ladybug.py [--nodes 20000] [--out results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CODE_TINY = _ROOT / "code-tiny"
if str(_CODE_TINY) not in sys.path:
    sys.path.insert(0, str(_CODE_TINY))

try:
    import ladybug  # noqa: F401
except ImportError as exc:  # pragma: no cover - opt-in tooling
    raise SystemExit(
        "The 'ladybug' package is required for this benchmark. "
        "Install it first (see pyproject.toml)."
    ) from exc

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - opt-in tooling
    raise SystemExit("pandas is required for the COPY FROM benchmark path.") from exc

from tools.graph.driver.ladybug_driver import LadybugDriver  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _make_nodes(count: int) -> tuple[list[dict], "pd.DataFrame"]:
    rows = [
        {
            "id": f"fn:{index:07d}",
            "name": f"function_{index}",
            "file_path": f"src/module_{index % 100}.py",
            "project_id": "bench",
            "code": f"def function_{index}(): return {index}",
        }
        for index in range(count)
    ]
    return rows, pd.DataFrame(rows)


async def _merge_path(driver: LadybugDriver, nodes: list[dict]) -> float:
    """Ingest row-by-row with MERGE upserts through the query lane."""

    started = time.perf_counter()
    batch: list[dict] = []

    async def flush(rows: list[dict]) -> None:
        await driver.execute_query(
            "UNWIND $rows AS row "
            "MERGE (n:Function {id: row.id}) "
            "SET n.name = row.name, n.file_path = row.file_path, "
            "n.project_id = row.project_id, n.code = row.code "
            "RETURN count(n) AS count",
            {"rows": rows},
        )

    for node in nodes:
        batch.append(node)
        if len(batch) >= 500:
            await flush(batch)
            batch = []
    if batch:
        await flush(batch)
    return time.perf_counter() - started


def _bulk_path(driver: LadybugDriver, frame: "pd.DataFrame") -> float:
    """Ingest via COPY FROM DataFrame (initial generation fast path)."""

    started = time.perf_counter()
    driver.bulk_load("BenchFunction", frame)
    return time.perf_counter() - started


def _read_latencies(driver: LadybugDriver, samples: int) -> dict[str, dict[str, float]]:
    queries = {
        "by_id": (
            "MATCH (n:`BenchFunction`) WHERE n.id = $id RETURN n.id AS id",
            {"id": "fn:0000042"},
        ),
        "traverse_depth2": (
            "MATCH p=(a:`BenchFunction` {id: $id})-[:BENCH_CALLS*1..2]->(b:`BenchFunction`) "
            "RETURN count(p) AS count",
            {"id": "fn:0000000"},
        ),
        "contains_scan": (
            "MATCH (n:`BenchFunction`) WHERE n.file_path CONTAINS $token "
            "RETURN count(n) AS count",
            {"token": "module_7"},
        ),
    }
    timings: dict[str, dict[str, float]] = {}
    for name, (query, params) in queries.items():
        samples_ms = []
        for _ in range(samples):
            started = time.perf_counter()
            driver.execute_query_sync(query, params)
            samples_ms.append((time.perf_counter() - started) * 1000.0)
        timings[name] = {
            "p50_ms": round(_percentile(samples_ms, 0.50), 3),
            "p95_ms": round(_percentile(samples_ms, 0.95), 3),
            "mean_ms": round(statistics.fmean(samples_ms), 3),
        }
    return timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=20000, help="node count per path")
    parser.add_argument("--read-samples", type=int, default=20, help="per-query samples")
    parser.add_argument("--out", type=Path, default=None, help="optional JSON output path")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ladybug-bench-"))
    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ladybug_version": ladybug.__version__,
        "nodes_per_path": args.nodes,
        "machine": {"python": sys.version.split()[0]},
    }

    # (a) MERGE path through the driver.
    merge_store = tmp / "merge.lbug"
    merge_driver = LadybugDriver(merge_store, graph="bench")
    try:
        nodes, _ = _make_nodes(args.nodes)
        results["merge_ingest_seconds"] = round(
            asyncio.run(_merge_path(merge_driver, nodes)), 3
        )
        results["read_latency_merge_store"] = _read_latencies(merge_driver, args.read_samples)
    finally:
        merge_driver.close()

    # (b) COPY FROM DataFrame on a fresh store (initial full ingest).
    bulk_store = tmp / "bulk.lbug"
    bulk_driver = LadybugDriver(bulk_store, graph="bench")
    try:
        _nodes, frame = _make_nodes(args.nodes)
        results["copy_ingest_seconds"] = round(_bulk_path(bulk_driver, frame), 3)
        count = bulk_driver.execute_query_sync(
            "MATCH (n:`BenchFunction`) RETURN count(n) AS count"
        )[0][0]["count"]
        results["copy_ingest_rows"] = int(count)
    finally:
        bulk_driver.close()

    merge_seconds = results.get("merge_ingest_seconds") or 0.0
    copy_seconds = results.get("copy_ingest_seconds") or 0.0
    results["copy_speedup_vs_merge"] = (
        round(merge_seconds / copy_seconds, 2) if copy_seconds else None
    )

    print(json.dumps(results, indent=2, sort_keys=True))
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[bench] wrote {args.out}")


if __name__ == "__main__":
    main()
