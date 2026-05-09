"""
intelligent_retrieval.py
────────────────────────
Context-aware intelligent retrieval engine for hyper-graph code nodes.

Extends the existing semantic (Qdrant vector) + keyword (Neo4j text) search
with four new signals:
  - graph_proximity  — call-graph / type-usage neighborhood from Neo4j
  - freshness        — exponential decay based on last_updated / dirty flag
  - semantic_confidence — doc_confidence from SemanticInferenceEngine stored
                          in Qdrant payload
  - usage_importance — call-site usage signal stored in Qdrant payload

Architecture (pipeline per query)
───────────────────────────────────────────────────────────────────────
  1. classify_query   → intent string + weight profile
  2. initial_retrieval → top-N seeds from Qdrant (semantic) + Neo4j (keyword)
  3. graph_expansion  → neighbor nodes from Neo4j call graph
  4. signal_collection → normalise semantic, keyword, graph, freshness,
                          confidence, usage per candidate
  5. score_all        → RetrievalScorer.score_all() → ScoredResult list
  6. top_k_rank       → return top-K

Public API
───────────────────────────────────────────────────────────────────────
  from tools.common.intelligent_retrieval import IntelligentRetrievalEngine

  engine = IntelligentRetrievalEngine(
      qdrant_url="http://localhost:6333",
      neo4j_driver=driver,          # sync neo4j.Driver
      embedder=my_embedder,         # callable: str → List[float]
      collection="ts_functions",
  )

  results = engine.search(
      query    = "who calls validateToken",
      top_k    = 10,
      debug    = True,
      expand_graph = True,
  )
  # → List[ScoredResult]

  # Override weights for a single query:
  results = engine.search(
      query           = "similar to getUserById",
      weight_override = {"semantic": 0.60, "graph": 0.10},
  )
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

from tools.common.graph_expander import GraphExpander, GraphNode
from tools.common.query_intent_classifier import classify_query, get_weight_profile
from tools.common.retrieval_scorer import RetrievalScorer, ScoredResult
from tools.common.signal_normalizer import (
    min_max_normalize,
    normalize_signals,
    freshness_from_dirty,
    freshness_from_elapsed,
)
try:
    from tools.common.bm25_ranker import BM25Ranker as _BM25Ranker
except ImportError:
    _BM25Ranker = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────

DEFAULT_QDRANT_URL   = "http://localhost:6333"
DEFAULT_SEED_K       = 20   # initial Qdrant retrieval count
DEFAULT_EXPAND_DEPTH = 2    # Neo4j expansion depth
DEFAULT_EXPAND_LIMIT = 50   # max graph-expanded candidates
DEFAULT_TOP_K        = 10

# ─────────────────────────────────────────────────────────────
# Qdrant HTTP helpers (no qdrant_client dependency)
# ─────────────────────────────────────────────────────────────


def _qdrant_search(
    qdrant_url: str,
    collection: str,
    vector: List[float],
    top_k: int,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Run a Qdrant vector search via the REST API.

    Returns a list of hit dicts:
      {"id": …, "score": …, "payload": {…}}
    """
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/search"
    body = {"vector": vector, "limit": top_k, "with_payload": True}
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] Qdrant search failed: %s", exc)
        return []


def _qdrant_search_by_ids(
    qdrant_url: str,
    collection: str,
    ids: List[str],
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """Retrieve specific Qdrant points by ID (for graph-expanded nodes)."""
    if not ids:
        return []
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points"
    body = {"ids": ids, "with_payload": True}
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] Qdrant id-fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────
# Neo4j keyword search helper
# ─────────────────────────────────────────────────────────────


def _neo4j_keyword_search(
    neo4j_driver: Any,
    query: str,
    database: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Text search in Neo4j.  Returns a list of node property dicts.
    """
    if neo4j_driver is None:
        return []
    tokens = [t.strip().lower() for t in query.split() if t.strip()]
    if not tokens:
        return []
    cypher = """
MATCH (n)
WHERE any(q IN $qs WHERE
    toLower(coalesce(n.name, '')) CONTAINS q
    OR toLower(coalesce(n.qualified_name, '')) CONTAINS q
    OR toLower(coalesce(n.comment, '')) CONTAINS q
)
RETURN n LIMIT $limit
"""
    try:
        with neo4j_driver.session(database=database) as session:
            result = session.run(cypher, {"qs": tokens, "limit": limit})
            return [dict(record["n"]) for record in result if record.get("n")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] Neo4j keyword search failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────
# Candidate dict builders
# ─────────────────────────────────────────────────────────────


def _qdrant_hit_to_candidate(
    hit: Dict[str, Any],
    semantic_score: float,
) -> Dict[str, Any]:
    """Convert a Qdrant search hit into a flat candidate dict."""
    payload = hit.get("payload") or {}
    return {
        # Identity
        "node_id":        str(payload.get("symbol_id") or hit.get("id") or ""),
        "name":           str(payload.get("name") or ""),
        "qualified_name": str(payload.get("qualified_name") or ""),
        "kind":           str(payload.get("kind") or ""),
        "file_path":      str(payload.get("file_path") or ""),
        # Signals (raw)
        "semantic":       float(semantic_score),
        "keyword":        0.0,
        "graph":          0.0,
        "freshness":      0.0,
        "confidence":     float(payload.get("doc_confidence") or 0.0),
        "usage":          float(payload.get("signals", {}).get("usage", 0.0)
                                if isinstance(payload.get("signals"), dict) else 0.0),
        # Metadata
        "intent":         str(payload.get("intent") or ""),
        "exported":       bool(payload.get("exported") or False),
        "side_effect":    bool(payload.get("side_effect") or False),
        "return_type":    str(payload.get("return_type") or ""),
        "project_id":     str(payload.get("project_id") or ""),
        "language":       str(payload.get("language") or ""),
        # Source tracking
        "_source":        "qdrant",
    }


def _neo4j_node_to_candidate(node: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Neo4j node property dict into a flat candidate dict."""
    return {
        "node_id":        str(node.get("id") or ""),
        "name":           str(node.get("name") or ""),
        "qualified_name": str(node.get("qualified_name") or ""),
        "kind":           str(node.get("kind") or ""),
        "file_path":      str(node.get("file_path") or ""),
        "semantic":       0.0,
        "keyword":        1.0,  # This node matched by name/comment — raw 1.0, normalized later
        "graph":          0.0,
        "freshness":      0.0,
        "confidence":     float(node.get("doc_confidence") or 0.0),
        "usage":          0.0,
        "intent":         str(node.get("intent") or ""),
        "exported":       bool(node.get("exported") or False),
        "side_effect":    bool(node.get("side_effect") or False),
        "return_type":    str(node.get("return_type") or ""),
        "project_id":     str(node.get("project_id") or ""),
        "language":       str(node.get("language") or ""),
        "_source":        "neo4j_keyword",
    }


def _graph_node_to_candidate(gnode: GraphNode) -> Dict[str, Any]:
    """Convert a GraphNode from graph expansion into a flat candidate dict."""
    props = gnode.properties
    return {
        "node_id":        gnode.node_id,
        "name":           gnode.name,
        "qualified_name": gnode.qualified_name,
        "kind":           gnode.kind,
        "file_path":      gnode.file_path,
        "semantic":       0.0,
        "keyword":        0.0,
        "graph":          gnode.graph_proximity,
        "freshness":      0.0,
        "confidence":     float(props.get("doc_confidence") or 0.0),
        "usage":          0.0,
        "intent":         str(props.get("intent") or ""),
        "exported":       bool(props.get("exported") or False),
        "side_effect":    bool(props.get("side_effect") or False),
        "return_type":    "",
        "project_id":     "",
        "language":       "",
        "_source":        "graph_expansion",
    }


# ─────────────────────────────────────────────────────────────
# IntelligentRetrievalEngine
# ─────────────────────────────────────────────────────────────


class IntelligentRetrievalEngine:
    """
    Context-aware multi-signal retrieval engine.

    Parameters
    ──────────
    qdrant_url   : Base URL for Qdrant REST API.
    collection   : Qdrant collection name.
    embedder     : Callable ``str → List[float]`` for query embedding.
    neo4j_driver : Optional sync ``neo4j.Driver``.  If None, graph expansion
                   and keyword search via Neo4j are skipped.
    database     : Neo4j database name.
    freshness_map : Optional mapping of node_id → last_updated ISO string.
                   If not provided, freshness defaults to 0.5 (neutral).
    dirty_set     : Optional set of node_ids known to be dirty.
    seed_k        : Number of initial Qdrant results to use as seeds.
    expand_depth  : Graph expansion hop depth.
    expand_limit  : Maximum graph-expanded candidates.
    """

    def __init__(
        self,
        qdrant_url: str = DEFAULT_QDRANT_URL,
        collection: str = "",
        embedder: Optional[Callable[[str], List[float]]] = None,
        neo4j_driver: Optional[Any] = None,
        database: str = "neo4j",
        freshness_map: Optional[Dict[str, str]] = None,
        dirty_set: Optional[set] = None,
        seed_k: int = DEFAULT_SEED_K,
        expand_depth: int = DEFAULT_EXPAND_DEPTH,
        expand_limit: int = DEFAULT_EXPAND_LIMIT,
        bm25_ranker: Optional[Any] = None,
        bm25_weight: float = 0.15,
    ) -> None:
        self._qdrant_url  = qdrant_url
        self._collection  = collection
        self._embedder    = embedder
        self._neo4j       = neo4j_driver
        self._database    = database
        self._freshness   = freshness_map or {}
        self._dirty       = dirty_set or set()
        self._seed_k      = seed_k
        self._expander    = GraphExpander(neo4j_driver, database) if neo4j_driver else None
        self._expand_depth = expand_depth
        self._expand_limit = expand_limit
        self._bm25_ranker  = bm25_ranker
        self._bm25_weight  = bm25_weight

    # ── public search ─────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        *,
        debug: bool = False,
        expand_graph: bool = True,
        weight_override: Optional[Dict[str, float]] = None,
        collection: Optional[str] = None,
    ) -> List[ScoredResult]:
        """
        Execute context-aware retrieval for *query*.

        Parameters
        ──────────
        query          : Natural-language search string.
        top_k          : Number of results to return.
        debug          : When True, each ScoredResult includes per-signal
                         explanation and weighted contributions.
        expand_graph   : When True and a Neo4j driver is configured, expand
                         top seeds through the call graph.
        weight_override : Override weight dict.  Only the provided keys are
                          overridden; the intent-based profile fills the rest.
        collection     : Override the default Qdrant collection.
        """
        q = (query or "").strip()
        if not q:
            return []

        t0 = time.perf_counter()

        # 1. Classify query → weight profile
        intent  = classify_query(q)
        weights = get_weight_profile(intent)
        if weight_override:
            weights.update(weight_override)
        logger.debug("[IR] query=%r intent=%s weights=%s", q, intent, weights)

        # 2. Initial retrieval
        col = collection or self._collection
        seeds_qdrant = self._retrieve_qdrant(q, col, self._seed_k)
        seeds_kw     = self._retrieve_keyword(q, self._seed_k)

        # Merge into candidate dict  node_id → candidate
        candidates: Dict[str, Dict[str, Any]] = {}
        for c in seeds_qdrant:
            nid = c["node_id"]
            if nid:
                candidates[nid] = c
        for c in seeds_kw:
            nid = c["node_id"]
            if nid and nid not in candidates:
                candidates[nid] = c
            elif nid in candidates:
                # Merge keyword signal into existing Qdrant candidate
                candidates[nid]["keyword"] = max(candidates[nid].get("keyword", 0.0), 1.0)

        seed_ids = list(candidates.keys())

        # 2b. BM25 signal injection (keyword precision boost)
        if self._bm25_ranker is not None:
            bm25_scores = self._bm25_ranker.score(q)
            for nid, bm25_score in bm25_scores.items():
                if nid in candidates:
                    candidates[nid]["bm25"] = bm25_score
                else:
                    # BM25 hit not in Qdrant/Neo4j seeds — add as candidate
                    candidates[nid] = {"node_id": nid, "bm25": bm25_score}

        # 3. Graph expansion
        if expand_graph and self._expander and seed_ids:
            graph_nodes = self._expander.expand(
                seed_ids  = seed_ids[:min(len(seed_ids), 10)],  # top-10 seeds only
                depth     = self._expand_depth,
                limit     = self._expand_limit,
                include_seeds = False,
            )
            for gnode in graph_nodes:
                nid = gnode.node_id
                if nid and nid not in candidates:
                    candidates[nid] = _graph_node_to_candidate(gnode)
                elif nid in candidates:
                    # Update graph proximity on existing candidate
                    existing = candidates[nid].get("graph", 0.0)
                    candidates[nid]["graph"] = max(existing, gnode.graph_proximity)

        # 4. Signal collection & normalization
        candidate_list = list(candidates.values())
        self._inject_freshness(candidate_list)
        candidate_list = self._normalize_batch_signals(candidate_list)

        # 5. Score and rank — inject BM25 weight if active
        scorer_weights = dict(weights)
        if self._bm25_ranker is not None and any(c.get("bm25", 0) > 0 for c in candidate_list):
            scorer_weights["bm25"] = self._bm25_weight
        scorer  = RetrievalScorer(weights=scorer_weights)
        results = scorer.score_all(candidate_list, top_k=top_k, debug=debug)

        elapsed = time.perf_counter() - t0
        logger.info(
            "[IR] query=%r intent=%s candidates=%d top_k=%d elapsed=%.3fs",
            q, intent, len(candidate_list), len(results), elapsed,
        )

        if debug:
            for r in results:
                r.explanation["query_intent"] = intent
                r.explanation["weights_used"] = weights

        return results

    # ── internal pipeline steps ───────────────────────────────

    def _retrieve_qdrant(
        self,
        query: str,
        collection: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Embed query and run Qdrant vector search."""
        if not self._embedder or not collection:
            return []
        try:
            vector = self._embedder(query)
            hits   = _qdrant_search(self._qdrant_url, collection, vector, top_k)
            return [_qdrant_hit_to_candidate(h, float(h.get("score") or 0.0)) for h in hits]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IR] Qdrant retrieval error: %s", exc)
            return []

    def _retrieve_keyword(
        self,
        query: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Run Neo4j keyword search."""
        if not self._neo4j:
            return []
        nodes = _neo4j_keyword_search(self._neo4j, query, self._database, top_k)
        return [_neo4j_node_to_candidate(n) for n in nodes]

    def _inject_freshness(self, candidates: List[Dict[str, Any]]) -> None:
        """Compute and inject freshness scores in-place."""
        for c in candidates:
            nid = c.get("node_id", "")
            is_dirty     = nid in self._dirty
            last_updated = self._freshness.get(nid, "")
            c["freshness"] = freshness_from_dirty(is_dirty, last_updated)

    def _normalize_batch_signals(
        self,
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Normalize semantic and keyword scores across all candidates.

        semantic/keyword are normalized relative to the batch extremes (min-max).
        graph, freshness, confidence, usage are already in [0, 1].
        """
        # Normalize semantic across batch
        sem_vals = [float(c.get("semantic") or 0.0) for c in candidates]
        kw_vals  = [float(c.get("keyword")  or 0.0) for c in candidates]

        sem_normed = min_max_normalize(sem_vals)
        kw_normed  = min_max_normalize(kw_vals)

        for c, s, k in zip(candidates, sem_normed, kw_normed):
            c["semantic"] = round(s, 6)
            c["keyword"]  = round(k, 6)
            # Clamp already-normalized signals
            c["graph"]      = min(1.0, max(0.0, float(c.get("graph")      or 0.0)))
            c["freshness"]  = min(1.0, max(0.0, float(c.get("freshness")  or 0.0)))
            c["confidence"] = min(1.0, max(0.0, float(c.get("confidence") or 0.0)))
            c["usage"]      = min(1.0, max(0.0, float(c.get("usage")      or 0.0)))
            c["bm25"]       = min(1.0, max(0.0, float(c.get("bm25")       or 0.0)))

        return candidates

    # ── configuration helpers ─────────────────────────────────

    def update_freshness(
        self,
        freshness_map: Dict[str, str],
        dirty_set: Optional[set] = None,
    ) -> None:
        """Update the freshness map and dirty set (e.g. after an incremental sync)."""
        self._freshness.update(freshness_map)
        if dirty_set is not None:
            self._dirty = dirty_set
