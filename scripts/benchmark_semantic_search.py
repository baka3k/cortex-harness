#!/usr/bin/env python3
"""Benchmark ``semantic_search`` latency by stage through the unified dispatch.

Measures the production surface (``unified_mcp._dispatch_tool`` → default
backend ``cplus``), never a single backend in isolation. Stage numbers
(embed / resolve / qdrant / expand) come from the ``MCP_SEARCH_TIMING``
log lines emitted inside each backend's ``tool_semantic_search``; the
dispatch wall time is measured around the call.

Modes:
  * synthetic (default) — deterministic tmp local-Qdrant store with three
    fixture collections (legacy-analyzer / primary_vector_sync / kotlin
    payload styles, vector size 8, seeded random vectors). The embed stage
    uses a stub unless ``--real-embed`` is passed.
  * live (--live) — the real store for the current project config, real
    model, no stubs.

Scenarios: scoped-repeat, scoped-cold, unscoped-multi, expand-graph.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
for entry in (
    str(SCRIPT_DIR),
    str(ROOT_DIR),
    str(ROOT_DIR / "code-tiny"),
    str(ROOT_DIR / "code-tiny" / "mcp"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

REPORT_DIR = ROOT_DIR / "plans" / "260829-2322-vector-search-query-optimization" / "reports"
SCENARIOS = ("scoped-repeat", "scoped-cold", "unscoped-multi", "expand-graph")
STAGES = ("embed", "resolve", "qdrant", "expand")
WARMUP = 3
RUNS = 20
VECTOR_SIZE = 8
POINTS_PER_COLLECTION = 40
BENCH_PROJECT = "bench-project"

_TIMING_RE = re.compile(
    r"semantic_search timing\[(?P<backend>\w+)\]: "
    r"embed=(?P<embed>[\d.]+) resolve=(?P<resolve>[\d.]+) "
    r"qdrant=(?P<qdrant>[\d.]+) expand=(?P<expand>[\d.]+) "
    r"total=(?P<total>[\d.]+)"
)


def _stub_vector(text: str, vector_size: int = VECTOR_SIZE) -> List[float]:
    """Deterministic unit vector derived from the query text (no model)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(vector_size)]
    norm = sum(value * value for value in raw) ** 0.5 or 1.0
    return [value / norm for value in raw]


class _StageCapture(logging.Handler):
    """Collect ``semantic_search timing`` records emitted by the backends."""

    def __init__(self) -> None:
        super().__init__()
        self.records: List[Dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        match = _TIMING_RE.search(str(record.getMessage()))
        if not match:
            return
        self.records.append(
            {
                "backend": match.group("backend"),
                **{
                    stage: float(match.group(stage))
                    for stage in ("embed", "resolve", "qdrant", "expand", "total")
                },
            }
        )


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def _stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0, "n": 0.0}
    return {
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "n": float(len(values)),
    }


def _seeded_vector(seed: int, vector_size: int = VECTOR_SIZE) -> List[float]:
    raw: List[float] = []
    counter = seed
    while len(raw) < vector_size:
        digest = hashlib.sha256(str(counter).encode("utf-8")).digest()
        for byte in digest:
            raw.append((byte / 255.0) - 0.5)
            if len(raw) == vector_size:
                break
        counter += 1
    norm = sum(value * value for value in raw) ** 0.5 or 1.0
    return [value / norm for value in raw]


_LEGACY_TEXT = "summary text for legacy analyzer fixture point {i} with a moderately long body " * 3
_PRIMARY_TEXT = "x" * 16_000


def _fixture_payload(style: str, index: int, project_id: str) -> Dict[str, Any]:
    from tools.common.project_scope import PROJECT_ID_NORMALIZED_FIELD, project_id_lookup_key

    base: Dict[str, Any] = {
        "project_id": project_id,
        PROJECT_ID_NORMALIZED_FIELD: project_id_lookup_key(project_id),
        "symbol_id": f"{style}_symbol_{index}",
        "name": f"{style}_symbol_{index}",
        "file_path": f"src/{style}/file_{index}.rs",
        "node_type": "function",
    }
    if style == "legacy":
        base.update(
            {
                "summary": _LEGACY_TEXT.format(i=index).strip(),
                "comment": f"comment for point {index}",
                "code": f"def point_{index}():\n    return {index}",
            }
        )
    elif style == "primary":
        base.update(
            {
                "parser": "rust",
                "root_scope": "org/repo",
                "qualified_name": f"pkg::{style}_symbol_{index}",
                "language": "rust",
                "text": _PRIMARY_TEXT,
            }
        )
    else:  # kotlin / android_kotlin style
        base.update(
            {
                "class_name": f"Bench{index}Class",
                "package_name": f"com.bench.{style}",
                "summary": _LEGACY_TEXT.format(i=index).strip(),
            }
        )
    return base


def build_synthetic_store(tmp_root: Path, vector_size: int = VECTOR_SIZE) -> tuple[Any, List[str]]:
    """Create the fixture store: three collections, two payload styles + kotlin."""
    from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, resolve_storage
    from qdrant_client.http import models as qmodels

    resolved = resolve_storage(tmp_root, qdrant_code_path=str(tmp_root / "code.qdrant"))
    store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    collections: List[str] = []
    point_id_seed = 0
    for style in ("legacy", "primary", "kotlin"):
        name = f"bench_{style}_functions"
        store.create_collection(
            name,
            vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
        )
        points = []
        for index in range(POINTS_PER_COLLECTION):
            point_id_seed += 1
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bench-{point_id_seed}")),
                    "vector": _seeded_vector(point_id_seed, vector_size),
                    "payload": _fixture_payload(style, index, BENCH_PROJECT),
                }
            )
        store.upsert(name, points, wait=True)
        collections.append(name)
    return store, collections


def run_scenario(
    loop: asyncio.AbstractEventLoop,
    dispatch: Callable[[Dict[str, Any]], Any],
    capture: _StageCapture,
    make_payload: Callable[[int], Dict[str, Any]],
) -> Dict[str, Any]:
    for warm in range(WARMUP):
        loop.run_until_complete(dispatch(make_payload(-1 - warm)))
    capture.records.clear()
    wall: List[float] = []
    stages: Dict[str, List[float]] = {stage: [] for stage in STAGES}
    backends: set[str] = set()
    result_shape_ok = True
    hits_seen = 0
    for run in range(RUNS):
        payload = make_payload(run)
        t0 = time.perf_counter()
        result = loop.run_until_complete(dispatch(payload))
        wall.append(time.perf_counter() - t0)
        if not isinstance(result, dict) or "results" not in result:
            result_shape_ok = False
        elif isinstance(result.get("results"), list):
            hits_seen += len(result["results"])
    for record in capture.records:
        backends.add(record["backend"])
        for stage in STAGES:
            stages[stage].append(record[stage])
    stage_stats = {stage: _stats(values) for stage, values in stages.items()}
    return {
        "wall": _stats(wall),
        "stages": stage_stats,
        "backends": sorted(backends),
        "runs": RUNS,
        "warmup": WARMUP,
        "result_shape_ok": result_shape_ok,
        "hits_total": hits_seen,
    }


def import_unified() -> Any:
    """Import unified_mcp (loads all backends) with benchmark-safe env."""
    os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")
    os.environ.pop("QDRANT_COLLECTION", None)
    import unified_mcp
    return unified_mcp


def wire_store(unified: Any, store: Any) -> None:
    """Point every loaded backend at the synthetic store."""

    def _resolver(*_args: Any, **_kwargs: Any) -> Any:
        return store

    for backend_name in ("cplus_backend", "android_backend", "fast_backend"):
        backend = getattr(unified, backend_name, None)
        if backend is not None:
            backend.get_code_qdrant_store = _resolver  # type: ignore[method-assign]


def wire_stub_embedder(unified: Any) -> None:
    """Replace the production embed stage with the deterministic stub."""
    unified.cplus_backend._embed_query = (  # type: ignore[method-assign]
        lambda text, model_name=None: _stub_vector(text)
    )


def resolve_model_dimension(unified: Any) -> int:
    """Probe the real model's output dimension (also warms model load)."""
    vector = unified.cplus_backend._embed_query(
        "dimension probe", unified.cplus_backend.DEFAULT_MODEL
    )
    return len(vector)


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    capture = _StageCapture()
    logging.getLogger("project_call_graph.mcp.server").addHandler(capture)
    logging.getLogger("project_call_graph.mcp.server").setLevel(logging.INFO)

    unified = import_unified()
    real_embed = args.live or args.real_embed
    store: Optional[Any] = None
    collections: List[str] = []
    tmp: Optional[tempfile.TemporaryDirectory] = None
    if not args.live:
        vector_size = VECTOR_SIZE
        if real_embed:
            vector_size = resolve_model_dimension(unified)
            print(f"[bench] real model dimension: {vector_size}")
        tmp = tempfile.TemporaryDirectory(prefix="bench-semantic-")
        store, collections = build_synthetic_store(Path(tmp.name), vector_size)
        wire_store(unified, store)
        print(f"[bench] synthetic store: {len(collections)} collections in {tmp.name}")
    if not real_embed:
        wire_stub_embedder(unified)
    dispatch = lambda payload: unified._dispatch_tool("semantic_search", payload)  # noqa: E731
    results: Dict[str, Any] = {}
    loop = asyncio.new_event_loop()
    try:
        scoped_collection = collections[0] if collections else None

        def payload_scoped_repeat(run: int) -> Dict[str, Any]:
            body: Dict[str, Any] = {"query": "bench repeat query alpha"}
            if scoped_collection:
                body["collection"] = scoped_collection
            if not args.live:
                body["project_id"] = BENCH_PROJECT
            return body

        def payload_scoped_cold(run: int) -> Dict[str, Any]:
            body: Dict[str, Any] = {"query": f"bench cold query variant {run}"}
            if scoped_collection:
                body["collection"] = scoped_collection
            if not args.live:
                body["project_id"] = BENCH_PROJECT
            return body

        def payload_unscoped(run: int) -> Dict[str, Any]:
            return {"query": f"bench unscoped query variant {run}"}

        def payload_expand(run: int) -> Dict[str, Any]:
            body: Dict[str, Any] = {
                "query": f"bench expand query variant {run}",
                "expand_graph": True,
            }
            if scoped_collection:
                body["collection"] = scoped_collection
            if not args.live:
                body["project_id"] = BENCH_PROJECT
            return body

        scenario_payloads = {
            "scoped-repeat": payload_scoped_repeat,
            "scoped-cold": payload_scoped_cold,
            "unscoped-multi": payload_unscoped,
            "expand-graph": payload_expand,
        }
        for name in SCENARIOS:
            print(f"[bench] scenario {name} ...", flush=True)
            results[name] = run_scenario(loop, dispatch, capture, scenario_payloads[name])
    finally:
        loop.close()
        if tmp is not None:
            tmp.cleanup()

    # Probe which backend actually served the traffic (once, cheap).
    probe_loop = asyncio.new_event_loop()
    try:
        probe_payload: Dict[str, Any] = {"query": "backend probe"}
        if collections and not args.live:
            probe_payload["collection"] = collections[0]
            probe_payload["project_id"] = BENCH_PROJECT
        probe = probe_loop.run_until_complete(dispatch(dict(probe_payload)))
    except Exception as exc:  # noqa: BLE001
        probe = {"error": str(exc)}
    finally:
        probe_loop.close()
    engine_resolved = None
    if isinstance(probe, dict):
        capability = probe.get("capability") or {}
        engine_resolved = (
            capability.get("query_engine")
            or probe.get("query_engine")
        )
    timing_backends = sorted({record["backend"] for record in capture.records})
    backend_resolved = timing_backends or engine_resolved

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "live" if args.live else ("synthetic-real-embed" if args.real_embed else "synthetic-stub"),
        "surface": "unified_mcp._dispatch_tool(semantic_search) -> default backend",
        "backend_resolved": backend_resolved,
        "query_engine_resolved": engine_resolved,
        "runs": RUNS,
        "warmup": WARMUP,
        "scenarios": results,
    }


def write_markdown(report: Dict[str, Any], path: Path) -> None:
    lines = [
        "# semantic_search benchmark",
        "",
        f"- generated: {report['generated_at']}",
        f"- mode: {report['mode']}",
        f"- surface: {report['surface']}",
        f"- backend resolved: {report['backend_resolved']}",
        f"- runs: {report['runs']} (warmup {report['warmup']})",
        "",
    ]
    for scenario, data in report["scenarios"].items():
        lines.append(f"## {scenario}")
        lines.append("")
        lines.append("| stage | p50 (s) | p95 (s) | mean (s) | min (s) | max (s) | n |")
        lines.append("|-------|---------|---------|----------|---------|---------|---|")
        wall = data["wall"]
        lines.append(
            f"| dispatch-wall | {wall['p50']:.4f} | {wall['p95']:.4f} | {wall['mean']:.4f} "
            f"| {wall['min']:.4f} | {wall['max']:.4f} | {int(wall['n'])} |"
        )
        for stage in STAGES:
            stats = data["stages"][stage]
            lines.append(
                f"| {stage} | {stats['p50']:.4f} | {stats['p95']:.4f} | {stats['mean']:.4f} "
                f"| {stats['min']:.4f} | {stats['max']:.4f} | {int(stats['n'])} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Benchmark the real configured store + model.")
    parser.add_argument(
        "--real-embed",
        action="store_true",
        help="Synthetic store with the real embedding model (measures the embed stage).",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Override JSON output path.")
    parser.add_argument("--md-out", type=Path, default=None, help="Override markdown output path.")
    parser.add_argument(
        "--label",
        default="baseline",
        help="Output file label when --json-out/--md-out are not given.",
    )
    args = parser.parse_args()

    report = run_benchmark(args)
    json_out = args.json_out or (REPORT_DIR / f"{args.label}.json")
    md_out = args.md_out or (REPORT_DIR / f"{args.label}.md")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, md_out)
    print(f"[bench] json  -> {json_out}")
    print(f"[bench] markdown -> {md_out}")
    for scenario, data in report["scenarios"].items():
        wall = data["wall"]
        print(f"  {scenario}: wall p50={wall['p50']:.4f}s p95={wall['p95']:.4f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
