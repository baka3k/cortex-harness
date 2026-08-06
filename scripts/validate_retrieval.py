#!/usr/bin/env python3
"""Validate Cortex vector/graph linkage, freshness, modules, and API coverage."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(ROOT_DIR / "code-tiny") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "code-tiny"))

from mcp_runtime_config import runtime_environment  # noqa: E402
from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, resolve_storage  # noqa: E402
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_.-")


def code_collections_for_project(collections: Iterable[str], project_id: str) -> List[str]:
    scope = _safe_segment(project_id)
    legacy_prefix = f"{scope}_"
    project_prefix = f"{scope}-code-"
    return sorted(
        name
        for name in {str(item) for item in collections}
        if (
            name.startswith(project_prefix)
            or (name.startswith(legacy_prefix) and name.endswith("_functions"))
        )
    )


def _same_location(vector: Mapping[str, object], graph: Mapping[str, object]) -> bool:
    vector_path = str(vector.get("file_path") or "").replace("/", "\\").lower()
    graph_path = str(graph.get("file_path") or "").replace("/", "\\").lower()
    if vector_path and graph_path and vector_path != graph_path:
        return False
    vector_line = vector.get("start_line")
    graph_line = graph.get("start_line")
    if vector_line is not None and graph_line is not None:
        try:
            return int(vector_line) == int(graph_line)
        except (TypeError, ValueError):
            return str(vector_line) == str(graph_line)
    return True


def evaluate_symbol_linkage(
    vector_points: Iterable[Mapping[str, object]],
    graph_nodes: Mapping[str, Mapping[str, object]],
    project_id: str,
) -> Dict[str, object]:
    points = [point for point in vector_points if str(point.get("symbol_id") or "").strip()]
    missing: List[str] = []
    project_mismatches: List[str] = []
    location_mismatches: List[str] = []
    linked = 0
    for point in points:
        symbol_id = str(point.get("symbol_id"))
        node = graph_nodes.get(symbol_id)
        if node is None:
            missing.append(symbol_id)
            continue
        linked += 1
        vector_project = str(point.get("project_id") or project_id)
        graph_project = str(node.get("project_id") or "")
        if vector_project != project_id or graph_project != project_id:
            project_mismatches.append(symbol_id)
        if not _same_location(point, node):
            location_mismatches.append(symbol_id)
    return {
        "vector_points": len(points),
        "graph_nodes": len(graph_nodes),
        "linked": linked,
        "missing_graph_ids": sorted(set(missing)),
        "project_mismatches": sorted(set(project_mismatches)),
        "location_mismatches": sorted(set(location_mismatches)),
        "ok": bool(points) and not missing and not project_mismatches and not location_mismatches,
    }


def _qdrant_points(store: LocalQdrantStore, collections: Iterable[str], project_id: str) -> List[dict]:
    points: List[dict] = []
    for collection in collections:
        offset: object = None
        while True:
            page, offset = store.scroll(
                collection, limit=512, with_payload=True, with_vectors=False, offset=offset,
            )
            for item in page:
                payload = getattr(item, "payload", None) or {}
                if str(payload.get("project_id") or "") == project_id:
                    points.append(payload)
            if offset is None:
                break
    return points


def _graph_query(driver: FalkorDBDriver, query: str, params: Optional[dict] = None):
    records, keys, _ = driver.execute_query_sync(query, params or {})
    return [[record.get(key) for key in keys] for record in records]


def _graph_nodes(driver: FalkorDBDriver, project_id: str) -> Dict[str, dict]:
    rows = _graph_query(
        driver,
        """
        MATCH (n)
        WHERE n.project_id = $project_id AND coalesce(n.id, n.symbol_id) IS NOT NULL
        RETURN coalesce(n.id, n.symbol_id), n.project_id, n.file_path, n.start_line
        """,
        {"project_id": project_id},
    )
    return {
        str(row[0]): {
            "project_id": row[1],
            "file_path": row[2],
            "start_line": row[3],
        }
        for row in rows
        if row and row[0] is not None
    }


def _scalar_graph_count(driver: FalkorDBDriver, query: str, project_id: str) -> int:
    rows = _graph_query(driver, query, {"project_id": project_id})
    return int(rows[0][0]) if rows and rows[0] else 0


def _freshness(root: Path, project_id: str) -> Dict[str, object]:
    candidates = sorted(
        (root / ".cache" / "incremental_sync").glob(f"**/{project_id}.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    indexed_commit = ""
    if candidates:
        try:
            indexed_commit = str(json.loads(candidates[0].read_text(encoding="utf-8")).get("last_good_sha") or "")
        except (OSError, json.JSONDecodeError, TypeError):
            indexed_commit = ""
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    head = completed.stdout.strip() if completed.returncode == 0 else ""
    return {
        "indexed_commit": indexed_commit,
        "head_commit": head,
        "ok": bool(indexed_commit and head and indexed_commit == head),
    }


def validate(root: Path, require_api: bool = False) -> Dict[str, object]:
    root = root.resolve()
    env = runtime_environment(root, "code-tiny")
    project_id = env.get("PROJECT_ID", "")
    if not project_id:
        return {"ok": False, "error": "Active code project is not configured."}
    if env.get("GRAPH_PROVIDER") != "falkordb":
        return {"ok": False, "error": "Validation currently requires FalkorDB."}

    resolved = resolve_storage(
        root,
        qdrant_code_path=env.get("QDRANT_CODE_PATH"),
        falkordb_code_path=env.get("FALKORDB_CODE_PATH") or env.get("FALKORDB_PATH"),
    )
    vector_store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    graph_driver = FalkorDBDriver(
        path=resolved.falkordb_code_path, graph=env["FALKORDB_GRAPH"],
        instance_id=resolved.instance_id, owner_id=resolved.code_owner_id,
    )
    try:
        collections = code_collections_for_project(vector_store.list_collection_names(), project_id)
        vector_points = _qdrant_points(vector_store, collections, project_id)
        graph_nodes = _graph_nodes(graph_driver, project_id)
        linkage = evaluate_symbol_linkage(vector_points, graph_nodes, project_id)
        module_count = _scalar_graph_count(
            graph_driver,
            "MATCH (n:File {project_id: $project_id}) RETURN count(n)",
            project_id,
        )
        api_count = _scalar_graph_count(
            graph_driver,
            "MATCH (n {project_id: $project_id}) WHERE n:ApiEndpoint OR n:HttpEndpoint RETURN count(n)",
            project_id,
        )
        expandable_seed_count = _scalar_graph_count(
            graph_driver,
            """
            MATCH (n {project_id: $project_id})-[:CALLS|CONTAINS]-()
            WHERE coalesce(n.id, n.symbol_id) IS NOT NULL
            RETURN count(DISTINCT n)
            """,
            project_id,
        )
    finally:
        graph_driver.close()
        vector_store.close()
    freshness = _freshness(root, project_id)
    ok = bool(
        linkage["ok"]
        and freshness["ok"]
        and module_count > 0
        and expandable_seed_count > 0
        and (api_count > 0 if require_api else True)
    )
    return {
        "ok": ok,
        "project_id": project_id,
        "graph": env.get("FALKORDB_GRAPH"),
        "qdrant_collections": collections,
        "linkage": linkage,
        "freshness": freshness,
        "module_count": module_count,
        "api_endpoint_count": api_count,
        "expandable_seed_count": expandable_seed_count,
        "api_required": require_api,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--require-api", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate(args.root, require_api=args.require_api)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
