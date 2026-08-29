"""Code-owner access to the shared embedded Qdrant store.

This module is the compatibility boundary for code-tiny.  Older public APIs
still call their storage argument ``qdrant_url``; the value is now either an
empty compatibility token or a local filesystem path.  Network endpoints are
rejected so active runtime code cannot accidentally bypass the owner-scoped
embedded store.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from cortex_harness.storage import (  # noqa: E402
    LocalQdrantStore,
    QdrantStore,
    QdrantStorageRole,
    create_storage,
    resolve_storage,
)
from qdrant_client.http import models as qmodels  # noqa: E402


ENV_STORAGE_PROJECT_ID = "CORTEX_STORAGE_PROJECT_ID"
ENV_HARNESS_CONFIG_PATH = "CORTEX_HARNESS_CONFIG_PATH"


class RemoteQdrantUnsupportedError(ValueError):
    """Raised when a legacy network locator reaches the local-only runtime.

    Kept as a compatibility error for code paths that explicitly opt into
    the legacy ``locator=...`` shape and want the legacy rejection
    semantics. New code should pass ``project_id=...`` and let
    :func:`get_code_qdrant_store` route through :class:`StorageFactory`.
    """


def _local_path(locator: Optional[str] = None, *, project_root: Optional[Path] = None) -> Path:
    value = str(locator or "").strip()
    if value:
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"} or "://" in value:
            raise RemoteQdrantUnsupportedError(
                "Remote Qdrant endpoints are not supported by the local runtime. "
                "Export or re-ingest the data, then use QDRANT_CODE_PATH or "
                "CORTEX_DATA_HOME."
            )
    root = Path(project_root or os.getcwd()).resolve()
    explicit = value or os.environ.get("QDRANT_CODE_PATH")
    return resolve_storage(root, qdrant_code_path=explicit).qdrant_code_path


def default_local_qdrant_path(*, project_root: Optional[Path] = None) -> str:
    """Return the resolved code-owner path without opening the database."""

    return str(_local_path(project_root=project_root))


def get_code_qdrant_store(
    locator: Optional[str] = None,
    *,
    project_root: Optional[Path] = None,
    project_id: Optional[str] = None,
) -> QdrantStore:
    """Open the cached code-owner store.

    Resolution order:

    1. ``project_id`` supplied → :class:`StorageFactory` chooses
       :class:`LocalQdrantStore` or :class:`RemoteQdrantStore` based on the
       project's ``storage_backend``.
    2. ``CORTEX_STORAGE_PROJECT_ID`` set by the MCP launcher → the same
       factory selects the runtime data source independently from any query
       ``project_id`` payload filter.
    3. ``locator`` URL-shaped → kept for backward compatibility: raises
       :class:`RemoteQdrantUnsupportedError` so legacy callers fail loudly
       instead of silently switching modes.
    4. Falls back to the local path resolution.
    """

    storage_project_id = str(
        project_id or os.environ.get(ENV_STORAGE_PROJECT_ID) or ""
    ).strip()
    if storage_project_id:
        # Lazy import to avoid a static code-tiny → tools.common.project_registry
        # import cycle when callers don't supply project_id.
        from tools.common.project_registry import resolve_project_targets

        config_path = str(os.environ.get(ENV_HARNESS_CONFIG_PATH) or "").strip()
        config_dir = Path(config_path).resolve().parent if config_path else None
        targets = resolve_project_targets(
            storage_project_id,
            config_dir=config_dir,
        )
        effective_root = project_root
        if effective_root is None and config_path:
            path = Path(config_path).resolve()
            if (
                path.parent.name == "config"
                and path.parent.parent.name == ".cortext-harness"
            ):
                effective_root = path.parents[2]
        factory = create_storage(targets, project_root=effective_root)
        return factory.get_qdrant_store(QdrantStorageRole.CODE)

    root = Path(project_root or os.getcwd()).resolve()
    path = _local_path(locator, project_root=root)
    resolved = resolve_storage(root, qdrant_code_path=path)
    return LocalQdrantStore(resolved, QdrantStorageRole.CODE)


def model_to_dict(value: Any) -> Any:
    """Convert qdrant-client models into JSON-shaped values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): model_to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [model_to_dict(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return model_to_dict(dump(exclude_none=True))
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return model_to_dict(legacy_dict(exclude_none=True))
    return value


def normalize_filter(value: Any) -> Any:
    if value is None or isinstance(value, qmodels.Filter):
        return value
    if isinstance(value, Mapping):
        return qmodels.Filter(**dict(value))
    return value


def vector_sizes(info: Any) -> dict[str, int]:
    """Return named/default vector sizes from a collection-info model."""

    vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
    if vectors is None:
        raw = model_to_dict(info)
        vectors = (((raw or {}).get("config") or {}).get("params") or {}).get("vectors")
    if isinstance(vectors, Mapping):
        if isinstance(vectors.get("size"), (int, float)):
            return {"default": int(vectors["size"])}
        sizes: dict[str, int] = {}
        for name, config in vectors.items():
            size = config.get("size") if isinstance(config, Mapping) else getattr(config, "size", None)
            if isinstance(size, (int, float)):
                sizes[str(name)] = int(size)
        return sizes
    size = getattr(vectors, "size", None)
    return {"default": int(size)} if isinstance(size, (int, float)) else {}


_INERT_TUNING_WARNED = False


def _tuning_kwargs(store: QdrantStore) -> dict[str, Any]:
    """Optional collection tuning from env (remote-mode value).

    ``QDRANT_HNSW_M`` / ``QDRANT_HNSW_EF_CONSTRUCT`` → ``HnswConfigDiff``;
    ``QDRANT_SCALAR_QUANT=1`` → int8 scalar quantization with
    ``always_ram=True``. Unset env (the default) sends nothing. Local-mode
    clients silently swallow these kwargs, so a one-time warning keeps the
    no-op from masquerading as a real tuning.
    """
    global _INERT_TUNING_WARNED
    hnsw_m = str(os.environ.get("QDRANT_HNSW_M", "")).strip()
    ef_construct = str(os.environ.get("QDRANT_HNSW_EF_CONSTRUCT", "")).strip()
    scalar_quant = str(os.environ.get("QDRANT_SCALAR_QUANT", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not hnsw_m and not ef_construct and not scalar_quant:
        return {}
    kwargs: dict[str, Any] = {}
    if hnsw_m or ef_construct:
        hnsw: dict[str, Any] = {}
        try:
            if hnsw_m:
                hnsw["m"] = int(hnsw_m)
            if ef_construct:
                hnsw["ef_construct"] = int(ef_construct)
        except ValueError as exc:
            raise ValueError(
                "QDRANT_HNSW_M / QDRANT_HNSW_EF_CONSTRUCT must be integers, got "
                f"m={hnsw_m!r} ef_construct={ef_construct!r}"
            ) from exc
        kwargs["hnsw_config"] = qmodels.HnswConfigDiff(**hnsw)
    if scalar_quant:
        kwargs["quantization_config"] = qmodels.ScalarQuantization(
            scalar=qmodels.ScalarQuantizationConfig(
                type=qmodels.ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            )
        )
    if isinstance(store, LocalQdrantStore) and not _INERT_TUNING_WARNED:
        _INERT_TUNING_WARNED = True
        print(
            "[qdrant] collection tuning kwargs are inert on local mode "
            "(HNSW/quantization apply only to a Qdrant server)."
        )
    return kwargs


def ensure_collection(
    store: QdrantStore,
    collection: str,
    vector_size: int,
    *,
    create: bool = True,
) -> None:
    if store.collection_exists(collection):
        sizes = vector_sizes(store.get_collection_info(collection))
        if sizes and vector_size not in sizes.values():
            actual = ", ".join(f"{name}={size}" for name, size in sorted(sizes.items()))
            raise ValueError(
                f"Qdrant collection {collection!r} has vector size {actual}, "
                f"but the configured embedder produces {vector_size}"
            )
        return
    if not create:
        raise LookupError(f"Qdrant collection not found: {collection}")
    store.create_collection(
        collection,
        vectors_config=qmodels.VectorParams(size=int(vector_size), distance=qmodels.Distance.COSINE),
        **_tuning_kwargs(store),
    )


def collection_info_payload(store: QdrantStore, collection: str) -> dict[str, Any]:
    info = store.get_collection_info(collection)
    return {"result": model_to_dict(info), "status": "ok"}


def collections_payload(
    store: QdrantStore,
    *,
    include_vectors: bool = False,
) -> dict[str, Any]:
    names = store.list_collection_names()
    raw = {"result": {"collections": [{"name": name} for name in names]}, "status": "ok"}
    result: dict[str, Any] = {"collections": names, "raw": raw}
    if include_vectors:
        result["vectors"] = {
            name: {"sizes": vector_sizes(store.get_collection_info(name))}
            for name in names
        }
    return result


def query_points(
    store: QdrantStore,
    collection: str,
    vector: Sequence[float],
    *,
    limit: int,
    vector_name: Optional[str] = None,
    query_filter: Any = None,
    with_payload: Any = True,
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {}
    if vector_name:
        kwargs["using"] = vector_name
    response = store.query_points(
        collection,
        query=list(vector),
        limit=int(limit),
        query_filter=normalize_filter(query_filter),
        with_payload=with_payload,
        with_vectors=False,
        **kwargs,
    )
    return [model_to_dict(point) for point in getattr(response, "points", response)]


def scroll_points(
    store: QdrantStore,
    collection: str,
    *,
    query_filter: Any = None,
    limit: int = 100,
    offset: Any = None,
    with_payload: bool = True,
    with_vectors: bool = False,
) -> tuple[list[dict[str, Any]], Any]:
    points, next_offset = store.scroll(
        collection,
        scroll_filter=normalize_filter(query_filter),
        limit=int(limit),
        offset=offset,
        with_payload=with_payload,
        with_vectors=with_vectors,
    )
    return [model_to_dict(point) for point in points], next_offset


def delete_by_filter(store: QdrantStore, collection: str, value: Any) -> None:
    store.delete(
        collection,
        filter_selector=qmodels.FilterSelector(filter=normalize_filter(value)),
        wait=True,
    )


class LocalQdrantWriter:
    """Drop-in replacement for analyzer-local REST writer classes."""

    def __init__(
        self,
        url: Optional[str],
        collection: str,
        vector_size: int,
        timeout: float = 300.0,
        retries: int = 3,
        retry_sleep: float = 2.0,
        *,
        point_transform: Optional[Callable[[Iterable[Mapping[str, Any]]], list[dict[str, Any]]]] = None,
    ) -> None:
        self.collection = collection
        self.vector_size = int(vector_size)
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep
        self._point_transform = point_transform
        # A URL-shaped locator (``http://``, ``https://``) means the project is
        # in remote mode and ``storage_overlay`` exposed the remote Qdrant
        # endpoint through ``QDRANT_CODE_PATH``. Use the shared remote store
        # so the embedding pass actually lands vectors on the remote server
        # instead of raising the legacy "remote URL not supported" error.
        api_key = os.environ.get("QDRANT_API_KEY")
        if url and (
            url.startswith("http://") or url.startswith("https://")
        ):
            from cortex_harness.storage import QdrantStorageRole
            from cortex_harness.storage.qdrant_remote import RemoteQdrantStore

            self.url = url
            self._store: QdrantStore = RemoteQdrantStore(
                url=url,
                api_key=api_key,
                timeout=timeout,
                role=QdrantStorageRole.CODE,
            )
            return
        self.url = default_local_qdrant_path() if not url else str(_local_path(url))
        self._store = get_code_qdrant_store(self.url)

    def ensure_collection(self) -> None:
        ensure_collection(self._store, self.collection, self.vector_size)

    def upsert(self, points: Sequence[Mapping[str, Any]]) -> None:
        if not points:
            return
        normalized = list(points)
        if self._point_transform is not None:
            normalized = self._point_transform(normalized)
        self._store.upsert(self.collection, normalized, wait=True)
