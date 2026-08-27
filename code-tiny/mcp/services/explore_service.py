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
  QDRANT_CODE_PATH    (owner-scoped local storage path)
  QDRANT_COLLECTION   (default: empty — auto-discovered)
  EMBED_MODEL         (default: empty — uses cplus_mcp DEFAULT_MODEL)
  CODE_GRAPH_PROVIDER / GRAPH_PROVIDER (default: falkordb)
  FALKORDB_PATH      (owner-scoped local storage path)
  FALKORDB_GRAPH       (default: hyper_graph)
  NEO4J_URI / NEO4J_USER / NEO4J_PASS / NEO4J_DB (legacy/Neo4j mode)

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
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from tools.graph.core.provider_contract import normalize_graph_provider_name
from cortex_harness.storage import GatewayErrorCode, StoreGatewayError

logger = logging.getLogger("project_call_graph.mcp.explore")


class _BoundedRetrievalExecutor:
    """One named, count-bounded lane for synchronous graph/vector retrieval."""

    def __init__(self, *, workers: int = 1, queue_items: int = 32) -> None:
        self._capacity = workers + queue_items
        self._pending = 0
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="cortex-retrieval",
        )

    async def run(self, operation: Callable[[], Any]) -> Any:
        with self._lock:
            if self._pending >= self._capacity:
                raise StoreGatewayError(
                    GatewayErrorCode.OVERLOADED,
                    "retrieval admission queue is full",
                    retryable=True,
                    retry_after_ms=100,
                    details={
                        "queued_items": max(0, self._pending - 1),
                        "capacity": self._capacity - 1,
                    },
                )
            self._pending += 1

        def execute() -> Any:
            try:
                return operation()
            finally:
                with self._lock:
                    self._pending -= 1

        future = asyncio.wrap_future(self._executor.submit(execute))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Retrieval may already hold embedded client state. Do not claim
            # cancellation or free admission until the synchronous call ends.
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise


_RETRIEVAL_EXECUTOR = _BoundedRetrievalExecutor()

# ─────────────────────────────────────────────────────────────────────────────
# Environment defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_QDRANT_PATH = os.environ.get("QDRANT_CODE_PATH", "")
_DEFAULT_COLLECTION  = os.environ.get("QDRANT_COLLECTION", "")
_DEFAULT_MODEL       = os.environ.get("EMBED_MODEL", "")
_DEFAULT_GRAPH_PROVIDER = normalize_graph_provider_name(
    os.environ.get("CODE_GRAPH_PROVIDER")
    or os.environ.get("GRAPH_PROVIDER")
    or os.environ.get("MCP_GRAPH_PROVIDER")
)
if _DEFAULT_GRAPH_PROVIDER == "neo4j":
    _DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    _DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER", "")
    _DEFAULT_NEO4J_PASS = os.environ.get("NEO4J_PASS", "")
    _DEFAULT_NEO4J_DB = os.environ.get("NEO4J_DB", "hyper_graph")
else:
    _DEFAULT_NEO4J_URI = "bolt://localhost:7687"
    _DEFAULT_NEO4J_USER = ""
    _DEFAULT_NEO4J_PASS = ""
    _DEFAULT_NEO4J_DB = "hyper_graph"
_DEFAULT_FALKORDB_GRAPH = (
    os.environ.get("FALKORDB_GRAPH")
    or os.environ.get("FALKORDB_DATABASE")
    or "hyper_graph"
)
_DEFAULT_GRAPH_DB = (
    _DEFAULT_FALKORDB_GRAPH
    if _DEFAULT_GRAPH_PROVIDER == "falkordb"
    else _DEFAULT_NEO4J_DB
)

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
        logger.warning(
            "[explore_service] sentence-transformers could not load %r: %s; "
            "falling back to the semantic backend embedder.",
            model_name,
            exc,
        )
        try:
            from cplus.cplus_mcp import _embed_query as backend_embed_query
        except Exception as fallback_exc:
            logger.warning(
                "[explore_service] Could not load fallback embedder %r: %s",
                model_name,
                fallback_exc,
            )
            return None

        def _embed_with_backend(text: str) -> List[float]:
            return backend_embed_query(text, model_name)

        return _embed_with_backend


def _is_falkordb_uri(uri: str) -> bool:
    if not uri or "://" not in uri:
        return False
    return urlparse(uri).scheme in {"falkor", "falkors", "redis", "rediss", "unix"}


async def _make_graph_driver(
    uri: str,
    user: str,
    password: str,
    database: str,
    *,
    provider: Optional[str] = None,
) -> Optional[Any]:
    """
    Build a graph driver using the shared GraphDriverFactory.

    Neo4j remains an explicit legacy option; FalkorDB uses its local file.
    """
    provider_text = normalize_graph_provider_name(
        provider,
        default=_DEFAULT_GRAPH_PROVIDER,
    )
    use_falkor = provider_text in {"falkor", "falkordb"} or _is_falkordb_uri(uri)
    if not use_falkor and not (user and password):
        logger.info(
            "[explore_service] Graph keyword search and expansion disabled "
            "(NEO4J_USER / NEO4J_PASS not set)."
        )
        return None
    try:
        from tools.graph import GraphProvider
        from tools.graph.core.shared_runtime import get_shared_graph_driver

        if use_falkor:
            from cortex_harness.storage import resolve_storage
            from falkordb_discovery import discover_falkordb_data_files

            remote_uri = os.environ.get("FALKORDB_URI") or os.environ.get("FALKORDB_URL")
            config = {
                "database": database,
                "graph": database,
                "_suppress_deprecation": bool(remote_uri),
                "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
                "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
            }
            if remote_uri:
                config.update(
                    {
                        "uri": remote_uri,
                        "password": os.environ.get("FALKORDB_PASSWORD"),
                        "ssl": os.environ.get("FALKORDB_SSL", "").strip().lower()
                        in {"1", "true", "yes", "on"},
                    }
                )
            else:
                config["path"] = (
                    os.environ.get("FALKORDB_PATH")
                    or str(resolve_storage(Path.cwd()).falkordb_code_path)
                )
                config["additional_paths"] = discover_falkordb_data_files()
            return await get_shared_graph_driver(GraphProvider.FALKORDB, config)

        return await get_shared_graph_driver(
            GraphProvider.NEO4J,
            {"uri": uri, "user": user, "password": password, "database": database},
        )
    except Exception as exc:
        logger.warning("[explore_service] Could not connect to graph database: %s", exc)
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
        graph_provider: Optional[str] = None,
    ) -> None:
        # MCP requests cannot select arbitrary filesystem-backed stores.
        # The process owns exactly the configured code store.
        self._qdrant_url  = _DEFAULT_QDRANT_PATH
        self._collection  = collection  or _DEFAULT_COLLECTION
        self._model_name  = model_name  or _DEFAULT_MODEL
        self._neo4j_uri   = neo4j_uri   or _DEFAULT_NEO4J_URI
        self._neo4j_user  = neo4j_user  or _DEFAULT_NEO4J_USER
        self._neo4j_pass  = neo4j_pass  or _DEFAULT_NEO4J_PASS
        self._graph_provider = normalize_graph_provider_name(
            graph_provider,
            default=_DEFAULT_GRAPH_PROVIDER,
        )
        self._neo4j_db    = neo4j_db or _DEFAULT_GRAPH_DB

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
        graph_rel_types: Optional[list[str]] = None,
        searchable_labels: Optional[list[str]] = None,
        searchable_properties: Optional[list[str]] = None,
        project_id: Optional[str] = None,
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
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 100:
            raise StoreGatewayError(
                GatewayErrorCode.REQUEST_TOO_LARGE,
                "top_k must be an integer between 1 and 100",
                retryable=False,
                details={"accepted_limit": 100, "requested_top_k": top_k},
            )

        mode = mode if mode in _VALID_MODES else MODE_HYBRID
        search_targets = self._resolve_search_targets(
            db=db,
            collection=collection,
            project_id=project_id,
        )

        # 1. Query understanding
        understanding = self._parse_query(query)
        logger.info(
            "[explore] query=%r intent=%s signals=%s entities=%s",
            query[:80],
            understanding.intent,
            understanding.domain_signals,
            understanding.entities[:5],
        )

        # 2. Build embedder + graph driver
        embedder = _make_embedder(self._model_name)
        graph_driver = (
            await _make_graph_driver(
                self._neo4j_uri,
                self._neo4j_user,
                self._neo4j_pass,
                search_targets[0][1],
                provider=self._graph_provider,
            )
            if mode != MODE_SEMANTIC
            else None
        )

        # 3. Run retrieval (sync engine → offload to thread)
        target_results: List[Tuple[str, list]] = []
        for target_project_id, target_db, target_collection in search_targets:
            scored = await self._run_retrieval(
                understanding  = understanding,
                embedder       = embedder,
                graph_driver   = graph_driver,
                database       = target_db,
                collection     = target_collection,
                top_k          = top_k,
                mode           = mode,
                debug          = debug,
                graph_rel_types= graph_rel_types,
                searchable_labels= searchable_labels,
                searchable_properties= searchable_properties,
                project_id     = project_id,
            )
            target_results.append((target_project_id, scored))
        scored_results = self._merge_target_results(target_results, top_k)

        # 4. Package results
        packed = self._pack(scored_results, understanding, mode)

        response = packed.to_dict()
        graph_requested = mode != MODE_SEMANTIC
        response["retrieval"] = {
            "graph_provider": self._graph_provider,
            "graph_database": search_targets[0][1] if len(search_targets) == 1 else None,
            "graph_databases": [target[1] for target in search_targets],
            "qdrant_collections": [target[2] for target in search_targets],
            "graph_requested": graph_requested,
            "graph_connected": graph_driver is not None,
            "graph_expansion_requested": bool(_MODE_EXPAND_GRAPH.get(mode, False)),
            "semantic_enabled": embedder is not None,
            "degraded": bool(graph_requested and graph_driver is None),
        }
        return response

    def _resolve_search_targets(
        self,
        *,
        db: Optional[str],
        collection: Optional[str],
        project_id: Optional[str],
    ) -> List[Tuple[str, str, str]]:
        """Return deterministic project/graph/collection targets for a search."""
        from tools.common.project_registry import (
            ProjectNotRegisteredError,
            list_registered_projects,
            resolve_project_targets,
        )

        if project_id:
            try:
                target = resolve_project_targets(project_id)
                return [(
                    target.project_id_normalized,
                    db or target.code_graph,
                    collection or target.code_qdrant_collection,
                )]
            except ProjectNotRegisteredError:
                normalized = str(project_id).strip().casefold()
                return [(normalized, db or str(project_id), collection or str(project_id))]

        # An explicit physical target remains a single-target request. The
        # implicit contract (no project and no target overrides) searches all
        # registered shards.
        if db or collection:
            return [("", db or self._neo4j_db, collection or self._collection)]

        resolved: List[Tuple[str, str, str]] = []
        seen: set[Tuple[str, str]] = set()
        for registered_project in list_registered_projects():
            target = resolve_project_targets(registered_project)
            key = (target.code_graph, target.code_qdrant_collection)
            if key in seen:
                continue
            seen.add(key)
            resolved.append((
                target.project_id_normalized,
                target.code_graph,
                target.code_qdrant_collection,
            ))
        return resolved or [("", self._neo4j_db, self._collection)]

    @staticmethod
    def _merge_target_results(
        target_results: List[Tuple[str, list]],
        top_k: int,
    ) -> list:
        """Stable global rank/dedup across independently searched shards."""
        best: Dict[Tuple[str, str], Tuple[int, Any]] = {}
        ordinal = 0
        for target_project_id, results in target_results:
            for result in results:
                node = result.node or {}
                result_project = str(
                    node.get("project_id_normalized")
                    or node.get("project_id")
                    or target_project_id
                    or ""
                ).casefold()
                key = (result_project, str(result.node_id))
                current = best.get(key)
                if current is None or float(result.score) > float(current[1].score):
                    best[key] = (ordinal if current is None else current[0], result)
                ordinal += 1
        ranked = sorted(
            best.values(),
            key=lambda item: (-float(item[1].score), item[0]),
        )
        return [result for _, result in ranked[:top_k]]

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
        graph_driver:   Optional[Any],
        database:       str,
        collection:     str,
        top_k:          int,
        mode:           str,
        debug:          bool,
        graph_rel_types: Optional[list[str]],
        searchable_labels: Optional[list[str]],
        searchable_properties: Optional[list[str]],
        project_id: Optional[str],
    ) -> list:
        """
        Build an ``IntelligentRetrievalEngine`` and run the search.

        The engine's ``search()`` method is synchronous (uses a sync graph
        driver and blocking vector calls). It runs on the named, bounded
        retrieval lane so bursts cannot consume the default executor.
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
            graph_driver = graph_driver,
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
                graph_rel_types= graph_rel_types,
                searchable_labels= searchable_labels,
                searchable_properties= searchable_properties,
                project_id     = project_id,
            )

        try:
            return await _RETRIEVAL_EXECUTOR.run(_run_sync)
        except Exception as exc:
            # A successful empty list is reserved for a completed zero-hit
            # query. Storage, overload, and timeout failures stay failures so
            # the MCP boundary can render a truthful structured response.
            logger.error("[explore] Retrieval failed: %s", exc, exc_info=True)
            raise

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
