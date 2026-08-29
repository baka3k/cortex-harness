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


def hnsw_ef_from_env() -> Optional[int]:
    """Query-time ``hnsw_ef`` override; unset env → server default."""
    raw = str(os.environ.get("QDRANT_HNSW_EF", "")).strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def search_collection(
    store: Any,
    collection: str,
    vector: Sequence[float],
    vector_name: Optional[str],
    top_k: int,
    project_id: Optional[str] = None,
    query_filter: Any = None,
) -> List[Dict[str, Any]]:
    """One narrowed, provenance-tagged vector search against a collection."""
    from tools.common.project_scope import qdrant_project_filter

    if query_filter is None:
        query_filter = qdrant_project_filter(project_id)
    kwargs: Dict[str, Any] = {}
    if vector_name:
        kwargs["using"] = vector_name
    hnsw_ef = hnsw_ef_from_env()
    if hnsw_ef is not None:
        kwargs["search_params"] = qmodels.SearchParams(hnsw_ef=hnsw_ef)
    response = store.query_points(
        collection,
        query=list(vector),
        limit=int(top_k),
        query_filter=normalize_filter(query_filter),
        with_payload=PAYLOAD_EXCLUDE_SELECTOR,
        with_vectors=False,
        **kwargs,
    )
    hits = [model_to_dict(point) for point in getattr(response, "points", response)]
    for hit in hits:
        hit["_collection"] = collection
    return hits


def merge_hits(
    per_collection_hits: Iterable[List[Dict[str, Any]]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Dedupe by point id keeping the higher score (with its provenance)."""
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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Search every (collection, vector_name) and merge to a single top_k."""
    per_collection: List[List[Dict[str, Any]]] = []
    errors: List[Dict[str, str]] = []
    for col, vector_name in collections:
        try:
            per_collection.append(
                search_collection(store, col, vector, vector_name, top_k, project_id)
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
