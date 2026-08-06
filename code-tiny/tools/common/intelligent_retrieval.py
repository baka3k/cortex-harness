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
  2. initial_retrieval → top-N seeds from Qdrant (semantic) + graph DB (keyword)
  3. graph_expansion  → neighbor nodes from the configured graph DB
  4. signal_collection → normalise semantic, keyword, graph, freshness,
                          confidence, usage per candidate
  5. score_all        → RetrievalScorer.score_all() → ScoredResult list
  6. top_k_rank       → return top-K

Public API
───────────────────────────────────────────────────────────────────────
  from tools.common.intelligent_retrieval import IntelligentRetrievalEngine

  engine = IntelligentRetrievalEngine(
      qdrant_url="/path/to/code-store",
      graph_driver=driver,          # GraphDriver / sync graph driver
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

from tools.common.graph_expander import GraphExpander, GraphNode
from tools.common.local_qdrant import (
    default_local_qdrant_path,
    get_code_qdrant_store,
    model_to_dict,
    query_points,
    vector_sizes,
)
from tools.common.project_scope import (
    matches_project_scope,
    normalize_project_id,
    prepare_project_scope_parameters,
    project_id_lookup_key,
    qdrant_project_filter,
)
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

DEFAULT_QDRANT_PATH  = default_local_qdrant_path()
DEFAULT_SEED_K       = 20   # initial Qdrant retrieval count
DEFAULT_EXPAND_DEPTH = 2    # Graph expansion depth
DEFAULT_EXPAND_LIMIT = 50   # max graph-expanded candidates
DEFAULT_TOP_K        = 10

# ─────────────────────────────────────────────────────────────
# Qdrant local-client helpers
# ─────────────────────────────────────────────────────────────

# Default named vector to query when a collection has named-vector
# layout but the caller didn't specify one. Matches
# ``hyper_pack_core.qdrant_search.DEFAULT_NAMED_VECTOR``.
_DEFAULT_NAMED_VECTOR = "semantic"

# Cache: (qdrant_url, collection) -> Optional[str]
#   None  → single unnamed vector layout
#   str   → use this named vector in queries
_VECTOR_LAYOUT_CACHE: Dict[tuple, Optional[str]] = {}


def _resolve_vector_layout(
    qdrant_url: str,
    collection: str,
    timeout: float = 10.0,
) -> Optional[str]:
    """Return the named vector to query, or ``None`` for single-vector.

    Cached per ``(qdrant_url, collection)``. Falls back to ``None`` on
    any error — the caller's ``_qdrant_search`` will then issue a
    legacy-style query that works for v1 collections; v2 misses will
    appear as empty results rather than crashes.
    """
    key = (qdrant_url, collection)
    if key in _VECTOR_LAYOUT_CACHE:
        return _VECTOR_LAYOUT_CACHE[key]

    try:
        del timeout
        sizes = vector_sizes(get_code_qdrant_store(qdrant_url).get_collection_info(collection))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[intelligent_retrieval] get_collection(%s) failed: %s",
            collection,
            exc,
        )
        _VECTOR_LAYOUT_CACHE[key] = None
        return None

    chosen: Optional[str]
    if set(sizes) != {"default"} and sizes:
        if _DEFAULT_NAMED_VECTOR in sizes:
            chosen = _DEFAULT_NAMED_VECTOR
        else:
            chosen = sorted(sizes)[0]
    else:
        chosen = None

    _VECTOR_LAYOUT_CACHE[key] = chosen
    return chosen


def _qdrant_search(
    qdrant_url: str,
    collection: str,
    vector: List[float],
    top_k: int,
    timeout: float = 10.0,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Run a Qdrant vector search through the local client adapter.

    Auto-detects whether ``collection`` is single-vector (v1 pipelines)
    or named-vector (v2 ``-summaries`` collections). Uses the unified
    The adapter accepts both schemas via the ``using`` parameter.

    Returns a list of hit dicts:
      {"id": …, "score": …, "payload": {…}}
    """
    named_vector = _resolve_vector_layout(qdrant_url, collection, timeout)
    project_filter = qdrant_project_filter(project_id)
    try:
        del timeout
        return query_points(
            get_code_qdrant_store(qdrant_url),
            collection,
            vector,
            limit=top_k,
            vector_name=named_vector,
            query_filter=project_filter,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] Qdrant search failed: %s", exc)
        return []


def _normalize_collection_tokens(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _qdrant_collection_names(qdrant_url: str, timeout: float = 10.0) -> List[str]:
    try:
        del timeout
        return get_code_qdrant_store(qdrant_url).list_collection_names()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] list collections failed: %s", exc)
        return []


def _is_project_scope_collection(scope: str, collection: str) -> bool:
    scope = (scope or "").strip()
    collection = (collection or "").strip()
    if not scope or not collection:
        return False
    if collection == f"{scope}_mess":
        return True
    return (
        collection.startswith(f"{scope}_")
        and "__" in collection
        and collection.endswith("_functions")
    )


def _resolve_qdrant_collections(qdrant_url: str, collection: Any) -> List[str]:
    """Resolve exact collection names or project scopes to Qdrant collections."""
    tokens = _normalize_collection_tokens(collection)
    available = _qdrant_collection_names(qdrant_url)
    if not tokens:
        return available
    if not available:
        return tokens

    resolved: List[str] = []
    for token in tokens:
        if token in available:
            candidates = [token]
        else:
            candidates = [
                name for name in available
                if _is_project_scope_collection(token, name)
            ]
        for candidate in candidates:
            if candidate not in resolved:
                resolved.append(candidate)

    return resolved or tokens


def _qdrant_search_by_ids(
    qdrant_url: str,
    collection: str,
    ids: List[str],
    timeout: float = 10.0,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve specific Qdrant points by ID (for graph-expanded nodes)."""
    if not ids:
        return []
    try:
        del timeout
        results = [
            model_to_dict(point)
            for point in get_code_qdrant_store(qdrant_url).retrieve(
                collection,
                ids,
                with_payload=True,
                with_vectors=False,
            )
        ]
        if normalize_project_id(project_id) is None:
            return results
        return [
            point for point in results
            if matches_project_scope(point.get("payload") or {}, project_id)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] Qdrant id-fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────
# Graph keyword search helper
# ─────────────────────────────────────────────────────────────


def _graph_keyword_search(
    graph_driver: Any,
    query: str,
    database: str,
    limit: int,
    labels: Optional[List[str]] = None,
    properties: Optional[List[str]] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Text search in the configured graph database.

    Accepts the shared GraphDriver abstraction (Neo4j/FalkorDB) and falls back
    to a raw Neo4j-style ``session`` only for older callers.
    """
    if graph_driver is None:
        return []
    tokens = [t.strip().lower() for t in query.split() if t.strip()]
    if not tokens:
        return []
    safe_labels = [value for value in (labels or []) if value.replace("_", "").isalnum()]
    safe_properties = [
        value for value in (properties or ["name", "qualified_name", "comment"])
        if value.replace("_", "").isalnum()
    ]
    label_clause = (
        "(" + " OR ".join(f"n:{label}" for label in safe_labels) + ") AND "
        if safe_labels else ""
    )
    property_clause = " OR ".join(
        f"toLower(coalesce(n.{property_name}, '')) CONTAINS q"
        for property_name in safe_properties
    )
    cypher = (
        "MATCH (n) WHERE ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized) AND "
        + label_clause
        + "any(q IN $qs WHERE " + property_clause + ") "
        + "RETURN n LIMIT $limit"
    )
    parameters = prepare_project_scope_parameters(
        cypher,
        {
            "qs": tokens,
            "limit": limit,
            "project_id": normalize_project_id(project_id),
        },
    )
    try:
        if hasattr(graph_driver, "execute_query_sync"):
            records, _, _ = graph_driver.execute_query_sync(cypher, parameters, database)
            return [_node_record_to_dict(record.get("n")) for record in records if record.get("n")]
        with graph_driver.session(database=database) as session:
            result = session.run(cypher, parameters)
            return [_node_record_to_dict(record["n"]) for record in result if record.get("n")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[intelligent_retrieval] graph keyword search failed: %s", exc)
        return []


def _node_record_to_dict(node: Any) -> Dict[str, Any]:
    if isinstance(node, dict):
        return dict(node)
    try:
        return dict(node)
    except Exception:
        properties = getattr(node, "properties", None)
        if isinstance(properties, dict):
            return dict(properties)
    return {}


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


def _graph_keyword_node_to_candidate(node: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a graph DB keyword hit into a flat candidate dict."""
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
        "_source":        "graph_keyword",
    }


def _graph_node_to_candidate(gnode: GraphNode) -> Dict[str, Any]:
    """Convert a GraphNode from graph expansion into a flat candidate dict."""
    props = gnode.properties
    candidate = {
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
        "project_id":     str(props.get("project_id") or ""),
        "language":       "",
        "_source":        "graph_expansion",
    }
    for key in (
        "seed_id", "seed_ids", "target_name", "signature", "framework",
        "resolution_status", "start_line", "end_line",
    ):
        if props.get(key) not in (None, "", []):
            candidate[key] = props[key]
    return candidate


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
    graph_driver : Optional graph driver. If None, graph expansion and graph
                   keyword search are skipped. ``neo4j_driver`` is accepted as
                   a legacy alias.
    database     : Graph database name.
    freshness_map : Optional mapping of node_id → last_updated ISO string.
                   If not provided, freshness defaults to 0.5 (neutral).
    dirty_set     : Optional set of node_ids known to be dirty.
    seed_k        : Number of initial Qdrant results to use as seeds.
    expand_depth  : Graph expansion hop depth.
    expand_limit  : Maximum graph-expanded candidates.
    """

    def __init__(
        self,
        qdrant_url: str = DEFAULT_QDRANT_PATH,
        collection: str = "",
        embedder: Optional[Callable[[str], List[float]]] = None,
        neo4j_driver: Optional[Any] = None,
        graph_driver: Optional[Any] = None,
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
        self._graph       = graph_driver if graph_driver is not None else neo4j_driver
        self._database    = database
        self._freshness   = freshness_map or {}
        self._dirty       = dirty_set or set()
        self._seed_k      = seed_k
        self._expander    = GraphExpander(self._graph, database) if self._graph else None
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
        graph_rel_types: Optional[List[str]] = None,
        searchable_labels: Optional[List[str]] = None,
        searchable_properties: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> List[ScoredResult]:
        """
        Execute context-aware retrieval for *query*.

        Parameters
        ──────────
        query          : Natural-language search string.
        top_k          : Number of results to return.
        debug          : When True, each ScoredResult includes per-signal
                         explanation and weighted contributions.
        expand_graph   : When True and a graph driver is configured, expand
                         top seeds through the code graph.
        weight_override : Override weight dict.  Only the provided keys are
                          overridden; the intent-based profile fills the rest.
        collection     : Override the default Qdrant collection.
        """
        q = (query or "").strip()
        if not q:
            return []
        active_project_id = project_id_lookup_key(project_id)

        t0 = time.perf_counter()

        # 1. Classify query → weight profile
        intent  = classify_query(q)
        weights = get_weight_profile(intent)
        if weight_override:
            weights.update(weight_override)
        logger.debug("[IR] query=%r intent=%s weights=%s", q, intent, weights)

        # 2. Initial retrieval
        col = collection or self._collection
        seeds_qdrant = self._retrieve_qdrant(
            q, col, self._seed_k, project_id=active_project_id
        )
        seeds_kw     = self._retrieve_keyword(
            q,
            self._seed_k,
            labels=searchable_labels,
            properties=searchable_properties,
            project_id=active_project_id,
        )

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
                elif active_project_id is None:
                    # BM25 hit not in Qdrant/graph DB seeds - add as candidate
                    candidates[nid] = {"node_id": nid, "bm25": bm25_score}

        # 3. Graph expansion
        if expand_graph and self._expander and seed_ids:
            graph_nodes = self._expander.expand(
                seed_ids  = seed_ids[:min(len(seed_ids), 10)],  # top-10 seeds only
                depth     = self._expand_depth,
                rel_types = graph_rel_types,
                limit     = self._expand_limit,
                include_seeds = False,
                project_id = active_project_id,
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
        candidate_list = [
            candidate for candidate in candidates.values()
            if matches_project_scope(candidate, active_project_id)
        ]
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
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Embed query and run Qdrant vector search."""
        if not self._embedder:
            return []
        try:
            vector = self._embedder(query)
            collections = _resolve_qdrant_collections(self._qdrant_url, collection)
            merged: Dict[str, Dict[str, Any]] = {}
            for col in collections:
                if normalize_project_id(project_id) is None:
                    hits = _qdrant_search(self._qdrant_url, col, vector, top_k)
                else:
                    hits = _qdrant_search(
                        self._qdrant_url,
                        col,
                        vector,
                        top_k,
                        project_id=project_id,
                    )
                for hit in hits:
                    payload = hit.get("payload") or {}
                    node_id = str(payload.get("symbol_id") or hit.get("id") or "")
                    if not node_id:
                        continue
                    score = float(hit.get("score") or 0.0)
                    existing = merged.get(node_id)
                    if existing is None or score > float(existing.get("score") or 0.0):
                        merged[node_id] = {**hit, "_qdrant_collection": col}
            hits = sorted(merged.values(), key=lambda h: float(h.get("score") or 0.0), reverse=True)[:top_k]
            return [_qdrant_hit_to_candidate(h, float(h.get("score") or 0.0)) for h in hits]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IR] Qdrant retrieval error: %s", exc)
            return []

    def _retrieve_keyword(
        self,
        query: str,
        top_k: int,
        *,
        labels: Optional[List[str]] = None,
        properties: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run graph DB keyword search."""
        if not self._graph:
            return []
        nodes = _graph_keyword_search(
            self._graph,
            query,
            self._database,
            top_k,
            labels=labels,
            properties=properties,
            project_id=project_id,
        )
        return [_graph_keyword_node_to_candidate(n) for n in nodes]

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
