"""TTL cache for Qdrant collection metadata (vector sizes, names).

Replaces the per-process unbounded dict caches that previously cached
``get_collection_info`` results forever — including error values — and
never invalidated after a collection was recreated.

* TTL: ``MCP_COLLECTION_META_CACHE`` env — unset/other = 300 s, number =
  TTL seconds, "0" disables caching.
* Errors are never cached: a failed loader call raises through and the
  next caller retries.
* Writes (``primary_vector_sync`` and the rebuild script) call
  :func:`invalidate` so a same-process read never sees a stale layout.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from tools.common.local_qdrant import vector_sizes

DEFAULT_TTL_SECONDS = 300
MAX_ENTRIES = 64

_META_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, int]]] = {}
_LIST_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_LOCK = threading.Lock()


def _ttl_seconds() -> float:
    raw = str(os.environ.get("MCP_COLLECTION_META_CACHE", str(DEFAULT_TTL_SECONDS))).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_TTL_SECONDS)
    return value if value > 0 else float(DEFAULT_TTL_SECONDS)


def _cache_disabled() -> bool:
    return _ttl_seconds() <= 0.0


def _resolve_store(url: str, loader: Optional[Any]) -> Any:
    """``loader`` is a zero-arg callable returning the store (or None → local)."""
    if loader is None:
        from tools.common.local_qdrant import get_code_qdrant_store

        return get_code_qdrant_store(url)
    return loader()


def _evict_overflow_locked() -> None:
    while len(_META_CACHE) + len(_LIST_CACHE) > MAX_ENTRIES:
        oldest_meta = min(_META_CACHE.items(), key=lambda kv: kv[1][0], default=None)
        oldest_list = min(_LIST_CACHE.items(), key=lambda kv: kv[1][0], default=None)
        candidates = [c for c in (oldest_meta, oldest_list) if c is not None]
        if not candidates:
            return
        oldest_key, _entry = min(candidates, key=lambda c: c[1][0])
        _META_CACHE.pop(oldest_key, None)
        _LIST_CACHE.pop(oldest_key, None)


def get_collection_meta(
    url: str,
    collection: str,
    loader: Optional[Any] = None,
) -> Dict[str, int]:
    """Named/default vector sizes for one collection (cached per url+name)."""
    key = (str(url or ""), str(collection))
    now = time.monotonic()
    if not _cache_disabled():
        with _LOCK:
            entry = _META_CACHE.get(key)
            if entry is not None and now - entry[0] <= _ttl_seconds():
                return dict(entry[1])
    store = _resolve_store(url, loader)
    info = store.get_collection_info(collection)
    sizes = vector_sizes(info)
    # An empty sizes dict is useless to every consumer (it reads as "no
    # matching vector size") — don't pin it for a full TTL; refetch next
    # time instead.
    if not _cache_disabled() and sizes:
        with _LOCK:
            _META_CACHE[key] = (now, dict(sizes))
            _evict_overflow_locked()
    return sizes


def list_collections(url: str, loader: Optional[Any] = None) -> List[str]:
    """Collection names for the store behind ``url`` (cached per url)."""
    key = str(url or "")
    now = time.monotonic()
    if not _cache_disabled():
        with _LOCK:
            entry = _LIST_CACHE.get(key)
            if entry is not None and now - entry[0] <= _ttl_seconds():
                return list(entry[1])
    store = _resolve_store(url, loader)
    names = store.list_collection_names()
    if not _cache_disabled():
        with _LOCK:
            _LIST_CACHE[key] = (now, list(names))
            _evict_overflow_locked()
    return names


def invalidate(url: Optional[str] = None, collection: Optional[str] = None) -> None:
    """Drop cached metadata; ``url=None`` clears everything."""
    with _LOCK:
        if url is None:
            _META_CACHE.clear()
            _LIST_CACHE.clear()
            return
        url_key = str(url or "")
        if collection is None:
            for key in [k for k in _META_CACHE if k[0] == url_key]:
                del _META_CACHE[key]
            _LIST_CACHE.pop(url_key, None)
            return
        _META_CACHE.pop((url_key, str(collection)), None)


def reset_cache() -> None:
    """Test helper: clear all entries."""
    with _LOCK:
        _META_CACHE.clear()
        _LIST_CACHE.clear()
