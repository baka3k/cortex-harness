"""
workflow_finder.py — Screen Workflow Finder

Given a Neo4j graph populated by ts_analyzer (with NAVIGATE edges between
Function nodes representing React Screens), discover ranked screen-only
workflow paths scoped to a single project.

Two modes:
  - pair:   input (node_a, node_b)                  -> paths a -> b
  - single: input node_a + direction in
            {inbound, outbound, bidirectional}      -> paths touching node_a

Every node on a returned path has ``react_role == 'screen'``. Paths are simple
(no repeated nodes). Ranking:

    (aggregate_confidence DESC, total_call_depth ASC, length ASC)

where ``aggregate_confidence`` is the product of per-edge ``confidence`` values
and ``total_call_depth`` is the sum of per-edge ``call_depth`` values. Paths
with the same sequence of screen symbol_ids are collapsed, keeping the highest
aggregate_confidence representative.

No APOC usage; pure Cypher only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


DEFAULT_MAX_HOPS = 8
DEFAULT_MAX_PATHS = 100
HARD_MAX_HOPS = 20
HARD_MAX_PATHS = 1000


# ─── Name resolution ─────────────────────────────────────────────────────────


async def _resolve_node(
    driver: Any,
    database: str,
    project_id: str,
    value: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve ``value`` to screen Function candidates.

    Returns ``(candidates, warnings)``. ``value`` is matched first by
    ``symbol_id`` (exact), then by ``name`` (exact, case-insensitive) scoped to
    ``project_id`` and ``react_role='screen'``.
    """

    warnings: List[str] = []
    query = """
    MATCH (f:Function {project_id: $pid})
    WHERE (f.symbol_id = $value OR toLower(f.name) = toLower($value))
      AND f.react_role = 'screen'
    RETURN f.symbol_id AS symbol_id,
           f.name AS name,
           f.file_path AS file_path,
           f.react_role AS react_role
    LIMIT 20
    """
    candidates = await _run_records(
        driver, database, query, {"pid": project_id, "value": value}
    )
    if not candidates:
        warnings.append(f"no screen node matched '{value}' in project '{project_id}'")
    elif len(candidates) > 1:
        warnings.append(
            f"'{value}' resolved to {len(candidates)} screen candidates; "
            "using all of them as sources/targets"
        )
    return candidates, warnings


# ─── Driver helpers ──────────────────────────────────────────────────────────


async def _run_records(
    driver: Any,
    database: str,
    query: str,
    params: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Execute a Cypher read and normalize to list of dicts.

    Works with both plain ``neo4j.AsyncDriver`` (whose ``execute_query``
    uses ``database_=`` per the trailing-underscore convention to avoid
    collision with user query parameters of the same name) and the
    project's ``GraphDriver`` abstraction (whose ``execute_query`` uses
    plain ``database=``). We pick the kwarg by inspecting the signature
    once rather than guessing — the prior code hard-coded ``database_=``
    which raised ``TypeError: unexpected keyword argument 'database_'``
    on every ``GraphDriver``-backed call (e.g. ``find_screen_workflows``).
    """
    import inspect

    # Preferred: the project abstraction
    run = getattr(driver, "run_read", None)
    if callable(run):
        rows = await run(query, params, database=database)
        return [dict(r) for r in rows]

    exec_q = getattr(driver, "execute_query", None)
    if callable(exec_q):
        try:
            sig_params = inspect.signature(exec_q).parameters
        except (TypeError, ValueError):
            sig_params = {}
        if "database_" in sig_params:
            db_kwarg = {"database_": database}
        elif "database" in sig_params:
            db_kwarg = {"database": database}
        else:
            # Last-resort: both names rejected by signature inspection.
            # Try the underscore-suffixed form first (matches modern
            # neo4j-driver's design) and fall back to plain.
            try:
                result = await exec_q(query, params, database_=database)  # type: ignore[misc]
            except TypeError:
                result = await exec_q(query, params, database=database)  # type: ignore[misc]
            # neo4j.AsyncDriver.execute_query returns EagerResult in v5+
            records = getattr(result, "records", None) or result[0]
            return [dict(r) for r in records]
        result = await exec_q(query, params, **db_kwarg)  # type: ignore[misc]
        records = getattr(result, "records", None) or result[0]
        return [dict(r) for r in records]

    # Fallback: raw async session
    async with driver.session(database=database) as session:
        result = await session.run(query, params)
        records = await result.data()
        return [dict(r) for r in records]


# ─── Cypher assembly ─────────────────────────────────────────────────────────


def _path_query_no_apoc(max_hops: int) -> str:
    """Pure-Cypher simple-path variant (no APOC).

    Uses a self-referential NONE() check to enforce node uniqueness on the
    path.
    """

    return f"""
    MATCH (a:Function)
    WHERE a.symbol_id IN $a_ids
      AND a.project_id = $pid
      AND a.react_role = 'screen'
    MATCH (b:Function)
    WHERE b.symbol_id IN $b_ids
      AND b.project_id = $pid
      AND b.react_role = 'screen'
    MATCH p = (a)-[:NAVIGATE*1..{max_hops}]->(b)
    WHERE ALL(n IN nodes(p) WHERE n.react_role = 'screen'
                               AND n.project_id = $pid)
      AND NONE(
            x IN nodes(p)
            WHERE size([y IN nodes(p) WHERE y.symbol_id = x.symbol_id]) > 1
          )
    RETURN [n IN nodes(p) | {{
              symbol_id: n.symbol_id,
              name:      n.name,
              file_path: n.file_path,
              react_role: n.react_role
           }}] AS nodes,
           [r IN relationships(p) | {{
              method:       r.method,
              target:       r.target,
              via:          r.via,
              trigger_type: r.trigger_type,
              guard:        r.guard,
              call_depth:   coalesce(r.call_depth, 0),
              confidence:   coalesce(r.confidence, 1.0)
           }}] AS rels,
           length(p) AS length
    LIMIT $limit
    """


# ─── Ranking / dedup ─────────────────────────────────────────────────────────


def _score_workflow(nodes: List[Dict[str, Any]], rels: List[Dict[str, Any]]) -> Dict[str, Any]:
    agg_conf = 1.0
    total_depth = 0
    for r in rels:
        c = r.get("confidence")
        agg_conf *= float(c) if c is not None else 1.0
        d = r.get("call_depth")
        total_depth += int(d) if d is not None else 0
    return {
        "aggregate_confidence": round(agg_conf, 6),
        "total_call_depth": total_depth,
        "length": len(rels),
    }


def _dedupe_and_rank(workflows: List[Dict[str, Any]], max_paths: int) -> Tuple[List[Dict[str, Any]], bool]:
    best: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for wf in workflows:
        key = tuple(n["symbol_id"] for n in wf["path"])
        prev = best.get(key)
        if prev is None or wf["aggregate_confidence"] > prev["aggregate_confidence"]:
            best[key] = wf
    ranked = sorted(
        best.values(),
        key=lambda w: (
            -w["aggregate_confidence"],
            w["total_call_depth"],
            w["length"],
        ),
    )
    truncated = len(ranked) > max_paths
    return ranked[:max_paths], truncated


# ─── Public API ──────────────────────────────────────────────────────────────


async def find_screen_workflows(
    driver: Any,
    database: str,
    *,
    project_id: str,
    node_a: str,
    node_b: Optional[str] = None,
    direction: str = "bidirectional",
    max_hops: int = DEFAULT_MAX_HOPS,
    max_paths: int = DEFAULT_MAX_PATHS,
    include_entry_function: bool = False,
    include_api_calls: bool = False,
) -> Dict[str, Any]:
    """Find ranked screen-only workflow paths.

    See module docstring for the contract. Returns a dict matching the shape
    documented in plans/hyper-graph/screen-workflow-finder.md.
    """

    if not project_id:
        raise ValueError("project_id is required")
    if not node_a:
        raise ValueError("node_a is required")

    max_hops = max(1, min(int(max_hops or DEFAULT_MAX_HOPS), HARD_MAX_HOPS))
    max_paths = max(1, min(int(max_paths or DEFAULT_MAX_PATHS), HARD_MAX_PATHS))

    direction = (direction or "bidirectional").lower()
    if node_b:
        mode = "pair"
    else:
        mode = "single"
        if direction not in {"inbound", "outbound", "bidirectional"}:
            raise ValueError(
                "direction must be one of: inbound, outbound, bidirectional"
            )

    uncertainties: List[str] = []

    cands_a, w_a = await _resolve_node(driver, database, project_id, node_a)
    uncertainties.extend(w_a)
    cands_b: List[Dict[str, Any]] = []
    if node_b:
        cands_b, w_b = await _resolve_node(driver, database, project_id, node_b)
        uncertainties.extend(w_b)

    resolved = {
        "node_a": {"input": node_a, "candidates": cands_a},
        "node_b": {"input": node_b, "candidates": cands_b} if node_b else None,
    }

    a_ids = [c["symbol_id"] for c in cands_a]
    b_ids = [c["symbol_id"] for c in cands_b]

    if not a_ids or (node_b and not b_ids):
        return {
            "mode": mode,
            "direction": "pair" if mode == "pair" else direction,
            "project_id": project_id,
            "resolved": resolved,
            "workflows": [],
            "uncertainties": uncertainties,
            "truncated": False,
        }

    workflows: List[Dict[str, Any]] = []
    query = _path_query_no_apoc(max_hops)

    async def _collect(src_ids: List[str], dst_ids: List[str], tag: str) -> None:
        rows = await _run_records(
            driver,
            database,
            query,
            {
                "a_ids": src_ids,
                "b_ids": dst_ids,
                "pid": project_id,
                "limit": max_paths * 3,  # over-fetch to dedupe later
            },
        )
        for row in rows:
            nodes = row["nodes"]
            rels = row["rels"]
            if not nodes or not rels:
                continue
            score = _score_workflow(nodes, rels)
            workflows.append(
                {
                    "path": nodes,
                    "edges": rels,
                    "direction": tag,
                    **score,
                }
            )

    if mode == "pair":
        await _collect(a_ids, b_ids, "pair")
    else:
        if direction in ("outbound", "bidirectional"):
            # outbound from node_a to any reachable screen — use node_a on both
            # sides of the query with end anchored to "any screen in project",
            # implemented by making $b_ids the full screen set is too expensive;
            # instead expand via a variable-length query anchored only on the
            # source.
            await _collect_open_ended(
                driver,
                database,
                query_anchor="source",
                anchor_ids=a_ids,
                project_id=project_id,
                max_hops=max_hops,
                max_paths=max_paths,
                sink=workflows,
                tag="outbound",
            )
        if direction in ("inbound", "bidirectional"):
            await _collect_open_ended(
                driver,
                database,
                query_anchor="target",
                anchor_ids=a_ids,
                project_id=project_id,
                max_hops=max_hops,
                max_paths=max_paths,
                sink=workflows,
                tag="inbound",
            )

    ranked, truncated = _dedupe_and_rank(workflows, max_paths)

    # Reserved enrichers — currently no-op to keep the API surface stable.
    if include_entry_function or include_api_calls:
        for wf in ranked:
            wf.setdefault("entry_function", None)
            wf.setdefault("api_calls", [])

    return {
        "mode": mode,
        "direction": "pair" if mode == "pair" else direction,
        "project_id": project_id,
        "resolved": resolved,
        "workflows": ranked,
        "uncertainties": uncertainties,
        "truncated": truncated,
    }


async def _collect_open_ended(
    driver: Any,
    database: str,
    *,
    query_anchor: str,
    anchor_ids: List[str],
    project_id: str,
    max_hops: int,
    max_paths: int,
    sink: List[Dict[str, Any]],
    tag: str,
) -> None:
    """Run an open-ended screen-only walk anchored on source or target."""

    if query_anchor == "source":
        pattern = "(a)-[:NAVIGATE*1..{h}]->(b)".format(h=max_hops)
        anchor_clause = "a.symbol_id IN $ids"
    else:
        pattern = "(a)-[:NAVIGATE*1..{h}]->(b)".format(h=max_hops)
        anchor_clause = "b.symbol_id IN $ids"

    query = f"""
    MATCH (a:Function), (b:Function)
    WHERE {anchor_clause}
      AND a.project_id = $pid AND b.project_id = $pid
      AND a.react_role = 'screen' AND b.react_role = 'screen'
    MATCH p = {pattern}
    WHERE ALL(n IN nodes(p) WHERE n.react_role = 'screen'
                               AND n.project_id = $pid)
      AND NONE(
            x IN nodes(p)
            WHERE size([y IN nodes(p) WHERE y.symbol_id = x.symbol_id]) > 1
          )
    RETURN [n IN nodes(p) | {{
              symbol_id: n.symbol_id,
              name:      n.name,
              file_path: n.file_path,
              react_role: n.react_role
           }}] AS nodes,
           [r IN relationships(p) | {{
              method:       r.method,
              target:       r.target,
              via:          r.via,
              trigger_type: r.trigger_type,
              guard:        r.guard,
              call_depth:   coalesce(r.call_depth, 0),
              confidence:   coalesce(r.confidence, 1.0)
           }}] AS rels,
           length(p) AS length
    LIMIT $limit
    """

    rows = await _run_records(
        driver,
        database,
        query,
        {"ids": anchor_ids, "pid": project_id, "limit": max_paths * 3},
    )
    for row in rows:
        nodes = row["nodes"]
        rels = row["rels"]
        if not nodes or not rels:
            continue
        score = _score_workflow(nodes, rels)
        sink.append(
            {
                "path": nodes,
                "edges": rels,
                "direction": tag,
                **score,
            }
        )


__all__ = [
    "find_screen_workflows",
    "DEFAULT_MAX_HOPS",
    "DEFAULT_MAX_PATHS",
]
