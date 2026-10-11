import argparse
import functools
import inspect
import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer

from embedding_utils import (
    DEFAULT_DOC_EMBEDDING_MODEL,
    resolve_embedding_device,
    resolve_embedding_model,
    st_query_encode_kwargs,
)
from graph_store import (
    FalkorDBGraphStore,
    create_graph_store_for_project,
    create_graph_store_from_env,
    env_graph_provider,
)
from doc_local_qdrant import get_document_qdrant_store
from project_contract import (
    ProjectNotRegisteredError,
    list_registered_projects,
    qdrant_project_filter as _pc_qdrant_project_filter,
    resolve_doc_candidates,
    resolve_project_targets,
)
from cortex_harness.storage import (
    QdrantStorageRole,
    RemoteQdrantStore,
    StorageRole,
    create_storage,
)
from cortex_harness.mcp_contract import (
    normalize_error,
    normalize_success,
    result_meta,
    result_summary,
)

# The storage layer resolves per-project backends through the code-side
# registry (tools.common.project_registry), whose ProjectNotRegisteredError
# shares the doc-side name but is an unrelated KeyError sibling. Fallbacks
# below must catch both or the naming-convention path stays dead: the store
# layer raises the registry class while the except matched only the mirror.
try:
    from tools.common.project_registry import (  # type: ignore
        ProjectNotRegisteredError as _RegistryProjectNotRegisteredError,
        resolve_project_targets as _registry_resolve_project_targets,
    )
except Exception:  # standalone doc-tiny runtime without the code registry
    _RegistryProjectNotRegisteredError = None  # type: ignore[assignment]
    _registry_resolve_project_targets = None  # type: ignore[assignment]

if _RegistryProjectNotRegisteredError is not None:
    _PROJECT_RESOLVE_ERRORS: tuple = (
        ProjectNotRegisteredError,
        _RegistryProjectNotRegisteredError,
    )
else:
    _PROJECT_RESOLVE_ERRORS = (ProjectNotRegisteredError,)


MCP_NAME = os.getenv("MCP_SERVER_NAME", "mind_mcp")

# Load .env if present (for DOC_GRAPH_PROVIDER/FALKORDB_*/QDRANT_*/EMBEDDING_*).
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
except Exception:
    pass

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION_DOC", "documents")

# Unified default (kept as an alias name for the MCP tool docs).
DEFAULT_TEXT_EMBEDDING_MODEL = DEFAULT_DOC_EMBEDDING_MODEL

DEFAULT_ENTITY_TYPES = [
    "ORG",
    "PRODUCT",
    "STANDARD",
    "TECH",
    "CRYPTO",
    "SECURITY",
    "PROTOCOL",
    "VEHICLE",
    "DEVICE",
    "SERVER",
    "APP",
    "CERTIFICATE",
    "KEY",
]


def _parse_entity_types(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


ENTITY_TYPES_DEFAULT = _parse_entity_types(
    os.getenv("DEFAULT_ENTITY_TYPES") or os.getenv("ENTITY_TYPES_DEFAULT")
) or DEFAULT_ENTITY_TYPES


_qdrant_stores: dict[str, Any] = {}
_graph_drivers: dict[str, Any] = {}
_embedder: Optional[SentenceTransformer] = None
_embedder_model_name: Optional[str] = None
logger = logging.getLogger("graph_rag.mcp")


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def get_qdrant(project_id: Optional[str] = None) -> Any:
    """Return the primary Qdrant store for ``project_id``.

    Per-project cache replaces the previous module-level singleton
    (``_qdrant_client``). Passing ``project_id`` routes through
    :class:`StorageFactory` so a remote project's Qdrant server is honored
    without restarting the MCP server. An unregistered id falls back to
    :func:`_fallback_doc_qdrant_stores` and keeps the best store (the
    launcher's remote vector backend when one is declared, else the local
    instance store); multi-store probing lives in
    :func:`_qdrant_search_targets`.
    """
    if project_id:
        if project_id not in _qdrant_stores:
            try:
                _qdrant_stores[project_id] = get_document_qdrant_store(project_id=project_id)
            except _PROJECT_RESOLVE_ERRORS:
                # Unregistered id (naming-convention fallback): the
                # collection name scopes the shard on the fallback stores.
                fallback_stores = _fallback_doc_qdrant_stores()
                _qdrant_stores[project_id] = (
                    fallback_stores[0] if fallback_stores else get_document_qdrant_store()
                )
        return _qdrant_stores[project_id]
    # Legacy / global access for scripts that don't carry a project_id.
    return get_document_qdrant_store()


def get_embedder() -> SentenceTransformer:
    global _embedder, _embedder_model_name
    if _embedder is None:
        model_name, local_files_only = resolve_embedding_model(None, DEFAULT_TEXT_EMBEDDING_MODEL)
        device = resolve_embedding_device(None)
        _embedder = SentenceTransformer(
            model_name, local_files_only=local_files_only, device=device
        )
        _embedder_model_name = model_name
    return _embedder


def _embedding_model_mismatch_note(
    project_id: Optional[str] = None, collection: Optional[str] = None
) -> Optional[str]:
    """Soft query-side warning when a probed doc collection was indexed by
    another embedding model (sentinel read; never fails the query)."""
    try:
        from tools.common import embedding_marker
    except Exception:  # standalone doc-tiny runtime without code-tiny
        return None
    model = _embedder_model_name
    if not model:
        return None
    notes: list[str] = []
    for qdrant, collection_names in _qdrant_search_targets(project_id, collection):
        for name in collection_names or []:
            try:
                mismatch = embedding_marker.mismatch(qdrant, name, model)
            except Exception:
                continue
            if mismatch:
                notes.append(
                    f"{name}: indexed by {mismatch['stamped_model']!r} but the query "
                    f"model is {mismatch['query_model']!r}; re-ingest this project"
                )
    return "; ".join(notes) or None


def _query_encode(vector_input: str) -> list[float]:
    """Embed one query with the model's query instruction when it has one.

    Documents/paragraphs are embedded bare (ingest side); only queries get
    the Qwen3 ``query`` prompt + normalization (see embedding_utils).
    """
    embedder = get_embedder()
    kwargs = st_query_encode_kwargs(_embedder_model_name)
    encoded = embedder.encode([vector_input], **kwargs) if kwargs else embedder.encode([vector_input])
    return encoded[0].tolist()


def _query_sparse_encode(vector_input: str) -> tuple[list[int], list[float]] | None:
    """Hybrid sparse half of the query pair, or ``None`` when not hybrid.

    Rides the code-tiny bridge (embed_runtime), reusing the doc query
    embedder instance so no second model load happens. Any failure
    degrades to dense-only (soft reader side).
    """
    from embedding_utils import hybrid_enabled

    if not hybrid_enabled(_embedder_model_name):
        return None
    try:
        from tools.common import embed_runtime

        dense_probe = get_embedder()
        return embed_runtime.encode_sparse(
            _embedder_model_name,
            [vector_input],
            device=str(getattr(dense_probe, "device", "")),
            model=dense_probe,
        )[0]
    except Exception as exc:
        logger.warning("[doc-query] sparse encode failed (%s); dense-only", exc)
        return None


def _fanout_supports_sparse(pairs: list) -> bool:
    """All-or-none: every (store, collection) must declare sparse vectors.

    RRF and cosine scores never mix in one doc fan-out — one dense-only
    member downgrades the whole fan-out (mirrors the code-side rule).
    ``None`` collection names mean "whatever the store has", so every
    collection the store exposes gets probed.
    """
    if not pairs:
        return False
    for qdrant, collection_names in pairs:
        names = collection_names
        if names is None:
            try:
                names = sorted(qdrant.list_collection_names())
            except Exception:
                return False
        for name in names:
            try:
                info = qdrant.get_collection_info(name)
            except Exception:
                return False
            if not getattr(
                getattr(getattr(info, "config", None), "params", None),
                "sparse_vectors", None,
            ):
                return False
    return True


def _unregistered_doc_graph_name(base: Any, project_id: Any) -> str:
    """Graph name for an unregistered id, exact raw name first.

    FalkorDB materializes graphs on first query, so a wrong name fails
    silently on the read path (empty results, never an error). Listing the
    backend (GRAPH.LIST — no side effects) and preferring a graph actually
    named after the raw id keeps an out-of-band shard named ``redacted_doc``
    reachable instead of pointing at the invented ``redacted_doc_doc``.
    Falls back to the naming convention when the backend cannot be listed.
    """
    stripped = str(project_id).strip()
    convention = f"{stripped}_doc"
    list_graphs = getattr(getattr(base, "_driver", None), "list_graphs", None)
    if not callable(list_graphs):
        return convention
    try:
        available = [str(name) for name in (list_graphs() or [])]
    except Exception as exc:
        logger.warning("Listing doc graphs failed: %s", exc)
        return convention
    lookup = {name.casefold(): name for name in available}
    return lookup.get(stripped.casefold(), convention)


def get_graph_store(project_id: Optional[str] = None) -> Any:
    """Return the graph driver for ``project_id``.

    Per-project cache replaces the previous module-level
    ``_neo4j_driver`` singleton. With a ``project_id``, callers route
    through :class:`StorageFactory` so remote FalkorDB URIs are honored.
    Without a project, the legacy env-seeded driver is returned for
    scripts that don't have a registry binding (e.g. ``make doctor``).
    """
    if project_id:
        if project_id not in _graph_drivers:
            targets = None
            if _registry_resolve_project_targets is not None:
                try:
                    targets = _registry_resolve_project_targets(project_id)
                except _PROJECT_RESOLVE_ERRORS:
                    targets = None
            if targets is not None:
                factory = create_storage(targets)
                driver = factory.get_falkordb_driver(
                    targets.doc_graph,
                    role=StorageRole.DOCUMENT,
                )
                _graph_drivers[project_id] = FalkorDBGraphStore(
                    driver,
                    targets.doc_graph,
                )
            else:
                # Unregistered id: prefer a graph actually named after the
                # raw id (out-of-band shard), else the naming convention.
                base = get_graph_store()
                if getattr(base, "provider", None) == "falkordb":
                    _graph_drivers[project_id] = base.for_graph(
                        _unregistered_doc_graph_name(base, project_id)
                    )
                else:
                    _graph_drivers[project_id] = base
        return _graph_drivers[project_id]
    return create_graph_store_from_env()


def _acquire_graph_store(project_id: Optional[str]):
    if project_id and env_graph_provider() == "neo4j":
        # Preserve the legacy Neo4j request-scoped database session behavior.
        return create_graph_store_for_project(project_id), True
    if project_id:
        return get_graph_store(project_id), False
    base = get_graph_store()
    return base, False


def _graph_store_candidates(project_id: Optional[str]):
    """Return deterministic graph stores for scoped or full-search queries.

    Scoped queries follow the project_id rules: an exact case-insensitive
    registry match (or a single prefix hit) pins one store; a multi-project
    prefix scope (``bank`` → ``bank_android``, ``bank_Cplus``) returns one
    store per candidate; no registry match falls back to the raw id via the
    naming convention.
    """
    if project_id:
        if env_graph_provider() == "neo4j":
            # Neo4j keeps one shared database; the per-query project filter
            # scopes rows, so a single request-scoped store is enough.
            return [_acquire_graph_store(project_id)]
        matched = resolve_doc_candidates(project_id)
        if len(matched) <= 1:
            return [_acquire_graph_store(
                matched[0].project_id if matched else project_id
            )]
        stores: List[Any] = []
        for targets in matched:
            store, owned = _acquire_graph_store(targets.project_id)
            stores.append((store, owned))
        return stores
    base = get_graph_store()
    if getattr(base, "provider", None) != "falkordb":
        return [(base, False)]

    graph_names: List[str] = []
    for registered_project in list_registered_projects():
        graph_name = resolve_project_targets(registered_project).doc_graph
        if graph_name and graph_name not in graph_names:
            graph_names.append(graph_name)
    if not graph_names:
        return [(base, False)]
    return [(base.for_graph(graph_name), False) for graph_name in graph_names]


def _available_doc_collection_names() -> List[str]:
    """Union of collection names visible on the fallback doc stores.

    Best-effort by design: a store without ``list_collection_names`` (or one
    that fails to answer) contributes nothing, so an unreachable backend
    degrades resolution to the naming-convention name instead of failing
    the query.
    """
    names: List[str] = []
    for store in _fallback_doc_qdrant_stores():
        list_names = getattr(store, "list_collection_names", None)
        if not callable(list_names):
            continue
        try:
            available = list_names() or []
        except Exception as exc:
            logger.warning(
                "Listing collections on %s failed: %s",
                getattr(store, "url", None) or "local instance store",
                exc,
            )
            continue
        for name in available:
            if name and name not in names:
                names.append(name)
    return names


def _unregistered_doc_collection_names(project_id: Any) -> List[str]:
    """Collection names an unregistered ``project_id`` may target.

    Backend-aware preference order (docs/PROJECT_ID_QUERY_RULES.md §3 tier 3):

    1. A collection named exactly after the raw id (case-insensitive) wins —
       out-of-band shards are created under the bare id, and appending the
       ``_doc`` suffix over an exact match skews the caller's intent.
    2. Otherwise every visible collection whose name casefold-starts-with the
       id, convention name first — mirrors the registry prefix rule; the
       payload filter still enforces the project scope on the hits.
    3. Nothing visible matches — keep the ``{project_id}_doc`` convention name
       so in-band shards stay reachable and the probe error names the shard
       the contract expects.
    """
    stripped = str(project_id).strip()
    convention = f"{stripped}_doc"
    available = _available_doc_collection_names()
    if not available:
        return [convention]
    lookup = {name.casefold(): name for name in available}
    exact = lookup.get(stripped.casefold())
    if exact:
        return [exact]
    query_key = stripped.casefold()
    prefixed = sorted(
        (name for name in available if name.casefold().startswith(query_key)),
        key=lambda name: (name != convention, name.casefold()),
    )
    return prefixed or [convention]


def _resolve_doc_collection(
    project_id: Optional[str], collection: Optional[str] = None
) -> str:
    if collection:
        return collection
    if project_id:
        return _resolve_doc_collections(project_id, None)[0]
    return QDRANT_COLLECTION


def _resolve_doc_collections(
    project_id: Optional[str], collection: Optional[str] = None
) -> List[str]:
    if collection:
        return [collection]
    if project_id:
        # project_id rules: exact case-insensitive match wins, else every
        # registered project whose id casefold-starts-with the query. No
        # registry match -> backend-aware fallback so out-of-band doc
        # shards stay reachable (exact name first, then the convention).
        names: List[str] = []
        for targets in resolve_doc_candidates(project_id):
            name = targets.doc_qdrant_collection
            if name and name not in names:
                names.append(name)
        if names:
            return names
        return _unregistered_doc_collection_names(project_id)

    collections: List[str] = []
    for registered_project in list_registered_projects():
        name = resolve_project_targets(registered_project).doc_qdrant_collection
        if name and name not in collections:
            collections.append(name)
    return collections or [QDRANT_COLLECTION]


def _fallback_doc_qdrant_stores() -> List[Any]:
    """Stores an unregistered/override doc query must probe, best first.

    Out-of-band shards (ingested without a registry entry) land on the
    launcher's remote vector backend, so it is probed before the local
    instance store — which is empty in harness-launched deployments.
    """
    stores: List[Any] = []
    url = str(os.getenv("QDRANT_URL") or "").strip()
    if url:
        cache_key = f"__remote__:{url}"
        if cache_key not in _qdrant_stores:
            try:
                _qdrant_stores[cache_key] = RemoteQdrantStore(
                    url, role=QdrantStorageRole.DOCUMENT
                )
            except Exception as exc:
                logger.warning("Remote doc store %s unavailable: %s", url, exc)
                _qdrant_stores[cache_key] = None
        if _qdrant_stores[cache_key] is not None:
            stores.append(_qdrant_stores[cache_key])
    stores.append(get_document_qdrant_store())
    return stores


def _qdrant_search_targets(
    project_id: Optional[str], collection: Optional[str] = None
) -> List[Tuple[Any, Optional[List[str]]]]:
    """Resolve the ``(store, collection_names)`` pairs a doc query probes.

    Implements the project_id query rule end to end: a registered id pins
    its own backend through the storage factory; an unregistered id resolves
    against what the fallback stores actually expose — a collection named
    exactly after the raw id wins, then prefix matches, with the
    ``{project_id}_doc`` naming convention as last resort — probing the
    launcher's remote vector backend first and the local instance store
    second; no scope fans out over every registered project's backend plus
    the instance store. An explicit ``collection`` overrides the names on
    every probed store. ``None`` collection names mean "whatever the store
    has" (instance-store tail of an unscoped query).
    """
    if project_id:
        try:
            store = get_document_qdrant_store(project_id=project_id)
        except _PROJECT_RESOLVE_ERRORS:
            names = _resolve_doc_collections(project_id, collection)
            return [(store, names) for store in _fallback_doc_qdrant_stores()]
        return [(store, _resolve_doc_collections(project_id, collection))]
    if collection:
        return [(store, [collection]) for store in _fallback_doc_qdrant_stores()]
    pairs: List[Tuple[Any, Optional[List[str]]]] = []
    for registered in list_registered_projects():
        try:
            store = get_document_qdrant_store(project_id=registered)
        except _PROJECT_RESOLVE_ERRORS:
            continue
        pairs.append((store, _resolve_doc_collections(registered, None)))
    pairs.append((get_document_qdrant_store(), None))
    return pairs


def qdrant_search_entity_payload(
    query_vector: List[float],
    top_k: int,
    source_id: Optional[str],
    collection: Optional[str] = None,
    project_id: Optional[str] = None,
    query_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Build the Qdrant filter. The project filter combines with the
    # ``source_id`` filter via AND. A missing ``project_id`` produces no
    # project predicate, so the search spans every project.
    must_conditions: List[qmodels.FieldCondition] = []
    if source_id:
        must_conditions.append(
            qmodels.FieldCondition(
                key="source_id",
                match=qmodels.MatchValue(value=source_id),
            )
        )
    project_filter = _pc_qdrant_project_filter(project_id)
    if project_filter and "must" in project_filter:
        for cond in project_filter["must"]:
            key = cond.get("key")
            match_spec = cond.get("match") or {}
            if not key:
                continue
            # The project scope filter carries either an exact value or the
            # LIKE/prefix key set ("any") — map both onto Qdrant conditions.
            if "value" in match_spec:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchValue(value=match_spec["value"]),
                    )
                )
            elif "any" in match_spec:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchAny(any=match_spec["any"]),
                    )
                )
    qdrant_filter = qmodels.Filter(must=must_conditions) if must_conditions else None

    payloads_by_key: Dict[Any, Dict[str, Any]] = {}
    probed_stores: List[str] = []
    found_any = False
    search_targets = _qdrant_search_targets(project_id, collection)
    # Hybrid: one sparse encode for the whole fan-out, fused only when EVERY
    # probed collection declares the sparse vector (all-or-none, so the
    # score-based merge below always compares one scale).
    sparse_vector: tuple[list[int], list[float]] | None = None
    use_fusion = False
    if query_text:
        sparse_vector = _query_sparse_encode(query_text)
        if sparse_vector is not None:
            try:
                use_fusion = _fanout_supports_sparse(search_targets)
            except Exception:
                use_fusion = False
            if not use_fusion:
                sparse_vector = None
    for qdrant, collection_names in search_targets:
        available: Optional[set] = None
        if hasattr(qdrant, "list_collection_names"):
            try:
                available = set(qdrant.list_collection_names())
            except Exception as exc:
                logger.warning(
                    "Listing collections on %s failed: %s",
                    getattr(qdrant, "url", None) or "local instance store",
                    exc,
                )
                continue
            probed_stores.append(
                getattr(qdrant, "url", None) or "local instance store"
            )
        if collection_names is None:
            collection_names = sorted(available or [])
        present = [
            name for name in collection_names
            if available is None or name in available
        ]
        if collection_names and not present:
            continue
        if present:
            found_any = True
        for collection_name in present:
            try:
                if sparse_vector is not None and hasattr(qdrant, "query_points"):
                    # Fused dense+sparse RRF (same hit shape; rank-based scores).
                    from tools.common.qdrant_query_support import hybrid_fusion_query

                    fusion_kwargs = hybrid_fusion_query(
                        query_vector,
                        sparse_vector,
                        vector_name=None,
                        top_k=top_k,
                        query_filter=qdrant_filter,
                    )
                    hits = qdrant.query_points(
                        collection_name=collection_name,
                        with_payload=True,
                        with_vectors=False,
                        **fusion_kwargs,
                    ).points
                elif hasattr(qdrant, "search"):
                    hits = qdrant.search(
                        collection_name=collection_name,
                        query_vector=query_vector,
                        limit=top_k,
                        query_filter=qdrant_filter,
                    )
                else:
                    hits = qdrant.query_points(
                        collection_name=collection_name,
                        query=query_vector,
                        limit=top_k,
                        query_filter=qdrant_filter,
                    ).points
            except Exception as exc:
                if len(present) == 1:
                    raise
                logger.warning("Skipping unavailable doc collection %s: %s", collection_name, exc)
                continue

            for hit in hits:
                if not hit.payload:
                    continue
                key = (
                    hit.payload.get("project_id_normalized"),
                    hit.payload.get("source_id"),
                    hit.payload.get("paragraph_id"),
                    hit.payload.get("text"),
                )
                row = {
                    "score": getattr(hit, "score", None),
                    "text": hit.payload.get("text"),
                    "source_id": hit.payload.get("source_id"),
                    "paragraph_id": hit.payload.get("paragraph_id"),
                    "project_id": hit.payload.get("project_id"),
                    "project_id_normalized": hit.payload.get("project_id_normalized"),
                    "entity_ids": hit.payload.get("entity_ids") or [],
                    "entity_mentions": hit.payload.get("entity_mentions") or [],
                    "collection": collection_name,
                }
                existing = payloads_by_key.get(key)
                if existing is None or float(row.get("score") or 0.0) > float(
                    existing.get("score") or 0.0
                ):
                    payloads_by_key[key] = row
    if not found_any and (project_id or collection):
        # Scoped query, but no reachable store had the requested collection.
        # Surface which stores were probed so a remote-only shard is not
        # mistaken for missing data.
        detail = ", ".join(_resolve_doc_collections(project_id, collection))
        if probed_stores:
            detail += f" (checked: {', '.join(probed_stores)})"
        raise LookupError(
            "Requested document collection is not ingested or unavailable: " + detail
        )
    payloads = list(payloads_by_key.values())
    payloads.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    return payloads[:top_k]


def fetch_entities_by_ids(
    entity_ids: List[str],
    project_id: Optional[str] = None,
    entity_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    types = entity_types or []
    entities: List[Dict[str, Any]] = []
    seen = set()
    for store, owned in _graph_store_candidates(project_id):
        try:
            with store.session() as session:
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.id IN $ids
                      AND ($types = [] OR e.type IN $types)
                      AND ($project_id_normalized IS NULL OR
                           e.project_id_normalized STARTS WITH $project_id_normalized)
                    RETURN e.id AS id, e.name AS name, e.type AS type
                    """,
                    ids=entity_ids,
                    types=types,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                for record in result:
                    row = dict(record)
                    key = row.get("id") or (row.get("name"), row.get("type"))
                    if key in seen:
                        continue
                    seen.add(key)
                    entities.append(row)
        finally:
            if owned:
                store.close()
    return entities[: len(entity_ids)]


def fetch_relations_by_entity_ids(
    entity_ids: List[str],
    entity_types: Optional[List[str]],
    related_k: int,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not entity_ids or related_k <= 0:
        return []
    entity_types = entity_types or []
    relations: List[Dict[str, Any]] = []
    seen = set()
    for store, owned in _graph_store_candidates(project_id):
        remaining = related_k - len(relations)
        if remaining <= 0:
            break
        try:
            with store.session() as session:
                result = session.run(
                    """
                    UNWIND $ids AS id
                    MATCH (e:Entity {id: id})-[r:RELATED]-(e2:Entity)
                    WHERE ($types = [] OR e.type IN $types OR e2.type IN $types)
                      AND ($project_id_normalized IS NULL OR
                           (e.project_id_normalized STARTS WITH $project_id_normalized AND
                            e2.project_id_normalized STARTS WITH $project_id_normalized))
                    RETURN e.id AS source_id, e.name AS source, e.type AS source_type,
                           r.type AS relation,
                           e2.id AS target_id, e2.name AS target, e2.type AS target_type
                    LIMIT $limit
                    """,
                    ids=entity_ids,
                    types=entity_types,
                    limit=remaining,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                for record in result:
                    row = dict(record)
                    key = (
                        row.get("source_id"),
                        row.get("relation"),
                        row.get("target_id"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    relations.append(row)
                    if len(relations) >= related_k:
                        break
        finally:
            if owned:
                store.close()
    return relations


def _entity_occurrence_counts(payloads: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count in how many retrieved passages each entity id appears."""
    counts: Dict[str, int] = {}
    for row in payloads:
        for entity_id in row.get("entity_ids") or []:
            counts[entity_id] = counts.get(entity_id, 0) + 1
    return counts


def _rank_entities_by_occurrence(
    entities: List[Dict[str, Any]],
    entity_ids: List[str],
    counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Order entities by passage frequency (desc); ties keep first-seen order."""
    first_seen = {eid: idx for idx, eid in enumerate(entity_ids)}
    unseen_rank = len(first_seen)

    def sort_key(row: Dict[str, Any]) -> Tuple[int, int]:
        eid = row.get("id")
        return (-(counts.get(eid) or 0), first_seen.get(eid, unseen_rank))

    return sorted(entities, key=sort_key)


def _filter_entity_ids_for_expansion(
    entity_ids: List[str],
    payloads: List[Dict[str, Any]],
    min_score_to_expand: Optional[float],
    min_entity_occurrences: Optional[int],
) -> List[str]:
    if not entity_ids:
        return []
    if min_score_to_expand is not None:
        scores = [row.get("score") for row in payloads if row.get("score") is not None]
        if scores and max(scores) < min_score_to_expand:
            return []
    if min_entity_occurrences and min_entity_occurrences > 1:
        counts = _entity_occurrence_counts(payloads)
        return [eid for eid in entity_ids if counts.get(eid, 0) >= min_entity_occurrences]
    return entity_ids


def fetch_relations_with_depth(
    entity_ids: List[str],
    entity_types: Optional[List[str]],
    related_k: int,
    depth: int,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not entity_ids or related_k <= 0 or depth <= 0:
        return []
    relations: List[Dict[str, Any]] = []
    seen_relations = set()
    seen_entities = set(entity_ids)
    frontier = list(entity_ids)
    remaining = related_k

    for _ in range(depth):
        if not frontier or remaining <= 0:
            break
        step_relations = fetch_relations_by_entity_ids(
            frontier, entity_types, remaining, project_id=project_id
        )
        new_frontier = set()
        for rel in step_relations:
            rel_key = (
                rel.get("source_id"),
                rel.get("relation"),
                rel.get("target_id"),
            )
            if rel_key in seen_relations:
                continue
            seen_relations.add(rel_key)
            relations.append(rel)
            source_id = rel.get("source_id")
            target_id = rel.get("target_id")
            if source_id and source_id not in seen_entities:
                new_frontier.add(source_id)
            if target_id and target_id not in seen_entities:
                new_frontier.add(target_id)

        remaining = related_k - len(relations)
        if not new_frontier:
            break
        seen_entities.update(new_frontier)
        frontier = list(new_frontier)

    return relations


def fetch_related_entity_ids(
    entity_ids: List[str],
    related_k: int,
    depth: int,
    entity_types: Optional[List[str]] = None,
    project_id: Optional[str] = None,
) -> List[str]:
    """Neighbor entity ids reached via ``RELATED`` edges within ``depth`` hops."""
    if not entity_ids or related_k <= 0 or depth <= 0:
        return []
    neighbors: List[str] = []
    seen = set(entity_ids)
    frontier = list(entity_ids)
    for _ in range(depth):
        if not frontier or len(neighbors) >= related_k:
            break
        step = fetch_relations_by_entity_ids(
            frontier, entity_types, related_k - len(neighbors), project_id=project_id
        )
        new_frontier: List[str] = []
        for rel in step:
            for key in ("source_id", "target_id"):
                neighbor_id = rel.get(key)
                if neighbor_id and neighbor_id not in seen:
                    seen.add(neighbor_id)
                    neighbors.append(neighbor_id)
                    new_frontier.append(neighbor_id)
        frontier = new_frontier
    return neighbors[:related_k]


def fetch_paragraphs_for_entities(
    entity_ids: List[str],
    entity_types: Optional[List[str]] = None,
    exclude_types: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Paragraph rows mentioning any of ``entity_ids`` (``HAS_ENTITY`` edges).

    Returns flat rows of ``{source_id, paragraph_id, text, entity_id,
    entity_name}``, one per (paragraph, entity) pair, capped at ``limit``.
    ``entity_types`` whitelists entity types; ``exclude_types`` blacklists
    them (e.g. YAKE ``KEYWORD`` noise) — an empty include list means all
    types.
    """
    if not entity_ids or limit <= 0:
        return []
    types = entity_types or []
    excludes = exclude_types or []
    rows: List[Dict[str, Any]] = []
    for store, owned in _graph_store_candidates(project_id):
        remaining = limit - len(rows)
        if remaining <= 0:
            break
        try:
            with store.session() as session:
                result = session.run(
                    """
                    UNWIND $ids AS id
                    MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity {id: id})
                    WHERE ($types = [] OR e.type IN $types)
                      AND ($excludes = [] OR NOT e.type IN $excludes)
                      AND ($project_id_normalized IS NULL OR
                           (p.project_id_normalized STARTS WITH $project_id_normalized AND
                            e.project_id_normalized STARTS WITH $project_id_normalized))
                    RETURN p.source_id AS source_id, p.paragraph_id AS paragraph_id,
                           p.text AS text, e.id AS entity_id, e.name AS entity_name
                    LIMIT $limit
                    """,
                    ids=entity_ids,
                    types=types,
                    excludes=excludes,
                    limit=remaining,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                for record in result:
                    rows.append(dict(record))
        finally:
            if owned:
                store.close()
    return rows[:limit]


def build_related_documents(
    anchor_passages: List[Dict[str, Any]],
    anchor_entity_ids: List[str],
    neighbor_entity_ids: List[str],
    entity_types: Optional[List[str]] = None,
    exclude_types: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    max_docs: int = 5,
    paragraphs_per_doc: int = 2,
    scan_limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Group entity-linked paragraphs into documents related to the anchors.

    A document's score is the sum of weights of the distinct expansion entities
    it shares with the vector-search anchors (anchor entity 1.0, entity reached
    via ``RELATED`` 0.5). Anchor passages themselves are excluded.
    """
    if not anchor_entity_ids and not neighbor_entity_ids:
        return []
    weights: Dict[str, float] = {eid: 1.0 for eid in anchor_entity_ids}
    for eid in neighbor_entity_ids:
        weights.setdefault(eid, 0.5)
    anchor_pairs = {
        (row.get("source_id"), row.get("paragraph_id"))
        for row in anchor_passages
    }
    rows = fetch_paragraphs_for_entities(
        list(weights),
        entity_types=entity_types,
        exclude_types=exclude_types,
        project_id=project_id,
        limit=scan_limit,
    )

    docs: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        source_id = row.get("source_id")
        paragraph_id = row.get("paragraph_id")
        if source_id is None or (source_id, paragraph_id) in anchor_pairs:
            continue
        doc = docs.setdefault(source_id, {
            "matched_entity_ids": set(),
            "linked_entities": [],
            "paragraphs": {},
        })
        entity_id = row.get("entity_id")
        counted = entity_id in weights
        if counted and entity_id not in doc["matched_entity_ids"]:
            doc["matched_entity_ids"].add(entity_id)
            name = row.get("entity_name")
            if name:
                doc["linked_entities"].append(name)
        para = doc["paragraphs"].get(paragraph_id)
        if para is None:
            doc["paragraphs"][paragraph_id] = {
                "paragraph_id": paragraph_id,
                "text": row.get("text"),
                "matched_entity_count": 1 if counted else 0,
            }
        elif counted:
            para["matched_entity_count"] += 1

    ranked = []
    for source_id, doc in docs.items():
        score = sum(weights.get(eid, 0.0) for eid in doc["matched_entity_ids"])
        ranked.append((score, len(doc["paragraphs"]), source_id, doc))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))

    results: List[Dict[str, Any]] = []
    for score, _, source_id, doc in ranked[: max(0, max_docs)]:
        paragraphs = sorted(
            doc["paragraphs"].values(),
            key=lambda p: (-p["matched_entity_count"], p["paragraph_id"] or 0),
        )[: max(0, paragraphs_per_doc)]
        results.append({
            "source_id": source_id,
            "score": round(score, 3),
            "linked_entities": doc["linked_entities"],
            "paragraphs": paragraphs,
        })
    return results


def _compute_heuristic_rerank_score(
    passage: Dict[str, Any],
    entity_types: List[str],
    entity_weight: float,
    type_weight: float,
    confidence_weight: float,
    length_penalty: float,
) -> float:
    base_score = passage.get("score") or 0.0
    text = passage.get("text") or ""
    mentions = passage.get("_entity_mentions") or []
    unique_entities = set()
    type_hits = 0
    confidences: List[float] = []

    for item in mentions:
        entity_id = item.get("id") or item.get("name")
        if entity_id:
            unique_entities.add(entity_id)
        if item.get("type") in entity_types:
            type_hits += 1
        conf = item.get("confidence")
        if conf is not None:
            confidences.append(float(conf))

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    penalty = length_penalty * len(text) if length_penalty else 0.0
    return (
        base_score
        + entity_weight * len(unique_entities)
        + type_weight * type_hits
        + confidence_weight * avg_conf
        - penalty
    )


def _apply_heuristic_rerank(
    passages: List[Dict[str, Any]],
    entity_types: List[str],
    entity_weight: float,
    type_weight: float,
    confidence_weight: float,
    length_penalty: float,
) -> List[Dict[str, Any]]:
    for passage in passages:
        passage["rerank_score"] = _compute_heuristic_rerank_score(
            passage,
            entity_types,
            entity_weight,
            type_weight,
            confidence_weight,
            length_penalty,
        )
    return sorted(passages, key=lambda row: row.get("rerank_score", 0.0), reverse=True)


def fetch_paragraph_by_source(
    source_id: str,
    paragraph_id: int,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for store, owned in _graph_store_candidates(project_id):
        try:
            with store.session() as session:
                result = session.run(
                    """
                    MATCH (p:Paragraph {source_id: $source_id, paragraph_id: $paragraph_id})
                    WHERE $project_id_normalized IS NULL OR
                          p.project_id_normalized STARTS WITH $project_id_normalized
                    RETURN p.text AS text,
                           p.short AS short,
                           p.source_id AS source_id,
                           p.paragraph_id AS paragraph_id
                    """,
                    source_id=source_id,
                    paragraph_id=paragraph_id,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                record = result.single()
                if record:
                    return dict(record)
        finally:
            if owned:
                store.close()
    return None


def _standard_tool(mcp: FastMCP):
    """Register a Mind tool with the shared Cortex MCP result contract."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            try:
                data = function(*args, **kwargs)
            except Exception as exc:
                envelope = normalize_error(exc)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=result_summary(
                                None,
                                ok=False,
                                message=envelope["error"]["message"],
                            ),
                        )
                    ],
                    structuredContent=envelope,
                    isError=True,
                    _meta=result_meta(function.__name__),
                )

            envelope = normalize_success(data)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=result_summary(data, ok=True),
                    )
                ],
                structuredContent=envelope,
                isError=False,
                _meta=result_meta(function.__name__),
            )

        # Keep typed inputs, but do not let the legacy SDK infer an output
        # model from the business function's original return annotation.  The
        # wrapper returns a complete CallToolResult with the shared envelope.
        wrapped.__annotations__ = {
            key: value
            for key, value in function.__annotations__.items()
            if key != "return"
        }
        wrapped.__signature__ = inspect.signature(function).replace(
            return_annotation=inspect.Signature.empty
        )
        return mcp.tool()(wrapped)

    return decorate




def register_tools(mcp: FastMCP) -> None:
    @_standard_tool(mcp)
    def list_source_ids(
        limit: int = 50,
        project_id: Optional[str] = None,
    ) -> List[str]:
        """List available source_id values from the graph store (Paragraph nodes)."""
        limit_val = max(0, int(limit))
        source_ids: List[str] = []
        seen = set()
        for store, owned in _graph_store_candidates(project_id):
            if len(source_ids) >= limit_val:
                break
            try:
                with store.session() as session:
                    result = session.run(
                        """
                        MATCH (p:Paragraph)
                        WHERE p.source_id IS NOT NULL
                          AND ($project_id_normalized IS NULL OR
                               p.project_id_normalized STARTS WITH $project_id_normalized)
                        RETURN DISTINCT p.source_id AS source_id
                        ORDER BY source_id
                        LIMIT $limit
                        """,
                        limit=limit_val - len(source_ids),
                        project_id_normalized=(project_id.strip().casefold() if project_id else None),
                    )
                    for record in result:
                        source_id = record["source_id"]
                        if source_id not in seen:
                            seen.add(source_id)
                            source_ids.append(source_id)
            finally:
                if owned:
                    store.close()
        return source_ids[:limit_val]

    @_standard_tool(mcp)
    def list_qdrant_collections(
        project_id: Optional[str] = None,
    ) -> List[str]:
        """List Qdrant collections.

        Per the unified ingest/query contract:
        - ``project_id`` is optional. When omitted (or empty), returns every
          collection visible across the registered projects' backends plus
          the local instance store (``None`` semantics = full-search).
        - When supplied, narrows to that project's resolved doc collection:
          the exact registry entry, or — for unregistered ids — the backend
          names matching the raw id (exact first, then prefix), with the
          ``{project_id}_doc`` naming convention as last resort.
        """
        names: List[str] = []
        for qdrant, scope_names in _qdrant_search_targets(project_id, None):
            if not hasattr(qdrant, "list_collection_names"):
                continue
            try:
                available = qdrant.list_collection_names()
            except Exception:
                continue
            for name in available:
                if scope_names is not None and name not in scope_names:
                    continue
                if name not in names:
                    names.append(name)
        return names

    @_standard_tool(mcp)
    def semantic_search(
        query: str,
        top_k: int = 5,
        source_id: Optional[str] = None,
        collection: Optional[str] = None,
        project_id: Optional[str] = None,
        max_passage_chars: Optional[int] = None,
        include_entity_ids: bool = False,
        include_entity_mentions: bool = False,
    ) -> Dict[str, Any]:
        """Vector-only search in Qdrant. Returns passages without graph expansion.

        Per Phase 05 of the unified ingest/query contract plan, ``project_id``
        scopes the query to one project's shard; omit it to search across all
        projects. The Qdrant collection is resolved through the registry when
        ``project_id`` is given; the explicit ``collection`` arg still wins as
        an escape hatch.

        Raw entity UUIDs (``passages[].entity_ids`` and the ``id`` key of
        ``entity_mentions`` entries) are stripped by default to keep the
        response compact; set ``include_entity_ids=true`` to keep them for
        debugging.
        """
        # Type coercion to handle n8n passing strings
        query = str(query) if query else ""
        top_k = int(top_k) if top_k is not None else 5
        max_passage_chars = int(max_passage_chars) if max_passage_chars is not None else None
        include_entity_ids = _coerce_bool(include_entity_ids, False)
        include_entity_mentions = _coerce_bool(include_entity_mentions, False)

        q_vec = _query_encode(query)

        payloads = qdrant_search_entity_payload(
            q_vec,
            top_k=top_k,
            source_id=source_id,
            collection=collection,
            project_id=project_id,
            query_text=query,
        )

        passages = []
        for row in payloads:
            text = row.get("text") or ""
            if max_passage_chars:
                text = text[:max_passage_chars]
            passage = {
                "text": text,
                "score": row.get("score"),
                "source_id": row.get("source_id"),
                "paragraph_id": row.get("paragraph_id"),
            }
            if include_entity_ids:
                passage["entity_ids"] = row.get("entity_ids") or []
            if include_entity_mentions:
                mentions = []
                for mention in row.get("entity_mentions") or []:
                    if not include_entity_ids:
                        mention = {k: v for k, v in mention.items() if k != "id"}
                    mentions.append(mention)
                passage["entity_mentions"] = mentions
            passages.append(passage)

        response: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "source_id": source_id,
            "collection": _resolve_doc_collection(project_id, collection),
            "collections_searched": _resolve_doc_collections(project_id, collection),
            "passages": passages,
        }
        mismatch_note = _embedding_model_mismatch_note(project_id, collection)
        if mismatch_note:
            response["embedding_model_mismatch"] = mismatch_note
        return response

    @_standard_tool(mcp)
    def semantic_graph_search(
        query: str,
        top_k: int = 5,
        source_id: Optional[str] = None,
        collection: Optional[str] = None,
        include_entities: bool = False,
        include_relations: bool = True,
        expand_related: bool = True,
        related_k: int = 50,
        graph_depth: int = 1,
        entity_types: Optional[List[str] | str] = None,
        max_passage_chars: Optional[int] = None,
        min_score_to_expand: Optional[float] = None,
        min_entity_occurrences: Optional[int] = None,
        rerank: bool = False,
        rerank_entity_weight: float = 0.05,
        rerank_type_weight: float = 0.1,
        rerank_confidence_weight: float = 0.3,
        rerank_length_penalty: float = 0.0002,
        project_id: Optional[str] = None,
        include_entity_ids: bool = False,
        max_entities: int = 30,
        expand_documents: bool = True,
        related_docs: int = 5,
        related_paragraphs_per_doc: int = 2,
    ) -> Dict[str, Any]:
        """
        Vector-search passages, then walk the entity graph to related documents.

        Given ``query``, retrieves the top-k passages and collects the entities
        those passages mention. It then finds documents that share those
        entities via ``(Paragraph)-[:HAS_ENTITY]->(Entity)`` edges, optionally
        widening the entity set through ``:RELATED`` edges first
        (``graph_depth`` hops, capped by ``related_k``). The matched documents
        are returned in ``related_documents`` (ranked by how many distinct
        expansion entities they share with the anchors), so callers get related
        documents without having to consume the entity graph themselves — the
        entities stay internal unless ``include_entities=true``.

        Raw entity UUIDs (``entities[].id`` and ``relations[].source_id``/
        ``target_id``) are stripped from the response by default to keep it
        compact; graph expansion still uses them internally. Set
        ``include_entity_ids=true`` to keep them for debugging.

        The expansion honors ``entity_types`` (the default curated set
        excludes extractor noise such as YAKE ``KEYWORD`` entries — pass the
        type explicitly, e.g. ``entity_types="KEYWORD"``, to reveal it).
        """
        # Type coercion to handle n8n passing strings
        query = str(query) if query else ""
        top_k = int(top_k) if top_k is not None else 5
        related_k = int(related_k) if related_k is not None else 50
        graph_depth = int(graph_depth) if graph_depth is not None else 1
        include_entities = _coerce_bool(include_entities, True)
        include_relations = _coerce_bool(include_relations, True)
        expand_related = _coerce_bool(expand_related, True)
        rerank = _coerce_bool(rerank, False)
        rerank_entity_weight = float(rerank_entity_weight) if rerank_entity_weight is not None else 0.05
        rerank_type_weight = float(rerank_type_weight) if rerank_type_weight is not None else 0.1
        rerank_confidence_weight = float(rerank_confidence_weight) if rerank_confidence_weight is not None else 0.3
        rerank_length_penalty = float(rerank_length_penalty) if rerank_length_penalty is not None else 0.0002
        max_passage_chars = int(max_passage_chars) if max_passage_chars is not None else None
        min_score_to_expand = float(min_score_to_expand) if min_score_to_expand is not None else None
        min_entity_occurrences = int(min_entity_occurrences) if min_entity_occurrences is not None else None
        max_entities = int(max_entities) if max_entities is not None else 30
        include_entity_ids = _coerce_bool(include_entity_ids, False)
        expand_documents = _coerce_bool(expand_documents, True)
        related_docs = int(related_docs) if related_docs is not None else 5
        related_paragraphs_per_doc = (
            int(related_paragraphs_per_doc)
            if related_paragraphs_per_doc is not None else 2
        )

        q_vec = _query_encode(query)

        types_explicit = entity_types is not None
        if entity_types is None:
            entity_types = list(ENTITY_TYPES_DEFAULT)
        elif isinstance(entity_types, str):
            entity_types = [t.strip() for t in entity_types.split(",") if t.strip()]

        payloads = qdrant_search_entity_payload(
            q_vec,
            top_k=top_k,
            source_id=source_id,
            collection=collection,
            project_id=project_id,
            query_text=query,
        )

        passages = []
        entity_ids: List[str] = []
        for row in payloads:
            text = row.get("text") or ""
            if max_passage_chars:
                text = text[:max_passage_chars]
            passages.append(
                {
                    "text": text,
                    "score": row.get("score"),
                    "source_id": row.get("source_id"),
                    "paragraph_id": row.get("paragraph_id"),
                    "_entity_mentions": row.get("entity_mentions") or [],
                }
            )
            entity_ids.extend(row.get("entity_ids") or [])

        entity_ids = list(dict.fromkeys(entity_ids))
        entity_ids = _filter_entity_ids_for_expansion(
            entity_ids,
            payloads,
            min_score_to_expand=min_score_to_expand,
            min_entity_occurrences=min_entity_occurrences,
        )

        entities = []
        if include_entities:
            entities = fetch_entities_by_ids(
                entity_ids, project_id=project_id, entity_types=entity_types
            )
            # Most-referenced entities first, bounded — extractor noise (e.g.
            # YAKE KEYWORDs) lives or dies by the entity_types filter above.
            entities = _rank_entities_by_occurrence(
                entities, entity_ids, _entity_occurrence_counts(payloads)
            )[:max_entities]
        relations = []
        if include_relations and expand_related:
            relations = fetch_relations_with_depth(
                entity_ids, entity_types, related_k, graph_depth,
                project_id=project_id,
            )

        # Document expansion: use the anchor entities (plus RELATED neighbors)
        # to surface documents that share them, instead of returning the
        # entity graph itself. By default every entity type counts toward
        # relatedness except extractor noise (KEYWORD); an explicit
        # entity_types argument narrows the expansion to those types.
        related_documents: List[Dict[str, Any]] = []
        if expand_documents and entity_ids:
            neighbor_ids: List[str] = []
            if expand_related and related_k > 0 and graph_depth > 0:
                neighbor_ids = fetch_related_entity_ids(
                    entity_ids, related_k, graph_depth,
                    entity_types=entity_types, project_id=project_id,
                )
            related_documents = build_related_documents(
                payloads,
                entity_ids,
                neighbor_ids,
                entity_types=entity_types if types_explicit else None,
                exclude_types=[] if types_explicit else ["KEYWORD"],
                project_id=project_id,
                max_docs=related_docs,
                paragraphs_per_doc=related_paragraphs_per_doc,
            )

        if rerank:
            passages = _apply_heuristic_rerank(
                passages,
                entity_types,
                rerank_entity_weight,
                rerank_type_weight,
                rerank_confidence_weight,
                rerank_length_penalty,
            )
            for passage in passages:
                passage.pop("_entity_mentions", None)
        else:
            for passage in passages:
                passage.pop("_entity_mentions", None)

        # Strip raw entity UUIDs last: relation dedupe and graph expansion above
        # still key on source_id/target_id.
        if not include_entity_ids:
            for entity in entities:
                entity.pop("id", None)
            for relation in relations:
                relation.pop("source_id", None)
                relation.pop("target_id", None)

        response_hybrid: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "source_id": source_id,
            "collection": _resolve_doc_collection(project_id, collection),
            "collections_searched": _resolve_doc_collections(project_id, collection),
            "graph_depth": graph_depth,
            "min_score_to_expand": min_score_to_expand,
            "min_entity_occurrences": min_entity_occurrences,
            "rerank_applied": rerank,
            "rerank_strategy": "heuristic" if rerank else None,
            "passages": passages,
            "related_documents": related_documents,
            "entities": entities,
            "relations": relations,
            "expand_documents": expand_documents,
        }
        mismatch_note = _embedding_model_mismatch_note(project_id, collection)
        if mismatch_note:
            response_hybrid["embedding_model_mismatch"] = mismatch_note
        return response_hybrid

    @_standard_tool(mcp)
    def get_paragraph_text(
        source_id: str,
        paragraph_id: int,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a paragraph's text by source_id + paragraph_id from the graph store."""
        # Type coercion to handle n8n passing strings
        source_id = str(source_id) if source_id else None
        paragraph_id = int(paragraph_id) if paragraph_id is not None else 0
        
        if not source_id:
            raise ValueError("source_id is required.")
        record = fetch_paragraph_by_source(
            source_id, paragraph_id, project_id=project_id
        )
        if not record:
            return {
                "source_id": source_id,
                "paragraph_id": paragraph_id,
                "text": None,
                "found": False,
            }
        return {**record, "found": True}


if __name__ == "__main__":
    force_quit = {"armed": False}

    def _handle_sigint(signum, _frame) -> None:
        if force_quit["armed"]:
            print("Force quitting now.")
            os._exit(0)
        force_quit["armed"] = True
        if signum == signal.SIGTERM:
            print("Received SIGTERM. Send again to force quit.")
        else:
            print("Received SIGINT. Press Ctrl+C again to force quit.")

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, _handle_sigint)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="streamable-http",
    )
    parser.add_argument("--host", default=os.getenv("FASTMCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FASTMCP_PORT", "8789")))
    parser.add_argument(
        "--path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path",
    )
    parser.add_argument(
        "--stream-path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path (deprecated, use --path)",
    )
    args = parser.parse_args()

    # FastMCP 4.x moved transport config (host/port/path/stateless_http/
    # json_response) out of the constructor into ``run`` kwargs. Mirrors the
    # pattern used by code-tiny's unified_mcp.py.
    mcp = FastMCP(MCP_NAME)
    register_tools(mcp)
    transport = args.transport
    if args.stream_path and not args.stream_path.startswith("/"):
        args.stream_path = "/" + args.stream_path
    endpoint = f"http://{args.host}:{args.port}{args.stream_path}"
    print(f"Starting MCP server: {MCP_NAME}")
    print(f"Transport: {transport}")
    if transport == "streamable-http":
        print(f"Endpoint: {endpoint}")
    else:
        print("Endpoint: (stdio)")
    run_kwargs: dict = {"transport": transport}
    if transport != "stdio":
        run_kwargs.update({"host": args.host, "port": args.port})
        if args.stream_path:
            run_kwargs["path"] = args.stream_path
    if transport == "streamable-http":
        run_kwargs["stateless_http"] = True
        run_kwargs["json_response"] = True
    mcp.run(**run_kwargs)
