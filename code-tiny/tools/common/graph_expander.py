"""
graph_expander.py
─────────────────
Graph-aware seed expansion and proximity scoring via the configured graph DB.

Given a set of seed node IDs (from initial vector/keyword retrieval), this
module expands the candidate set by following call-graph and type-usage edges
in the graph database, then computes a **graph_proximity** score in [0, 1] for each
candidate.

Public API
───────────────────────────────────────────────────────
  from tools.common.graph_expander import GraphExpander

  expander = GraphExpander(driver, database="neo4j")

  # Expand seed node IDs to neighboring candidates
  nodes = expander.expand(
      seed_ids=["proj:file.ts:myFunc"],
      depth=2,
      rel_types=["CALLS", "USES_TYPE"],
      limit=50,
  )
  # → List[GraphNode]

  # Compute proximity score for a single candidate
  score = expander.proximity_score(
      seed_ids=["proj:file.ts:myFunc"],
      candidate_id="proj:file.ts:helperFunc",
  )
  # → float in [0, 1]

Design
──────
Proximity is inversely proportional to shortest hop-distance from any seed:
  - distance 0 (is a seed)  → 1.0
  - distance 1              → 0.80
  - distance 2              → 0.60
  - distance n (n ≥ depth)  → decay formula: max(0, 1 - n * 0.20)

The module is sync-first and supports async execution via a thin async wrapper
when an async-compatible graph driver is provided.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from tools.common.project_scope import (
    normalize_project_id,
    prepare_project_scope_parameters,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Default relationship types that represent functional dependencies in the
# hyper-graph code graph schema.
DEFAULT_REL_TYPES: List[str] = [
    "CALLS", "USES_TYPE", "REFERENCES", "INHERITS", "ALIASES", "ALIAS_OF",
]

# Hop-count to proximity score decay table.
# Entries beyond the table end use the extrapolation formula below.
_HOP_DECAY: Dict[int, float] = {
    0: 1.00,
    1: 0.80,
    2: 0.60,
    3: 0.40,
    4: 0.20,
}
_HOP_DECAY_STEP = 0.20  # score decreases by this per extra hop


def _hop_proximity(hops: int) -> float:
    if hops in _HOP_DECAY:
        return _HOP_DECAY[hops]
    return max(0.0, 1.0 - hops * _HOP_DECAY_STEP)


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────


@dataclass
class GraphNode:
    """A code node returned by graph expansion."""
    node_id: str
    name: str
    qualified_name: str
    kind: str                       # Function, Class, Method, …
    file_path: str
    hop_distance: int               # 0 = seed, 1 = direct neighbor, …
    graph_proximity: float          # [0, 1]
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":        self.node_id,
            "name":           self.name,
            "qualified_name": self.qualified_name,
            "kind":           self.kind,
            "file_path":      self.file_path,
            "hop_distance":   self.hop_distance,
            "graph_proximity": self.graph_proximity,
            **self.properties,
        }


# ─────────────────────────────────────────────────────────────
# Cypher helpers
# ─────────────────────────────────────────────────────────────


def _rel_pattern(rel_types: List[str], depth: int, direction: str = "both") -> str:
    """Build a Cypher variable-length relationship pattern string."""
    rel = "|".join(rel_types) if rel_types else "CALLS|USES_TYPE"
    if direction == "out":
        arrow = f"-[:{rel}*1..{depth}]->"
    elif direction == "in":
        arrow = f"<-[:{rel}*1..{depth}]-"
    else:
        arrow = f"-[:{rel}*1..{depth}]-"
    return arrow


def _expand_cypher(rel_types: List[str], depth: int) -> str:
    rel = "|".join(rel_types) if rel_types else "CALLS|USES_TYPE"
    return f"""
UNWIND $seed_ids AS sid
MATCH (seed {{id: sid}})
WHERE ($project_id IS NULL OR seed.project_id_normalized = $project_id_normalized)
    MATCH p = (seed)-[:{rel}*1..{depth}]-(neighbor)
    WHERE neighbor.id <> sid
      AND ($project_id IS NULL OR neighbor.project_id_normalized = $project_id_normalized)
    WITH neighbor, min(length(p)) AS hops, collect(DISTINCT sid) AS seed_ids
    RETURN
        seed_ids,
        seed_ids[0]             AS seed_id,
        hops,
        neighbor.id            AS node_id,
    neighbor.name          AS name,
    coalesce(neighbor.qualified_name, neighbor.name) AS qualified_name,
    coalesce(labels(neighbor)[0], 'Node') AS kind,
    coalesce(neighbor.file_path, '')       AS file_path,
    neighbor.doc_confidence                AS doc_confidence,
    neighbor.intent                        AS intent,
    neighbor.exported                      AS exported,
        neighbor.side_effect                   AS side_effect,
        neighbor.project_id                    AS project_id,
        neighbor.target_name                   AS target_name,
        neighbor.signature                     AS signature,
        neighbor.language                      AS language,
        neighbor.framework                     AS framework,
        neighbor.resolution_status             AS resolution_status,
        neighbor.start_line                    AS start_line,
        neighbor.end_line                      AS end_line
    ORDER BY hops ASC
LIMIT $limit
"""


def _shortest_hop_cypher(rel_types: List[str], depth: int) -> str:
    rel = "|".join(rel_types) if rel_types else "CALLS|USES_TYPE"
    return f"""
UNWIND $seed_ids AS sid
MATCH (seed {{id: sid}})
WHERE ($project_id IS NULL OR seed.project_id_normalized = $project_id_normalized)
MATCH p = (seed)-[:{rel}*1..{depth}]-(target {{id: $target_id}})
WHERE ($project_id IS NULL OR target.project_id_normalized = $project_id_normalized)
RETURN length(p) AS hops
ORDER BY hops
LIMIT 1
"""


# ─────────────────────────────────────────────────────────────
# GraphExpander
# ─────────────────────────────────────────────────────────────


class GraphExpander:
    """
    Expand a set of seed node IDs via graph call-graph relationships and
    compute graph_proximity scores for all discovered neighbors.

    Parameters
    ──────────
    driver   : GraphDriver or Neo4j-style driver.
    database : Graph database name (default: "neo4j")
    """

    def __init__(
        self,
        driver: Any,
        database: str = "neo4j",
    ) -> None:
        self._driver = driver
        self._database = database

    # ── sync interface ────────────────────────────────────────

    def expand(
        self,
        seed_ids: List[str],
        depth: int = 2,
        rel_types: Optional[List[str]] = None,
        limit: int = 50,
        include_seeds: bool = True,
        project_id: Optional[str] = None,
    ) -> List[GraphNode]:
        """
        Expand *seed_ids* up to *depth* hops and return all discovered nodes.

        Seeds themselves are included as hop-0 nodes when ``include_seeds=True``.
        """
        rels = rel_types or DEFAULT_REL_TYPES
        nodes: List[GraphNode] = []

        if include_seeds:
            seed_nodes = self._fetch_seeds(seed_ids, project_id=project_id)
            nodes.extend(seed_nodes)

        if not seed_ids:
            return nodes

        cypher = _expand_cypher(rels, depth)
        records = self._run_query(cypher, {
            "seed_ids": seed_ids,
            "limit": limit,
            "project_id": normalize_project_id(project_id),
        })

        seen: Set[str] = {n.node_id for n in nodes}
        for row in records:
            nid = str(row.get("node_id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            hops = max(1, int(row.get("hops") or depth))
            nodes.append(GraphNode(
                node_id=nid,
                name=str(row.get("name") or ""),
                qualified_name=str(row.get("qualified_name") or ""),
                kind=str(row.get("kind") or "Node"),
                file_path=str(row.get("file_path") or ""),
                hop_distance=hops,
                graph_proximity=_hop_proximity(hops),
                properties={
                    "doc_confidence": float(row.get("doc_confidence") or 0.0),
                    "intent":         str(row.get("intent") or ""),
                    "exported":       bool(row.get("exported") or False),
                    "side_effect":    bool(row.get("side_effect") or False),
                    "project_id":     str(row.get("project_id") or ""),
                    "seed_id":        str(row.get("seed_id") or ""),
                    "seed_ids":       list(row.get("seed_ids") or []),
                    "target_name":    str(row.get("target_name") or ""),
                    "signature":      str(row.get("signature") or ""),
                    "language":       str(row.get("language") or ""),
                    "framework":      str(row.get("framework") or ""),
                    "resolution_status": str(row.get("resolution_status") or ""),
                    "start_line":     row.get("start_line"),
                    "end_line":       row.get("end_line"),
                },
            ))

        return nodes

    def proximity_score(
        self,
        seed_ids: List[str],
        candidate_id: str,
        depth: int = 4,
        rel_types: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> float:
        """
        Return a graph_proximity score in [0, 1] for *candidate_id* relative
        to the set of seed IDs.

        Returns 1.0 when *candidate_id* is itself a seed, 0.0 when unreachable.
        """
        if candidate_id in seed_ids:
            return 1.0

        rels = rel_types or DEFAULT_REL_TYPES
        cypher = _shortest_hop_cypher(rels, depth)
        records = self._run_query(cypher, {
            "seed_ids": seed_ids,
            "target_id": candidate_id,
            "project_id": normalize_project_id(project_id),
        })

        if not records:
            return 0.0
        hops = int(records[0].get("hops") or depth)
        return _hop_proximity(hops)

    # ── internal helpers ──────────────────────────────────────

    def _fetch_seeds(
        self,
        seed_ids: List[str],
        project_id: Optional[str] = None,
    ) -> List[GraphNode]:
        if not seed_ids:
            return []
        cypher = """
UNWIND $seed_ids AS sid
MATCH (n {id: sid})
WHERE ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
RETURN
    n.id                                   AS node_id,
    n.name                                 AS name,
    coalesce(n.qualified_name, n.name)     AS qualified_name,
    coalesce(labels(n)[0], 'Node')         AS kind,
    coalesce(n.file_path, '')              AS file_path,
    n.doc_confidence                       AS doc_confidence,
    n.intent                               AS intent,
    n.exported                             AS exported,
    n.side_effect                          AS side_effect,
    n.project_id                           AS project_id
"""
        records = self._run_query(cypher, {
            "seed_ids": seed_ids,
            "project_id": normalize_project_id(project_id),
        })
        nodes: List[GraphNode] = []
        for row in records:
            nid = str(row.get("node_id") or "")
            if not nid:
                continue
            nodes.append(GraphNode(
                node_id=nid,
                name=str(row.get("name") or ""),
                qualified_name=str(row.get("qualified_name") or ""),
                kind=str(row.get("kind") or "Node"),
                file_path=str(row.get("file_path") or ""),
                hop_distance=0,
                graph_proximity=1.0,
                properties={
                    "doc_confidence": float(row.get("doc_confidence") or 0.0),
                    "intent":         str(row.get("intent") or ""),
                    "exported":       bool(row.get("exported") or False),
                    "side_effect":    bool(row.get("side_effect") or False),
                    "project_id":     str(row.get("project_id") or ""),
                },
            ))
        return nodes

    def _compute_hop_distances(
        self,
        seed_ids: List[str],
        records: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Use the shortest returned variable-length path to compute exact hop
        distances for returned neighbor node IDs across supported providers.

        The expansion query returns the minimum path length for every node.
        Keep a conservative fallback for legacy/fake drivers that omit it.
        """
        return {
            str(row.get("node_id") or ""): max(1, int(row.get("hops") or 1))
            for row in records
            if row.get("node_id")
        }

    def _run_query(
        self,
        cypher: str,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return rows as plain dicts."""
        params = prepare_project_scope_parameters(cypher, params)
        try:
            if hasattr(self._driver, "execute_query_sync"):
                records, _, _ = self._driver.execute_query_sync(cypher, params, self._database)
                return [dict(record) for record in records]
            with self._driver.session(database=self._database) as session:
                result = session.run(cypher, params)
                return [dict(record) for record in result]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GraphExpander] graph query failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────
# Async wrapper (thin delegation)
# ─────────────────────────────────────────────────────────────


class AsyncGraphExpander:
    """
    Async variant of GraphExpander for use in async MCP server contexts.

    Wraps an async graph driver or Neo4j AsyncDriver.
    """

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    async def expand(
        self,
        seed_ids: List[str],
        depth: int = 2,
        rel_types: Optional[List[str]] = None,
        limit: int = 50,
        include_seeds: bool = True,
        project_id: Optional[str] = None,
    ) -> List[GraphNode]:
        rels = rel_types or DEFAULT_REL_TYPES
        nodes: List[GraphNode] = []

        if include_seeds:
            seed_nodes = await self._fetch_seeds(seed_ids, project_id=project_id)
            nodes.extend(seed_nodes)

        if not seed_ids:
            return nodes

        cypher = _expand_cypher(rels, depth)
        records = await self._run_query(cypher, {
            "seed_ids": seed_ids,
            "limit": limit,
            "project_id": normalize_project_id(project_id),
        })

        seen: Set[str] = {n.node_id for n in nodes}
        for row in records:
            nid = str(row.get("node_id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            nodes.append(GraphNode(
                node_id=nid,
                name=str(row.get("name") or ""),
                qualified_name=str(row.get("qualified_name") or ""),
                kind=str(row.get("kind") or "Node"),
                file_path=str(row.get("file_path") or ""),
                hop_distance=max(1, int(row.get("hops") or 1)),
                graph_proximity=_hop_proximity(max(1, int(row.get("hops") or 1))),
                properties={
                    "doc_confidence": float(row.get("doc_confidence") or 0.0),
                    "intent":         str(row.get("intent") or ""),
                    "exported":       bool(row.get("exported") or False),
                    "side_effect":    bool(row.get("side_effect") or False),
                    "project_id":     str(row.get("project_id") or ""),
                    "seed_id":        str(row.get("seed_id") or ""),
                    "seed_ids":       list(row.get("seed_ids") or []),
                    "target_name":    str(row.get("target_name") or ""),
                    "signature":      str(row.get("signature") or ""),
                    "language":       str(row.get("language") or ""),
                    "framework":      str(row.get("framework") or ""),
                    "resolution_status": str(row.get("resolution_status") or ""),
                    "start_line":     row.get("start_line"),
                    "end_line":       row.get("end_line"),
                },
            ))
        return nodes

    async def proximity_score(
        self,
        seed_ids: List[str],
        candidate_id: str,
        depth: int = 4,
        rel_types: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> float:
        if candidate_id in seed_ids:
            return 1.0
        rels = rel_types or DEFAULT_REL_TYPES
        cypher = _shortest_hop_cypher(rels, depth)
        records = await self._run_query(cypher, {
            "seed_ids": seed_ids,
            "target_id": candidate_id,
            "project_id": normalize_project_id(project_id),
        })
        if not records:
            return 0.0
        hops = int(records[0].get("hops") or depth)
        return _hop_proximity(hops)

    async def _fetch_seeds(
        self,
        seed_ids: List[str],
        project_id: Optional[str] = None,
    ) -> List[GraphNode]:
        if not seed_ids:
            return []
        cypher = """
UNWIND $seed_ids AS sid
MATCH (n {id: sid})
WHERE ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
RETURN
    n.id                                   AS node_id,
    n.name                                 AS name,
    coalesce(n.qualified_name, n.name)     AS qualified_name,
    coalesce(labels(n)[0], 'Node')         AS kind,
    coalesce(n.file_path, '')              AS file_path,
    n.doc_confidence                       AS doc_confidence,
    n.intent                               AS intent,
    n.exported                             AS exported,
    n.side_effect                          AS side_effect,
    n.project_id                           AS project_id
"""
        records = await self._run_query(cypher, {
            "seed_ids": seed_ids,
            "project_id": normalize_project_id(project_id),
        })
        nodes: List[GraphNode] = []
        for row in records:
            nid = str(row.get("node_id") or "")
            if not nid:
                continue
            nodes.append(GraphNode(
                node_id=nid,
                name=str(row.get("name") or ""),
                qualified_name=str(row.get("qualified_name") or ""),
                kind=str(row.get("kind") or "Node"),
                file_path=str(row.get("file_path") or ""),
                hop_distance=0,
                graph_proximity=1.0,
                properties={
                    "doc_confidence": float(row.get("doc_confidence") or 0.0),
                    "intent":         str(row.get("intent") or ""),
                    "exported":       bool(row.get("exported") or False),
                    "side_effect":    bool(row.get("side_effect") or False),
                    "project_id":     str(row.get("project_id") or ""),
                },
            ))
        return nodes

    async def _run_query(
        self,
        cypher: str,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        params = prepare_project_scope_parameters(cypher, params)
        try:
            if hasattr(self._driver, "execute_query"):
                records, _, _ = await self._driver.execute_query(cypher, params, self._database)
                return [dict(record) for record in records]
            async with self._driver.session(database=self._database) as session:
                result = await session.run(cypher, params)
                return [dict(record) async for record in result]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AsyncGraphExpander] graph query failed: %s", exc)
            return []
