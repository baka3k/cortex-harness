"""Embedding-model identity marker (sentinel) for Qdrant collections.

One sentinel point per collection records which embedding model produced
its vectors. Writer chokepoints (:mod:`tools.common.local_qdrant`,
cobol's sync, the doc-side ``create_collection``) refuse to mix models;
query paths surface a visible warning instead of silently degrading.

The point id derives from a **private** uuid5 namespace — never
``NAMESPACE_URL``: real document/symbol point ids in this repo are
``uuid5(NAMESPACE_URL, ...)`` (``python_analyzer._stable_point_id``,
``graphrag_ingest_langextract._point_id``), so a shared namespace could
make the stamp overwrite a live point or a re-ingest overwrite the marker.

Keep this module dependency-light (stdlib + ``qdrant_client`` models): it
is imported through doc-tiny's flat-tree bridge too, which must not pull
in the code-tiny package graph.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from qdrant_client.http import models as qmodels

EMBED_META_FLAG = "_embed_meta"
EMBEDDING_MODEL_FIELD = "embedding_model"
VECTOR_SIZE_FIELD = "vector_size"
PROJECT_ID_FIELD = "project_id"
STAMPED_AT_FIELD = "stamped_at"

# Query-side sentinel read cache. Writers always re-probe (use_cache has no
# effect on enforce); queries reuse the read for a short TTL so a reset in
# another process is picked up without hammering the store.
DEFAULT_CACHE_TTL_SECONDS = 60.0
_CACHE_MISS = object()

# Private namespace for sentinel point ids only (uuid4, fixed forever).
PRIVATE_NAMESPACE = uuid.UUID("c1a9d7e2-4b6f-5c3a-9d28-77e0b1a4f5c6")

_META_CACHE: Dict[str, Any] = {}
_META_CACHE_LOCK = threading.Lock()


def _cache_ttl() -> float:
    raw = str(os.environ.get("EMBEDDING_MARKER_CACHE_TTL", "")).strip()
    if not raw:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS


def _cache_entry_fresh(entry: Any) -> bool:
    ttl = _cache_ttl()
    if ttl <= 0:
        return False
    try:
        _, stamped = entry
    except (TypeError, ValueError):
        return False
    return (time.monotonic() - stamped) < ttl


class EmbeddingModelMismatchError(ValueError):
    """A writer tried to mix vectors from a different embedding model."""

    def __init__(self, message: str, mismatch: Dict[str, Any]) -> None:
        super().__init__(message)
        self.mismatch = mismatch


def meta_point_id(collection: str) -> str:
    """Deterministic sentinel point id for ``collection``."""
    return str(uuid.uuid5(PRIVATE_NAMESPACE, f"{collection}:embed-meta"))


def is_meta_payload(payload: Any) -> bool:
    """Whether a point payload is the sentinel (never user data)."""
    return isinstance(payload, dict) and bool(payload.get(EMBED_META_FLAG))


def sentinel_exclusion() -> qmodels.FieldCondition:
    """``must_not`` condition hiding the sentinel from vector search."""
    return qmodels.FieldCondition(key=EMBED_META_FLAG, match=qmodels.MatchValue(value=True))


def sentinel_point(
    collection: str,
    *,
    embedding_model: str,
    vector_size: int,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the sentinel ``{"id", "payload"}`` without writing it.

    Transport-free so infra tooling holding a raw ``QdrantClient`` (which
    wants ``PointStruct``, not dicts) can wrap it itself.
    """
    return {
        "id": meta_point_id(collection),
        "payload": {
            EMBED_META_FLAG: True,
            EMBEDDING_MODEL_FIELD: str(embedding_model),
            VECTOR_SIZE_FIELD: int(vector_size),
            PROJECT_ID_FIELD: str(project_id or ""),
            STAMPED_AT_FIELD: datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


def stamp(
    store: Any,
    collection: str,
    *,
    embedding_model: str,
    vector_size: int,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write (or refresh) the sentinel point. Invalidates the read cache."""
    payload: Dict[str, Any] = {
        EMBED_META_FLAG: True,
        EMBEDDING_MODEL_FIELD: str(embedding_model),
        VECTOR_SIZE_FIELD: int(vector_size),
        PROJECT_ID_FIELD: str(project_id or ""),
        STAMPED_AT_FIELD: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    store.upsert(
        collection,
        [{
            "id": meta_point_id(collection),
            "vector": [0.0] * int(vector_size),
            "payload": payload,
        }],
        wait=True,
    )
    with _META_CACHE_LOCK:
        _META_CACHE.pop(collection, None)
    return payload


def check(
    store: Any,
    collection: str,
    *,
    use_cache: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return the collection's marker payload, or ``None`` when unstamped.

    Raises on store errors other than a missing collection — a transient
    remote failure must fail the writer closed, never look like "fresh
    collection" and launder a stale marker away. The query-side
    :func:`mismatch` wraps this with its own soft error handling.
    """
    if use_cache:
        with _META_CACHE_LOCK:
            entry = _META_CACHE.get(collection, _CACHE_MISS)
            if entry is not _CACHE_MISS and _cache_entry_fresh(entry):
                return entry[0]
    payload: Optional[Dict[str, Any]] = None
    points = store.retrieve(
        collection,
        [meta_point_id(collection)],
        with_payload=True,
        with_vectors=False,
    )
    for point in points or []:
        if isinstance(point, dict):
            raw_payload = point.get("payload")
        else:
            raw_payload = getattr(point, "payload", None)
        if is_meta_payload(raw_payload):
            payload = dict(raw_payload)
            break
    if use_cache:
        with _META_CACHE_LOCK:
            _META_CACHE[collection] = (payload, time.monotonic())
    return payload


def enforce(
    store: Any,
    collection: str,
    *,
    embedding_model: str,
    vector_size: int,
    project_id: Optional[str] = None,
    reset_hint: str = "Re-index the collection (drop + re-ingest) before switching models.",
) -> Dict[str, Any]:
    """Chokepoint gate: stamp, re-stamp, or raise.

    * no marker → stamp and proceed (fresh or pre-marker collection);
    * marker matches → proceed;
    * marker differs but the collection holds *only* the sentinel (a reset
      that kept the collection) → re-stamp and proceed;
    * marker differs otherwise → :class:`EmbeddingModelMismatchError`.
    """
    meta = check(store, collection, use_cache=False)
    if meta is None:
        return stamp(
            store,
            collection,
            embedding_model=embedding_model,
            vector_size=vector_size,
            project_id=project_id,
        )
    stamped = str(meta.get(EMBEDDING_MODEL_FIELD) or "")
    if stamped == str(embedding_model):
        return meta
    if _only_sentinel_left(store, collection):
        return stamp(
            store,
            collection,
            embedding_model=embedding_model,
            vector_size=vector_size,
            project_id=project_id,
        )
    mismatch = {
        "collection": collection,
        "stamped_model": stamped,
        "query_model": str(embedding_model),
        "stamped_at": str(meta.get(STAMPED_AT_FIELD) or ""),
    }
    raise EmbeddingModelMismatchError(
        f"Collection {collection!r} was indexed with embedding model "
        f"{stamped!r}, but this ingest uses {embedding_model!r}. Mixing the "
        f"two silently corrupts retrieval. {reset_hint}",
        mismatch,
    )


def mismatch(
    store: Any,
    collection: str,
    embedding_model: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Query-side read: mismatch descriptor or ``None`` (soft, never raises).

    Store errors degrade to "no warning" here — only the writer path is
    allowed to fail closed.
    """
    if not embedding_model:
        return None
    try:
        meta = check(store, collection)
    except Exception:  # noqa: BLE001 - soft query-side signal only
        return None
    if not meta:
        return None
    stamped = str(meta.get(EMBEDDING_MODEL_FIELD) or "")
    if not stamped or stamped == str(embedding_model):
        return None
    return {
        "collection": collection,
        "stamped_model": stamped,
        "query_model": str(embedding_model),
    }


def _only_sentinel_left(store: Any, collection: str) -> bool:
    """Whether the collection holds at most the sentinel point."""
    try:
        count = store.count(collection, count_filter=None, exact=True)
        return int(getattr(count, "count", count if isinstance(count, int) else 0)) <= 1
    except Exception:  # noqa: BLE001 - count unavailable → assume real data, raise
        return False


def reset_sentinel_cache() -> None:
    """Test helper: clear the per-process sentinel read cache."""
    with _META_CACHE_LOCK:
        _META_CACHE.clear()
