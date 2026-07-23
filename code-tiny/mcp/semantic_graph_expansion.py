from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from framework_registry import servlet_active_generation_predicate
from tools.common.project_scope import prepare_project_scope_parameters


RunCypherFirst = Callable[[str, Dict[str, Any], List[str]], Awaitable[Tuple[str, List[Dict[str, Any]]]]]

DEFAULT_GRAPH_REL_TYPES = ["CALLS", "USES_TYPE", "REFERENCES", "INHERITS"]
MAX_GRAPH_DEPTH = 5
DEFAULT_GRAPH_LIMIT = 50


def _normalize_positive_int(value: Any, default: int, max_value: Optional[int] = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < 1:
        number = default
    if max_value is not None:
        number = min(number, max_value)
    return number


def _normalize_graph_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"in", "incoming", "upstream"}:
        return "in"
    if text in {"out", "outgoing", "downstream"}:
        return "out"
    return "both"


def normalize_graph_rel_types(value: Any) -> List[str]:
    if value is None:
        items: Iterable[Any] = DEFAULT_GRAPH_REL_TYPES
    elif isinstance(value, str):
        text = value.strip()
        items = DEFAULT_GRAPH_REL_TYPES if not text else text.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]

    rel_types: List[str] = []
    for item in items:
        rel = str(item).strip().upper()
        if not rel:
            continue
        if not rel.replace("_", "").isalnum():
            raise ValueError(f"Invalid graph relationship type: {item}")
        if rel not in rel_types:
            rel_types.append(rel)
    return rel_types or list(DEFAULT_GRAPH_REL_TYPES)


def _build_rel_pattern(rel_types: List[str], depth: int, direction: str) -> str:
    rel = "|".join(rel_types)
    if direction == "in":
        return f"<-[:{rel}*1..{depth}]-"
    if direction == "out":
        return f"-[:{rel}*1..{depth}]->"
    return f"-[:{rel}*1..{depth}]-"


def _build_edge_pattern(rel_types: List[str], direction: str) -> str:
    rel = "|".join(rel_types)
    if direction == "in":
        return f"<-[r:{rel}]-"
    if direction == "out":
        return f"-[r:{rel}]->"
    return f"-[r:{rel}]-"


def _proximity(hops: int) -> float:
    return max(0.0, round(1.0 - max(hops, 0) * 0.2, 6))


def extract_seed_ids(items: List[Dict[str, Any]]) -> List[str]:
    seed_ids: List[str] = []
    for item in items:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        node_id = payload.get("node_id") or payload.get("symbol_id")
        if node_id:
            text = str(node_id)
            if text not in seed_ids:
                seed_ids.append(text)
    return seed_ids


async def expand_semantic_results(
    results: Dict[str, Any],
    *,
    run_cypher_first: RunCypherFirst,
    db_candidates: List[str],
    expand_graph: bool = False,
    graph_depth: Any = 2,
    graph_direction: Any = "both",
    graph_rel_types: Any = None,
    graph_limit: Any = DEFAULT_GRAPH_LIMIT,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    items = results.get("results")
    if not isinstance(items, list):
        return results

    depth = _normalize_positive_int(graph_depth, 2, MAX_GRAPH_DEPTH)
    limit = _normalize_positive_int(graph_limit, DEFAULT_GRAPH_LIMIT, 500)
    direction = _normalize_graph_direction(graph_direction)
    rel_types = normalize_graph_rel_types(graph_rel_types)
    project_id = (project_id or "").strip() or None
    seed_ids = extract_seed_ids(items)

    expansion: Dict[str, Any] = {
        "enabled": bool(expand_graph),
        "seed_ids": seed_ids,
        "depth": depth,
        "direction": direction,
        "relationship_types": rel_types,
        "results": [],
        "edges": [],
    }
    results["graph_expansion"] = expansion

    if not expand_graph or not seed_ids:
        return results

    rel_pattern = _build_rel_pattern(rel_types, depth, direction)
    edge_pattern = _build_edge_pattern(rel_types, direction)
    node_query = f"""
    UNWIND $seed_ids AS sid
    MATCH (seed {{id: sid}})
    WHERE ($project_id IS NULL OR seed.project_id_normalized = $project_id_normalized)
    MATCH p = (seed){rel_pattern}(neighbor)
    WHERE neighbor.id IS NOT NULL
      AND NOT neighbor.id IN $seed_ids
      AND ($project_id IS NULL OR neighbor.project_id_normalized = $project_id_normalized)
      AND {servlet_active_generation_predicate('neighbor')}
    WITH neighbor, min(length(p)) AS hops, collect(DISTINCT sid) AS seed_ids
    ORDER BY hops ASC
    LIMIT $limit
    RETURN seed_ids[0] AS seed_id,
           seed_ids AS seed_ids,
           neighbor.id AS node_id,
           neighbor.name AS name,
           coalesce(neighbor.qualified_name, neighbor.name) AS qualified_name,
           coalesce(neighbor.kind, labels(neighbor)[0], 'Node') AS kind,
           neighbor.framework AS framework,
           neighbor.resolution_status AS resolution_status,
           coalesce(neighbor.file_path, '') AS file_path,
           neighbor.start_line AS start_line,
           neighbor.end_line AS end_line,
           neighbor.intent AS intent,
           neighbor.exported AS exported,
           neighbor.side_effect AS side_effect,
           neighbor.doc_confidence AS doc_confidence,
           neighbor.project_id AS project_id,
           neighbor.target_name AS target_name,
           neighbor.signature AS signature,
           neighbor.language AS language,
           hops AS hop_distance
    """
    params = prepare_project_scope_parameters(
        node_query,
        {"seed_ids": seed_ids, "limit": limit, "project_id": project_id},
    )

    try:
        used_db, rows = await run_cypher_first(node_query, params, db_candidates)
        expansion["db"] = used_db
    except Exception as exc:  # noqa: BLE001
        expansion["error"] = str(exc)
        return results

    graph_nodes: List[Dict[str, Any]] = []
    node_ids = list(seed_ids)
    for row in rows:
        node_id = str(row.get("node_id") or "")
        if not node_id:
            continue
        hops = _normalize_positive_int(row.get("hop_distance"), depth)
        graph_nodes.append(
            {
                "seed_id": row.get("seed_id"),
                "seed_ids": list(row.get("seed_ids") or ([row.get("seed_id")] if row.get("seed_id") else [])),
                "node_id": node_id,
                "name": row.get("name") or "",
                "qualified_name": row.get("qualified_name") or "",
                "kind": row.get("kind") or "Node",
                "framework": row.get("framework") or "",
                "resolution_status": row.get("resolution_status") or "",
                "file_path": row.get("file_path") or "",
                "start_line": row.get("start_line"),
                "end_line": row.get("end_line"),
                "intent": row.get("intent") or "",
                "exported": bool(row.get("exported") or False),
                "side_effect": bool(row.get("side_effect") or False),
                "doc_confidence": float(row.get("doc_confidence") or 0.0),
                "project_id": row.get("project_id") or "",
                "target_name": row.get("target_name") or "",
                "signature": row.get("signature") or "",
                "language": row.get("language") or "",
                "hop_distance": hops,
                "graph_proximity": _proximity(hops),
            }
        )
        if node_id not in node_ids:
            node_ids.append(node_id)
    expansion["results"] = graph_nodes

    if not graph_nodes:
        return results

    edge_query = f"""
    MATCH (source){edge_pattern}(target)
    WHERE source.id IN $node_ids
      AND target.id IN $node_ids
      AND ($project_id IS NULL OR source.project_id_normalized = $project_id_normalized)
      AND ($project_id IS NULL OR target.project_id_normalized = $project_id_normalized)
    RETURN DISTINCT source.id AS source,
           target.id AS target,
           type(r) AS type,
           coalesce(r.confidence, r.score) AS confidence,
           r.call_depth AS call_depth
    LIMIT $edge_limit
    """
    try:
        edge_dbs = [str(expansion["db"])] if expansion.get("db") else db_candidates
        _, edge_rows = await run_cypher_first(
            edge_query,
            prepare_project_scope_parameters(edge_query, {
                "node_ids": node_ids,
                "edge_limit": max(limit * 2, 25),
                "project_id": project_id,
            }),
            edge_dbs,
        )
        expansion["edges"] = [
            {
                "source": row.get("source"),
                "target": row.get("target"),
                "type": row.get("type"),
                "confidence": row.get("confidence"),
                "call_depth": row.get("call_depth"),
            }
            for row in edge_rows
        ]
    except Exception as exc:  # noqa: BLE001
        expansion["edge_error"] = str(exc)

    return results
