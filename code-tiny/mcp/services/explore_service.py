"""
explore_service.py
──────────────────
Orchestration service for the Graph Explorer semantic search system.

Wires together:
  QueryUnderstanding  →  IntelligentRetrievalEngine  →  ResultPackager

Produces the structured ``PackedResult`` envelope defined in the spec:
  {
    "matched_nodes":   [...],
    "entry_points":    [...],
    "related_paths":   [...],
    "explanation":     str,
    "confidence":      float,
    "query_analysis":  {...},
    "mode":            str,
  }

Configuration is read from environment variables:
  QDRANT_URL          (default: http://localhost:6333)
  QDRANT_COLLECTION   (default: empty — auto-discovered)
  EMBED_MODEL         (default: empty — uses cplus_mcp DEFAULT_MODEL)
  NEO4J_URI           (default: bolt://localhost:7687)
  NEO4J_USER
  NEO4J_PASS
  NEO4J_DB            (default: neo4j)

Usage
─────
  from services.explore_service import ExploreService

  service = ExploreService()
  result = await service.explore(
      query="function xử lý thanh toán bị lỗi khi user chưa login",
      top_k=10,
      mode="graph_expanded",
      db="neo4j",
      collection="ts_functions",
  )
  # → dict (PackedResult.to_dict())
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("project_call_graph.mcp.explore")

# ─────────────────────────────────────────────────────────────────────────────
# Environment defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
_DEFAULT_COLLECTION  = os.environ.get("QDRANT_COLLECTION", "")
_DEFAULT_MODEL       = os.environ.get("EMBED_MODEL", "")
_DEFAULT_NEO4J_URI   = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_DEFAULT_NEO4J_USER  = os.environ.get("NEO4J_USER", "")
_DEFAULT_NEO4J_PASS  = os.environ.get("NEO4J_PASS", "")
_DEFAULT_NEO4J_DB    = os.environ.get("NEO4J_DB", "neo4j")

# ─────────────────────────────────────────────────────────────────────────────
# Lazy imports (avoid hard failure at import time if libraries missing)
# ─────────────────────────────────────────────────────────────────────────────

def _make_embedder(model_name: str) -> Optional[Callable[[str], List[float]]]:
    """
    Build a simple sentence-transformers embedder callable.
    Returns None if sentence_transformers is not installed.
    """
    if not model_name:
        return None
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _model = SentenceTransformer(model_name)
        def _embed(text: str) -> List[float]:
            return _model.encode([text])[0].tolist()  # type: ignore[return-value]
        return _embed
    except Exception as exc:
        logger.warning("[explore_service] Could not load embedder %r: %s", model_name, exc)
        return None


def _make_neo4j_driver(
    uri: str,
    user: str,
    password: str,
) -> Optional[Any]:
    """
    Build a raw neo4j sync Driver.
    Returns None if neo4j library unavailable or credentials missing.
    """
    if not (user and password):
        logger.info(
            "[explore_service] Neo4j keyword search and graph expansion disabled "
            "(NEO4J_USER / NEO4J_PASS not set)."
        )
        return None
    try:
        import neo4j  # type: ignore
        return neo4j.GraphDatabase.driver(uri, auth=(user, password))
    except Exception as exc:
        logger.warning("[explore_service] Could not connect to Neo4j: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Mode constants
# ─────────────────────────────────────────────────────────────────────────────

MODE_SEMANTIC       = "semantic"
MODE_HYBRID         = "hybrid"
MODE_GRAPH_EXPANDED = "graph_expanded"

_VALID_MODES = {MODE_SEMANTIC, MODE_HYBRID, MODE_GRAPH_EXPANDED}

# expand_graph flag per mode
_MODE_EXPAND_GRAPH = {
    MODE_SEMANTIC:       False,
    MODE_HYBRID:         False,
    MODE_GRAPH_EXPANDED: True,
}

# Weight overrides per mode (partial — intent profile fills the rest)
_MODE_WEIGHT_OVERRIDES: Dict[str, Dict[str, float]] = {
    MODE_SEMANTIC:       {"semantic": 0.70, "keyword": 0.05, "graph": 0.05},
    MODE_HYBRID:         {},   # use intent-based profile
    MODE_GRAPH_EXPANDED: {"graph": 0.30},
}


# ─────────────────────────────────────────────────────────────────────────────
# ExploreService
# ─────────────────────────────────────────────────────────────────────────────

class ExploreService:
    """
    Orchestration service for intent-aware, multi-strategy graph search.

    Thread-safety: instances are safe to create per-request.  The shared
    Neo4j driver and embedder are module-level singletons loaded lazily.
    """

    def __init__(
        self,
        qdrant_url:   Optional[str] = None,
        collection:   Optional[str] = None,
        model_name:   Optional[str] = None,
        neo4j_uri:    Optional[str] = None,
        neo4j_user:   Optional[str] = None,
        neo4j_pass:   Optional[str] = None,
        neo4j_db:     Optional[str] = None,
    ) -> None:
        self._qdrant_url  = qdrant_url  or _DEFAULT_QDRANT_URL
        self._collection  = collection  or _DEFAULT_COLLECTION
        self._model_name  = model_name  or _DEFAULT_MODEL
        self._neo4j_uri   = neo4j_uri   or _DEFAULT_NEO4J_URI
        self._neo4j_user  = neo4j_user  or _DEFAULT_NEO4J_USER
        self._neo4j_pass  = neo4j_pass  or _DEFAULT_NEO4J_PASS
        self._neo4j_db    = neo4j_db    or _DEFAULT_NEO4J_DB

    # ── Public API ────────────────────────────────────────────────────────────

    async def explore(
        self,
        query:      str,
        *,
        top_k:      int = 10,
        mode:       str = MODE_HYBRID,
        db:         Optional[str] = None,
        collection: Optional[str] = None,
        debug:      bool = False,
    ) -> Dict[str, Any]:
        """
        Run intent-aware multi-strategy search.

        Parameters
        ----------
        query:      Natural language text (keyword, sentence, or paragraph).
        top_k:      Maximum number of matched nodes to return.
        mode:       "semantic" | "hybrid" | "graph_expanded"
        db:         Neo4j database name override.
        collection: Qdrant collection name override.
        debug:      When True, include per-signal score breakdown in each node.

        Returns
        -------
        dict — ``PackedResult.to_dict()`` with keys:
          matched_nodes, entry_points, related_paths, explanation,
          confidence, query_analysis, mode
        """
        query = (query or "").strip()
        if not query:
            return _empty_response(mode)

        mode = mode if mode in _VALID_MODES else MODE_HYBRID
        active_collection = collection or self._collection
        active_db         = db or self._neo4j_db

        # 1. Query understanding
        understanding = self._parse_query(query)
        logger.info(
            "[explore] query=%r intent=%s signals=%s entities=%s",
            query[:80],
            understanding.intent,
            understanding.domain_signals,
            understanding.entities[:5],
        )

        # 2. Build embedder + neo4j driver
        embedder = _make_embedder(self._model_name)
        neo4j_driver = (
            _make_neo4j_driver(self._neo4j_uri, self._neo4j_user, self._neo4j_pass)
            if mode != MODE_SEMANTIC
            else None
        )

        # 3. Run retrieval (sync engine → offload to thread)
        scored_results = await self._run_retrieval(
            understanding  = understanding,
            embedder       = embedder,
            neo4j_driver   = neo4j_driver,
            database       = active_db,
            collection     = active_collection,
            top_k          = top_k,
            mode           = mode,
            debug          = debug,
        )

        # 4. Package results
        packed = self._pack(scored_results, understanding, mode)

        return packed.to_dict()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _parse_query(self, text: str) -> Any:
        """
        Parse the raw query into a QueryUnderstanding.
        Uses from_paragraph for multi-line input, from_text otherwise.
        """
        from tools.common.query_understanding import QueryUnderstanding

        if "\n" in text or len(text) > 200:
            return QueryUnderstanding.from_paragraph(text)
        return QueryUnderstanding.from_text(text)

    async def _run_retrieval(
        self,
        understanding:  Any,
        embedder:       Optional[Callable],
        neo4j_driver:   Optional[Any],
        database:       str,
        collection:     str,
        top_k:          int,
        mode:           str,
        debug:          bool,
    ) -> list:
        """
        Build an ``IntelligentRetrievalEngine`` and run the search.

        The engine's ``search()`` method is synchronous (uses sync neo4j Driver
        and blocking HTTP).  We offload it to a thread pool to avoid blocking
        the event loop.
        """
        from tools.common.intelligent_retrieval import IntelligentRetrievalEngine

        expand_graph    = _MODE_EXPAND_GRAPH.get(mode, False)
        weight_override = _MODE_WEIGHT_OVERRIDES.get(mode, {}).copy()

        # Use the enriched embedding_text for better recall on vague/multilingual queries
        embed_query = understanding.embedding_text or understanding.raw_query

        engine = IntelligentRetrievalEngine(
            qdrant_url   = self._qdrant_url,
            collection   = collection,
            embedder     = embedder,
            neo4j_driver = neo4j_driver,
            database     = database,
        )

        def _run_sync() -> list:
            return engine.search(
                query          = embed_query,
                top_k          = top_k,
                debug          = debug,
                expand_graph   = expand_graph,
                weight_override= weight_override if weight_override else None,
                collection     = collection,
            )

        try:
            scored = await asyncio.to_thread(_run_sync)
        except Exception as exc:
            logger.error("[explore] Retrieval failed: %s", exc, exc_info=True)
            scored = []

        return scored

    @staticmethod
    def _pack(
        scored_results: list,
        understanding:  Any,
        mode:           str,
    ) -> Any:
        """Package scored results into a structured PackedResult."""
        from tools.common.result_packager import ResultPackager
        return ResultPackager.pack(
            scored_results,
            query_understanding=understanding,
            mode=mode,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module singleton (reused by the MCP tool wrapper)
# ─────────────────────────────────────────────────────────────────────────────

_service_singleton: Optional[ExploreService] = None


def get_explore_service() -> ExploreService:
    """Return the module-level singleton ExploreService (lazy init)."""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = ExploreService()
    return _service_singleton


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_response(mode: str) -> Dict[str, Any]:
    return {
        "matched_nodes":  [],
        "entry_points":   [],
        "related_paths":  [],
        "explanation":    "No query provided.",
        "confidence":     0.0,
        "query_analysis": {},
        "mode":           mode,
    }
