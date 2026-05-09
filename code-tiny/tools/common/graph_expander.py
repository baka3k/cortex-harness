"""
graph_expander.py
─────────────────
Graph-aware seed expansion and proximity scoring via Neo4j.

Given a set of seed node IDs (from initial vector/keyword retrieval), this
module expands the candidate set by following call-graph and type-usage edges
in Neo4j, then computes a **graph_proximity** score in [0, 1] for each
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
when a Neo4j AsyncDriver is provided.  Pass a sync ``neo4j.Driver`` for
synchronous access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Default relationship types that represent functional dependencies in the
# hyper-graph code graph schema.
DEFAULT_REL_TYPES: List[str] = ["CALLS", "USES_TYPE", "REFERENCES", "INHERITS"]

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
MATCH (seed)-[:{rel}*1..{depth}]-(neighbor)
WHERE neighbor.id <> sid
RETURN DISTINCT
    neighbor.id            AS node_id,
    neighbor.name          AS name,
    coalesce(neighbor.qualified_name, neighbor.name) AS qualified_name,
    coalesce(labels(neighbor)[0], 'Node') AS kind,
    coalesce(neighbor.file_path, '')       AS file_path,
    neighbor.doc_confidence                AS doc_confidence,
    neighbor.intent                        AS intent,
    neighbor.exported                      AS exported,
    neighbor.side_effect                   AS side_effect
LIMIT $limit
"""


def _shortest_hop_cypher(rel_types: List[str], depth: int) -> str:
    rel = "|".join(rel_types) if rel_types else "CALLS|USES_TYPE"
    return f"""
UNWIND $seed_ids AS sid
MATCH (seed {{id: sid}})
MATCH p = shortestPath((seed)-[:{rel}*1..{depth}]-(target {{id: $target_id}}))
RETURN length(p) AS hops
ORDER BY hops
LIMIT 1
"""


# ─────────────────────────────────────────────────────────────
# GraphExpander
# ─────────────────────────────────────────────────────────────


class GraphExpander:
    """
    Expand a set of seed node IDs via Neo4j call-graph relationships and
    compute graph_proximity scores for all discovered neighbors.

    Parameters
    ──────────
    driver   : neo4j.Driver (sync)  *or*  neo4j.AsyncDriver (async)
    database : Neo4j database name (default: "neo4j")
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
    ) -> List[GraphNode]:
        """
        Expand *seed_ids* up to *depth* hops and return all discovered nodes.

        Seeds themselves are included as hop-0 nodes when ``include_seeds=True``.
        """
        rels = rel_types or DEFAULT_REL_TYPES
        nodes: List[GraphNode] = []

        if include_seeds:
            seed_nodes = self._fetch_seeds(seed_ids)
            nodes.extend(seed_nodes)

        if not seed_ids:
            return nodes

        cypher = _expand_cypher(rels, depth)
        records = self._run_query(cypher, {"seed_ids": seed_ids, "limit": limit})

        seen: Set[str] = {n.node_id for n in nodes}
        hop_map = self._compute_hop_distances(seed_ids, records)

        for row in records:
            nid = str(row.get("node_id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            hops = hop_map.get(nid, depth)
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
                },
            ))

        return nodes

    def proximity_score(
        self,
        seed_ids: List[str],
        candidate_id: str,
        depth: int = 4,
        rel_types: Optional[List[str]] = None,
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
        records = self._run_query(cypher, {"seed_ids": seed_ids, "target_id": candidate_id})

        if not records:
            return 0.0
        hops = int(records[0].get("hops") or depth)
        return _hop_proximity(hops)

    # ── internal helpers ──────────────────────────────────────

    def _fetch_seeds(self, seed_ids: List[str]) -> List[GraphNode]:
        if not seed_ids:
            return []
        cypher = """
UNWIND $seed_ids AS sid
MATCH (n {id: sid})
RETURN
    n.id                                   AS node_id,
    n.name                                 AS name,
    coalesce(n.qualified_name, n.name)     AS qualified_name,
    coalesce(labels(n)[0], 'Node')         AS kind,
    coalesce(n.file_path, '')              AS file_path,
    n.doc_confidence                       AS doc_confidence,
    n.intent                               AS intent,
    n.exported                             AS exported,
    n.side_effect                          AS side_effect
"""
        records = self._run_query(cypher, {"seed_ids": seed_ids})
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
                },
            ))
        return nodes

    def _compute_hop_distances(
        self,
        seed_ids: List[str],
        records: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Use Neo4j shortestPath to compute exact hop distances for returned
        neighbor node IDs.

        For simplicity we approximate by assigning all returned nodes a
        distance of 1 (direct neighbor) since the expand query already returns
        nodes reachable within ``depth`` hops but doesn't return the actual
        distance per node.  Callers that need exact distances can call
        ``proximity_score`` per candidate.

        A future enhancement can use APOC ``shortestPath`` or a Cypher-level
        DISTINCT on hop lengths.
        """
        # Minimal implementation: assign hop=1 for all expanded candidates.
        # Override with exact values if Neo4j APOC is available.
        return {str(r.get("node_id") or ""): 1 for r in records if r.get("node_id")}

    def _run_query(
        self,
        cypher: str,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return rows as plain dicts."""
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(cypher, params)
                return [dict(record) for record in result]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GraphExpander] Neo4j query failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────
# Async wrapper (thin delegation)
# ─────────────────────────────────────────────────────────────


class AsyncGraphExpander:
    """
    Async variant of GraphExpander for use in async MCP server contexts.

    Wraps an ``neo4j.AsyncDriver``.
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
    ) -> List[GraphNode]:
        rels = rel_types or DEFAULT_REL_TYPES
        nodes: List[GraphNode] = []

        if include_seeds:
            seed_nodes = await self._fetch_seeds(seed_ids)
            nodes.extend(seed_nodes)

        if not seed_ids:
            return nodes

        cypher = _expand_cypher(rels, depth)
        records = await self._run_query(cypher, {"seed_ids": seed_ids, "limit": limit})

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
                hop_distance=1,
                graph_proximity=_hop_proximity(1),
                properties={
                    "doc_confidence": float(row.get("doc_confidence") or 0.0),
                    "intent":         str(row.get("intent") or ""),
                    "exported":       bool(row.get("exported") or False),
                    "side_effect":    bool(row.get("side_effect") or False),
                },
            ))
        return nodes

    async def proximity_score(
        self,
        seed_ids: List[str],
        candidate_id: str,
        depth: int = 4,
        rel_types: Optional[List[str]] = None,
    ) -> float:
        if candidate_id in seed_ids:
            return 1.0
        rels = rel_types or DEFAULT_REL_TYPES
        cypher = _shortest_hop_cypher(rels, depth)
        records = await self._run_query(cypher, {"seed_ids": seed_ids, "target_id": candidate_id})
        if not records:
            return 0.0
        hops = int(records[0].get("hops") or depth)
        return _hop_proximity(hops)

    async def _fetch_seeds(self, seed_ids: List[str]) -> List[GraphNode]:
        if not seed_ids:
            return []
        cypher = """
UNWIND $seed_ids AS sid
MATCH (n {id: sid})
RETURN
    n.id                                   AS node_id,
    n.name                                 AS name,
    coalesce(n.qualified_name, n.name)     AS qualified_name,
    coalesce(labels(n)[0], 'Node')         AS kind,
    coalesce(n.file_path, '')              AS file_path,
    n.doc_confidence                       AS doc_confidence,
    n.intent                               AS intent,
    n.exported                             AS exported,
    n.side_effect                          AS side_effect
"""
        records = await self._run_query(cypher, {"seed_ids": seed_ids})
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
                },
            ))
        return nodes

    async def _run_query(
        self,
        cypher: str,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(cypher, params)
                return [dict(record) async for record in result]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AsyncGraphExpander] Neo4j query failed: %s", exc)
            return []
