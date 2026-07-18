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
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mcp_runtime_config import runtime_environment  # noqa: E402


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


def _json_request(url: str, payload: Optional[dict] = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - configured local/remote service
        return json.loads(response.read().decode("utf-8"))


def _qdrant_points(qdrant_url: str, collections: Iterable[str], project_id: str) -> List[dict]:
    points: List[dict] = []
    for collection in collections:
        offset: object = None
        while True:
            body = {"limit": 512, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = _json_request(
                f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                body,
            )
            result = response.get("result") or {}
            for item in result.get("points") or []:
                payload = item.get("payload") or {}
                if str(payload.get("project_id") or "") == project_id:
                    points.append(payload)
            offset = result.get("next_page_offset")
            if offset is None:
                break
    return points


def _graph_query(env: Mapping[str, str], query: str, params: Optional[dict] = None):
    from falkordb import FalkorDB

    client = FalkorDB(
        host=env.get("FALKORDB_HOST", "localhost"),
        port=int(env.get("FALKORDB_PORT", "6379")),
        username=env.get("FALKORDB_USER") or None,
        password=env.get("FALKORDB_PASSWORD") or None,
        ssl=env.get("FALKORDB_SSL", "false").lower() in {"1", "true", "yes", "on"},
    )
    graph = client.select_graph(env["FALKORDB_GRAPH"])
    return graph.query(query, params or {}).result_set


def _graph_nodes(env: Mapping[str, str], project_id: str) -> Dict[str, dict]:
    rows = _graph_query(
        env,
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


def _scalar_graph_count(env: Mapping[str, str], query: str, project_id: str) -> int:
    rows = _graph_query(env, query, {"project_id": project_id})
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

    qdrant_url = env.get("QDRANT_URL", "http://localhost:6333")
    collection_response = _json_request(f"{qdrant_url.rstrip('/')}/collections")
    names = [item["name"] for item in collection_response.get("result", {}).get("collections", [])]
    collections = code_collections_for_project(names, project_id)
    vector_points = _qdrant_points(qdrant_url, collections, project_id)
    graph_nodes = _graph_nodes(env, project_id)
    linkage = evaluate_symbol_linkage(vector_points, graph_nodes, project_id)
    module_count = _scalar_graph_count(
        env,
        "MATCH (n:File {project_id: $project_id}) RETURN count(n)",
        project_id,
    )
    api_count = _scalar_graph_count(
        env,
        "MATCH (n {project_id: $project_id}) WHERE n:ApiEndpoint OR n:HttpEndpoint RETURN count(n)",
        project_id,
    )
    expandable_seed_count = _scalar_graph_count(
        env,
        """
        MATCH (n {project_id: $project_id})-[:CALLS|CONTAINS]-()
        WHERE coalesce(n.id, n.symbol_id) IS NOT NULL
        RETURN count(DISTINCT n)
        """,
        project_id,
    )
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
