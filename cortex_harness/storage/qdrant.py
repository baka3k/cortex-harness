"""Shared Qdrant local-mode adapter for Cortex Harness.

Wraps ``qdrant_client.QdrantClient`` in a small, application-owned boundary
so every collection/point/scroll/search call crosses one place. The adapter:

- Resolves its storage path from :class:`cortex_harness.storage.ResolvedStorage`.
- Owns one client per resolved path per process.
- Exposes the operations used by ingest, retrieval, cleanup, reset, backfill,
  Living Docs, and validation scripts.
- Translates plain dictionaries to ``qdrant_client.models`` at one boundary.
- Provides test injection so unit tests never touch real project data.
"""

from __future__ import annotations

import atexit
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .config import QdrantStorageRole, ResolvedStorage
from .lease import StorageLease


try:  # pragma: no cover - exercised only when qdrant-client is installed
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "Local Qdrant storage requires the 'qdrant-client' package. "
        "Install dependencies from requirements.txt or pyproject.toml."
    ) from exc


# Per-process client cache keyed by absolute path. Qdrant local mode takes an
# exclusive lock on its directory, so we must not open the same path twice
# in the same process. Distinct roles live in distinct directories, so they
# cache independently.
_client_lock = threading.Lock()
_clients: dict[str, "QdrantClient"] = {}
_leases: dict[str, StorageLease] = {}


def get_client(resolved: ResolvedStorage, role: QdrantStorageRole) -> "QdrantClient":
    """Return a cached :class:`QdrantClient` for *resolved* and *role*.

    Two distinct roles (code/document) map to two distinct paths and never
    collide. Calling this twice with the same role returns the same instance;
    the lock is only contended when the first caller is initializing the
    client.
    """
    path = str(resolved.path_for_role(role))
    with _client_lock:
        existing = _clients.get(path)
        if existing is not None:
            return existing
        owner_id = resolved.code_owner_id if role == QdrantStorageRole.CODE else resolved.doc_owner_id
        lease = StorageLease(
            Path(path), instance_id=resolved.instance_id, owner_id=owner_id, backend="qdrant",
        ).acquire()
        try:
            client = QdrantClient(path=path)
        except Exception:
            lease.release()
            raise
        _clients[path] = client
        _leases[path] = lease
        return client


def reset_clients() -> None:
    """Drop cached clients (test-only convenience)."""
    with _client_lock:
        for client in _clients.values():
            try:
                client.close()
            except Exception:  # pragma: no cover - best-effort close
                pass
        for lease in _leases.values():
            lease.release()
        _clients.clear()
        _leases.clear()


# Local Qdrant imports ``portalocker`` while closing.  Ensure cached clients
# are closed before Python starts tearing down its import machinery; otherwise
# QdrantClient.__del__ can emit ``sys.meta_path is None`` during shutdown.
atexit.register(reset_clients)


class LocalQdrantStore:
    """Single owner of vector operations for one storage role.

    Every method maps to a :class:`QdrantClient` operation; no call site
    constructs raw Qdrant HTTP endpoint strings. The class accepts
    plain dictionaries where it is convenient but translates them to
    ``qdrant_client.models`` at one boundary.
    """

    def __init__(self, resolved: ResolvedStorage, role: QdrantStorageRole) -> None:
        self._resolved = resolved
        self._role = role
        self._client = get_client(resolved, role)

    @property
    def client(self) -> "QdrantClient":
        return self._client

    @property
    def role(self) -> QdrantStorageRole:
        return self._role

    @property
    def path(self) -> Path:
        return self._resolved.path_for_role(self._role)

    # ── collections ─────────────────────────────────────────────────────────

    def list_collection_names(self) -> list[str]:
        return [c.name for c in self._client.get_collections().collections]

    def collection_exists(self, name: str) -> bool:
        return self._client.collection_exists(collection_name=name)

    def get_collection_info(self, name: str) -> Any:
        return self._client.get_collection(collection_name=name)

    def create_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any:
        return self._client.create_collection(
            collection_name=name,
            vectors_config=vectors_config,
            **kwargs,
        )

    def recreate_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any:
        return self._client.recreate_collection(
            collection_name=name,
            vectors_config=vectors_config,
            **kwargs,
        )

    def delete_collection(self, name: str) -> Any:
        return self._client.delete_collection(collection_name=name)

    # ── points ──────────────────────────────────────────────────────────────

    def upsert(
        self,
        collection_name: str,
        points: Sequence[Any],
        **kwargs: Any,
    ) -> Any:
        normalized = [qmodels.PointStruct(**point) if isinstance(point, Mapping) else point for point in points]
        return self._client.upsert(
            collection_name=collection_name,
            points=normalized,
            **kwargs,
        )

    def upload_points(
        self, collection_name: str, points: Iterable[Any], **kwargs: Any,
    ) -> Any:
        normalized = (
            qmodels.PointStruct(**point) if isinstance(point, Mapping) else point
            for point in points
        )
        return self._client.upload_points(
            collection_name=collection_name, points=normalized, **kwargs,
        )

    def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        query_filter: Any = None,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> list[Any]:
        legacy_search = getattr(self._client, "search", None)
        if callable(legacy_search):
            return legacy_search(
                collection_name=collection_name, query_vector=query_vector,
                limit=limit, query_filter=query_filter, with_payload=with_payload,
                with_vectors=with_vectors, **kwargs,
            )
        response = self._client.query_points(
            collection_name=collection_name, query=query_vector, limit=limit,
            query_filter=query_filter, with_payload=with_payload,
            with_vectors=with_vectors, **kwargs,
        )
        return list(response.points)

    def query_points(
        self,
        collection_name: str,
        *,
        query: Any = None,
        limit: int = 10,
        query_filter: Any = None,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> Any:
        return self._client.query_points(
            collection_name=collection_name,
            query=query,
            limit=limit,
            query_filter=query_filter,
            with_payload=with_payload,
            with_vectors=with_vectors,
            **kwargs,
        )

    def scroll(
        self,
        collection_name: str,
        *,
        scroll_filter: Any = None,
        limit: int = 100,
        with_payload: Any = True,
        with_vectors: Any = False,
        offset: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=with_payload,
            with_vectors=with_vectors,
            offset=offset,
            **kwargs,
        )

    def retrieve(
        self,
        collection_name: str,
        ids: Sequence[Any],
        *,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> list[Any]:
        return self._client.retrieve(
            collection_name=collection_name,
            ids=ids,
            with_payload=with_payload,
            with_vectors=with_vectors,
            **kwargs,
        )

    def count(
        self,
        collection_name: str,
        *,
        count_filter: Any = None,
        exact: bool = True,
        **kwargs: Any,
    ) -> Any:
        return self._client.count(
            collection_name=collection_name,
            count_filter=count_filter,
            exact=exact,
            **kwargs,
        )

    def delete(
        self,
        collection_name: str,
        *,
        points_selector: Any = None,
        points_selector_ids: Any = None,
        filter_selector: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points_selector
        if selector is None and points_selector_ids is not None:
            selector = qmodels.PointIdsList(points=list(points_selector_ids))
        if selector is None and filter_selector is not None:
            selector = filter_selector
        if selector is None:
            raise ValueError("delete requires point IDs or a filter selector")
        return self._client.delete(
            collection_name=collection_name, points_selector=selector, **kwargs,
        )

    def set_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points if points is not None else filter
        if selector is None:
            raise ValueError("set_payload requires point IDs or a filter")
        return self._client.set_payload(
            collection_name=collection_name,
            payload=payload,
            points=selector,
            **kwargs,
        )

    def overwrite_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points if points is not None else filter
        if selector is None:
            raise ValueError("overwrite_payload requires point IDs or a filter")
        return self._client.overwrite_payload(
            collection_name=collection_name,
            payload=payload,
            points=selector,
            **kwargs,
        )

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        *,
        field_schema: Any = None,
        field_type: Any = None,
        **kwargs: Any,
    ) -> Any:
        if field_schema is None and field_type is None:
            field_schema = qmodels.PayloadSchemaType.KEYWORD
        return self._client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            field_type=field_type,
            **kwargs,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close only the client owned by *role*.

        Other roles keep their cached clients; the process-level cache is
        cleared explicitly via :func:`reset_clients` (test helper).
        """
        with _client_lock:
            existing = _clients.pop(str(self.path), None)
            lease = _leases.pop(str(self.path), None)
        if existing is None:
            if lease is not None:
                lease.release()
            return
        try:
            existing.close()
        except Exception:  # pragma: no cover - best-effort close
            pass
        finally:
            if lease is not None:
                lease.release()


def build_filter(conditions: Iterable[Mapping[str, Any]]) -> Any:
    """Translate a plain ``{"field": "x", "match": {"value": ...}}`` filter to ``models.Filter``."""
    must: list[Any] = []
    for condition in conditions:
        must.append(qmodels.FieldCondition(**condition))
    return qmodels.Filter(must=must)
