"""Shared vector-query helpers for every MCP backend.

Production `semantic_search` traffic is dispatched by ``unified_mcp`` to one
backend module (``cplus`` by default), but android/java/fastmcp keep
byte-parallel copies of the search pipeline. Every behavior change lives
here so all four backends delegate to one implementation:

* payload narrowing — ``text`` (up to 16k chars) is excluded from search
  reads; the display preview / full text is fetched lazily per top-k hit.
* merge provenance — point ids can collide across collections (uuid5 is
  deterministic), so every hit is tagged ``_collection`` before merging.
* metadata caching — collection info round-trips go through
  :mod:`tools.common.qdrant_layout_cache`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from qdrant_client.http import models as qmodels

from tools.common import embedding_marker
from tools.common import qdrant_layout_cache
from tools.common.local_qdrant import (
    model_to_dict,
    normalize_filter,
)

logger = logging.getLogger("tools.common.qdrant_query_support")

# Exclude (never Include): no writer-family field list needs maintaining —
# kotlin/android_kotlin payloads carry class_name/package_name that a
# hand-built include list would silently drop (red team #5).
PAYLOAD_EXCLUDE_SELECTOR = qmodels.PayloadSelectorExclude(exclude=["text"])

CONTENT_FIELDS = ("summary", "comment", "code")
PREVIEW_CHARS = 400

# Hybrid retrieval: the named sparse vector writers declare at creation and
# the prefetch depth per fusion channel.
HYBRID_SPARSE_VECTOR_NAME = "sparse"
DEFAULT_FUSE_K_MIN = 64
DEFAULT_FUSE_K_MULTIPLIER = 4


def fuse_k_from_env(top_k: int) -> int:
    """Prefetch depth per fusion channel: ``max(4×top_k, 64)``.

    ``EMBED_FUSE_K`` overrides for operators (red team F8: payload grows
    with channels × fan-out, so the knob stays available).
    """
    raw = str(os.environ.get("EMBED_FUSE_K", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return max(DEFAULT_FUSE_K_MULTIPLIER * max(1, int(top_k)), DEFAULT_FUSE_K_MIN)


def fanout_supports_sparse(
    store: Any,
    collections: Sequence[Tuple[str, Optional[str]]],
    qdrant_url: str = "",
    loader: Any = None,
) -> bool:
    """All-or-none fusion capability across a fan-out (red-team F1).

    ``merge_hits`` compares scores directly, so RRF and cosine must never
    mix inside one fan-out: only when EVERY selected collection declares
    the sparse vector does the whole fan-out fuse. ``_mess`` hash-vector
    collections always read as not-hybrid (red-team F6). Requires the
    ``EMBED_HYBRID`` flag as well — a collection upgraded ahead of a flag
    flip keeps serving dense-only.
    """
    from tools.common.embed_runtime import hybrid_enabled
    from tools.common.local_qdrant import MESSAGE_COLLECTION_SUFFIX

    if not collections or not hybrid_enabled():
        return False
    for name, _vector_name in collections:
        col = str(name)
        if col.endswith(MESSAGE_COLLECTION_SUFFIX):
            return False
        try:
            if not qdrant_layout_cache.collection_has_sparse(
                qdrant_url, col, loader=loader or (lambda store=store: store)
            ):
                return False
        except Exception:  # noqa: BLE001 - probe failure → dense-only fan-out
            return False
    return True


def hybrid_fusion_query(
    dense: Sequence[float],
    sparse: Optional[Tuple[List[int], List[float]]],
    *,
    vector_name: Optional[str],
    top_k: int,
    query_filter: Any,
) -> Dict[str, Any]:
    """``query_points`` kwargs for dense+sparse RRF fusion.

    Both prefetch channels carry the caller's filter (which already
    excludes the embedding-model sentinel); the dense channel keeps the
    legacy ``using`` resolution (unnamed default via ``""`` or the named
    dense vector of legacy layouts), the sparse channel uses the fixed
    ``sparse`` name. RRF is rank-based, so the two channels' score scales
    never need to be comparable.
    """
    fuse_k = fuse_k_from_env(top_k)
    prefetch: List[qmodels.Prefetch] = [
        qmodels.Prefetch(
            query=list(dense),
            using=vector_name or "",
            limit=fuse_k,
            filter=query_filter,
        ),
    ]
    if sparse:
        indices, values = sparse
        prefetch.append(
            qmodels.Prefetch(
                query=qmodels.SparseVector(indices=list(indices), values=list(values)),
                using=HYBRID_SPARSE_VECTOR_NAME,
                limit=fuse_k,
                filter=query_filter,
            ),
        )
    hnsw_ef = hnsw_ef_from_env()
    search_params = qmodels.SearchParams(hnsw_ef=hnsw_ef) if hnsw_ef is not None else None
    dense_prefetch = prefetch[0]
    if search_params is not None and dense_prefetch.search_params is None:
        dense_prefetch.search_params = search_params
    return {
        "prefetch": prefetch,
        "query": qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
        "limit": int(top_k),
        "query_filter": query_filter,
    }


def hnsw_ef_from_env() -> Optional[int]:
    """Query-time ``hnsw_ef`` override; unset env → server default."""
    raw = str(os.environ.get("QDRANT_HNSW_EF", "")).strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def _with_sentinel_exclusion(query_filter: Any) -> Any:
    """Add a ``must_not`` clause so the embedding-model sentinel point can
    never surface as a search hit (scoped or unscoped queries alike).

    Always returns a fresh Filter — the caller's model object is never
    mutated (reused filters would otherwise accumulate duplicate clauses).
    """
    base = normalize_filter(query_filter)
    must_not = list(getattr(base, "must_not", None) or [])
    must_not.append(embedding_marker.sentinel_exclusion())
    if base is None:
        return qmodels.Filter(must_not=must_not)
    cloned = base.model_copy(deep=True)
    cloned.must_not = must_not
    return cloned


def search_collection(
    store: Any,
    collection: str,
    vector: Sequence[float],
    vector_name: Optional[str],
    top_k: int,
    project_id: Optional[str] = None,
    query_filter: Any = None,
    embedding_model: Optional[str] = None,
    sparse_vector: Optional[Tuple[List[int], List[float]]] = None,
    fusion: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """One narrowed, provenance-tagged vector search against a collection.

    The embedding-model sentinel point is excluded from every query, and
    when ``embedding_model`` is given, a collection stamped by a different
    model yields a structured mismatch entry in ``errors`` (soft warning —
    results are still returned).

    ``sparse_vector`` + ``fusion=True`` switch this one search to dense+sparse
    RRF fusion (identical response shape; scores become rank-based RRF).
    ``fusion=None`` (default) auto-fuses when a sparse vector is present AND
    this collection declares one; fan-out callers pass the all-or-none
    decision from :func:`fanout_supports_sparse` explicitly.
    """
    from tools.common.project_scope import qdrant_project_filter

    if query_filter is None:
        query_filter = qdrant_project_filter(project_id)
    query_filter = _with_sentinel_exclusion(query_filter)
    if sparse_vector is None:
        use_fusion = False
    elif fusion is None:
        from tools.common.local_qdrant import collection_supports_sparse

        use_fusion = collection_supports_sparse(store, collection)
    else:
        use_fusion = bool(fusion)
    if use_fusion:
        kwargs = hybrid_fusion_query(
            vector,
            sparse_vector,
            vector_name=vector_name,
            top_k=top_k,
            query_filter=query_filter,
        )
    else:
        kwargs: Dict[str, Any] = {}
        if vector_name:
            kwargs["using"] = vector_name
        hnsw_ef = hnsw_ef_from_env()
        if hnsw_ef is not None:
            kwargs["search_params"] = qmodels.SearchParams(hnsw_ef=hnsw_ef)
        kwargs.update({
            "query": list(vector),
            "limit": int(top_k),
            "query_filter": query_filter,
        })
    response = store.query_points(
        collection,
        with_payload=PAYLOAD_EXCLUDE_SELECTOR,
        with_vectors=False,
        **kwargs,
    )
    hits = [model_to_dict(point) for point in getattr(response, "points", response)]
    for hit in hits:
        hit["_collection"] = collection
    if embedding_model:
        mismatch = embedding_marker.mismatch(store, collection, embedding_model)
        if mismatch:
            logger.warning(
                "[semantic_search] embedding model mismatch on %r: indexed by %r, "
                "query model %r (re-index the collection)",
                collection,
                mismatch["stamped_model"],
                mismatch["query_model"],
            )
    return hits


def merge_hits(
    per_collection_hits: Iterable[List[Dict[str, Any]]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Dedupe by point id keeping the higher score (with its provenance).

    Precondition: every input list was produced with the SAME scoring scale —
    all-cosine (dense-only fan-out) or all-RRF (fused fan-out). The
    all-or-none capability gate in :func:`merge_collections` guarantees this;
    mixing scales would silently corrupt the ranking.
    """
    combined: Dict[str, Dict[str, Any]] = {}
    for hits in per_collection_hits:
        for item in hits:
            point_id = str(item.get("id"))
            score = item.get("score", 0)
            existing = combined.get(point_id)
            if existing is None or score > existing.get("score", 0):
                combined[point_id] = item
    return sorted(combined.values(), key=lambda x: x.get("score", 0), reverse=True)[:top_k]


def payload_needs_lazy_text(item: Any) -> bool:
    """Whether a hit lacks every content field (narrowed primary-style hit)."""
    if not isinstance(item, dict) or not item.get("_collection"):
        return False
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return False
    return all(not str(payload.get(field) or "").strip() for field in CONTENT_FIELDS)


def lazy_full_payload(store: Any, hits: List[Dict[str, Any]]) -> None:
    """Re-fetch full payloads for narrowed hits, grouped per collection.

    Mutates ``hits`` in place: fetched summary/comment/code/text (and any
    other stored field) are merged back into each hit's payload. At most
    one ``retrieve`` round-trip per collection, ≤ len(hits) points total.
    """
    ids_by_collection: Dict[str, List[Any]] = {}
    for item in hits:
        collection = item.get("_collection")
        if not collection:
            continue
        ids_by_collection.setdefault(str(collection), []).append(item.get("id"))
    for collection, ids in ids_by_collection.items():
        try:
            points = store.retrieve(
                collection,
                ids,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001 - enrichment must not fail search
            logger.warning(
                "[semantic_search] lazy payload fetch failed for %s: %s", collection, exc
            )
            continue
        fetched: Dict[str, Any] = {}
        for point in points or []:
            point_dict = point if isinstance(point, dict) else model_to_dict(point)
            fetched[str(point_dict.get("id"))] = point_dict.get("payload")
        for item in hits:
            if item.get("_collection") != collection:
                continue
            full_payload = fetched.get(str(item.get("id")))
            if isinstance(full_payload, dict):
                item["payload"].update(full_payload)


def lazy_fetch_missing(
    results: Dict[str, Any],
    mode: str,
    store_loader: Any,
) -> None:
    """Fetch full payloads for narrowed top-k hits when content is needed.

    Called after merge cuts to ``top_k``: hits whose payload lacks every
    content field (primary_vector_sync-style collections store only
    ``text``) get one grouped ``retrieve`` per source collection. The
    ``"name"`` content mode never needs text, so it skips the fetch.
    """
    if mode == "name":
        return
    items = results.get("results") or []
    missing = [item for item in items if payload_needs_lazy_text(item)]
    if missing:
        lazy_full_payload(store_loader(), missing)


def preview_from_text(payload: Dict[str, Any]) -> Optional[str]:
    """Bounded preview of the narrowed-away ``text`` field, or None."""
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + "…"


def select_content_with_fallback(
    payload: Dict[str, Any],
    node_id: Optional[str],
    mode: str,
    fallback_node_name: Any,
    summary_empty_fallback: bool = False,
) -> str:
    """Backend ``_select_content`` behavior + text-preview fallback.

    ``fallback_node_name`` is the backend's own name-fallback callable so
    per-backend naming preferences stay in place. The cplus backend
    historically falls back to the node name when ``mode="summary"`` and
    the summary is empty (``summary_empty_fallback=True``); the other
    backends return the empty string, exactly as before.
    """
    summary = payload.get("summary")
    comment = payload.get("comment")
    code = payload.get("code")
    summary_text = summary if isinstance(summary, str) else ""
    comment_text = comment if isinstance(comment, str) else ""
    code_text = code if isinstance(code, str) else ""
    if mode == "summary":
        if summary_text.strip() or not summary_empty_fallback:
            return summary_text
        return fallback_node_name(payload, node_id)
    if mode == "comment":
        return comment_text
    if mode == "code":
        return code_text
    if mode == "name":
        return fallback_node_name(payload, node_id)
    if summary_text.strip():
        return summary_text
    if comment_text.strip():
        return comment_text
    if not code_text.strip():
        preview = preview_from_text(payload)
        if preview is not None:
            return preview
    return fallback_node_name(payload, node_id)


def merge_collections(
    store: Any,
    collections: List[Tuple[str, Optional[str]]],
    vector: Sequence[float],
    top_k: int,
    project_id: Optional[str] = None,
    embedding_model: Optional[str] = None,
    qdrant_url: str = "",
    sparse_vector: Optional[Tuple[List[int], List[float]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Search every (collection, vector_name) and merge to a single top_k.

    With ``embedding_model`` set, collections stamped by a different model
    produce a structured ``embedding_model_mismatch`` entry in ``errors`` —
    the search still returns results (soft warning, never a failure).

    Hybrid: when the query carries a sparse vector AND the whole fan-out
    supports sparse (:func:`fanout_supports_sparse` — all-or-none, red-team
    F1), every collection runs the fused RRF query; any dense-only member
    downgrades the ENTIRE fan-out to dense-only so ``merge_hits`` compares
    one scale.
    """
    use_fusion = sparse_vector is not None and fanout_supports_sparse(
        store, collections, qdrant_url=qdrant_url
    )
    per_collection: List[List[Dict[str, Any]]] = []
    errors: List[Dict[str, str]] = []
    for col, vector_name in collections:
        try:
            per_collection.append(
                search_collection(
                    store, col, vector, vector_name, top_k, project_id,
                    sparse_vector=sparse_vector,
                    fusion=use_fusion,
                )
            )
            mismatch = embedding_marker.mismatch(store, col, embedding_model)
            if mismatch:
                errors.append(
                    {
                        "collection": col,
                        "embedding_model_mismatch": (
                            f"collection indexed by {mismatch['stamped_model']!r} "
                            f"but query model is {mismatch['query_model']!r}; "
                            "re-index the collection or reset it and ingest again"
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - mirrors backend error contract
            errors.append({"collection": col, "error": str(exc)})
    return merge_hits(per_collection, top_k), errors


def filter_collections_for_vector(
    store: Any,
    collections: List[str],
    vector_len: int,
    qdrant_url: str,
) -> Tuple[List[Tuple[str, Optional[str]]], List[Dict[str, str]]]:
    """Cached variant of the backend collection-filter pipeline."""
    errors: List[Dict[str, str]] = []
    selected: List[Tuple[str, Optional[str]]] = []
    for col in collections:
        try:
            sizes = qdrant_layout_cache.get_collection_meta(
                qdrant_url, col, loader=lambda store=store: store
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"collection": col, "error": str(exc)})
            continue
        if not sizes:
            errors.append({"collection": col, "error": "No matching vector size."})
            continue
        if set(sizes) == {"default"}:
            if sizes.get("default") == vector_len:
                selected.append((col, None))
            else:
                errors.append(
                    {
                        "collection": col,
                        "error": (
                            f"Vector size mismatch (expected {vector_len}, "
                            f"got {sizes.get('default')})"
                        ),
                    }
                )
            continue
        vector_name = _select_named_vector(sizes, vector_len)
        if vector_name is not None:
            selected.append((col, vector_name))
        else:
            errors.append(
                {
                    "collection": col,
                    "error": (
                        f"No matching vector size (expected {vector_len}); "
                        f"available: {sizes}"
                    ),
                }
            )
    return selected, errors


def _select_named_vector(sizes: Dict[str, int], vector_len: int) -> Optional[str]:
    for name, size in sizes.items():
        if size == vector_len:
            return str(name)
    return None


def cached_collections_payload(qdrant_url: str, loader: Any) -> Dict[str, Any]:
    """Collections payload through the layout cache (same shape as before)."""
    names = qdrant_layout_cache.list_collections(qdrant_url, loader=loader)
    return {
        "collections": list(names),
        "raw": {
            "result": {"collections": [{"name": name} for name in names]},
            "status": "ok",
        },
    }
